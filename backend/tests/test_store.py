"""Firestore エミュレーターを使った結合テスト（注文のトランザクション、outbox、通知、在庫の競合）。

エミュレーターが動いていなければスキップする：
  gcloud emulators firestore start --host-port=127.0.0.1:8681
"""

import asyncio
import os
import socket
import uuid

import pytest
from google.cloud import firestore

from app.domain.cart import Decision
from app.domain.escalation import Reason
from app.domain.models import LineStatus
from app.domain.orders import TransitionError
from app.realtime.hub import Hub
from app.realtime.scheduler import LocalScheduler
from app.seed.izakaya import KNOWLEDGE, build_menu, build_store
from app.services.table import TableService
from app.store.dispatcher import Dispatcher
from app.store.repo import Repo

EMULATOR = os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8681")


def _emulator_up() -> bool:
    host, port = EMULATOR.split(":")
    try:
        socket.create_connection((host, int(port)), timeout=0.5).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _emulator_up(), reason="Firestore emulator is not running")


@pytest.fixture
async def env():
    client = firestore.AsyncClient(project="demo-noren")
    repo = Repo(client, f"test_{uuid.uuid4().hex[:8]}")
    await repo.seed(build_store(repo.store_id), build_menu(), KNOWLEDGE)
    hub = Hub()
    sched = LocalScheduler()
    sched.time_scale = 0.01
    dispatcher = Dispatcher(hub, sched)
    svc = TableService(repo, dispatcher, clock=lambda: "19:00")
    yield svc, repo, hub, dispatcher, sched
    await sched.drain()


async def seat_dialogue1(svc):
    s = await svc.seat("2", 2, "en")
    await svc.declare(s.id, [{"guest_id": "g1", "allergies": ["shrimp"]}, {"guest_id": "g2"}])
    return s


async def test_dialogue1_end_to_end(env):
    svc, repo, hub, dispatcher, _ = env
    s = await seat_dialogue1(svc)
    staff_q = hub.subscribe(repo.store_id, "staff")
    table_q = hub.subscribe(repo.store_id, f"table:{s.id}")

    check, _ = await svc.add_to_cart(s.id, "f_karaage", 1)
    assert check.decision == Decision.need_guest
    check, _ = await svc.add_to_cart(s.id, "f_karaage", 1, for_guest="g2")
    assert check.ok and check.warnings == ["do_not_share:g1"]
    check, _ = await svc.add_to_cart(s.id, "s_hiyayakko", 1)
    assert check.ok
    check, _ = await svc.add_to_cart(s.id, "d_beer", 1, for_guest="g2")
    assert check.decision == Decision.need_alcohol_check
    await svc.set_alcohol(s.id, "g2", adult=True, driving=False)
    check, session = await svc.add_to_cart(s.id, "d_beer", 1, for_guest="g2")
    assert check.ok and len(session.cart) == 3

    rb = await svc.read_back(s.id)
    assert rb["problems"] == [] and rb["confirm_token"]
    karaage = next(l for l in rb["lines"] if l["item_id"] == "f_karaage")
    assert "shrimp" in karaage["cross_contact"]  # 復唱で混入の可能性を伝えられる
    result = await svc.submit(s.id, rb["confirm_token"], "k1")
    assert result["ok"] and result["message_key"] == "received"

    await dispatcher.dispatch(repo)
    kinds = {(await asyncio.wait_for(staff_q.get(), 1))["kind"], (await asyncio.wait_for(table_q.get(), 1))["kind"]}
    assert kinds == {"new_order", "order_status"}
    bill = await svc.request_bill(s.id)
    assert bill["total"] == 638 + 385 + 600 + 500 * 2  # お通し 2 人分を含む


