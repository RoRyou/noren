"""業務ルールの単体テスト。project.md 第 6 節の会話例に出てくる判定をそのまま確かめる。"""

import pytest

from app.domain.allergens import ALLERGENS, convert_ingredients
from app.domain.cart import Decision, cart_token, check_add, recheck_cart
from app.domain.dietary import Verdict, judge
from app.domain.escalation import Reason, build_summary, detect_medical
from app.domain.events import escalation_followups, on_sold_out
from app.domain.models import (
    AlcoholCheck, CartLine, Guest, LineStatus, Order, OrderLine, SessionStatus, TableSession,
)
from app.domain.orders import (
    StockError, TransitionError, aggregate_status, build_lines, make_bill, otoshi_charge, release_stock, take_stock,
    transition,
)
from app.domain.recommend import alternatives, recommend
from app.domain.table_state import Phase, derive_phase
from app.seed.izakaya import build_menu, build_store

MENU = build_menu()
STORE = build_store()


def guest(gid, allergies=(), diets=(), declared=True, alcohol=None, severe=False):
    return Guest(id=gid, label=gid, allergies=list(allergies), diets=list(diets), declared=declared,
                 alcohol=alcohol, severe=severe)


def session(*guests, cart=(), status=SessionStatus.dining):
    return TableSession(id="s1", table_no="2", party_size=len(guests), guests=list(guests), cart=list(cart), status=status)


# --- アレルゲン辞書 ---------------------------------------------------------

def test_29_allergens_including_2026_additions():
    assert len(ALLERGENS) == 29
    assert ALLERGENS["cashew"][2] == "mandatory"
    assert ALLERGENS["pistachio"][2] == "recommended"


def test_convert_soy_sauce_and_unknown():
    c = convert_ingredients(["醤油", "しょう油"])
    assert c.allergens == {"wheat", "soybean"} and c.complete
    c = convert_ingredients(["豚肉", "市販のタレ"])
    assert not c.complete and c.unknown_ingredients == ["市販のタレ"]
    assert convert_ingredients(["謎の食材"]).unknown_ingredients == ["謎の食材"]  # 辞書にないものは推測しない


def test_seed_cross_contact_and_unconfirmed():
    assert "shrimp" in MENU["f_karaage"].cross_contact  # 海老天と同じ油
    assert {"shrimp", "egg", "wheat"} <= set(MENU["f_ebi_ten"].allergens)
    assert MENU["g_buta_kimchi"].allergen_status == "unconfirmed"  # 市販のタレ
    assert MENU["s_hiyayakko"].cross_contact == []


# --- 食事判定（○✕△？） -------------------------------------------------------

SHRIMP = guest("g1", ["shrimp"])


@pytest.mark.parametrize("item,expected", [
    ("f_ebi_ten", Verdict.ng), ("f_karaage", Verdict.cross), ("s_hiyayakko", Verdict.ok), ("g_buta_kimchi", Verdict.unknown),
])
def test_shrimp_allergy(item, expected):
    assert judge(MENU[item], SHRIMP).verdict == expected


def test_severity_does_not_change_verdict():
    mild = judge(MENU["f_karaage"], guest("g1", ["shrimp"], severe=False))
    severe = judge(MENU["f_karaage"], guest("g1", ["shrimp"], severe=True))
    assert mild.verdict == severe.verdict == Verdict.cross


@pytest.mark.parametrize("diet,item,expected", [
    ("halal", "g_yakitori", Verdict.unknown),  # ハラール認証のない肉
    ("halal", "d_beer", Verdict.ng),
    ("halal", "g_mapo", Verdict.ng),  # 豚肉
    ("halal", "s_dashimaki", Verdict.ng),  # みりん（アルコール）
    ("vegetarian", "s_hiyayakko", Verdict.ng),  # かつお節
    ("vegetarian", "s_cucumber", Verdict.ok),
    ("vegan", "s_dashimaki", Verdict.ng),
    ("gluten_free", "f_karaage", Verdict.ng),
    ("gluten_free", "s_edamame", Verdict.ok),
    ("no_five_pungent", "s_cucumber", Verdict.ng),  # にんにく
    ("vegetarian", "f_fries", Verdict.cross),  # 唐揚げ・海老天と同じ油
    ("vegan", "f_fries", Verdict.cross),
])
def test_diets(diet, item, expected):
    assert judge(MENU[item], guest("g1", diets=[diet])).verdict == expected


