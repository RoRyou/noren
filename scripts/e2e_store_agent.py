"""通しテスト：店舗エージェント（店員の一言 → 変更案 → 確認 → 反映 → お客様への通知）。

使い方：cd backend && uv run python ../scripts/e2e_store_agent.py --base http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    args = ap.parse_args()
    http = httpx.AsyncClient(base_url=args.base, timeout=60)
    http.headers["x-noren-demo"] = (await http.post("/api/demo/start")).json()["token"]
    sid = (await http.post("/api/tables/seat", json={"party_size": 1, "lang": "ja"})).json()["id"]
    await http.post(f"/api/tables/{sid}/declare", json={"declarations": [{"guest_id": "g1"}]})
    await http.post(f"/api/tables/{sid}/cart", json={"item_id": "s_dashimaki", "qty": 1})
    report: dict = {}
    ok = True

    r = (await http.post("/api/staff/agent", json={"text": "出汁が切れた"})).json()
    report["dashi"] = r
    names = [c["name"] for c in r["changes"]]
    print("出汁が切れた →", r["intent"], names, "確認待ち" if r.get("pending_id") else "即反映")
    ok &= r["intent"] == "sold_out" and "だし巻き玉子" in names and bool(r.get("pending_id"))
    await http.post(f"/api/staff/agent/{r['pending_id']}/confirm")
    await asyncio.sleep(1.5)
    notes = (await http.get(f"/api/tables/{sid}/notifications")).json()
    print("  お客様への通知:", [n["kind"] for n in notes])
    ok &= any(n["kind"] == "sold_out" for n in notes)

    for text, expect in [("今日のおすすめはまぐろの刺身", "todays_pick"), ("本日のさばは鳥取県産です", "knowledge"),
                         ("唐揚げ終わりです", "sold_out")]:
        r = (await http.post("/api/staff/agent", json={"text": text})).json()
        report[text] = r
        print(f"{text} →", r["intent"], r["changes"], "確認待ち" if r.get("pending_id") else ("反映" if r.get("applied") else "未反映"))
        ok &= r["intent"] == expect

    out = REPO_ROOT / "logs" / "e2e_store_agent" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("OK" if ok else "NG", out / "report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