async def test_submit_is_idempotent_and_needs_fresh_read_back(env):
    svc, repo, *_ = env
    s = await seat_dialogue1(svc)
    await svc.add_to_cart(s.id, "s_edamame", 1)
    rb = await svc.read_back(s.id)
    await svc.add_to_cart(s.id, "s_hiyayakko", 1)  # 復唱の後にカートを変えた
    assert (await svc.submit(s.id, rb["confirm_token"], "k1"))["reason"] == "not_confirmed"
    rb = await svc.read_back(s.id)
    a = await svc.submit(s.id, rb["confirm_token"], "k2")
    b = await svc.submit(s.id, rb["confirm_token"], "k2")  # 同じ送信が二度（AI の重複呼び出しなど）
    assert a["ok"] and b["ok"] and a["order"]["id"] == b["order"]["id"]
    assert len(await repo.orders_for(s.id)) == 1


async def test_last_portion_race_and_release(env):
    svc, repo, *_ = env
    tables = []
    for no in ("3", "4"):
        s = await svc.seat(no, 1, "ja")
        await svc.declare(s.id, [{"guest_id": "g1"}])
        check, _ = await svc.add_to_cart(s.id, "g_saba", 3)  # 残り 5 食を 2 卓が 3 食ずつ
        assert check.ok
        tables.append((s, (await svc.read_back(s.id))["confirm_token"]))
    results = await asyncio.gather(*[svc.submit(s.id, tok, "k") for s, tok in tables])
    assert sorted(r["ok"] for r in results) == [False, True]
    assert next(r for r in results if not r["ok"])["reason"] == "not_enough_stock"
    assert (await repo.menu())["g_saba"].remaining == 2
    winner = next(r for r in results if r["ok"])["order"]
    await repo.set_line_status(winner["id"], winner["lines"][0]["line_id"], LineStatus.rejected, "焼き台の故障")
    assert (await repo.menu())["g_saba"].remaining == 5  # 断ったら在庫を戻す


async def test_stock_zero_marks_sold_out_and_notifies_carts(env):
    svc, repo, hub, dispatcher, _ = env
    a = await svc.seat("5", 1, "ja")
    await svc.declare(a.id, [{"guest_id": "g1"}])
    b = await svc.seat("6", 1, "ja")
    await svc.declare(b.id, [{"guest_id": "g1"}])
    await svc.add_to_cart(b.id, "g_saba", 1)  # b のカートにも入っている
    await svc.add_to_cart(a.id, "g_saba", 5)
    rb = await svc.read_back(a.id)
    assert (await svc.submit(a.id, rb["confirm_token"], "k"))["ok"]
    item = (await repo.menu())["g_saba"]
    assert item.sold_out and item.sold_out_reason == "stock"
    await dispatcher.dispatch(repo)
    notes = await repo.unacked_notifications(f"table:{b.id}")
    assert any(n["kind"] == "sold_out" for n in notes)
    alts = await svc.alternatives_for(b.id, "g_saba", None)
    assert alts and all(c["item_id"] != "g_saba" for c in alts)


async def test_line_status_flow_and_customer_cancel(env):
    svc, repo, *_ = env
    s = await seat_dialogue1(svc)
    await svc.add_to_cart(s.id, "s_edamame", 1)
    await svc.add_to_cart(s.id, "s_hiyayakko", 1)
    rb = await svc.read_back(s.id)
    order = (await svc.submit(s.id, rb["confirm_token"], "k"))["order"]
    l1, l2 = (l["line_id"] for l in order["lines"])
    assert (await svc.cancel_line(order["id"], l1))["ok"]  # 調理前は取り消せる
    await repo.set_line_status(order["id"], l2, LineStatus.confirmed)
    await repo.set_line_status(order["id"], l2, LineStatus.cooking)
    assert (await svc.cancel_line(order["id"], l2))["reason"] == "already_cooking"
    with pytest.raises(TransitionError):
        await repo.set_line_status(order["id"], l2, LineStatus.confirmed)