def test_no_restriction_is_ok_even_if_unconfirmed():
    assert judge(MENU["g_buta_kimchi"], guest("g1")).verdict == Verdict.ok


# --- カートの共通チェック（会話例 1・2・3） --------------------------------------

def test_dialogue1_who_is_it_for():
    s = session(guest("g1", ["shrimp"]), guest("g2"))
    r = check_add(s, STORE, MENU["f_karaage"], 1, None)
    assert r.decision == Decision.need_guest  # 「me」をゲストに結び付けない
    assert r.verdicts["g1"].verdict == Verdict.cross and r.verdicts["g2"].verdict == Verdict.ok
    r = check_add(s, STORE, MENU["f_karaage"], 1, "g2")
    assert r.ok and r.warnings == ["do_not_share:g1"]
    assert check_add(s, STORE, MENU["f_karaage"], 1, "g1").decision == Decision.need_staff  # △ は自動で通さない
    assert check_add(s, STORE, MENU["s_hiyayakko"], 1, None).ok  # 全員 ○ ならゲスト指定は不要


def test_dialogue1_alcohol():
    s = session(guest("g1", ["shrimp"]), guest("g2"))
    assert check_add(s, STORE, MENU["d_beer"], 1, None).decision == Decision.need_guest
    assert check_add(s, STORE, MENU["d_beer"], 1, "g2").decision == Decision.need_alcohol_check
    s.guests[1].alcohol = AlcoholCheck(adult=True, driving=False)
    assert check_add(s, STORE, MENU["d_beer"], 1, "g2").ok
    s.guests[1].alcohol = AlcoholCheck(adult=True, driving=True)
    assert check_add(s, STORE, MENU["d_beer"], 1, "g2").reasons == ["alcohol_not_allowed"]
    s.guests[1].alcohol = AlcoholCheck(adult=False, driving=False)
    assert check_add(s, STORE, MENU["d_beer"], 1, "g2").decision == Decision.rejected


def test_dialogue2_quick_order_still_checks_alcohol():
    s = session(guest("g1"), guest("g2"), guest("g3"))
    assert check_add(s, STORE, MENU["d_beer"], 1, None).decision == Decision.need_guest
    assert check_add(s, STORE, MENU["d_beer"], 1, "g1").decision == Decision.need_alcohol_check
    assert check_add(s, STORE, MENU["s_edamame"], 1, None).ok


def test_dialogue3_reject_and_alternative_only_ok_items():
    s = session(guest("g1", ["shrimp"]), guest("g2"))
    r = check_add(s, STORE, MENU["f_ebi_ten"], 1, "g1")
    assert r.decision == Decision.rejected and "allergen:shrimp" in r.reasons
    assert check_add(s, STORE, MENU["f_ebi_ten"], 1, "g2").ok
    alts = alternatives(MENU, s, MENU["f_ebi_ten"], "g1")
    assert alts and all(MENU[a.item_id].id != "f_karaage" for a in alts)  # △ の唐揚げは勧めない
    assert all(a.marks["g1"] == "○" for a in alts)


def test_single_guest_needs_no_selection_but_still_alcohol_check():
    s = session(guest("g1"))
    r = check_add(s, STORE, MENU["d_beer"], 1, None)
    assert r.decision == Decision.need_alcohol_check and r.for_guest == "g1"


