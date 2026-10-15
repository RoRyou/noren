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


class _Repo:
    async def session(self, _):
        from app.domain.models import CartLine, TableSession

        return TableSession(id="s", store_id="d", table_no="1", party_size=1, lang="en",
                            cart=[CartLine(line_id="c1", item_id="s_cucumber", qty=1)])

    async def menu(self):
        from app.seed.izakaya import build_menu

        return build_menu()


class _Svc:
    repo = _Repo()


class _Queue:
    def __init__(self):
        self.sent = []

    def send_content(self, c):
        self.sent.append(c.parts[0].text)


async def _voice():
    async def noop(_):
        pass

    v = VoiceSession(svc=_Svc(), session_id="s", send_json=noop, send_audio=noop)
    v.queue = _Queue()
    return v


async def test_claimed_add_without_tool_is_corrected_once():
    """「入れました」と言ったのに add_item を呼んでいない → いまのカートを添えて 1 回だけ直させる（ZI-068）。"""
    v = await _voice()
    v._new_turn()
    v._turn_text = "I've added the edamame to your cart."
    v._turn_tool_names = ["search_menu"]
    await v._check_claims()
    await v._check_claims()
    assert len(v.queue.sent) == 1 and "たたききゅうり×1" in v.queue.sent[0]


async def test_claim_is_fine_after_add_item_or_screen_tap():
    v = await _voice()
    v._new_turn()
    v._turn_text, v._turn_tool_names, v._turn_added_ok = "枝豆をカートに入れました。", ["add_item"], True
    await v._check_claims()
    v._new_turn(screen=True)  # 画面で入れた料理について話すのは正しい
    v._turn_text = "I've added the beer for Guest 2."
    await v._check_claims()
    v._new_turn()
    v._turn_text = "You have edamame in your cart. Shall I place the order?"
    await v._check_claims()
    assert v.queue.sent == []


async def test_claim_after_rejected_add_is_corrected():
    """add_item は呼んだが断られた（数量の上限など）のに「入れました」と言った → 直させる。"""
    v = await _voice()
    v._new_turn()
    v._turn_text, v._turn_tool_names = "I've added 30 edamame to your cart.", ["add_item"]
    await v._check_claims()
    assert len(v.queue.sent) == 1


def test_usage_delta_splits_text_and_audio():
    from google.genai import types

    from app.agent.live import usage_delta

    meta = types.GenerateContentResponseUsageMetadata(  # ADK のイベントが持つ型
        prompt_token_count=251, candidates_token_count=103, thoughts_token_count=66,
        prompt_tokens_details=[types.ModalityTokenCount(modality=types.MediaModality.AUDIO, token_count=107),
                               types.ModalityTokenCount(modality=types.MediaModality.TEXT, token_count=144)],
        candidates_tokens_details=[types.ModalityTokenCount(modality=types.MediaModality.AUDIO, token_count=92),
                                   types.ModalityTokenCount(modality=types.MediaModality.TEXT, token_count=11)])
    assert usage_delta(meta) == {"in_text": 144, "in_audio": 107, "out_text": 11, "out_audio": 92, "thoughts": 66}


async def test_silent_reply_without_tools_is_nudged_once(monkeypatch):
    """お客様の「はい」「以上です」に AI が何も言わずに返答を終えたら、4 秒後に 1 回だけ促す（ZI-074）。"""
    import app.agent.live as live

    monkeypatch.setattr(live, "NUDGE_AFTER_S", 0.05)
    v = await _voice()
    v._new_turn()
    v._watch = asyncio.create_task(v._after_tool_turn())  # 何も話さずに turn_complete が来たとき
    await asyncio.sleep(0.2)
    assert len(v.queue.sent) == 1 and "答えて" in v.queue.sent[0]
