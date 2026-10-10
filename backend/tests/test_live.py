"""音声の会話の制御（Gemini にはつながない）。"""

import asyncio
import time

from app.agent.live import VoiceSession


async def test_idle_close_waits_while_ai_is_busy():
    """AI がツールを呼び続けている間は閉じず、動きが止まってから idle_close_s 秒で閉じる（ZI-065）。"""
    sent = []

    async def send_json(m):
        sent.append(m)

    async def send_audio(_):
        pass

    v = VoiceSession(svc=None, session_id="s", send_json=send_json, send_audio=send_audio, idle_close_s=0.3)
    v._arm_idle()
    end = time.monotonic() + 0.8
    while time.monotonic() < end:  # AI の動き（ツール・声）が続く
        v._last_activity = time.monotonic()
        await asyncio.sleep(0.05)
    assert not v.closed.is_set()
    await asyncio.sleep(0.5)
    assert v.closed.is_set()
    assert sent[-1] == {"type": "voice_state", "state": "closed", "reason": "idle"}
