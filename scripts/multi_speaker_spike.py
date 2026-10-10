"""検証：gemini-3.8-live で複数人のテーブルに対応できるか（話者分離 / proactive audio / 接続時間）。

確かめること：
  1. 入力の文字起こしで `diarization=True` にすると、2 人が続けて注文したときに
     別々の `speaker_label` が返るか
  2. `proactivity.proactive_audio=True` のとき、お客様同士の会話（タブレットへの発話ではない）
     には黙り、話しかけられたときは答えるか
  3. `live.connect` にどれくらいかかるか（「タップして話す」までの待ち時間）

入力音声は macOS の `say` で合成する（言語ごとに 2 種類の声）。

使い方（リポジトリのルートから）：
  cd backend && uv run python ../scripts/multi_speaker_spike.py
  cd backend && uv run python ../scripts/multi_speaker_spike.py --case side_chat_en_on --repeat 3
出力：logs/multi_speaker_spike/<タイムスタンプ>/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from app.config import settings  # noqa: E402
from app.genai_client import make_client  # noqa: E402
from live_smoke import ADD_ITEM, CHUNK_MS, IN_RATE, synthesize_pcm16k  # noqa: E402

EN_A, EN_B = "Samantha", "Daniel (英語（イギリス）)"
JA_A, JA_B = "Kyoko", "Eddy (日本語（日本）)"

SYSTEM_INSTRUCTION = (
    "あなたは居酒屋のテーブルに置かれたタブレットの接客 AI です。"
    "お客様が話した言語で、1〜2文で短く答えてください。"
    "料理を注文されたら add_item ツールを使ってください。"
    "お客様同士の会話で、あなたに話しかけていないときは返事をしないでください。"
)
VOCAB = ["生ビール", "枝豆", "だし巻き玉子", "冷奴", "唐揚げ", "edamame", "draft beer"]

# (声, テキスト) の区間。短い間を空けて続けて話す
CASES: dict[str, dict] = {
    "diarize_en": {
        "segments": [(EN_A, "I'd like a draft beer, please."), (EN_B, "And edamame for me, please.")],
        "diarization": True, "proactive": False, "expect": "answer",
    },
    "diarize_ja": {
        "segments": [(JA_A, "生ビールをひとつください。"), (JA_B, "僕は枝豆でお願いします。")],
        "diarization": True, "proactive": False, "expect": "answer",
    },
    "side_chat_en_off": {
        "segments": [(EN_A, "What do you feel like eating tonight?"),
                     (EN_B, "Hmm, I'm not sure yet. Let me look at the pictures first.")],
        "diarization": False, "proactive": False, "expect": "silent",
    },
    "side_chat_en_on": {
        "segments": [(EN_A, "What do you feel like eating tonight?"),
                     (EN_B, "Hmm, I'm not sure yet. Let me look at the pictures first.")],
        "diarization": False, "proactive": True, "expect": "silent",
    },
    "side_chat_ja_on": {
        "segments": [(JA_A, "ねえ、何食べたい？"), (JA_B, "うーん、まだ決めてない。写真を見てから決めるよ。")],
        "diarization": False, "proactive": True, "expect": "silent",
    },
    "addressed_en_on": {
        "segments": [(EN_A, "Excuse me, can I order a draft beer?")],
        "diarization": False, "proactive": True, "expect": "answer",
    },
}

GAP_S = 0.4
WAIT_FOR_REPLY_S = 8.0


@dataclass
class Result:
    case: str
    expect: str
    ok: bool = False
    error: str = ""
    connect_ms: float | None = None
    first_audio_ms: float | None = None
    audio_seconds: float = 0.0
    responded: bool = False
    speaker_segments: list[list[str]] = field(default_factory=list)  # [話者ラベル, テキスト]
    distinct_speakers: int = 0
    input_transcript: str = ""
    output_transcript: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    timeline: list[str] = field(default_factory=list)  # 「経過ms 種類 テキスト」（接続時点からの相対時間）


def build_config(spec: dict) -> types.LiveConnectConfig:
    kwargs: dict = {}
    if spec["proactive"]:
        kwargs["proactivity"] = types.ProactivityConfig(proactive_audio=True)
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM_INSTRUCTION,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.live_voice))
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(
            diarization=True if spec["diarization"] else None,
            custom_vocabulary=VOCAB,
        ),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        tools=[types.Tool(function_declarations=[ADD_ITEM])],
        **kwargs,
    )


async def run_case(client: genai.Client, name: str, spec: dict) -> Result:
    res = Result(case=name, expect=spec["expect"])
    audio_bytes = 0
    t_end: float | None = None
    last_label: str | None = None

    t0 = time.monotonic()
    async with client.aio.live.connect(model=settings.live_model, config=build_config(spec)) as session:
        res.connect_ms = round((time.monotonic() - t0) * 1000)

        t_conn = time.monotonic()

        def mark(kind: str, text: str = "") -> None:
            res.timeline.append(f"{round((time.monotonic() - t_conn) * 1000)} {kind} {text}".rstrip())

        async def receiver() -> None:
            nonlocal audio_bytes, last_label
            while True:
                async for msg in session.receive():
                    if msg.tool_call:
                        rs = []
                        for fc in msg.tool_call.function_calls or []:
                            res.tool_calls.append({"name": fc.name, "args": fc.args})
                            rs.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"status": "ok"}))
                        await session.send_tool_response(function_responses=rs)
                    sc = msg.server_content
                    if not sc:
                        continue
                    it = sc.input_transcription
                    if it and it.text:
                        mark("in", f"[{it.speaker_label or '-'}] {it.text}")
                        res.input_transcript += it.text
                        label = it.speaker_label or "-"
                        if res.speaker_segments and last_label == label:
                            res.speaker_segments[-1][1] += it.text
                        else:
                            res.speaker_segments.append([label, it.text])
                        last_label = label
                    if sc.output_transcription and sc.output_transcription.text:
                        mark("out", sc.output_transcription.text)
                        res.output_transcript += sc.output_transcription.text
                    if sc.interrupted:
                        mark("interrupted")
                    if sc.turn_complete:
                        mark("turn_complete")
                    if sc.model_turn:
                        for p in sc.model_turn.parts or []:
                            if p.inline_data and p.inline_data.data:
                                if audio_bytes == 0:
                                    mark("audio_start")
                                if res.first_audio_ms is None and t_end is not None:
                                    res.first_audio_ms = round((time.monotonic() - t_end) * 1000)
                                audio_bytes += len(p.inline_data.data)

        recv = asyncio.create_task(receiver())
        try:
            step = IN_RATE * 2 * CHUNK_MS // 1000
            mime = f"audio/pcm;rate={IN_RATE}"
            silence = b"\x00" * step

            async def send(pcm: bytes) -> None:
                for i in range(0, len(pcm), step):
                    await session.send_realtime_input(audio=types.Blob(data=pcm[i : i + step], mime_type=mime))
                    await asyncio.sleep(CHUNK_MS / 1000)

            pcms = [synthesize_pcm16k(text, voice) for voice, text in spec["segments"]]
            for idx, pcm in enumerate(pcms):
                if idx:
                    await send(silence * int(GAP_S * 1000 / CHUNK_MS))
                mark("speak", spec["segments"][idx][1])
                await send(pcm)
            t_end = time.monotonic()
            mark("input_end")
            # 待つ間も、開いたマイクのように無音を送り続ける
            deadline = t_end + WAIT_FOR_REPLY_S
            while time.monotonic() < deadline:
                await send(silence)
        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
        finally:
            recv.cancel()

    res.audio_seconds = round(audio_bytes / 2 / 24000, 2)
    res.responded = res.audio_seconds > 0.3
    labels = {lab for lab, _ in res.speaker_segments if lab != "-"}
    res.distinct_speakers = len(labels)
    res.ok = not res.error and (res.responded == (spec["expect"] == "answer"))
    return res


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(CASES), action="append")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    client = make_client()
    out_dir = REPO_ROOT / "logs" / "multi_speaker_spike" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    for _ in range(args.repeat):
        for name in args.case or list(CASES):
            try:
                r = await run_case(client, name, CASES[name])
            except Exception as e:
                r = Result(case=name, expect=CASES[name]["expect"], error=f"{type(e).__name__}: {e}")
            results.append(r)
            print(json.dumps(asdict(r), ensure_ascii=False), flush=True)
    (out_dir / "report.json").write_text(
        json.dumps({"model": settings.live_model, "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2)
    )
    print(f"\nreport: {out_dir / 'report.json'}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