@pytest.mark.parametrize("mutate,reason", [
    (lambda s, m: setattr(s, "status", SessionStatus.billing), "session_billing"),
    (lambda s, m: setattr(m, "sold_out", True), "sold_out"),
    (lambda s, m: setattr(s.guests[0], "declared", False), "undeclared_guest"),
])
def test_rejections(mutate, reason):
    s = session(guest("g1"))
    item = MENU["s_edamame"].model_copy()
    mutate(s, item)
    assert reason in check_add(s, STORE, item, 1, None).reasons


def test_qty_stock_and_last_order():
    s = session(guest("g1"))
    assert check_add(s, STORE, MENU["s_edamame"], 11, None).reasons == ["qty_limit"]
    assert check_add(s, STORE, MENU["g_saba"], 6, None).reasons == ["not_enough_stock"]  # 残り 5
    assert check_add(s, STORE, MENU["s_edamame"], 1, None, now_hhmm="22:30").reasons == ["last_order_passed"]


def test_recheck_after_declaration_change_and_token():
    s = session(guest("g1"), cart=[CartLine(line_id="l1", item_id="f_ebi_ten", qty=1, for_guest="g1")])
    token = cart_token(s.cart)
    assert recheck_cart(s, STORE, MENU) == []
    s.guests[0].allergies = ["shrimp"]  # 後からアレルギーを申告
    problems = recheck_cart(s, STORE, MENU)
    assert problems and problems[0][1].decision == Decision.rejected
    assert cart_token(s.cart) == token
    s.cart[0].qty = 2
    assert cart_token(s.cart) != token


# --- 注文・在庫・会計 ----------------------------------------------------------

def test_stock_take_and_release():
    items = {k: v.model_copy() for k, v in MENU.items()}
    lines = build_lines([CartLine(line_id="l1", item_id="g_saba", qty=5)], items)
    [change] = take_stock(items, lines)
    assert change.remaining == 0 and change.became_sold_out
    items["g_saba"].remaining, items["g_saba"].sold_out, items["g_saba"].sold_out_reason = 0, True, "stock"
    with pytest.raises(StockError):
        take_stock(items, lines)
    back = release_stock(items["g_saba"], 2)
    assert back.remaining == 2 and back.became_available


def test_line_transitions_and_aggregate():
    lines = build_lines([CartLine(line_id=f"l{i}", item_id=x, qty=1) for i, x in enumerate(["d_beer", "f_karaage", "s_edamame"])], MENU)
    order = Order(id="o1", session_id="s1", lines=lines, idempotency_key="k")
    order.lines[0] = transition(transition(transition(order.lines[0], LineStatus.confirmed), LineStatus.cooking), LineStatus.served)
    order.lines[1] = transition(transition(order.lines[1], LineStatus.confirmed), LineStatus.cooking)
    order.lines[2] = transition(order.lines[2], LineStatus.rejected)
    assert aggregate_status(order) == "partly_served"
    with pytest.raises(TransitionError):
        transition(order.lines[0], LineStatus.cancelled)  # 提供済みは取り消せない
    with pytest.raises(TransitionError):
        transition(order.lines[1], LineStatus.cancelled)  # 調理中はお客様が取り消せない


def test_bill_includes_otoshi_and_excludes_rejected():
    s = session(guest("g1"), guest("g2"))
    s.charges.append(otoshi_charge(500, 2, STORE.otoshi_label))
    lines = build_lines([CartLine(line_id="l1", item_id="d_beer", qty=1), CartLine(line_id="l2", item_id="f_karaage", qty=1),
                         CartLine(line_id="l3", item_id="s_edamame", qty=1)], MENU)
    lines[2] = lines[2].model_copy(update={"status": LineStatus.rejected})
    bill = make_bill(s, [Order(id="o1", session_id="s1", lines=lines, idempotency_key="k")])
    assert bill.total == 600 + 638 + 1000
    assert bill.per_person * 2 + bill.remainder == bill.total


