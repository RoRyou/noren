"""音声の会話 1 回分（タップで開始 → 一定時間話さなければ終了）。ADK run_live で Gemini Live とつなぐ。

- 会話を開くたびに、テーブルの状態と直近の会話を指示文に入れる（会話を閉じても続きのように話せる）。
- AI の返答が終わってから IDLE_CLOSE_S 秒、お客様の発話がなければ閉じる（話さなければ課金されない）。
- お客様の発話の文字起こしを見て、急病の言葉があれば AI を待たずに緊急で店員へ、
  アレルギーらしき言葉が記録されていなければ画面で登録を確認する（AI の記録漏れの保険）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable

from google.adk.agents import LlmAgent
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.models import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent.instructions import build_instruction, build_interpret_instruction
from app.agent.tools import build_tools
from app.config import settings
from app.domain.cart import cart_text
from app.domain.escalation import Reason, detect_medical
from app.domain.keywords import find_mentions
from app.domain.models import TableSession
from app.domain.table_state import Phase
from app.realtime.hub import hub
from app.services.table import TableService

log = logging.getLogger("noren.live")

IDLE_CLOSE_S = 20.0
NUDGE_AFTER_S = 4.0
# 「カートに入れました」と言ったか（読み上げの「カートに〜があります」は含めない）
CLAIM_ADDED = re.compile(r"\b(i'?ve added|i have added|added (the|your|it|them|that|those|one|two|\d))|"
                         r"(?:入れ|入れし|追加し)ました", re.I)
TRACE = bool(__import__("os").environ.get("NOREN_LIVE_TRACE"))
IN_RATE = 16000

SendJson = Callable[[dict], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]


def _vertex_kwargs() -> dict:
    return {"vertexai": True, "project": settings.google_cloud_project, "location": settings.live_location}


def usage_delta(meta) -> dict[str, int]:
    """Live の使用量を、入力・出力 × 文字・音声と、考えた分に分ける（0 は含めない）。"""
    def split(details, total: int) -> tuple[int, int]:
        if not details:
            return total or 0, 0
        text = sum(d.token_count or 0 for d in details if "TEXT" in str(d.modality).upper())
        audio = sum(d.token_count or 0 for d in details if "AUDIO" in str(d.modality).upper())
        return text, audio

    in_text, in_audio = split(meta.prompt_tokens_details, meta.prompt_token_count)
    out_text, out_audio = split(meta.candidates_tokens_details, meta.candidates_token_count)
    counts = {"in_text": in_text, "in_audio": in_audio, "out_text": out_text, "out_audio": out_audio,
              "thoughts": meta.thoughts_token_count or 0}
    return {k: v for k, v in counts.items() if v}


# このツールを呼んだ返答は、もう画面にカードや注文の欄が出ている
SHOWS_CARDS = {"recommend", "search_menu", "get_item", "read_back", "submit_order"}


def spoken_items(text: str, items: dict, lang: str, limit: int = 4) -> list[str]:
    """話した文の中に出てきた料理（言った順）。料理名（括弧の前まで）と読みで探す。"""
    from app.services.table import _kana

    said = _kana(text.lower())
    hits: list[tuple[int, str]] = []
    for item in items.values():
        if not item.published:
            continue
        names = {re.split(r"[（(]", n)[0].strip().lower() for n in (item.name.ja, item.name.get(lang)) if n}
        names |= {w for w in item.search_terms.split() if len(w) >= 3}
        pos = [said.find(_kana(n)) for n in names if len(n) >= 2 and _kana(n) in said]
        if pos:
            hits.append((min(pos), item.id))
    return [i for _, i in sorted(hits)][:limit]


class VoiceSession:
    def __init__(self, svc: TableService, session_id: str, send_json: SendJson, send_audio: SendAudio,
                 idle_close_s: float = IDLE_CLOSE_S, mode: str = "reception"):
        self.mode = mode  # reception = 接客 / interpret = 店員とお客様の通訳（ツールなし）
        self.svc = svc
        self.session_id = session_id
        self.send_json = send_json
        self.send_audio = send_audio
        self.idle_close_s = idle_close_s
        self.queue: LiveRequestQueue | None = None
        self.task: asyncio.Task | None = None
        self.idle_task: asyncio.Task | None = None
        self._last_activity = 0.0
        self.closed = asyncio.Event()
        self.turn = 0
        self.opened_at = 0.0
        self.lang = "ja"
        self.metrics: dict = {"tool_calls": [], "first_audio_ms": [], "turns": 0, "nudges": 0}
        self._input_end: float | None = None
        # 1 回の返答の中で：ツールを呼んだか・声を出したか・もう催促したか（ツールの後に黙る対策）
        self._turn_tools = False
        self._turn_spoke = False
        self._nudged = False
        self._watch: asyncio.Task | None = None
        self._awaiting_reply = False  # お客様の発言のあと、AI がまだ一度も話していない
        self.heard = 0  # お客様の発話・画面操作を受け取った回数
        self._mentions: tuple[set[str], set[str]] = (set(), set())  # 直前のお客様の発話で拾ったアレルギー・食事制限
        # 1 回の返答で話した文字と呼んだツール（「入れました」と言ったのに add_item を呼んでいない対策）
        self._turn_text = ""
        self._turn_tool_names: list[str] = []
        self._turn_added_ok = False  # この返答で add_item / update_item が実際にカートを変えたか
        self._screen_turn = False
        self._claim_checked = False

    def duration_s(self) -> float:
        """会話を開いていた秒数（費用の上限の計算に使う）。"""
        return time.monotonic() - self.opened_at if self.opened_at else 0.0

    def correlation(self) -> str:
        return f"{self.session_id}:t{self.turn}"

    async def emit(self, event: dict) -> None:
        """ツールの結果を画面へ送る（同じテーブルのすべての端末に届く）。"""
        body = dict(event)
        hub.publish(self.svc.repo.store_id, f"table:{self.session_id}", {"type": "ui", "event": body.pop("type"), **body})

    # --- 開始・終了 ---------------------------------------------------------
    async def open(self) -> None:
        await self.send_json({"type": "voice_state", "state": "connecting"})
        view = await self.svc.view(self.session_id)
        session = TableSession.model_validate(view["session"])
        self.lang = session.lang
        store = await self.svc.repo.store()
        items = await self.svc.repo.menu()
        cart_names = [f"{items[l.item_id].name.ja}×{l.qty}" + (f"（{l.for_guest}）" if l.for_guest else "")
                      for l in session.cart if l.item_id in items]
        order_lines = [f"{l['name']['ja']}×{l['qty']}[{l['status']}]" for o in view["orders"] for l in o["lines"]]
        if self.mode == "interpret":
            agent = LlmAgent(name="interpreter", model=Gemini(model=settings.live_model, client_kwargs=_vertex_kwargs()),
                             instruction=build_interpret_instruction(self.lang), tools=[])
        else:
            instruction = build_instruction(store, session, Phase(view["phase"]), cart_names, order_lines, items)
            agent = LlmAgent(
                name="reception",
                model=Gemini(model=settings.live_model, client_kwargs=_vertex_kwargs()),
                instruction=instruction,
                tools=build_tools(self.svc, self.session_id, self.lang, self.emit, self.correlation, lambda: self.heard),
                generate_content_config=(
                    types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_budget=settings.live_thinking_budget))
                    if settings.live_thinking_budget is not None else None),
            )
        service = InMemorySessionService()
        runner = Runner(app_name="noren", agent=agent, session_service=service)
        adk_session = await service.create_session(app_name="noren", user_id=self.session_id)
        self.queue = LiveRequestQueue()
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.live_voice))),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=(
                types.RealtimeInputConfig(automatic_activity_detection=types.AutomaticActivityDetection(
                    silence_duration_ms=settings.live_silence_ms))
                if settings.live_silence_ms is not None else None),
        )
        self.opened_at = time.monotonic()
        self.task = asyncio.create_task(self._consume(runner, adk_session.id, run_config))
        await self.send_json({"type": "voice_state", "state": "listening", "mode": self.mode})
        self._arm_idle()

    async def close(self, reason: str = "idle") -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        self._cancel_watch()
        if self.idle_task:
            self.idle_task.cancel()
        if self.queue:
            self.queue.close()
        if self.task and not self.task.done():
            self.task.cancel()
        await self.send_json({"type": "voice_state", "state": "closed", "reason": reason})

    def _arm_idle(self) -> None:
        """最後の動き（お客様の発話・画面操作、AI の声・文字・ツール）から idle_close_s 秒たったら閉じる。
        AI がツールを続けて呼んでいる間は閉じない（以前は返答の途中で閉じ、注文が途切れた：ZI-065）。"""
        self._last_activity = time.monotonic()
        if self.idle_task and not self.idle_task.done():
            return

        async def watch():
            while not self.closed.is_set():
                left = self.idle_close_s - (time.monotonic() - self._last_activity)
                if left <= 0:
                    await self.close("idle")
                    return
                await asyncio.sleep(min(left, 1.0))

        self.idle_task = asyncio.create_task(watch())

    # --- 入力 -------------------------------------------------------------
    def push_audio(self, pcm16k: bytes) -> None:
        if self.queue and not self.closed.is_set():
            self.queue.send_realtime(types.Blob(data=pcm16k, mime_type=f"audio/pcm;rate={IN_RATE}"))

    def mark_input_end(self) -> None:
        self._input_end = time.monotonic()

    async def push_text(self, text: str) -> None:
        if not self.queue or self.closed.is_set():
            return
        self.turn += 1
        self.heard += 1
        self._arm_idle()
        await self._on_user_final(text)
        self._input_end = time.monotonic()
        self._new_turn()
        self.queue.send_content(types.Content(role="user", parts=[types.Part(text=text)]))

    def push_screen_action(self, text_ja: str) -> None:
        """お客様が画面で押した結果を会話に伝える（AI が続きを進められるように）。"""
        if self.queue and not self.closed.is_set():
            self.heard += 1
            self._arm_idle()
            self._input_end = time.monotonic()
            self._new_turn(screen=True)  # 画面で入れた料理を AI が「入れました」と言うのは正しい
            self.queue.send_content(types.Content(role="user", parts=[types.Part(text=f"[画面操作] {text_ja}")]))

    def _new_turn(self, screen: bool = False) -> None:
        self._awaiting_reply = True
        self._turn_tools = self._turn_spoke = self._nudged = False
        self._turn_text, self._turn_tool_names, self._turn_added_ok = "", [], False
        self._screen_turn, self._claim_checked = screen, False
        self._cancel_watch()

    async def _after_tool_turn(self) -> None:
        """ツールの後の返答を待つ。3.8 Live はツールの後にいったん turn_complete を出し、それから話す。
        まれに話さないまま終わるので、NUDGE_AFTER_S 秒たっても声がなければ 1 回だけ結果を伝えるよう促す。"""
        try:
            await asyncio.sleep(NUDGE_AFTER_S)
            # ツールの前にもうしっかり話していたなら、ツールの後に黙っても問題ない（促すと同じことを二度言う）。
            # 「確認します」程度の短い前置きだけなら促す
            if not self._nudged and len(self._turn_text) < 40 and self.queue and not self.closed.is_set():
                self._nudged = True
                self.metrics["nudges"] += 1
                log.info("silent after tools; nudging (%s)", self.correlation())
                self.queue.send_content(types.Content(role="user", parts=[types.Part(
                    text="[システム] お客様の直前の発言に、短く答えてください（ツールを使ったならその結果も）。")]))
                await asyncio.sleep(NUDGE_AFTER_S)
            await self.send_json({"type": "turn_complete"})
        except asyncio.CancelledError:
            pass

    async def _show_spoken_items(self) -> None:
        """AI が話の中で料理名を言ったのにカードを出していなければ、その料理のカードを画面に出す（ZI-083）。"""
        if not self._turn_text or set(self._turn_tool_names) & SHOWS_CARDS:
            return
        items = await self.svc.repo.menu()
        found = spoken_items(self._turn_text, items, self.lang)
        if not found:
            return
        session = await self.svc.repo.session(self.session_id)
        await self.emit({"type": "show_items", "items": [self.svc.card(items[i], session) for i in found], "source": "speech"})

    async def _check_claims(self) -> None:
        """「カートに入れました」と言ったのに、この返答で add_item / update_item がカートを変えていなければ
        （呼んでいない・断られた）、
        いまのカートを添えて 1 回だけ直させる（ZI-068。画面で入れた料理の返答は除く）。"""
        if self._claim_checked or self._screen_turn or not CLAIM_ADDED.search(self._turn_text):
            return
        if self._turn_added_ok:
            return
        self._claim_checked = True
        if not self.queue or self.closed.is_set():
            return
        session = await self.svc.repo.session(self.session_id)
        items = await self.svc.repo.menu()
        cart = "、".join(cart_text(session.cart, items)) or "空"
        self.metrics["claim_fixes"] = self.metrics.get("claim_fixes", 0) + 1
        log.info("claimed add without add_item (%s): %s", self.correlation(), self._turn_text[:80])
        self.queue.send_content(types.Content(role="user", parts=[types.Part(text=(
            f"[システム] いまの返答で「カートに入れた」と言いましたが、add_item は呼ばれていません。いまのカート：{cart}。"
            "お客様が頼んだ料理でカートにないものだけ add_item を呼び、入れられなかったものはお客様に訂正してください。"))]))

    def _cancel_watch(self) -> None:
        if self._watch:
            self._watch.cancel()
            self._watch = None

    # --- 出力 -------------------------------------------------------------
    async def _consume(self, runner: Runner, adk_session_id: str, run_config: RunConfig) -> None:
        user_partial = ""
        ai_partial = ""
        try:
            async for ev in runner.run_live(user_id=self.session_id, session_id=adk_session_id,
                                            live_request_queue=self.queue, run_config=run_config):
                self._last_activity = time.monotonic()  # AI が動いている間は閉じない
                if TRACE and ev.usage_metadata:
                    log.info("usage %s %s", self.correlation(), ev.usage_metadata.model_dump(exclude_none=True))
                if ev.usage_metadata and (delta := usage_delta(ev.usage_metadata)):  # 返答ごとの使用量（累計ではない）
                    usage = self.metrics.setdefault("usage", {})
                    for k, v in delta.items():
                        usage[k] = usage.get(k, 0) + v
                    try:
                        await self.svc.repo.add_usage(self.session_id, delta)
                    except Exception:  # 記録の失敗で会話を止めない
                        log.exception("failed to record usage (%s)", self.correlation())
                if TRACE:
                    log.info("ev %s fc=%s fr=%s audio=%s out=%s tc=%s partial=%s", self.correlation(),
                             [f.name for f in ev.get_function_calls()], [f.name for f in ev.get_function_responses()],
                             bool(ev.content and any(p.inline_data for p in ev.content.parts or [])),
                             (ev.output_transcription.text[:30] if ev.output_transcription and ev.output_transcription.text else ""),
                             ev.turn_complete, ev.partial)
                if ev.error_code or ev.error_message:
                    await self.send_json({"type": "error", "message": f"{ev.error_code}: {ev.error_message}"})
                if ev.input_transcription and ev.input_transcription.text:
                    self.heard += 1
                    self._arm_idle()
                    if ev.partial:
                        user_partial += ev.input_transcription.text
                        await self.send_json({"type": "transcript", "role": "user", "text": user_partial, "final": False})
                    else:
                        self.turn += 1
                        self._new_turn()
                        text = ev.input_transcription.text
                        user_partial = ""
                        await self.send_json({"type": "transcript", "role": "user", "text": text, "final": True})
                        await self._on_user_final(text)
                if ev.output_transcription and ev.output_transcription.text:
                    if ev.partial:  # 途中の文字は差分で届くので、ここでつなげてから送る
                        ai_partial += ev.output_transcription.text
                        await self.send_json({"type": "transcript", "role": "ai", "text": ai_partial, "final": False})
                    else:
                        ai_partial = ""
                        self._turn_text += ev.output_transcription.text
                        await self.send_json({"type": "transcript", "role": "ai", "text": ev.output_transcription.text,
                                              "final": True})
                        await self.svc.repo.append_turn(self.session_id, "ai", ev.output_transcription.text)
                for fc in ev.get_function_calls():
                    self.metrics["tool_calls"].append(fc.name)
                    self._turn_tools = True
                    self._turn_tool_names.append(fc.name)
                for fr in ev.get_function_responses():
                    r = fr.response or {}
                    if (fr.name == "add_item" and r.get("decision") == "ok") or (fr.name == "update_item" and r.get("ok")):
                        self._turn_added_ok = True
                    self._turn_spoke = False  # 見るのはツールの後に話したかどうか
                    self._cancel_watch()
                if ev.content and ev.content.parts:
                    for p in ev.content.parts:
                        if p.inline_data and p.inline_data.data and (p.inline_data.mime_type or "").startswith("audio"):
                            if self._input_end is not None:
                                self.metrics["first_audio_ms"].append(round((time.monotonic() - self._input_end) * 1000))
                                self._input_end = None
                            self._turn_spoke = True
                            self._awaiting_reply = False
                            self._cancel_watch()
                            await self.send_audio(p.inline_data.data)
                if ev.interrupted:
                    await self.send_json({"type": "interrupted"})
                if ev.turn_complete:
                    self.metrics["turns"] += 1
                    if ai_partial:  # 確定の文字起こしが来ないまま区切られた → 途中までの文字を確定として送る
                        self._turn_text += ai_partial
                        await self.send_json({"type": "transcript", "role": "ai", "text": ai_partial, "final": True})
                        await self.svc.repo.append_turn(self.session_id, "ai", ai_partial)
                        ai_partial = ""
                    self._arm_idle()
                    self._cancel_watch()
                    # 声がまだない：ツールの直後（この後に話すはず）か、何も言わずに終わった（まれに起きる）→ 待って促す
                    if not self._turn_spoke and (self._turn_tools or self._turn_text == "" and self._awaiting_reply):
                        self._watch = asyncio.create_task(self._after_tool_turn())
                    else:
                        await self.send_json({"type": "turn_complete"})
                        await self._check_claims()
                        await self._check_mentions()
                        await self._show_spoken_items()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # 会話は閉じても、画面のタッチ操作は続けられる
            log.exception("live session failed")
            await self.send_json({"type": "error", "message": str(e)[:200], "fallback": "touch"})
            await self.close("error")

    async def _on_user_final(self, text: str) -> None:
        await self.svc.repo.append_turn(self.session_id, "guest", text)
        if detect_medical(text):  # AI の判断を待たない
            await self.svc.escalate(self.session_id, Reason.medical, text, ["症状"], actor="keyword_guard",
                                    correlation_id=self.correlation(), translated=False)
            await self.emit({"type": "escalated", "reason": "medical", "urgent": True})
        self._mentions = find_mentions(text)  # AI がこの返答で記録したかを、返答の後に確かめる

    async def _check_mentions(self) -> None:
        """お客様が言ったアレルギー・食事制限を、AI が記録しなかったときだけ画面で登録を確認する（AI の記録漏れの保険）。"""
        allergens, diets = self._mentions
        self._mentions = (set(), set())
        if allergens or diets:
            session = await self.svc.repo.session(self.session_id)
            known_a = {a for g in session.guests for a in g.allergies}
            known_d = {d for g in session.guests for d in g.diets}
            missing_a, missing_d = allergens - known_a, diets - known_d
            if missing_a or missing_d:
                await self.emit({"type": "confirm_declaration", "allergies": sorted(missing_a), "diets": sorted(missing_d)})
                if self.queue and not self.closed.is_set():  # 画面で押させず、AI に会話で確かめさせる（ZI-079）
                    words = "、".join(sorted(missing_a) + sorted(missing_d))
                    self.queue.send_content(types.Content(role="user", parts=[types.Part(text=(
                        f"[システム] お客様が食事の制限（{words}）に触れましたが、まだ記録されていません。"
                        "どなたのことかを会話で確かめ、set_dietary_profile で記録してください。"))]))
