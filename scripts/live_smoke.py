"""Smoke test for Gemini Live (Japanese / English, tools, latency).

Headless: input speech is synthesized with macOS `say` (Kyoko = ja, Samantha = en),
converted to 16 kHz mono PCM with `afconvert`, and streamed at real-time pace.
Nothing is recorded from a microphone.

Per case it reports:
  - latency from end of user speech (or text send) to the first audio chunk
  - seconds of audio returned, and a "silent turn" flag (transcript but < 0.3 s audio,
    a reported gemini-3.8-live issue)
  - input / output transcription
  - tool calls (name, args, id) and whether the same call was repeated
  - usage_metadata token counts

Usage (from repo root):
  cd backend && uv run python ../scripts/live_smoke.py            # all cases
  cd backend && uv run python ../scripts/live_smoke.py --case audio_ja
Outputs: logs/live_smoke/<timestamp>/{report.json, <case>.wav}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from app.config import settings  # noqa: E402

IN_RATE = 16000
OUT_RATE = 24000
CHUNK_MS = 20

SYSTEM_INSTRUCTION = (
    "あなたは日本の小さな定食屋の接客スタッフです。"
    "お客様が話した言語（日本語・英語・中国語）で、1〜2文で短く答えてください。"
    "料理を注文されたら必ず add_item ツールを使い、ツールの結果だけを根拠に答えてください。"
    "価格やアレルゲンを推測で答えてはいけません。"
)

ADD_ITEM = types.FunctionDeclaration(
    name="add_item",
    description="カートに料理を追加する。お客様が料理を注文したときに呼ぶ。",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "item_name": types.Schema(type=types.Type.STRING, description="料理名（日本語）"),
            "quantity": types.Schema(type=types.Type.INTEGER, description="数量"),
        },
        required=["item_name", "quantity"],
    ),
    behavior=types.Behavior.BLOCKING,
)

CASES = {
    "text_ja": {"kind": "text", "text": "こんにちは。今日のおすすめは何ですか？"},
    "audio_ja": {"kind": "audio", "voice": "Kyoko", "text": "醤油ラーメンを二つください。"},
    "audio_en": {"kind": "audio", "voice": "Samantha", "text": "Hi, can I get one chicken teriyaki set, please?"},
}


@dataclass
class CaseResult:
    case: str
    ok: bool = False
    error: str = ""
    first_audio_latency_ms: float | None = None
    audio_seconds: float = 0.0
    silent_turn: bool = False
    input_transcript: str = ""
    output_transcript: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    duplicate_tool_calls: bool = False
    usage: dict = field(default_factory=dict)


def synthesize_pcm16k(text: str, voice: str) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        aiff, wav = Path(d) / "in.aiff", Path(d) / "in.wav"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", f"LEI16@{IN_RATE}", "-c", "1", str(aiff), str(wav)],
            check=True,
        )
        with wave.open(str(wav), "rb") as w:
            return w.readframes(w.getnframes())


def live_config() -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM_INSTRUCTION,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.live_voice)
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        tools=[types.Tool(function_declarations=[ADD_ITEM])],
    )


async def run_case(client: genai.Client, name: str, spec: dict, out_dir: Path) -> CaseResult:
    res = CaseResult(case=name)
    audio_out = bytearray()
    t_input_end: float | None = None
    seen_calls: set[str] = set()

    async with client.aio.live.connect(model=settings.live_model, config=live_config()) as session:

        async def receiver() -> None:
            nonlocal t_input_end
            done = False
            while not done:
                async for msg in session.receive():
                    if msg.usage_metadata:
                        res.usage = msg.usage_metadata.model_dump(exclude_none=True, mode="json")
                    if msg.tool_call:
                        responses = []
                        for fc in msg.tool_call.function_calls or []:
                            key = json.dumps([fc.name, fc.args], sort_keys=True, ensure_ascii=False)
                            if key in seen_calls:
                                res.duplicate_tool_calls = True
                            seen_calls.add(key)
                            res.tool_calls.append({"name": fc.name, "args": fc.args, "id": fc.id})
                            responses.append(
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={"status": "ok", "message": "カートに追加しました"},
                                )
                            )
                        await session.send_tool_response(function_responses=responses)
                    sc = msg.server_content
                    if not sc:
                        continue
                    if sc.input_transcription and sc.input_transcription.text:
                        res.input_transcript += sc.input_transcription.text
                    if sc.output_transcription and sc.output_transcription.text:
                        res.output_transcript += sc.output_transcription.text
                    if sc.model_turn:
                        for part in sc.model_turn.parts or []:
                            if part.inline_data and part.inline_data.data:
                                if res.first_audio_latency_ms is None and t_input_end is not None:
                                    res.first_audio_latency_ms = round((time.monotonic() - t_input_end) * 1000)
                                audio_out.extend(part.inline_data.data)
                    if sc.turn_complete:
                        # A tool call ends one receive() loop without a spoken answer;
                        # keep listening until a turn that actually produced audio/text.
                        if audio_out or res.output_transcript:
                            done = True
                        break

        recv_task = asyncio.create_task(receiver())

        if spec["kind"] == "text":
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=spec["text"])]),
                turn_complete=True,
            )
            t_input_end = time.monotonic()
        else:
            pcm = synthesize_pcm16k(spec["text"], spec["voice"])
            step = IN_RATE * 2 * CHUNK_MS // 1000
            mime = f"audio/pcm;rate={IN_RATE}"
            for i in range(0, len(pcm), step):
                await session.send_realtime_input(audio=types.Blob(data=pcm[i : i + step], mime_type=mime))
                await asyncio.sleep(CHUNK_MS / 1000)
            t_input_end = time.monotonic()
            silence = b"\x00" * step
            for _ in range(1500 // CHUNK_MS):  # 1.5 s trailing silence for server VAD
                await session.send_realtime_input(audio=types.Blob(data=silence, mime_type=mime))
                await asyncio.sleep(CHUNK_MS / 1000)

        await asyncio.wait_for(recv_task, timeout=60)

    res.audio_seconds = round(len(audio_out) / 2 / OUT_RATE, 2)
    res.silent_turn = bool(res.output_transcript) and res.audio_seconds < 0.3
    res.ok = res.audio_seconds > 0
    with wave.open(str(out_dir / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(OUT_RATE)
        w.writeframes(bytes(audio_out))
    return res


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(CASES), action="append")
    parser.add_argument("--repeat", type=int, default=1, help="run each case N times (silent-turn rate)")
    args = parser.parse_args()

    if not (settings.google_api_key or os.environ.get("GOOGLE_API_KEY")):
        print("GOOGLE_API_KEY is not set (.env). Aborting.", file=sys.stderr)
        return 2

    client = genai.Client(api_key=settings.google_api_key or None)
    out_dir = REPO_ROOT / "logs" / "live_smoke" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[CaseResult] = []
    for name in args.case or list(CASES):
        for n in range(args.repeat):
            label = name if args.repeat == 1 else f"{name}_{n + 1}"
            try:
                r = await run_case(client, label, CASES[name], out_dir)
            except Exception as e:  # report and continue with the next case
                r = CaseResult(case=label, error=f"{type(e).__name__}: {e}")
            results.append(r)
            print(json.dumps(asdict(r), ensure_ascii=False))

    (out_dir / "report.json").write_text(
        json.dumps({"model": settings.live_model, "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2)
    )
    print(f"\nreport: {out_dir / 'report.json'}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
