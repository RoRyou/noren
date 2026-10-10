"""リポジトリ内の Python のコメントと docstring が日本語で書かれているかを確認する。

日本語（ひらがな・カタカナ・漢字）を含まず、英単語が 3 語以上あるコメント・docstring を
「英語のまま」として一覧にする。`noqa` などのツール向け指示は対象外。

使い方（リポジトリのルートから）：
  python3 scripts/check_ja.py          # 問題があれば一覧を出して終了コード 1
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JA = re.compile(r"[぀-ヿ㐀-鿿]")
WORD = re.compile(r"[A-Za-z]{2,}")
TOOL_DIRECTIVE = re.compile(r"^\s*#\s*(noqa|type:|pragma|pylint|fmt:)")


def needs_ja(text: str) -> bool:
    return not JA.search(text) and len(WORD.findall(text)) >= 3


def check_file(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    problems: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT and not TOOL_DIRECTIVE.match(tok.string) and needs_ja(tok.string):
            problems.append(f"{path.relative_to(REPO_ROOT)}:{tok.start[0]}: コメント: {tok.string.strip()[:80]}")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node)
            if doc and needs_ja(doc):
                line = getattr(node, "lineno", 1)
                problems.append(f"{path.relative_to(REPO_ROOT)}:{line}: docstring: {doc.splitlines()[0][:80]}")
    return problems


def main() -> int:
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    problems: list[str] = []
    for name in files.stdout.split():
        problems += check_file(REPO_ROOT / name)
    for p in problems:
        print(p)
    print(f"英語のままのコメント・docstring: {len(problems)} 件")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