def test_bill_per_guest():
    """各自払い：自分の料理 + 共有分（ゲストのない料理とお通し）を人数で割る。合計は必ず一致する。"""
    s = session(guest("g1"), guest("g2"))
    s.charges.append(otoshi_charge(500, 2, STORE.otoshi_label))
    lines = build_lines([
        CartLine(line_id="l1", item_id="d_beer", qty=1, for_guest="g1"),
        CartLine(line_id="l2", item_id="f_karaage", qty=1, for_guest="g2"),
        CartLine(line_id="l3", item_id="s_edamame", qty=1),
    ], MENU)
    bill = make_bill(s, [Order(id="o1", session_id="s1", lines=lines, idempotency_key="k")])
    assert bill.total == 600 + 638 + 418 + 1000  # 枝豆 418 円、お通し 500 円 × 2
    shared = (418 + 1000) // 2
    assert bill.per_guest == {"g1": 600 + shared, "g2": 638 + shared}
    assert sum(bill.per_guest.values()) == bill.total


def test_price_snapshot_survives_menu_change():
    items = {k: v.model_copy() for k, v in MENU.items()}
    lines = build_lines([CartLine(line_id="l1", item_id="d_beer", qty=1)], items)
    items["d_beer"].price = 999
    assert lines[0].unit_price == 600


# --- テーブル状態・イベント・引き継ぎ・おすすめ ------------------------------------

def test_phases():
    s = session(guest("g1"))
    assert derive_phase(s, [], STORE, "19:00") == Phase.seated
    s.cart = [CartLine(line_id="l1", item_id="s_edamame", qty=1)]
    assert derive_phase(s, [], STORE, "19:00") == Phase.ordering
    order = Order(id="o1", session_id="s1", lines=build_lines(s.cart, MENU), idempotency_key="k")
    assert derive_phase(s, [order], STORE, "19:00") == Phase.waiting
    order.lines[0].status = LineStatus.served
    assert derive_phase(s, [order], STORE, "19:00") == Phase.eating
    assert derive_phase(s, [order], STORE, "22:20") == Phase.near_last_order
    s.status = SessionStatus.billing
    assert derive_phase(s, [order], STORE, "22:20") == Phase.billing


def test_sold_out_notifies_cart_and_unconfirmed_orders_only():
    a = session(guest("g1"), cart=[CartLine(line_id="l1", item_id="s_dashimaki", qty=1)])
    b = TableSession(id="s2", table_no="3", party_size=1, guests=[guest("g1")])
    c = TableSession(id="s3", table_no="4", party_size=1, guests=[guest("g1")])
    ob = Order(id="o2", session_id="s2", lines=build_lines([CartLine(line_id="x", item_id="s_dashimaki", qty=1)], MENU), idempotency_key="k2")
    notes = on_sold_out("ev1", "s_dashimaki", [a, b, c], {"s2": [ob]})
    targets = {n.target for n in notes}
    assert targets == {"table:s1", "table:s2", "staff"}
    assert len({n.dedupe_key for n in notes}) == len(notes)


def test_escalation_schedule():
    assert escalation_followups("e1", urgent=True)[-1] == (120, "escalation_all")
    assert escalation_followups("e1", urgent=False) == [(120, "escalation_repeat")]


def test_summary_does_not_invent_facts():
    s = session(guest("g1"))
    order = Order(id="o1", session_id="s1", lines=build_lines([CartLine(line_id="l1", item_id="f_karaage", qty=1)], MENU), idempotency_key="k")
    order.lines[0].status = LineStatus.served
    text = build_summary("2", Reason.wrong_dish, "注文したものと違う", s, [order], unknown_facts=["届いた料理名"])
    assert "誤配膳" in text and "提供済み：鶏の唐揚げ×1" in text and "届いた料理名は未確認" in text
    assert "軟骨" not in text


def test_detect_medical():
    assert detect_medical("I can't breathe")
    assert detect_medical("救急車を呼んでください")
    assert not detect_medical("This is delicious")


