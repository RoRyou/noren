"""餐厅シミュレーター：複数のテーブルが同時に着席・注文し、厨房が順に出す流れを再現する（AI は使わない）。

用途：
- 同時注文での正しさ（限定 5 食のさばの取り合い、注文の重複がないか）と、API の応答時間を測る
- デモの録画で、店員の画面に何卓も動いている状態を作る（--demo-token で既存のデモ店に流し込む）

使い方：
  cd backend && uv run python ../scripts/simulate_tables.py --base http://127.0.0.1:8080 --tables 5
出力：logs/simulate/<タイムスタンプ>/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from datetime import datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
FOOD = ["s_edamame", "s_hiyayakko", "f_karaage", "g_yakitori", "g_saba", "s_dashimaki", "g_maguro", "r_onigiri"]
DRINK = ["d_beer", "d_highball", "d_oolong", "d_yuzu_sour"]


class Timer:
    def __init__(self):
        self.ms: list[float] = []

    async def call(self, coro):
        t = time.monotonic()
        r = await coro
        self.ms.append((time.monotonic() - t) * 1000)
        return r


async def table(http: httpx.AsyncClient, timer: Timer, no: int, rng: random.Random) -> dict:
    party = rng.randint(1, 4)
    s = (await timer.call(http.post("/api/tables/seat", json={"table_no": str(no), "party_size": party, "lang": rng.choice(["ja", "en"])}))).json()
    sid = s["id"]
    decl = [{"guest_id": g["id"], "allergies": ["shrimp"] if rng.random() < 0.2 else []} for g in s["guests"]]
    await timer.call(http.post(f"/api/tables/{sid}/declare", json={"declarations": decl}))
    for g in s["guests"]:
        await timer.call(http.post(f"/api/tables/{sid}/alcohol", json={"guest_id": g["id"], "adult": True, "driving": False}))
    wanted = rng.sample(FOOD, 3) + ["g_saba"]  # さば（限定 5 食）を全卓が狙う
    results = []
    for item in wanted:
        r = (await timer.call(http.post(f"/api/tables/{sid}/cart", json={"item_id": item, "qty": rng.randint(1, 2), "for_guest": "g1"}))).json()
        results.append((item, r["check"]["decision"]))
    for g in s["guests"]:
        await timer.call(http.post(f"/api/tables/{sid}/cart", json={"item_id": rng.choice(DRINK), "qty": 1, "for_guest": g["id"]}))
    rb = (await timer.call(http.post(f"/api/tables/{sid}/read_back"))).json()
    sub = {"ok": False, "reason": "nothing"}
    if rb.get("confirm_token"):
        sub = (await timer.call(http.post(f"/api/tables/{sid}/submit", json={"confirm_token": rb["confirm_token"], "idempotency_key": "sim"}))).json()
    return {"table": no, "sid": sid, "party": party, "adds": results, "submit_ok": sub.get("ok"), "reason": sub.get("reason")}


async def kitchen(http: httpx.AsyncClient, rounds: int, pause: float) -> int:
    """厨房役：受付 → 厨房確認 → 調理 → 提供 を順に進める。"""
    moved = 0
    nxt = {"accepted": "confirmed", "confirmed": "cooking", "cooking": "served"}
    for _ in range(rounds):
        board = (await http.get("/api/staff/board")).json()
        for o in board["orders"]:
            for l in o["lines"]:
                if l["status"] in nxt:
                    await http.post(f"/api/staff/orders/{o['id']}/lines/{l['line_id']}", json={"status": nxt[l["status"]]})
                    moved += 1
        await asyncio.sleep(pause)
    return moved


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--tables", type=int, default=5)
    ap.add_argument("--demo-token", help="既存のデモ店に流し込む（店員の画面を動かす録画用）")
    ap.add_argument("--kitchen-rounds", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    http = httpx.AsyncClient(base_url=args.base, timeout=60)
    token = args.demo_token or (await http.post("/api/demo/start", params={"reset": "true"})).json()["token"]
    http.headers["x-noren-demo"] = token
    timer = Timer()
    rng = random.Random(args.seed)
    t0 = time.monotonic()
    tables = await asyncio.gather(*[table(http, timer, i + 1, random.Random(rng.random())) for i in range(args.tables)])
    elapsed = time.monotonic() - t0
    moved = await kitchen(http, args.kitchen_rounds, 0.5)

    menu = {c["item_id"]: c for c in (await http.get(f"/api/tables/{tables[0]['sid']}/menu")).json()}
    saba_ordered = 0
    for t in tables:
        view = (await http.get(f"/api/tables/{t['sid']}")).json()
        saba_ordered += sum(l["qty"] for o in view["orders"] for l in o["lines"] if l["item_id"] == "g_saba" and l["status"] != "cancelled")
        orders = view["orders"]
        t["orders"] = len(orders)
    report = {
        "tables": tables,
        "seconds": round(elapsed, 1),
        "api_ms_median": round(statistics.median(timer.ms)),
        "api_ms_p95": round(sorted(timer.ms)[int(len(timer.ms) * 0.95) - 1]),
        "api_calls": len(timer.ms),
        "saba_ordered": saba_ordered,
        "saba_remaining": menu["g_saba"]["remaining"],
        "saba_consistent": saba_ordered + (menu["g_saba"]["remaining"] or 0) == 5,
        "duplicate_orders": [t["table"] for t in tables if t["orders"] > 1],
        "kitchen_moves": moved,
    }
    out = REPO_ROOT / "logs" / "simulate" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    for t in tables:
        print(f"table {t['table']}: party {t['party']} submit={t['submit_ok']} {t['reason'] or ''} adds={t['adds']}")
    print({k: v for k, v in report.items() if k != "tables"})
    ok = report["saba_consistent"] and not report["duplicate_orders"]
    print("OK" if ok else "NG", out / "report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