async def test_dispatch_is_idempotent(env):
    svc, repo, _, dispatcher, _ = env
    s = await seat_dialogue1(svc)
    await svc.add_to_cart(s.id, "s_edamame", 1)
    rb = await svc.read_back(s.id)
    await svc.submit(s.id, rb["confirm_token"], "k")
    await dispatcher.dispatch(repo)
    # 配信済みのイベントをもう一度「未送信」に戻しても、通知は増えない（Cloud Tasks の再試行と同じ状況）
    async for snap in repo.col("outbox").stream():
        await snap.reference.update({"status": "pending"})
    await dispatcher.dispatch(repo)
    notes = await repo.unacked_notifications("staff")
    assert len([n for n in notes if n["kind"] == "new_order"]) == 1


async def test_urgent_escalation_reminds_until_acknowledged(env):
    svc, repo, _, dispatcher, sched = env
    s = await seat_dialogue1(svc)
    esc = await svc.escalate(s.id, Reason.medical, "息が苦しい", ["症状"])
    await dispatcher.dispatch(repo)
    await sched.drain()
    kinds = [n["kind"] for n in await repo.unacked_notifications("staff")]
    assert "escalation" in kinds and "escalation_repeat" in kinds and "escalation_all" in kinds

    esc2 = await svc.escalate(s.id, Reason.complaint, "料理が冷たい", [])
    await repo.update_escalation(esc2["id"], {"status": "acknowledged", "handler": "店員A"})  # すぐ対応
    await dispatcher.dispatch(repo)
    await sched.drain()
    assert (await repo.escalation(esc2["id"]))["reminders"] == 0
    assert "症状は未確認" in esc["summary"]


async def test_audit_log_records_blocks(env):
    svc, repo, *_ = env
    s = await seat_dialogue1(svc)
    await svc.add_to_cart(s.id, "f_ebi_ten", 1, for_guest="g1", actor="reception_agent", correlation_id="turn-1")
    logs = await repo.audit_logs()
    blocked = next(l for l in logs if l["action"] == "add_item")
    assert blocked["result"] == "blocked" and "allergen:shrimp" in blocked["reason"] and blocked["correlation_id"] == "turn-1"


async def test_prepay_kitchen_waits_for_payment(env):
    svc, repo, *_ = env
    from app.services.owner import OwnerService

    await OwnerService(repo).set_business_type("ramen")
    s = await svc.seat("7", 1, "ja")
    await svc.declare(s.id, [{"guest_id": "g1"}])
    await svc.add_to_cart(s.id, "s_edamame", 1)
    rb = await svc.read_back(s.id)
    r = await svc.submit(s.id, rb["confirm_token"], "k")
    assert r["message_key"] == "pay_first"
    order = r["order"]
    with pytest.raises(TransitionError):
        await repo.set_line_status(order["id"], order["lines"][0]["line_id"], LineStatus.confirmed)  # 未払いは厨房が受けない
    await repo.mark_paid(order["id"])
    await repo.set_line_status(order["id"], order["lines"][0]["line_id"], LineStatus.confirmed)


async def test_service_policy_cancel_goes_to_staff(env):
    svc, repo, *_ = env
    from app.services.owner import OwnerService

    await OwnerService(repo).set_policy("cancel_before_cooking", "staff")
    s = await svc.seat("8", 1, "ja")
    await svc.declare(s.id, [{"guest_id": "g1"}])
    await svc.add_to_cart(s.id, "s_edamame", 1)
    rb = await svc.read_back(s.id)
    order = (await svc.submit(s.id, rb["confirm_token"], "k"))["order"]
    r = await svc.cancel_line(order["id"], order["lines"][0]["line_id"])
    assert r["reason"] == "staff_will_handle"
    assert any(e["session_id"] == s.id for e in await repo.open_escalations())
    assert (await repo.store()).rule_version == 2  # ポリシーの変更で規則のバージョンが上がる


