"""評価の第 2 層（会話の質）：eval_run.py の会話記録を、別の AI が採点する。

観点（各 1〜5 点）：自然さ / おすすめが客の希望に合っているか / しつこくないか / 店の事実を作っていないか
「作っていないか」は、店の知識とメニューに書いてある事実だけを正しいものとして判定する。

使い方：cd backend && uv run python ../scripts/eval_judge.py logs/eval/<タイムスタンプ>/report.json
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from google.genai import types  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.agent.menu_import import client  # noqa: E402
from app.config import settings  # noqa: E402
from app.seed.izakaya import KNOWLEDGE, build_menu, build_store  # noqa: E402


class Score(BaseModel):
    natural: int
    relevant: int
    not_pushy: int
    no_invented_facts: int
    invented: list[str]
    comment_ja: str


def store_facts() -> str:
    menu = build_menu()
    lines = [f"- {i.name.ja} / {i.name.en}: {i.price}円, 食材 {'、'.join(i.ingredients)}"
             + (f", 量 {i.portion.ja}" if i.portion else "") for i in menu.values()]
    lines += [f"- 知識: {k['text'].ja}" for k in KNOWLEDGE]
    store = build_store()
    lines.append(f"- 店のルール: お通し（{store.otoshi_label.ja}）お一人 {store.otoshi_price} 円、ラストオーダー {store.last_order}")
    lines += [f"- 店のルール: {r['text']['ja']}" for r in store.rules]
    lines += [f"- 店のルール: {v.ja}" for v in store.self_service.values()]
    return "\n".join(lines)


async def judge(history: list[dict]) -> Score:
    instruction = (
        "あなたは飲食店の接客 AI の会話を採点する審査員です。各観点を 1〜5 点で採点してください。\n"
        "natural：自然で感じがよいか / relevant：おすすめがお客様の希望・制限に合っているか / "
        "not_pushy：しつこく勧めていないか / no_invented_facts：店の事実（産地・量・材料・価格など）を作っていないか。\n"
        "店の事実として正しいのは、下の一覧に書いてあることだけです。一般的な食文化の話は事実の捏造に含めません。"
        "作られた事実があれば invented に書き出してください。\n\n店の事実：\n" + store_facts()
    )
    convo = "\n".join(f"{h['role']}: {h['text']}" for h in history)
    r = await client().aio.models.generate_content(
        model=settings.text_model, contents=convo,
        config=types.GenerateContentConfig(system_instruction=instruction, response_mime_type="application/json",
                                           response_schema=Score, temperature=0))
    return Score.model_validate_json(r.text)


async def main() -> int:
    path = Path(sys.argv[1])
    report = json.loads(path.read_text())
    scores = []
    for r in report["results"]:
        if not r.get("history"):
            continue
        s = await judge(r["history"])
        r["quality"] = s.model_dump()
        scores.append(s)
        print(f"{r['id']}: 自然 {s.natural} 適合 {s.relevant} しつこくない {s.not_pushy} 捏造なし {s.no_invented_facts} "
              f"{('捏造: ' + '; '.join(s.invented)) if s.invented else ''} — {s.comment_ja}", flush=True)
    report["quality_summary"] = {k: round(statistics.mean(getattr(s, k) for s in scores), 2)
                                 for k in ("natural", "relevant", "not_pushy", "no_invented_facts")}
    report["quality_summary"]["conversations_with_invented_facts"] = sum(1 for s in scores if s.invented)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("\n", report["quality_summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
