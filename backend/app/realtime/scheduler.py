"""時間指定の処理（30 秒後の再通知、2 分後の全員通知など）。

本番は Cloud Tasks（実行時刻の指定と失敗時の再試行がある）で /api/tasks/{name} を呼ぶ。
ローカルとテストでは、プロセス内の asyncio タスクで代用する。
タスク名で重複を防ぐ（同じ名前は 1 回だけ）。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

Handler = Callable[[dict], Awaitable[None]]


class LocalScheduler:
    def __init__(self) -> None:
        self.handlers: dict[str, Handler] = {}
        self._names: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self.time_scale = 1.0  # テストでは小さくして待ち時間を縮める

    def register(self, name: str, handler: Handler) -> None:
        self.handlers[name] = handler

    def schedule(self, delay_s: float, name: str, payload: dict, task_name: str) -> bool:
        if task_name in self._names:
            return False
        self._names.add(task_name)

        async def run() -> None:
            await asyncio.sleep(delay_s * self.time_scale)
            await self.handlers[name](payload)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


scheduler = LocalScheduler()