async def test_agent_cannot_submit_before_guest_answers_read_back(env):
    """AI が復唱と送信を同じ返答の中でしても、お客様の返事を受け取るまでは送らない（ZI-062）。"""
    from app.agent.tools import build_tools

    svc, repo, *_ = env
    s = await svc.seat("5", 1, "ja")
    await svc.declare(s.id, [{"guest_id": "g1"}])
    heard = [1]
    events = []

    async def emit(e):
        events.append(e)

    tools = {t.__name__: t for t in build_tools(svc, s.id, "ja", emit, lambda: "t", lambda: heard[0])}
    added = await tools["add_item"]("s_edamame", 1)
    assert added["decision"] == "ok" and "note" in added
    rb = await tools["read_back"]()
    early = await tools["submit_order"](rb["confirm_token"])
    assert early == {"ok": False, "reason": "not_confirmed_yet", "say": early["say"]}
    assert await repo.orders_for(s.id) == []
    heard[0] += 1  # お客様が「はい」と言った（すぐ後の同じ呼び出しも通る）
    done = await tools["submit_order"](rb["confirm_token"])
    assert done["ok"] is True


async def test_tick_notifies_overdue_once(env):
    """見回り：注文から 20 分たって出ていない料理を店員に 1 回だけ知らせる。"""
    from datetime import datetime, timedelta

    from app.store.repo import JST

    svc, repo, hub, dispatcher, _ = env
    s = await svc.seat("6", 1, "ja")
    await svc.declare(s.id, [{"guest_id": "g1"}])
    await svc.add_to_cart(s.id, "s_edamame", 1, "g1")
    rb = await svc.read_back(s.id)
    await svc.submit(s.id, rb["confirm_token"], idempotency_key="k1")
    later = datetime.now(JST) + timedelta(minutes=25)
    assert await dispatcher.tick(repo, "19:25", later) == 1
    assert await dispatcher.tick(repo, "19:26", later) == 0  # 同じ料理は 1 回だけ
    staff = await repo.unacked_notifications("staff")
    assert [n["kind"] for n in staff if n["kind"] == "overdue"] == ["overdue"]


async def test_metrics_summary(env):
    svc, repo, *_ = env
    s = await seat_dialogue1(svc)
    await svc.add_to_cart(s.id, "s_edamame", 1)
    rb = await svc.read_back(s.id)
    await svc.submit(s.id, rb["confirm_token"], "k_metrics")
    await svc.escalate(s.id, Reason.dietary_check, "エビフライの確認")

    m = await repo.metrics()
    assert m["tables"] == 1
    assert m["orders"] == 1
    assert m["escalations"] == 1
    assert m["escalations_by_reason"] == {Reason.dietary_check.value: 1}
    assert m["unattended_rate"] == 0.0


async def test_usage_and_metrics_cost(env, monkeypatch):
    svc, repo, *_ = env
    s = await seat_dialogue1(svc)
    await repo.add_usage(s.id, {"in_text": 100, "in_audio": 200})
    await repo.add_usage(s.id, {"in_text": 50, "out_audio": 300, "thoughts": 50})

    loaded = await repo.session(s.id)
    assert loaded.usage == {"in_text": 150, "in_audio": 200, "out_audio": 300, "thoughts": 50}

    m = await repo.metrics()
    assert m["usage_total"] == {"in_text": 150, "in_audio": 200, "out_audio": 300, "thoughts": 50}
    assert m["tokens_per_table"] == 700
    assert m["ai_cost_yen_per_table"] is None

    from app.config import settings
    monkeypatch.setattr(settings, "live_price_usd_per_mtok", {
        "in_text": 1.0,
        "in_audio": 10.0,
        "out_text": 2.0,
        "out_audio": 20.0,
        "thoughts": 1.0,
    })
    m2 = await repo.metrics()
    assert m2["ai_cost_yen_per_table"] == 1.2


