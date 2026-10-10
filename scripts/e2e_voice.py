"""通しテスト：ローカルのサーバーに WebSocket でつなぎ、合成音声で会話例 1（英語・2 名・えびアレルギー）を最後まで行う。

ゲストの選択・お酒の確認など「画面で押す」操作は、REST を呼んで代わりに行う。
記録するもの：各ターンの文字起こし、画面イベント、最初の音声までの遅延、最終的な注文、AI が言ってはいけない言葉。

使い方：
  1) gcloud emulators firestore start --host-port=127.0.0.1:8681
  2) cd backend && FIRESTORE_EMULATOR_HOST=127.0.0.1:8681 uv run uvicorn app.main:app --port 8080
  3) cd backend && uv run python ../scripts/e2e_voice.py --base http://127.0.0.1:8080
出力：logs/e2e_voice/<タイムスタンプ>/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import websockets

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from live_smoke import CHUNK_MS, IN_RATE, synthesize_pcm16k  # noqa: E402

VOICE = "Samantha"
FORBIDDEN = ["it's safe", "it is safe", "completely safe", "安全です", "大丈夫です"]
STEP = IN_RATE * 2 * CHUNK_MS // 1000


class Client:
    def __init__(self, base: str):
        self.base = base
        self.http = httpx.AsyncClient(base_url=base, timeout=30)
        self.events: list[dict] = []
        self.turns: list[dict] = []
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        r = await self.http.post("/api/demo/start")
        self.token = r.json()["token"]
        self.http.headers["x-noren-demo"] = self.token
        s = (await self.http.post("/api/tables/seat", json={"table_no": "2", "party_size": 2, "lang": "en"})).json()
        self.sid = s["id"]
        await self.http.post(f"/api/tables/{self.sid}/declare",
                             json={"declarations": [{"guest_id": "g1", "allergies": ["shrimp"]}, {"guest_id": "g2"}]})

    async def receiver(self, ws) -> None:
        async for msg in ws:
            if isinstance(msg, bytes):
                await self.inbox.put({"type": "_audio", "n": len(msg), "t": time.monotonic()})
            else:
                data = json.loads(msg)
                self.events.append(data)
                await self.inbox.put(data)

    async def speak(self, ws, text: str, timeout: float = 45) -> dict:
        """1 発話を送り、AI の返答（音声つきのターン完了）まで待つ。"""
        while not self.inbox.empty():
            self.inbox.get_nowait()
        pcm = synthesize_pcm16k(text, VOICE) if text else b""
        for i in range(0, len(pcm), STEP):
            await ws.send(pcm[i:i + STEP])
            await asyncio.sleep(CHUNK_MS / 1000)
        t_end = time.monotonic()
        await ws.send(json.dumps({"type": "input_end"}))
        turn = {"user": text, "ai": "", "ui": [], "first_audio_ms": None, "audio_chunks": 0}
        silence = b"\x00" * STEP
        deadline = time.monotonic() + timeout
        got_audio = False
        while time.monotonic() < deadline:
            await ws.send(silence)
            try:
                ev = await asyncio.wait_for(self.inbox.get(), CHUNK_MS / 1000)
            except asyncio.TimeoutError:
                if turn.get("completed_at") and time.monotonic() - turn["completed_at"] > 2.0:
                    break
                continue
            if ev["type"] == "_audio":
                turn["audio_chunks"] += 1
                turn.pop("completed_at", None)
                if turn["first_audio_ms"] is None:
                    turn["first_audio_ms"] = round((ev["t"] - t_end) * 1000)
                got_audio = True
            elif ev["type"] == "transcript" and ev["role"] == "ai" and ev.get("final"):
                turn["ai"] += ev["text"]
            elif ev["type"] == "ui":
                turn["ui"].append({k: v for k, v in ev.items() if k in ("event", "expert", "tool", "purpose", "item_id",
                                                                          "for_guest", "confirm_token", "ok", "check")})
            elif ev["type"] == "turn_complete" and got_audio:
                turn["completed_at"] = time.monotonic()
            if turn.get("completed_at") and time.monotonic() - turn["completed_at"] > 2.0:
                break  # AI が話し終えてから 2 秒、新しい音声がない（画面ではこの間マイクを止めている）
            elif ev["type"] == "voice_state" and ev.get("state") == "closed":
                break
        self.turns.append(turn)
        print(f"\n[user] {text}\n[ai]   {turn['ai']}\n[ui]   {[u.get('event') for u in turn['ui']]}  first_audio={turn['first_audio_ms']}ms",
              flush=True)
        return turn

    async def listen(self, ws, timeout: float = 30) -> dict:
        """発話せずに AI の返答を待つ（画面で押した結果に AI が続けて話すとき）。"""
        return await self.speak(ws, "", timeout)

    async def handle_screen(self, turn: dict) -> bool:
        """画面で押す操作の代わり：ゲスト選択・お酒の確認。押したら True。"""
        tapped = False
        for u in turn["ui"]:
            if u.get("event") == "ask_guest" and u.get("item_id"):
                r = (await self.http.post(f"/api/tables/{self.sid}/cart",
                                          json={"item_id": u["item_id"], "qty": 1, "for_guest": "g2"})).json()
                print(f"[tap]  {u['item_id']} → Guest 2: {r['check']['decision']}")
                tapped = True
                if r["check"]["decision"] == "need_alcohol_check":
                    await self.http.post(f"/api/tables/{self.sid}/alcohol", json={"guest_id": "g2", "adult": True, "driving": False})
                    r = (await self.http.post(f"/api/tables/{self.sid}/cart",
                                              json={"item_id": u["item_id"], "qty": 1, "for_guest": "g2"})).json()
                    print(f"[tap]  20歳以上・運転しない → {r['check']['decision']}")
            if u.get("event") == "ask_alcohol" and u.get("item_id"):
                await self.http.post(f"/api/tables/{self.sid}/alcohol", json={"guest_id": u.get("for_guest") or "g2", "adult": True, "driving": False})
                r = (await self.http.post(f"/api/tables/{self.sid}/cart",
                                          json={"item_id": u["item_id"], "qty": 1, "for_guest": u.get("for_guest") or "g2"})).json()
                print(f"[tap]  お酒の確認 → {r['check']['decision']}")
                tapped = True
        return tapped


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    args = ap.parse_args()
    c = Client(args.base)
    await c.start()
    ws_url = args.base.replace("http", "ws") + f"/ws/tables/{c.sid}?demo={c.token}"
    script = [
        "Hi! What's good here? We just came down from Hokkaido.",
        "Is the karaage spicy? How big is it?",
        "OK, karaage for me, and a draft beer for me too.",
        "Can you read back our order?",
        "Yes, please send it.",
    ]
    async with websockets.connect(ws_url, max_size=None) as ws:
        recv = asyncio.create_task(c.receiver(ws))
        await ws.send(json.dumps({"type": "voice_start"}))
        await asyncio.sleep(1.0)
        for line in script:
            turn = await c.speak(ws, line)
            while await c.handle_screen(turn):
                turn = await c.listen(ws)  # 画面で押した後の AI の続き
        recv.cancel()

    view = (await c.http.get(f"/api/tables/{c.sid}")).json()
    orders = [{"name": l["name"]["ja"], "qty": l["qty"], "for_guest": l["for_guest"], "status": l["status"]}
              for o in view["orders"] for l in o["lines"]]
    said = " ".join(t["ai"] for t in c.turns).lower()
    report = {
        "turns": c.turns,
        "orders": orders,
        "cart_left": view["session"]["cart"],
        "forbidden_phrases": [w for w in FORBIDDEN if w.lower() in said],
        "first_audio_ms": [t["first_audio_ms"] for t in c.turns],
        "ui_events": sorted({u.get("event") for t in c.turns for u in t["ui"] if u.get("event")}),
    }
    out = REPO_ROOT / "logs" / "e2e_voice" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("\norders:", orders)
    print("forbidden:", report["forbidden_phrases"], "ui_events:", report["ui_events"])
    print("report:", out / "report.json")
    return 0 if orders and not report["forbidden_phrases"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
