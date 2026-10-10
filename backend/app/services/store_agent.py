"""店舗エージェントの操作（理解 → 変更案 → 確認 → 反映）。"""

from __future__ import annotations

import uuid

from google.cloud import firestore

from app.agent import store_agent
from app.store.repo import Repo, now_iso

CONFIRM_THRESHOLD = 2  # この品数以上の品切れは、店員の確認を待つ


class StoreAgentService:
    def __init__(self, repo: Repo):
        self.repo = repo

    async def handle(self, text: str, actor: str = "staff") -> dict:
        items = await self.repo.menu()
        intent = await store_agent.understand(text, items)
        valid_ids = [i for i in intent.item_ids if i in items]
        changes: list[dict] = []
        if intent.intent == "sold_out":
            ids = sorted(set(valid_ids) | set(store_agent.affected_by_ingredients(items, intent.ingredients)))
            changes = [{"item_id": i, "name": items[i].name.ja, "sold_out": True} for i in ids]
        elif intent.intent == "restock":
            changes = [{"item_id": i, "name": items[i].name.ja, "sold_out": False,
                        **({"add": intent.quantity} if intent.quantity else {})} for i in valid_ids]
        elif intent.intent == "todays_pick":
            changes = [{"item_id": i, "name": items[i].name.ja, "owner_weight": 3} for i in valid_ids]
        elif intent.intent == "knowledge" and intent.knowledge_ja:
            changes = [{"knowledge": intent.knowledge_ja, "items": valid_ids}]

        result = {"intent": intent.intent, "ingredients": intent.ingredients, "changes": changes}
        policy = (await self.repo.store()).service_policy
        if intent.intent == "sold_out":
            key = "sold_out_multi" if len(changes) >= CONFIRM_THRESHOLD else "sold_out_single"
            needs_confirm = policy.get(key, "confirm") == "confirm"
        else:
            needs_confirm = intent.intent == "todays_pick" and policy.get("todays_pick", "auto") == "confirm"
        await self.repo.audit("store_agent", "understand", {"text": text}, intent.intent,
                              reason=f"{len(changes)} changes, confirm={needs_confirm}")
        if not changes:
            return {**result, "applied": False, "message": "分かりませんでした。料理名か食材名で言い直してください。"}
        if needs_confirm:  # 影響が大きいので、店員に確認してもらう
            pending_id = f"chg_{uuid.uuid4().hex[:8]}"
            await self.repo.col("pending_changes").document(pending_id).set(
                {"id": pending_id, "text": text, "actor": actor, **result, "created_at": now_iso()})
            return {**result, "applied": False, "pending_id": pending_id}
        await self._apply(result, actor)
        return {**result, "applied": True}

    async def confirm(self, pending_id: str, actor: str = "staff") -> dict:
        ref = self.repo.col("pending_changes").document(pending_id)
        snap = await ref.get()
        if not snap.exists:
            raise KeyError(pending_id)
        data = snap.to_dict()
        await self._apply(data, actor)
        await ref.delete()
        return {"applied": True, "changes": data["changes"]}

    async def _apply(self, result: dict, actor: str) -> None:
        changes = result["changes"]
        if result["intent"] == "sold_out":
            await self.repo.set_sold_out([c["item_id"] for c in changes], True, actor=f"store_agent/{actor}")
        elif result["intent"] == "restock":
            items = await self.repo.menu()
            for c in changes:
                fields = {"sold_out": False, "sold_out_reason": ""}
                if c.get("add") and items[c["item_id"]].remaining is not None:
                    fields["remaining"] = items[c["item_id"]].remaining + c["add"]
                await self.repo.update_item(c["item_id"], fields)
        elif result["intent"] == "todays_pick":
            for c in changes:
                await self.repo.update_item(c["item_id"], {"owner_weight": 3})
        elif result["intent"] == "knowledge":
            for c in changes:
                kid = f"k_{uuid.uuid4().hex[:8]}"
                await self.repo.col("knowledge").document(kid).set({
                    "id": kid, "category": "store", "items": c["items"], "owner_confirmed": True,
                    "text": {"ja": c["knowledge"], "en": "", "zh-Hans": "", "zh-Hant": "", "ko": ""}, "added_by": actor,
                    "added_at": now_iso()})
        await self.repo.audit(f"store_agent/{actor}", "apply", {"intent": result["intent"], "changes": changes}, "applied")