async def test_agent_tool_errors_do_not_break_the_session(env):
    """間違った id で例外が出ても、ツールはエラーを返すだけ（音声の会話は続く）。料理の id での数量変更も受け付ける（ZI-070）。"""
    from app.agent.tools import build_tools

    svc, repo, *_ = env
    s = await svc.seat("7", 1, "ja")
    await svc.declare(s.id, [{"guest_id": "g1"}])

    async def emit(_):
        pass

    tools = {t.__name__: t for t in build_tools(svc, s.id, "ja", emit, lambda: "t")}
    assert (await tools["add_item"]("draft_beer"))["ok"] is False
    assert (await tools["get_item"]("nope"))["ok"] is False
    added = await tools["add_item"]("s_edamame", 1)
    assert any("[line_id=" in c for c in added["cart_now"])
    assert (await tools["update_item"]("s_edamame", 3))["ok"] is True  # 料理の id で数量を変える
    assert (await repo.session(s.id)).cart[0].qty == 3
    assert (await tools["update_item"]("f_karaage", 0))["error"] == "unknown_line_id"


async def test_parallel_adds_to_same_table_all_succeed(env):
    """AI が add_item を同時に 3 回呼んでも、全部カートに入る（ZI-075）。"""
    svc, repo, *_ = env
    s = await svc.seat("8", 3, "en")
    await svc.declare(s.id, [{"guest_id": "g1"}, {"guest_id": "g2"}, {"guest_id": "g3"}])
    results = await asyncio.gather(*(svc.add_to_cart(s.id, item, 1, g)
                                     for item, g in [("s_edamame", "g1"), ("s_cucumber", "g2"), ("g_yakitori", "g3")]))
    assert all(c.ok for c, _ in results)
    assert sorted(l.item_id for l in (await repo.session(s.id)).cart) == ["g_yakitori", "s_cucumber", "s_edamame"]


async def test_confirmation_in_new_voice_session_after_same_read_back(env):
    """復唱の後、返事の前に会話が閉じても、同じ内容なら新しい会話の「はい」で送れる（ZI-076）。"""
    from app.agent.tools import build_tools

    svc, repo, *_ = env
    s = await svc.seat("9", 1, "ja")
    await svc.declare(s.id, [{"guest_id": "g1"}])

    async def emit(_):
        pass

    first = {t.__name__: t for t in build_tools(svc, s.id, "ja", emit, lambda: "t", lambda: 1)}
    await first["add_item"]("s_edamame", 1)
    rb = await first["read_back"]()  # ここで会話が閉じた
    second = {t.__name__: t for t in build_tools(svc, s.id, "ja", emit, lambda: "t", lambda: 1)}  # 「はい」で開き直す
    again = await second["read_back"]()
    assert again["confirm_token"] == rb["confirm_token"]
    assert (await second["submit_order"](again["confirm_token"]))["ok"] is True


async def test_alcohol_confirmed_by_conversation(env):
    """お酒の確認を会話で記録（confirm_alcohol）すれば、画面の操作なしで入れられる。運転する方には入らない。"""
    from app.agent.tools import build_tools

    svc, repo, *_ = env
    s = await svc.seat("10", 2, "ja")
    await svc.declare(s.id, [{"guest_id": "g1"}, {"guest_id": "g2"}])

    async def emit(_):
        pass

    tools = {t.__name__: t for t in build_tools(svc, s.id, "ja", emit, lambda: "t")}
    assert (await tools["add_item"]("d_beer", 1, "g1"))["decision"] == "need_alcohol_check"
    assert (await tools["confirm_alcohol"]("g1", True, False))["can_drink"] is True
    assert (await tools["add_item"]("d_beer", 1, "g1"))["decision"] == "ok"
    await tools["confirm_alcohol"]("g2", True, True)
    assert (await tools["add_item"]("d_beer", 1, "g2"))["decision"] == "rejected"


async def test_declare_and_add_at_the_same_time_keep_both(env):
    """申告とカートの追加が同時に動いても、申告が消えない（ZI-078）。"""
    svc, repo, *_ = env
    s = await svc.seat("11", 2, "en")
    await asyncio.gather(svc.declare(s.id, [{"guest_id": "g2", "allergies": ["shrimp"]}, {"guest_id": "g1"}]),
                         svc.add_to_cart(s.id, "g_yakitori", 1, "g2"))
    s2 = await repo.session(s.id)
    assert s2.guest("g2").allergies == ["shrimp"] and all(g.declared for g in s2.guests)
