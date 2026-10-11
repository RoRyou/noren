"""開発の補助：コードの変更案を Gemini に作らせ、差分を確認してから当てる。

Gemini には「探す文字列 → 置き換える文字列」の組で答えさせる（ファイル全体を書き直させない）。
探す文字列がファイルにちょうど 1 回だけ出てこない変更は当てない。当てた後は、人がテストと差分を確認する。

使い方（backend から）：
  uv run python ../scripts/gemini_dev.py --task "やること" --files app/x.py app/y.py [--context app/z.py]
      → 変更案を logs/gemini_dev/<時刻>/ に保存し、差分を表示する（まだ当てない）
  uv run python ../scripts/gemini_dev.py --apply logs/gemini_dev/<時刻>/edits.json
      → 確認した変更案を当てる
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.config import settings  # noqa: E402

RULES = """\
あなたは NOREN（飲食店のテーブル AI 接客。FastAPI + ADK + Firestore + Vite/React）の開発者です。
- 頼まれたことだけを変える。周りのコードの書き方（名前の付け方・コメントの量・言い回し）に合わせる。
- コメントと docstring は日本語で、周りと同じくらい短く。英語のコメントは書かない。
- アレルゲンや安全の判定を AI に推測させない（コードの辞書と規則で決める）。
- 答えは edits の配列：path（リポジトリのルートからのパス）、search（いまのファイルにちょうど 1 回だけ出てくる、
  前後数行を含む文字列。空白・インデントも完全に一致させる）、replace（置き換え後の文字列）。
  新しいファイルは search を空にして replace に全文を書く。
- summary_ja に、何をなぜ変えたかを 2〜3 文で書く。テストを足すべきなら tests の変更も edits に含める。
"""


class Edit(BaseModel):
    path: str
    search: str = Field(description="いまのファイルにちょうど 1 回だけ出てくる文字列。新しいファイルなら空")
    replace: str


class Proposal(BaseModel):
    edits: list[Edit]
    summary_ja: str


def _read(paths: list[str]) -> str:
    out = []
    for p in paths:
        f = (REPO_ROOT / p) if not Path(p).is_absolute() else Path(p)
        if not f.exists():
            f = REPO_ROOT / "backend" / p
        rel = f.resolve().relative_to(REPO_ROOT) if f.exists() else Path(p.removeprefix("../"))
        out.append(f"===== {rel} =====\n{f.read_text() if f.exists() else '（まだない。新しいファイル）'}")
    return "\n\n".join(out)


def _resolve(path: str) -> Path:
    return REPO_ROOT / path


def check(edits: list[Edit]) -> tuple[list[str], str]:
    """当てられるか確かめ、差分を作る（ファイルはまだ変えない）。"""
    problems, diffs, staged = [], [], {}
    for e in edits:
        f = _resolve(e.path)
        before = staged.get(e.path, f.read_text() if f.exists() else "")
        if not e.search:
            if f.exists() and e.path not in staged:
                problems.append(f"{e.path}: 既にあるファイルに search なしの変更")
                continue
            after = e.replace
        else:
            n = before.count(e.search)
            if n != 1:
                problems.append(f"{e.path}: search が {n} 回見つかった（1 回である必要）：{e.search[:60]!r}")
                continue
            after = before.replace(e.search, e.replace, 1)
        staged[e.path] = after
    for path, after in staged.items():
        f = _resolve(path)
        before = f.read_text() if f.exists() else ""
        diffs.append("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{path}", f"b/{path}")))
    return problems, "\n".join(diffs)


def apply(edits: list[Edit]) -> None:
    for e in edits:
        f = _resolve(e.path)
        if not e.search:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(e.replace)
        else:
            f.write_text(f.read_text().replace(e.search, e.replace, 1))


async def propose(task: str, files: list[str], context: list[str], model: str) -> Proposal:
    client = genai.Client(vertexai=True, project=settings.google_cloud_project, location=settings.text_location)
    contents = (f"# やること\n{task}\n\n# 変えてよいファイル\n{_read(files)}\n\n"
                + (f"# 参考（変えない）\n{_read(context)}\n" if context else ""))
    r = await asyncio.wait_for(client.aio.models.generate_content(
        model=model, contents=contents,
        config=types.GenerateContentConfig(system_instruction=RULES, response_mime_type="application/json",
                                           response_schema=Proposal, temperature=0.2)), 300)
    return Proposal.model_validate_json(r.text)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task")
    ap.add_argument("--task-file")
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--context", nargs="*", default=[])
    ap.add_argument("--model", default=settings.text_model)
    ap.add_argument("--apply", help="確認済みの edits.json を当てる")
    args = ap.parse_args()

    if args.apply:
        edits = [Edit(**e) for e in json.loads(Path(args.apply).read_text())["edits"]]
        problems, _ = check(edits)
        if problems:
            print("当てられません：\n" + "\n".join(problems))
            return 1
        apply(edits)
        print(f"{len(edits)} 件を当てました")
        return 0

    task = args.task or Path(args.task_file).read_text()
    proposal = await propose(task, args.files, args.context, args.model)
    out = REPO_ROOT / "logs" / "gemini_dev" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "edits.json").write_text(proposal.model_dump_json(indent=2))
    problems, diff = check(proposal.edits)
    (out / "diff.patch").write_text(diff)
    print(f"# 要約\n{proposal.summary_ja}\n")
    if problems:
        print("# 当てられない変更\n" + "\n".join(problems) + "\n")
    print(diff)
    print(f"\n# 保存：{out / 'edits.json'}")
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
