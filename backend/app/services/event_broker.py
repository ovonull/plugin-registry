"""进程内 OneBot 事件广播总线。"""

from contextlib import asynccontextmanager
import asyncio
from collections.abc import AsyncIterator
from typing import Any


class EventBroker:
    """为每个业务 WebSocket 客户端维护有界事件队列。"""

    def __init__(self, queue_size: int) -> None:
        self.queue_size = queue_size
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    @property
    def subscriber_count(self) -> int:
        """返回当前业务事件订阅数量。"""
        return len(self._subscribers)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """注册事件订阅队列，并在退出时自动注销。"""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self.queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        """向全部订阅者广播事件，慢客户端仅丢弃其最旧事件。"""
        async with self._lock:
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

