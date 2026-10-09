"""Spike: can the live (voice) root agent delegate to text sub-agents under ADK run_live?

Compares, on the same spoken question, how the root `gemini-3.8-live` agent reaches menu data:
  direct       root calls a deterministic function tool                       (baseline)
  agent_tool   root calls AgentTool(menu_agent); menu_agent calls the tool    (2 text LLM calls)
  single_turn  menu_agent attached as sub_agents with mode='single_turn'      (ADK 2.x recommended)
  agent_1call  root calls AgentTool(menu_agent); candidates are fetched by code
               and put in the prompt, so the sub-agent is a single LLM call
  llm_tool     same single LLM call, but made directly with google-genai inside a plain
               function tool (no ADK sub-runner), to isolate the AgentTool overhead

Per run it reports latency from end of user speech to first audio, how long the root tool call
took (= sub-agent time), whether the sub-agent actually ran, silent turns and duplicate calls.

Usage (from repo root):
  cd backend && uv run python ../scripts/agent_tool_spike.py --repeat 3
  cd backend && uv run python ../scripts/agent_tool_spike.py --variant agent_tool --query ja
Outputs: logs/agent_tool_spike/<timestamp>/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from google.adk.agents import LlmAgent  # noqa: E402
from google.adk.agents.live_request_queue import LiveRequestQueue  # noqa: E402
from google.adk.agents.run_config import RunConfig, StreamingMode  # noqa: E402
from google.adk.models import Gemini  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.adk.tools.agent_tool import AgentTool  # noqa: E402
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from app.config import settings  # noqa: E402
from live_smoke import IN_RATE, CHUNK_MS, synthesize_pcm16k  # noqa: E402

SUB_MODEL = "gemini-2.5-flash-lite"  # only fast text model served regionally (us-central1)
SUB_LOCATION = "us-central1"

MENU = [
    {"id": "m01", "name": "枝豆", "tags": ["vegetarian", "not_spicy"], "allergens": ["soy"], "price": 418},
    {"id": "m02", "name": "冷奴", "tags": ["vegetarian", "not_spicy"], "allergens": ["soy"], "price": 385},
    {"id": "m03", "name": "鶏の唐揚げ", "tags": ["chicken", "not_spicy"], "allergens": ["wheat", "soy"], "price": 638},
    {"id": "m04", "name": "豚キムチ", "tags": ["pork", "spicy"], "allergens": ["soy", "wheat"], "price": 682},
    {"id": "m05", "name": "麻婆豆腐", "tags": ["pork", "spicy"], "allergens": ["soy", "wheat"], "price": 748},
    {"id": "m06", "name": "だし巻き玉子", "tags": ["fish_dashi", "not_spicy"], "allergens": ["egg"], "price": 528},
    {"id": "m07", "name": "海老の天ぷら", "tags": ["seafood", "not_spicy"], "allergens": ["shrimp", "wheat", "egg"], "price": 858},
    {"id": "m08", "name": "ピリ辛こんにゃく", "tags": ["vegetarian", "spicy"], "allergens": [], "price": 352},
]

QUERIES = {
    "ja": {"voice": "Kyoko", "text": "豚肉を使っていない、辛くないおつまみはありますか？"},
    "en": {"voice": "Samantha", "text": "Do you have any snacks without pork that are not spicy?"},
}

VARIANTS = ["direct", "agent_tool", "single_turn", "agent_1call", "llm_tool"]

TOOL_LOG: list[dict] = []


def search_menu(keyword: str) -> dict:
    """メニューを検索する。keyword には料理名・食材・特徴（例: 辛くない、豚肉なし）を入れる。"""
    TOOL_LOG.append({"tool": "search_menu", "keyword": keyword, "t": time.monotonic()})
    return {"items": MENU}


_SUB_CLIENT: genai.Client | None = None


async def pick_menu(request: str) -> dict:
    """メニュー担当に、条件に合う料理を選んでもらう。request には条件（例: 豚肉なし・辛くない）を入れる。"""
    global _SUB_CLIENT
    _SUB_CLIENT = _SUB_CLIENT or genai.Client(**_vertex(SUB_LOCATION))
    menu_json = json.dumps(MENU, ensure_ascii=False)
    r = await _SUB_CLIENT.aio.models.generate_content(
        model=SUB_MODEL,
        contents=f"条件: {request}",
        config=types.GenerateContentConfig(
            system_instruction=SUB_RULES.format(extra=f"メニュー（これ以外は存在しない）: {menu_json}"),
            response_mime_type="application/json",
        ),
    )
    return json.loads(r.text)


def _vertex(location: str) -> dict:
    return {"vertexai": True, "project": settings.google_cloud_project, "location": location}


ROOT_RULES = (
    "あなたは居酒屋の接客スタッフです。お客様が話した言語で、1〜2文で短く答えてください。"
    "料理についての質問には必ず {tool} を使い、その結果だけを根拠に答えてください。推測で答えてはいけません。"
)
SUB_RULES = (
    "あなたは居酒屋のメニュー担当です。依頼された条件に合う料理を最大3つ選び、"
    'JSON {{"items":[{{"id":..,"name":..,"reason":..}}]}} だけを返してください。{extra}'
)


def build_root(variant: str) -> LlmAgent:
    live = Gemini(model=settings.live_model, client_kwargs=_vertex(settings.live_location))
    sub_model = Gemini(model=SUB_MODEL, client_kwargs=_vertex(SUB_LOCATION))

    if variant == "direct":
        return LlmAgent(name="host", model=live, instruction=ROOT_RULES.format(tool="search_menu"), tools=[search_menu])

    if variant == "llm_tool":
        return LlmAgent(name="host", model=live, instruction=ROOT_RULES.format(tool="pick_menu"), tools=[pick_menu])

    if variant == "agent_1call":
        menu_json = json.dumps(MENU, ensure_ascii=False)
        sub = LlmAgent(
            name="menu_agent",
            model=sub_model,
            description="メニューから条件に合う料理を選ぶ担当。条件を request に入れて呼ぶ。",
            instruction=SUB_RULES.format(extra=f"メニュー（これ以外は存在しない）: {menu_json}"),
        )
        return LlmAgent(name="host", model=live, instruction=ROOT_RULES.format(tool="menu_agent"), tools=[AgentTool(sub)])

    sub = LlmAgent(
        name="menu_agent",
        model=sub_model,
        description="メニューから条件に合う料理を選ぶ担当。条件を request に入れて呼ぶ。",
        instruction=SUB_RULES.format(extra="必ず search_menu で調べてから選んでください。"),
        tools=[search_menu],
    )
    if variant == "agent_tool":
        return LlmAgent(name="host", model=live, instruction=ROOT_RULES.format(tool="menu_agent"), tools=[AgentTool(sub)])
    if variant == "single_turn":
        sub.mode = "single_turn"
        return LlmAgent(name="host", model=live, instruction=ROOT_RULES.format(tool="menu_agent"), sub_agents=[sub])
    raise ValueError(variant)


@dataclass
class RunResult:
    variant: str
    query: str
    ok: bool = False
    error: str = ""
    first_audio_ms: float | None = None  # end of speech -> first audio chunk (may be a filler)
    answer_audio_ms: float | None = None  # end of speech -> first audio after the tool response
    tool_ms: float | None = None  # root function_call event -> function_response event
    root_calls: list[str] = field(default_factory=list)
    sub_tool_calls: int = 0  # search_menu calls made inside the sub-agent
    duplicate_calls: bool = False
    silent_turn: bool = False
    audio_seconds: float = 0.0
    input_transcript: str = ""
    output_transcript: str = ""
    tool_response_preview: str = ""


async def run_once(variant: str, qname: str) -> RunResult:
    res = RunResult(variant=variant, query=qname)
    TOOL_LOG.clear()
    root = build_root(variant)
    svc = InMemorySessionService()
    runner = Runner(app_name="spike", agent=root, session_service=svc)
    session = await svc.create_session(app_name="spike", user_id="u")
    queue = LiveRequestQueue()
    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.live_voice))
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    t_end: float | None = None
    t_call: float | None = None
    t_resp: float | None = None
    audio_bytes = 0
    seen: set[str] = set()
    out_partial = out_final = in_partial = in_final = ""

    async def consume() -> None:
        nonlocal t_call, t_resp, audio_bytes, out_partial, out_final, in_partial, in_final
        async for ev in runner.run_live(user_id="u", session_id=session.id, live_request_queue=queue, run_config=run_config):
            now = time.monotonic()
            if ev.error_code or ev.error_message:
                res.error = f"{ev.error_code}: {ev.error_message}"
            for fc in ev.get_function_calls():
                key = json.dumps([fc.name, fc.args], sort_keys=True, ensure_ascii=False)
                if key in seen:
                    res.duplicate_calls = True
                seen.add(key)
                res.root_calls.append(f"{ev.author}:{fc.name}")
                t_call = t_call or now
            for fr in ev.get_function_responses():
                t_resp = t_resp or now
                res.tool_response_preview = json.dumps(fr.response, ensure_ascii=False)[:300]
            if ev.input_transcription and ev.input_transcription.text:
                if ev.partial:
                    in_partial += ev.input_transcription.text
                else:
                    in_final += ev.input_transcription.text
            if ev.output_transcription and ev.output_transcription.text:
                if ev.partial:
                    out_partial += ev.output_transcription.text
                else:
                    out_final += ev.output_transcription.text
            if ev.content and ev.content.parts:
                for p in ev.content.parts:
                    if p.inline_data and p.inline_data.data and (p.inline_data.mime_type or "").startswith("audio"):
                        if t_end is not None and res.first_audio_ms is None:
                            res.first_audio_ms = round((now - t_end) * 1000)
                        if t_end is not None and t_resp is not None and res.answer_audio_ms is None:
                            res.answer_audio_ms = round((now - t_end) * 1000)
                        audio_bytes += len(p.inline_data.data)
            if ev.turn_complete and t_resp is not None and audio_bytes and res.answer_audio_ms is not None:
                return

    task = asyncio.create_task(consume())
    try:
        q = QUERIES[qname]
        pcm = synthesize_pcm16k(q["text"], q["voice"])
        step = IN_RATE * 2 * CHUNK_MS // 1000
        mime = f"audio/pcm;rate={IN_RATE}"
        for i in range(0, len(pcm), step):
            queue.send_realtime(types.Blob(data=pcm[i : i + step], mime_type=mime))
            await asyncio.sleep(CHUNK_MS / 1000)
        t_end = time.monotonic()
        silence = b"\x00" * step
        for _ in range(1500 // CHUNK_MS):
            if task.done():
                break
            queue.send_realtime(types.Blob(data=silence, mime_type=mime))
            await asyncio.sleep(CHUNK_MS / 1000)
        await asyncio.wait_for(task, timeout=45)
    except Exception as e:
        res.error = res.error or f"{type(e).__name__}: {e}"
    finally:
        queue.close()
        if not task.done():
            task.cancel()

    if t_call and t_resp:
        res.tool_ms = round((t_resp - t_call) * 1000)
    res.sub_tool_calls = len(TOOL_LOG) if variant != "direct" else 0
    res.audio_seconds = round(audio_bytes / 2 / 24000, 2)
    res.input_transcript = in_final or in_partial
    res.output_transcript = out_final or out_partial
    res.silent_turn = bool(res.output_transcript) and res.audio_seconds < 0.3
    res.ok = not res.error and res.answer_audio_ms is not None
    return res


def summarize(results: list[RunResult]) -> dict:
    out: dict = {}
    for v in sorted({r.variant for r in results}):
        rs = [r for r in results if r.variant == v]
        ok = [r for r in rs if r.ok]

        def med(attr: str) -> float | None:
            xs = [getattr(r, attr) for r in ok if getattr(r, attr) is not None]
            return round(statistics.median(xs)) if xs else None

        out[v] = {
            "runs": len(rs),
            "ok": len(ok),
            "median_answer_audio_ms": med("answer_audio_ms"),
            "median_first_audio_ms": med("first_audio_ms"),
            "median_tool_ms": med("tool_ms"),
            "silent_turns": sum(r.silent_turn for r in rs),
            "duplicate_calls": sum(r.duplicate_calls for r in rs),
            "errors": [r.error for r in rs if r.error],
        }
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=VARIANTS, action="append")
    parser.add_argument("--query", choices=sorted(QUERIES), action="append")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    if settings.google_application_credentials:
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", settings.google_application_credentials)
    out_dir = REPO_ROOT / "logs" / "agent_tool_spike" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[RunResult] = []
    for _ in range(args.repeat):
        for v in args.variant or VARIANTS:
            for q in args.query or ["ja", "en"]:
                r = await run_once(v, q)
                results.append(r)
                print(json.dumps(asdict(r), ensure_ascii=False), flush=True)

    summary = summarize(results)
    (out_dir / "report.json").write_text(
        json.dumps({"live_model": settings.live_model, "sub_model": SUB_MODEL, "summary": summary,
                    "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2)
    )
    print("\nsummary:", json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report: {out_dir / 'report.json'}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