def test_recommendations_respect_table():
    s = session(guest("g1", ["shrimp"]), guest("g2"))
    recs = recommend(MENU, s, Phase.seated, for_guest="g1", limit=10)
    ids = {r.item_id for r in recs}
    assert "f_karaage" not in ids and "f_ebi_ten" not in ids and "g_buta_kimchi" not in ids
    table_recs = recommend(MENU, s, Phase.seated, limit=5)
    karaage = next((r for r in table_recs if r.item_id == "f_karaage"), None)
    if karaage:
        assert karaage.marks == {"g1": "△", "g2": "○"}  # 卓全体のおすすめでも、人ごとの印を付ける
    halal = session(guest("g1", diets=["halal"]), guest("g2"))
    assert not any(MENU[r.item_id].is_alcohol for r in recommend(MENU, halal, Phase.seated, limit=20))


def test_taste_hint_moves_spicy_up():
    s = session(guest("g1"))
    recs = recommend(MENU, s, Phase.ordering, taste=["spicy"], category="grill", limit=2)
    assert {r.item_id for r in recs} <= {"g_mapo", "g_buta_kimchi"}


def test_options_price_and_validation():
    from app.domain.orders import unit_price

    beer = MENU["d_beer"]
    assert unit_price(beer, {}) == 600  # 既定は中ジョッキ
    assert unit_price(beer, {"size": "large"}) == 800
    s = session(guest("g1", alcohol=AlcoholCheck(adult=True, driving=False)))
    assert check_add(s, STORE, beer, 1, None, options={"size": "huge"}).reasons == ["invalid_option"]
    assert check_add(s, STORE, beer, 1, None, options={"size": "large"}).ok
    lines = build_lines([CartLine(line_id="l1", item_id="d_beer", qty=2, options={"size": "large"})], MENU)
    assert lines[0].unit_price == 800


def test_time_rules_last_order_and_overdue():
    """ラストオーダー 15 分前から 1 回だけ知らせる。注文から 20 分たって出ていない料理は店員へ（先払いの未払いは除く）。"""
    from datetime import datetime, timedelta

    from app.domain.events import time_rules
    from app.store.repo import JST

    store = STORE  # ラストオーダー 22:30
    s = TableSession(id="t1", store_id="s1", table_no="1", party_size=1, lang="en")
    now = datetime(2026, 10, 11, 22, 20, tzinfo=JST)
    line = OrderLine(line_id="l1", item_id="f_karaage", name=MENU["f_karaage"].name, qty=1, unit_price=638,
                     status=LineStatus.confirmed)
    old = Order(id="o1", session_id="t1", lines=[line], idempotency_key="k", created_at=(now - timedelta(minutes=25)).isoformat())
    fresh = old.model_copy(update={"id": "o2", "created_at": (now - timedelta(minutes=5)).isoformat()})
    unpaid = old.model_copy(update={"id": "o3", "prepay": True})
    out = time_rules(store, [s], [old, fresh, unpaid], "22:20", now)
    kinds = sorted((n.kind, n.target) for n in out)
    assert kinds == [("last_order", "table:t1"), ("overdue", "staff")]
    assert {n.dedupe_key for n in out} == {"lo:2026-10-11:t1", "overdue:l1"}
    assert [n.kind for n in time_rules(store, [s], [], "21:00", now)] == []


def test_menu_search_matches_words_and_ranks():
    """「chicken skewers」でも焼き鳥が見つかり、唐揚げより上に来る。日本語は部分一致。"""
    from app.services.table import match_score

    assert match_score(MENU["g_yakitori"], "chicken skewers") > match_score(MENU["f_karaage"], "chicken skewers") > 0
    assert match_score(MENU["g_mapo"], "mapo tofu") == 100
    assert match_score(MENU["g_buta_kimchi"], "辛い") == 100
    assert match_score(MENU["ds_matcha_ice"], "pizza") == 0
