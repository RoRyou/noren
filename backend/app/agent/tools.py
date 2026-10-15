"""接客エージェントのツール（専門機能）。すべて TableService を通すので、画面のボタンと同じチェックを受ける。

- 結果は AI に返すと同時に、画面へ UI イベントとして送る（聞いた内容と画面が一致する）。
- 料理を返すツールは、お客様ごとの食事判定（○✕△？）を必ず付ける（AI が判定ツールを呼ばなくても）。
- 3.8 Live は中断後に同じ呼び出しを繰り返すことがあるので、書き込み系は 5 秒以内の同じ呼び出しを 1 回にまとめる。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Awaitable, Callable

from app.domain.allergens import allergen_label
from app.domain.cart import Decision, cart_text
from app.domain.escalation import Reason
from app.services.table import TableService

Emit = Callable[[dict], Awaitable[None]]
log = logging.getLogger("noren.tools")

EXPERT = {
    "search_menu": "menu", "get_item": "menu", "recommend": "recommend", "get_store_knowledge": "knowledge",
    "check_dietary": "allergy", "set_dietary_profile": "allergy", "confirm_alcohol": "order", "add_item": "order", "update_item": "order",
    "read_back": "order", "submit_order": "order", "cancel_order_item": "order", "get_order_status": "order",
    "call_staff": "staff", "escalate": "staff", "request_bill": "bill",
}
DEDUPE_S = 5.0
MAX_CALLS_PER_TURN = 16
MUTATING = {"add_item", "confirm_alcohol", "update_item", "submit_order", "escalate", "call_staff", "set_dietary_profile", "cancel_order_item"}


COURSE = [("drink", ["drink"], "最初のお飲み物"), ("snack", ["snack"], "すぐ出るおつまみ"),
          ("main", ["fried", "grill", "sashimi"], "メインの料理（揚げ物・焼き物・刺身）"), ("shime", ["rice"], "締めのご飯もの"),
          ("dessert", ["dessert"], "デザート")]


# 店舗知識を聞かれたときの言い方 → 知識の分類
KNOWLEDGE_TOPICS = {
    "season": ["季節", "旬", "秋", "限定", "おすすめ", "season", "autumn", "fall", "special", "recommend", "当季", "推荐", "제철", "가을"],
    "origin": ["産地", "どこ", "仕入", "origin", "where", "from", "产地", "산지"],
    "sake": ["お酒", "日本酒", "地酒", "sake", "alcohol", "清酒", "술"],
    "story": ["こだわり", "作り方", "手作り", "story", "made", "特色"],
}


def next_course(have: set[str]) -> dict | None:
    """カートと注文済みの分類から、おすすめの流れの次の段階を返す（全部そろっていれば None）。"""
    for stage, cats, label in COURSE:
        if not have & set(cats):
            return {"stage": stage, "categories": cats, "label": label,
                    "how": f"「ほかにご注文は？」で終わらせず、recommend（category は {'・'.join(cats)} のどれか）で{label}を出して一言ですすめる"}
    # 一通りそろった：確定へ案内する
    return {"stage": "confirm", "categories": [], "label": "ご注文の確定",
            "how": "「ほかにご注文は？」ではなく「ご注文はこれでおそろいですか？確認しましょうか」と確定へ案内し、はいなら read_back で復唱する"}


def _brief(card: dict, lang: str) -> dict:
    """AI に返す料理情報（短く）。"""
    label = "ja" if lang == "ja" else "en"
    return {
        "item_id": card["item_id"], "name": card["name"], "price": card["price"],
        "sold_out": card["sold_out"],
        "for_each_guest": {gid: m["mark"] + ("（" + "、".join(m["reasons"]) + "）" if m["reasons"] else "")
                           for gid, m in card["marks"].items()},
        # 自分からすすめてよいのは、全員が ○ の料理だけ（✕ △ ？ が 1 人でもいれば false）
        "ok_for_everyone": all(m["mark"] == "○" for m in card["marks"].values()),
        "allergens": [allergen_label(a, label) for a in card["allergens"]],
        "cross_contact": [allergen_label(a, label) for a in card["cross_contact"]],
        "allergy_info_confirmed": card["allergen_status"] == "confirmed",
        **({"portion": card["portion"]} if card.get("portion") else {}),
        **({"how_to_eat": card["how_to_eat"]} if card.get("how_to_eat") else {}),
        **({"note": card["description"]} if card.get("description") else {}),
        **({"options": {o["key"]: {"label": o["label"], "choices": {c["key"]: f"{c['label']}" + (f" +{c['price_delta']}円" if c["price_delta"] else "")
                                                                   for c in o["choices"]}, "default": o["default"]}
                        for o in card["options"]}} if card.get("options") else {}),
    }


def build_tools(svc: TableService, session_id: str, lang: str, emit: Emit, correlation: Callable[[], str],
                heard: Callable[[], int] = lambda: 0, ai_said: Callable[[], str] = lambda: "") -> list:
    """heard：お客様の発話・画面操作を受け取った回数。復唱の後にお客様の返事があったかを見るのに使う。
    ai_said：この返答で AI がここまでに話した文字。質問した直後に先へ進まないように使う。"""
    recent: dict[str, tuple[float, dict]] = {}
    read_back_at: list[int] = []  # 復唱したときの heard()
    calls: list[int] = []  # [この返答で呼んだ回数, そのときの heard()]
    pending_adds: list[dict] = []  # 申告の前に頼まれた料理（申告が終わったら自動で入れる。ZI-084）

    async def run(name: str, args: dict, body: Callable[[], Awaitable[dict]]) -> dict:
        key = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
        turn = heard()
        calls[:] = [calls[0] + 1 if calls and calls[1] == turn else 1, turn] if calls else [1, turn]
        if calls[0] > MAX_CALLS_PER_TURN:  # 同じ呼び出しを繰り返して止まらないときの歯止め（ZI-078）
            log.warning("%s too many tool calls (%s)", correlation(), name)
            return {"ok": False, "error": "too_many_calls",
                    "say": "ツールを呼ぶのをやめ、いまのカートと分かっていることをお客様に伝えて、次にどうするか聞いてください。"}
        if name in MUTATING and key in recent and time.monotonic() - recent[key][0] < DEDUPE_S:
            return recent[key][1]  # 同じ呼び出しの繰り返し → 前の結果を返す
        await emit({"type": "expert", "expert": EXPERT.get(name, "other"), "tool": name})
        try:
            result = await body()
        except Exception as e:  # 間違った id などで例外が出ても、音声の会話は止めずに AI に直させる（ZI-070）
            log.warning("%s %s %s failed: %r", correlation(), name, json.dumps(args, ensure_ascii=False), e)
            return {"ok": False, "error": "invalid_arguments" if isinstance(e, (KeyError, ValueError, TypeError)) else "failed",
                    "hint": "item_id は検索・おすすめの結果の item_id、line_id はカートの [line_id=...] を使う。分からなければ search_menu で確かめる"}
        # まとめるのは通った結果だけ（「返事を待って」「お酒の確認が必要」などの後のやり直しは通す）
        if result.get("reason") != "not_confirmed_yet" and result.get("decision") in (None, "ok"):
            recent[key] = (time.monotonic(), result)
        log.info("%s %s %s → %s", correlation(), name, json.dumps(args, ensure_ascii=False),
                 json.dumps(result, ensure_ascii=False)[:300])
        return result

    async def search_menu(query: str = "", category: str = "", taste: str = "") -> dict:
        """メニュー専門：料理や飲み物を探す。query は料理名や食材、category は drink/snack/fried/grill/sashimi/rice/dessert、
        taste は spicy/light/rich/sweet/sour のいずれか。結果にはゲストごとの判定（○✕△？）が付く。"""
        async def body():
            cards = await svc.menu_for(session_id, category or None, query or None, [taste] if taste else None)
            await emit({"type": "show_items", "items": cards[:8], "source": "search"})
            return {"items": [_brief(c, lang) for c in cards[:8]], "count": len(cards)}
        return await run("search_menu", {"query": query, "category": category, "taste": taste}, body)

    async def get_item(item_id: str) -> dict:
        """メニュー専門：1 品の詳しい情報（量、食べ方、アレルゲン、混入の可能性、ゲストごとの判定）。"""
        async def body():
            card = await svc.check_dietary(session_id, item_id)
            await emit({"type": "show_items", "items": [card], "source": "detail"})
            return _brief(card, lang)
        return await run("get_item", {"item_id": item_id}, body)

    async def recommend(taste: str = "", category: str = "", for_guest: str = "") -> dict:
        """おすすめ専門：いまのテーブルに合うおすすめ（最大 3 品と理由）。taste は spicy/light/rich/sweet/sour、
        for_guest を指定するとその人が ○ の料理だけ。ここで返った料理だけをすすめる。"""
        async def body():
            recs = await svc.recommend(session_id, [taste] if taste else None, category or None, for_guest or None)
            await emit({"type": "recommendations", "items": recs})
            return {"items": [{**_brief(r, lang), "why": r["reasons"]} for r in recs]}
        return await run("recommend", {"taste": taste, "category": category, "for_guest": for_guest}, body)

    async def get_store_knowledge(topic: str) -> dict:
        """店舗知識専門：産地・旬・お酒・店の話など、店主が登録した事実。ないときは found=false（推測しない）。"""
        async def body():
            facts = []
            t = topic.lower()
            words = [w for w in t.replace("、", " ").split() if w]
            cats = {c for c, keys in KNOWLEDGE_TOPICS.items() if any(k in t for k in keys)}  # 「季節のおすすめ」→ season
            for k in await svc.repo.knowledge():
                text = k["text"]
                hay = f"{text.ja} {text.en} {' '.join(k.get('items', []))} {k.get('category', '')}".lower()
                if k.get("category") in cats or any(w in hay for w in words):
                    facts.append(text.get(lang))
            if facts:
                await emit({"type": "knowledge", "facts": facts[:3]})
            return {"found": bool(facts), "facts": facts[:3]}
        return await run("get_store_knowledge", {"topic": topic}, body)

    async def check_dietary(item_id: str) -> dict:
        """アレルギー専門：この料理をゲストごとに食べられるか（○✕△？）と理由。判定はコードが行う。"""
        async def body():
            card = await svc.check_dietary(session_id, item_id)
            await emit({"type": "show_items", "items": [card], "source": "dietary"})
            return _brief(card, lang)
        return await run("check_dietary", {"item_id": item_id}, body)

    async def set_dietary_profile(guest_id: str, allergies: list[str], diets: list[str], severe: bool = False) -> dict:
        """アレルギー専門：ゲストのアレルギー・食事制限を記録する。allergies はアレルゲンコード
        （shrimp crab walnut wheat buckwheat egg milk peanut cashew almond abalone squid salmon_roe orange kiwi beef sesame
        salmon mackerel soybean chicken banana pistachio pork macadamia peach yam apple gelatin）、
        diets は halal/vegetarian/vegan/gluten_free/no_pork/no_beef/no_alcohol/no_five_pungent。"""
        async def body():
            s = await svc.repo.session(session_id)
            g = s.guest(guest_id)
            if g is None:
                await emit({"type": "ask_guest", "purpose": "dietary"})
                return {"ok": False, "reason": "unknown_guest", "guests": [x.id for x in s.guests]}
            decl = {"guest_id": guest_id, "allergies": sorted(set(g.allergies) | set(allergies)),
                    "diets": sorted(set(g.diets) | set(diets)), "severe": severe or g.severe}
            s = await svc.declare(session_id, [decl])
            await emit({"type": "guests_updated", "guests": [x.model_dump() for x in s.guests]})
            out = {"ok": True, "guest": s.guest(guest_id).model_dump(), "escalated": severe}
            if pending_adds and all(g.declared for g in s.guests):  # 申告の前に頼まれた料理を入れる
                menu = await svc.repo.menu()
                added = []
                for p in list(pending_adds):
                    check, s = await svc.add_to_cart(session_id, p["item_id"], p["qty"], p["for_guest"], p["options"],
                                                     actor="reception_agent", correlation_id=correlation())
                    name = menu[p["item_id"]].name.ja if p["item_id"] in menu else p["item_id"]
                    added.append({"item": name, "qty": p["qty"], **check.to_dict()})
                pending_adds.clear()
                await emit({"type": "cart", "cart": [l.model_dump() for l in s.cart]})
                out["pending_orders"] = added
                out["note"] = "申告の前に頼まれた料理を処理した。decision が ok ならカートに入れたと伝え、need_* ならその確認をする"
            return out
        return await run("set_dietary_profile", {"guest_id": guest_id, "allergies": allergies, "diets": diets, "severe": severe}, body)

    async def confirm_alcohol(guest_id: str, adult: bool, driving: bool) -> dict:
        """お酒の確認：その方が 20 歳以上か（adult）、今日運転するか（driving）を、会話で聞いた答えのとおり記録する（自己申告）。"""
        async def body():
            s = await svc.set_alcohol(session_id, guest_id, adult, driving)
            await emit({"type": "guests_updated", "guests": [x.model_dump() for x in s.guests]})
            return {"ok": True, "can_drink": adult and not driving}
        return await run("confirm_alcohol", {"guest_id": guest_id, "adult": adult, "driving": driving}, body)

    async def add_item(item_id: str, qty: int = 1, for_guest: str = "", options: list[str] | None = None,
                       table_ok: bool = False) -> dict:
        """注文：カートに追加する。ふつうは for_guest を空にして、テーブル全体の料理として入れる。
        options は料理の選択肢を "グループ=選択肢" の形で（例：["size=large"]）。選ばなければ既定の選択肢になる。
        結果の decision に従う：ok / need_table_ok（食べられない・飲めない方がいる。not_for の方を伝えて
        「それでもご注文されますか？」と聞き、はいなら table_ok=true で呼び直す）/ need_alcohol_check（全員に 20 歳以上か・
        今日運転しないかを聞き、confirm_alcohol で記録してから呼び直す）/ need_staff（店員の確認が必要）/
        need_declaration（申告がまだ）/ rejected（理由と代わりの料理 alternatives を伝える）。"""
        async def body():
            opts = dict(o.split("=", 1) for o in (options or []) if "=" in o)
            check, s = await svc.add_to_cart(session_id, item_id, qty, for_guest or None, opts or None,
                                             actor="reception_agent", correlation_id=correlation(), table_ok=table_ok)
            out = check.to_dict()
            if check.decision == Decision.need_declaration:
                pending_adds.append({"item_id": item_id, "qty": qty, "for_guest": for_guest or None, "options": opts or None})
                out["note"] = "まだ食べられないものを聞いていない方がいる。聞いて set_dietary_profile で記録すると、この注文は自動でカートに入る"
            cannot = [w.split(":", 1)[1] for w in check.warnings if w.startswith("not_for:")]
            if cannot:  # 召し上がらない方と理由（「ゲスト 2：卵を含みます」）
                out["not_for"] = {gid: out["verdicts"].get(gid, {}).get("reasons") or ["お酒を飲めない方"] for gid in cannot}
            if check.decision == Decision.need_guest:
                await emit({"type": "ask_guest", "purpose": "item", "item_id": item_id, "qty": qty, "verdicts": out["verdicts"], "options": opts})
            elif check.decision == Decision.need_alcohol_check:
                await emit({"type": "ask_alcohol", "item_id": item_id, "qty": qty, "for_guest": check.for_guest, "options": opts})
            elif check.decision in (Decision.rejected, Decision.need_staff):
                alts = await svc.alternatives_for(session_id, item_id, check.for_guest)
                out["alternatives"] = [_brief(a, lang) for a in alts]
                await emit({"type": "blocked", "item_id": item_id, "check": out, "alternatives": alts})
            if check.ok:
                await emit({"type": "cart", "cart": [l.model_dump() for l in s.cart]})
            menu = await svc.repo.menu()
            out["cart_now"] = cart_text(s.cart, menu)
            if check.ok:  # 「受け付けました」と言ってしまう対策（注文はまだ送っていない）
                out["note"] = "カートに入れただけで、注文はまだ送っていません。ほかに注文がなければ read_back で復唱します。"
                orders = await svc.repo.orders_for(session_id)
                have = {menu[l.item_id].category for l in s.cart if l.item_id in menu}
                have |= {menu[l.item_id].category for o in orders for l in o.lines if l.item_id in menu}
                nxt = next_course(have)
                if nxt:  # おすすめの流れの次（飲み物 → おつまみ → メイン → 締め）。カードは AI を待たずに出す
                    recs: list[dict] = []
                    for cat in nxt["categories"]:
                        recs += await svc.recommend(session_id, None, cat, None, 2)
                    recs = sorted(recs, key=lambda r: -len(r.get("reasons", [])))[:4]
                    if recs:
                        await emit({"type": "recommendations", "items": recs})
                        nxt["items"] = [{"item_id": r["item_id"], "name": r["name"], "why": r["reasons"]} for r in recs]
                        nxt["how"] = f"画面に{nxt['label']}のカードを出した。この中から一つ二つ、名前と一言の理由ですすめる"
                    out["next_course"] = nxt
            return out
        return await run("add_item", {"item_id": item_id, "qty": qty, "for_guest": for_guest, "options": options or [],
                                      "table_ok": table_ok}, body)

    async def update_item(line_id: str, qty: int) -> dict:
        """注文：カートの数量を変える（0 で削除）。line_id はカートの [line_id=...]。"""
        async def body():
            session = await svc.repo.session(session_id)
            target = line_id
            if not any(l.line_id == line_id for l in session.cart):  # 料理の id が渡されたら、その料理の行が 1 つならそれを使う
                same = [l.line_id for l in session.cart if l.item_id == line_id]
                if len(same) != 1:
                    menu = await svc.repo.menu()
                    return {"ok": False, "error": "unknown_line_id", "cart_now": cart_text(session.cart, menu)}
                target = same[0]
            check, s = await svc.update_line(session_id, target, qty, actor="reception_agent")
            await emit({"type": "cart", "cart": [l.model_dump() for l in s.cart]})
            return {"ok": check is None or check.ok, **({"check": check.to_dict()} if check else {})}
        return await run("update_item", {"line_id": line_id, "qty": qty}, body)

    async def read_back() -> dict:
        """注文：送信前の復唱。カートを再チェックし、確認用の confirm_token を返す。画面に確認シートが出る。"""
        async def body():
            if "？" in ai_said() or "?" in ai_said():  # この返答で質問したばかり（「デザートは？」→ 答えを待たずに復唱）
                return {"ok": False, "reason": "wait_for_answer",
                        "say": "いまお客様に質問したので、答えを待ってから read_back を呼ぶ。ここでは何も言わずに待つ。"}
            prev = (await svc.repo.session(session_id)).confirm_token
            rb = await svc.read_back(session_id)
            if not (prev and rb.get("confirm_token") == prev):
                read_back_at[:] = [heard()]  # 新しい内容の復唱：この後のお客様の返事を待つ
            elif not read_back_at:
                # 同じ内容をすでに復唱していた（前の会話で。返事の前に会話が閉じ、「はい」で開き直した場合）（ZI-076）
                read_back_at[:] = [-1]
            await emit({"type": "read_back", **rb})
            return rb
        return await run("read_back", {}, body)

    async def submit_order(confirm_token: str) -> dict:
        """注文：お客様が復唱に「はい」と答えた後だけ呼ぶ。厨房が受けるまでは「受け付けました」と伝える。"""
        async def body():
            if read_back_at and heard() <= read_back_at[0]:  # 復唱をお客様が聞く前に送ろうとした
                return {"ok": False, "reason": "not_confirmed_yet",
                        "say": "復唱した内容をお客様に伝え、「はい」の返事を聞いてから submit_order を呼んでください。"}
            result = await svc.submit(session_id, confirm_token, idempotency_key=confirm_token, actor="reception_agent",
                                      correlation_id=correlation())
            await emit({"type": "order_result", **result})
            if result.get("ok"):
                # pay_first：先払いの店。「レジでお支払いください。お支払いの後に作ります」と伝える
                return {"ok": True, "say": result["message_key"], "order_id": result["order"]["id"]}
            return result
        return await run("submit_order", {"confirm_token": confirm_token}, body)

    async def get_order_status() -> dict:
        """注文：注文済みの料理ごとの状態（accepted 受付 / confirmed 厨房が受けた / cooking / served / rejected / cancelled）。"""
        async def body():
            view = await svc.view(session_id)
            lines = [{"order_id": o["id"], "line_id": l["line_id"], "name": l["name"].get("ja"), "qty": l["qty"],
                      "status": l["status"], "updated_at": l["updated_at"]} for o in view["orders"] for l in o["lines"]]
            return {"lines": lines}
        return await run("get_order_status", {}, body)

    async def cancel_order_item(order_id: str, line_id: str) -> dict:
        """注文：注文済みの料理を取り消す。調理が始まっていたら店員につなぐ。"""
        async def body():
            return await svc.cancel_line(order_id, line_id)
        return await run("cancel_order_item", {"order_id": order_id, "line_id": line_id}, body)

    async def call_staff(kind: str, note: str = "") -> dict:
        """店員を呼ぶ（水・取り皿・おしぼりなど）。kind は water/tableware/towel/other。
        セルフサービスのものは場所を案内するだけで、店員は呼ばない。"""
        async def body():
            store = await svc.repo.store()
            if kind in store.self_service:
                return {"self_service": True, "guide": store.self_service[kind].get(lang)}
            data, _ = await svc.repo.put_notification("staff", "call", {"session_id": session_id, "kind": kind, "note": note},
                                                      f"call:{session_id}:{kind}:{int(time.time() // 60)}")
            from app.realtime.hub import hub
            hub.publish(svc.repo.store_id, "staff", {"type": "notification", **data})
            return {"called": True}
        return await run("call_staff", {"kind": kind, "note": note}, body)

    async def request_bill() -> dict:
        """会計：明細とお一人あたりの金額。これ以降は追加注文できない。支払いはレジで。"""
        async def body():
            bill = await svc.request_bill(session_id)
            await emit({"type": "bill", **bill})
            return bill
        return await run("request_bill", {}, body)

    async def escalate(reason: str, guest_words_ja: str = "") -> dict:
        """店員につなぐ。reason は not_understood/severe_allergy/complaint/wrong_dish/foreign_object/bill_dispute/
        abnormal_quantity/medical/dietary_check/other。要約はコードが作るので、お客様の言葉の日本語訳だけを渡す。"""
        async def body():
            try:
                r = Reason(reason)
            except ValueError:
                r = Reason.other
            unknown = ["届いた料理名"] if r == Reason.wrong_dish else (["症状"] if r == Reason.medical else [])
            data = await svc.escalate(session_id, r, guest_words_ja, unknown, correlation_id=correlation())
            await emit({"type": "escalated", "reason": r.value, "urgent": data["urgent"]})
            return {"ok": True, "urgent": data["urgent"]}
        return await run("escalate", {"reason": reason, "guest_words_ja": guest_words_ja}, body)

    return [search_menu, get_item, recommend, get_store_knowledge, check_dietary, set_dietary_profile, confirm_alcohol, add_item,
            update_item, read_back, submit_order, get_order_status, cancel_order_item, call_staff, request_bill, escalate]
