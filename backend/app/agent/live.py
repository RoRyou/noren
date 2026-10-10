"""音声の会話 1 回分（タップで開始 → 一定時間話さなければ終了）。ADK run_live で Gemini Live とつなぐ。

- 会話を開くたびに、テーブルの状態と直近の会話を指示文に入れる（会話を閉じても続きのように話せる）。
- AI の返答が終わってから IDLE_CLOSE_S 秒、お客様の発話がなければ閉じる（話さなければ課金されない）。
- お客様の発話の文字起こしを見て、急病の言葉があれば AI を待たずに緊急で店員へ、
  アレルギーらしき言葉が記録されていなければ画面で登録を確認する（AI の記録漏れの保険）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from google.adk.agents import LlmAgent
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.models import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent.instructions import build_instruction
from app.agent.tools import build_tools
from app.config import settings
from app.domain.escalation import Reason, detect_medical
from app.domain.keywords import find_mentions
from app.domain.models import TableSession
from app.domain.table_state import Phase
from app.realtime.hub import hub
from app.services.table import TableService

log = logging.getLogger("noren.live")

IDLE_CLOSE_S = 20.0
IN_RATE = 16000

SendJson = Callable[[dict], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]


def _vertex_kwargs() -> dict:
    return {"vertexai": True, "project": settings.google_cloud_project, "location": settings.live_location}


class VoiceSession:
    def __init__(self, svc: TableService, session_id: str, send_json: SendJson, send_audio: SendAudio,
                 idle_close_s: float = IDLE_CLOSE_S):
        self.svc = svc
        self.session_id = session_id
        self.send_json = send_json
        self.send_audio = send_audio
        self.idle_close_s = idle_close_s
        self.queue: LiveRequestQueue | None = None
        self.task: asyncio.Task | None = None
        self.idle_task: asyncio.Task | None = None
        self.closed = asyncio.Event()
        self.turn = 0
        self.opened_at = 0.0
        self.lang = "ja"
        self.metrics: dict = {"tool_calls": [], "first_audio_ms": [], "turns": 0}
        self._input_end: float | None = None

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
        instruction = build_instruction(store, session, Phase(view["phase"]), cart_names, order_lines)

        agent = LlmAgent(
            name="reception",
            model=Gemini(model=settings.live_model, client_kwargs=_vertex_kwargs()),
            instruction=instruction,
            tools=build_tools(self.svc, self.session_id, self.lang, self.emit, self.correlation),
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
        )
        self.opened_at = time.monotonic()
        self.task = asyncio.create_task(self._consume(runner, adk_session.id, run_config))
        await self.send_json({"type": "voice_state", "state": "listening"})
        self._arm_idle()

    async def close(self, reason: str = "idle") -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        if self.idle_task:
            self.idle_task.cancel()
        if self.queue:
            self.queue.close()
        if self.task and not self.task.done():
            self.task.cancel()
        await self.send_json({"type": "voice_state", "state": "closed", "reason": reason})

    def _arm_idle(self) -> None:
        if self.idle_task:
            self.idle_task.cancel()

        async def wait():
            await asyncio.sleep(self.idle_close_s)
            await self.close("idle")

        self.idle_task = asyncio.create_task(wait())

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
        self._arm_idle()
        await self._on_user_final(text)
        self._input_end = time.monotonic()
        self.queue.send_content(types.Content(role="user", parts=[types.Part(text=text)]))

    def push_screen_action(self, text_ja: str) -> None:
        """お客様が画面で押した結果を会話に伝える（AI が続きを進められるように）。"""
        if self.queue and not self.closed.is_set():
            self._arm_idle()
            self._input_end = time.monotonic()
            self.queue.send_content(types.Content(role="user", parts=[types.Part(text=f"[画面操作] {text_ja}")]))

    # --- 出力 -------------------------------------------------------------
    async def _consume(self, runner: Runner, adk_session_id: str, run_config: RunConfig) -> None:
        user_partial = ""
        try:
            async for ev in runner.run_live(user_id=self.session_id, session_id=adk_session_id,
                                            live_request_queue=self.queue, run_config=run_config):
                if ev.error_code or ev.error_message:
                    await self.send_json({"type": "error", "message": f"{ev.error_code}: {ev.error_message}"})
                if ev.input_transcription and ev.input_transcription.text:
                    self._arm_idle()
                    if ev.partial:
                        user_partial += ev.input_transcription.text
                        await self.send_json({"type": "transcript", "role": "user", "text": user_partial, "final": False})
                    else:
                        self.turn += 1
                        text = ev.input_transcription.text
                        user_partial = ""
                        await self.send_json({"type": "transcript", "role": "user", "text": text, "final": True})
                        await self._on_user_final(text)
                if ev.output_transcription and ev.output_transcription.text:
                    await self.send_json({"type": "transcript", "role": "ai", "text": ev.output_transcription.text,
                                          "final": not ev.partial})
                    if not ev.partial:
                        await self.svc.repo.append_turn(self.session_id, "ai", ev.output_transcription.text)
                for fc in ev.get_function_calls():
                    self.metrics["tool_calls"].append(fc.name)
                if ev.content and ev.content.parts:
                    for p in ev.content.parts:
                        if p.inline_data and p.inline_data.data and (p.inline_data.mime_type or "").startswith("audio"):
                            if self._input_end is not None:
                                self.metrics["first_audio_ms"].append(round((time.monotonic() - self._input_end) * 1000))
                                self._input_end = None
                            await self.send_audio(p.inline_data.data)
                if ev.interrupted:
                    await self.send_json({"type": "interrupted"})
                if ev.turn_complete:
                    self.metrics["turns"] += 1
                    await self.send_json({"type": "turn_complete"})
                    self._arm_idle()
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
        allergens, diets = find_mentions(text)
        if allergens or diets:
            session = await self.svc.repo.session(self.session_id)
            known_a = {a for g in session.guests for a in g.allergies}
            known_d = {d for g in session.guests for d in g.diets}
            missing_a, missing_d = allergens - known_a, diets - known_d
            if missing_a or missing_d:
                await self.emit({"type": "confirm_declaration", "allergies": sorted(missing_a), "diets": sorted(missing_d)})
