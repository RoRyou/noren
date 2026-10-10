"""お客様のテーブルとの WebSocket。画面への配信と、音声の会話（タップで開始）を 1 本で扱う。

クライアント → サーバー：
  バイナリ = マイク音声（16 kHz・16 bit・モノラル PCM）。会話中だけ送る。
  {"type": "voice_start"} / {"type": "voice_stop"} / {"type": "text", "text": "..."} / {"type": "input_end"}
サーバー → クライアント：
  バイナリ = AI の音声（24 kHz・16 bit・モノラル PCM）
  {"type": "voice_state" | "transcript" | "turn_complete" | "interrupted" | "ui" | "notification" | "error", ...}
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent.live import VoiceSession
from app.api.context import ws_ctx
from app.realtime.hub import hub

log = logging.getLogger("noren.ws")
router = APIRouter()

MAX_VOICE_SESSIONS = 20  # 同時に開ける音声会話の上限（費用の保護）
_active_voice = 0


@router.websocket("/ws/tables/{sid}")
async def table_ws(ws: WebSocket, sid: str):
    global _active_voice
    try:
        ctx = ws_ctx(ws)
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    send_lock = asyncio.Lock()

    async def send_json(msg: dict) -> None:
        async with send_lock:
            try:
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
            except Exception:  # 切断済み（再接続時に未確認の通知は取り直せる）
                pass

    async def send_audio(data: bytes) -> None:
        async with send_lock:
            try:
                await ws.send_bytes(data)
            except Exception:
                pass

    target = f"table:{sid}"
    q = hub.subscribe(ctx.repo.store_id, target)

    async def forward():
        while True:
            msg = await q.get()
            await send_json(msg)
            # 画面での操作は、開いている音声会話にも伝える
            if msg.get("source") == "touch" and msg.get("say_ja") and voice and not voice.closed.is_set():
                voice.push_screen_action(msg["say_ja"])

    forwarder = asyncio.create_task(forward())
    voice: VoiceSession | None = None
    try:
        await ctx.repo.session(sid)  # この店のテーブルでなければ例外
        await send_json({"type": "hello", "session_id": sid})
        for n in await ctx.repo.unacked_notifications(target):  # 切断中の通知を取り直す
            await send_json({"type": "notification", **n})
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if voice and not voice.closed.is_set():
                    voice.push_audio(msg["bytes"])
                continue
            data = json.loads(msg.get("text") or "{}")
            kind = data.get("type")
            if kind in ("voice_start", "text") and (voice is None or voice.closed.is_set()):
                if _active_voice >= MAX_VOICE_SESSIONS:
                    await send_json({"type": "voice_state", "state": "unavailable", "reason": "busy", "fallback": "touch"})
                    continue
                voice = VoiceSession(ctx.svc, sid, send_json, send_audio)
                _active_voice += 1

                async def release(v=voice):
                    global _active_voice
                    await v.closed.wait()
                    _active_voice -= 1

                asyncio.create_task(release())
                await voice.open()
            if kind == "text" and voice:
                await voice.push_text(data.get("text", ""))
            elif kind == "input_end" and voice:
                voice.mark_input_end()
            elif kind == "voice_stop" and voice:
                await voice.close("user")
            elif kind == "ack" and data.get("id"):
                await ctx.repo.ack_notification(data["id"])
    except WebSocketDisconnect:
        pass
    except KeyError:
        await send_json({"type": "error", "message": "unknown_table"})
    finally:
        forwarder.cancel()
        hub.unsubscribe(ctx.repo.store_id, target, q)
        if voice:
            await voice.close("disconnect")
