"""通しテスト：通訳モード（店員の日本語 → お客様の言語、お客様の言語 → 日本語。訳すだけで意見を足さない）。

使い方：cd backend && uv run python ../scripts/e2e_interpret.py --base http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re

import httpx
import websockets

LINES = [
    ("ja", "大変申し訳ございません。すぐに正しいお料理をお持ちします。"),
    ("en", "That's okay, thank you. Could we also get some water?"),
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    args = ap.parse_args()
    http = httpx.AsyncClient(base_url=args.base, timeout=30)
    token = (await http.post("/api/demo/start")).json()["token"]
    http.headers["x-noren-demo"] = token
    sid = (await http.post("/api/tables/seat", json={"party_size": 1, "lang": "en"})).json()["id"]
    await http.post(f"/api/tables/{sid}/declare", json={"declarations": [{"guest_id": "g1"}]})
    ok = True
    async with websockets.connect(args.base.replace("http", "ws") + f"/ws/tables/{sid}?demo={token}", max_size=None) as ws:
        await ws.send(json.dumps({"type": "voice_start", "mode": "interpret"}))
        for lang, text in LINES:
            await ws.send(json.dumps({"type": "text", "text": text}))
            said, end = "", asyncio.get_event_loop().time() + 30
            while asyncio.get_event_loop().time() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), 3)
                except asyncio.TimeoutError:
                    if said:
                        break
                    continue
                if isinstance(msg, str):
                    m = json.loads(msg)
                    if m.get("type") == "transcript" and m.get("role") == "ai" and m.get("final"):
                        said += m["text"]
            japanese = bool(re.search(r"[぀-ヿ一-鿿]", said))
            good = (not japanese) if lang == "ja" else japanese  # 日本語 → 英語、英語 → 日本語
            ok &= good and bool(said)
            print(f"[{lang}] {text}\n  → {said}  {'OK' if good else 'NG'}")
    print("OK" if ok else "NG")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
