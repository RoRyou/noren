"""outbox（送信待ちイベント）を処理して、通知を作り、画面へ配信する。

- 通知は dedupe_key で 1 つだけ作るので、同じイベントを何度処理しても重複しない（至少 1 回の配信に対応）。
- 処理を始めるきっかけは 2 つ：書き込んだリクエストの直後（kick）と、定期的な見回り（sweep）。
"""

from __future__ import annotations

import asyncio
import logging

from app.domain import events as rules
from app.realtime.hub import Hub
from app.realtime.scheduler import LocalScheduler
from app.store.repo import Repo

log = logging.getLogger("noren.dispatcher")


class Dispatcher:
    def __init__(self, hub: Hub, scheduler: LocalScheduler, repo_factory=None):
        self.hub = hub
        self.scheduler = scheduler
        self._locks: dict[str, asyncio.Lock] = {}
        scheduler.register("escalation_followup", self._escalation_followup)
        self._repos: dict[str, Repo] = {}
        self.repo_factory = repo_factory  # 別のインスタンスで登録されたタスクでも店を開けるように

    def _lock(self, store_id: str) -> asyncio.Lock:
        return self._locks.setdefault(store_id, asyncio.Lock())

    def kick(self, repo: Repo) -> None:
        """書き込みの直後に呼ぶ。失敗しても見回りで拾われる。"""
        self._repos[repo.store_id] = repo
        asyncio.create_task(self.dispatch(repo))

    async def dispatch(self, repo: Repo) -> int:
        self._repos[repo.store_id] = repo
        async with self._lock(repo.store_id):
            count = 0
            for ev in await repo.pending_outbox():
                try:
                    for n in await self._notifications_for(repo, ev):
                        data, created = await repo.put_notification(n.target, n.kind, n.payload, n.dedupe_key)
                        if created:
                            self.hub.publish(repo.store_id, n.target, {"type": "notification", **data})
                    self._after(repo, ev)
                    await repo.mark_dispatched(ev["id"])
                    count += 1
                except Exception:  # 次の見回りで再試行
                    log.exception("dispatch failed: %s", ev.get("id"))
            return count

    async def tick(self, repo: Repo, now_hhmm: str, now) -> int:
        """時間で決まる通知（ラストオーダー前・提供が遅い）を出す。見回りのたびに呼ぶ。"""
        store = await repo.store()
        sessions = await repo.open_sessions()
        orders = await repo.open_orders()
        count = 0
        for n in rules.time_rules(store, sessions, orders, now_hhmm, now):
            data, created = await repo.put_notification(n.target, n.kind, n.payload, n.dedupe_key)
            if created:
                self.hub.publish(repo.store_id, n.target, {"type": "notification", **data})
                count += 1
        return count

    async def _notifications_for(self, repo: Repo, ev: dict) -> list[rules.Notification]:
        kind, p, eid = ev["kind"], ev["payload"], ev["id"]
        if kind == "order_accepted":
            return [rules.Notification("staff", "new_order", p, f"{eid}:staff"),
                    rules.Notification(f"table:{p['session_id']}", "order_status", p, f"{eid}:table")]
        if kind == "line_status":
            out = [rules.Notification("staff", "order_updated", p, f"{eid}:staff"),
                   rules.Notification(f"table:{p['session_id']}", "order_status", p, f"{eid}:table")]
            if p["status"] == "rejected":
                out += rules.on_rejected(eid, p["session_id"], p["line_id"], p["item_id"], p.get("reason", ""))
            return out
        if kind == "sold_out":
            sessions = await repo.open_sessions()
            orders = {s.id: await repo.orders_for(s.id) for s in sessions}
            return rules.on_sold_out(eid, p["item_id"], sessions, orders) + [
                rules.Notification("staff", "menu_updated", p, f"{eid}:menu")]
        if kind == "escalation_created":
            return [rules.Notification("staff", "escalation", p, f"{eid}:staff")]
        if kind == "order_paid":
            return [rules.Notification("staff", "order_updated", p, f"{eid}:staff"),
                    rules.Notification(f"table:{p['session_id']}", "order_status", p, f"{eid}:table")]
        return []

    def _after(self, repo: Repo, ev: dict) -> None:
        if ev["kind"] == "escalation_created":
            p = ev["payload"]
            for delay, action in rules.escalation_followups(p["escalation_id"], p["urgent"]):
                self.scheduler.schedule(delay, "escalation_followup",
                                        {"store_id": repo.store_id, "escalation_id": p["escalation_id"], "action": action,
                                         "delay": delay}, task_name=f"{repo.store_id}:{p['escalation_id']}:{delay}")

    async def _escalation_followup(self, payload: dict) -> None:
        repo = self._repos.get(payload["store_id"])
        if repo is None and self.repo_factory:
            repo = self.repo_factory(payload["store_id"])
        if repo is None:
            return
        esc = await repo.escalation(payload["escalation_id"])
        if not esc or esc["status"] != "pending":
            return  # 誰かが対応を始めた
        await repo.update_escalation(esc["id"], {"reminders": esc.get("reminders", 0) + 1})
        data, created = await repo.put_notification("staff", payload["action"], {"escalation_id": esc["id"], "urgent": esc["urgent"]},
                                                    f"{esc['id']}:{payload['delay']}")
        if created:
            self.hub.publish(repo.store_id, "staff", {"type": "notification", **data})
