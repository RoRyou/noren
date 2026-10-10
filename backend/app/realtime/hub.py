"""プロセス内の配信ハブ。宛先（"table:<session_id>" / "staff"）ごとに購読キューを持つ。

Cloud Run は最大インスタンス 1 で動かす前提（デモ用）。複数インスタンスにする場合は、
ここを Firestore のリスナーか Pub/Sub に置き換える（known_issues 参照）。
届かなかった通知は Firestore に残っているので、再接続時に取り直せる。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict


class Hub:
    def __init__(self) -> None:
        self._subs: dict[tuple[str, str], set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, store_id: str, target: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subs[(store_id, target)].add(q)
        return q

    def unsubscribe(self, store_id: str, target: str, q: asyncio.Queue) -> None:
        self._subs[(store_id, target)].discard(q)

    def publish(self, store_id: str, target: str, message: dict) -> int:
        delivered = 0
        for q in list(self._subs.get((store_id, target), ())):
            try:
                q.put_nowait(message)
                delivered += 1
            except asyncio.QueueFull:
                pass  # 取りこぼしは再接続時の取り直しで補う
        return delivered


hub = Hub()
