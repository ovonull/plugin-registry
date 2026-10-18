"""NapCat 反向 WebSocket 连接和 OneBot 动作响应关联。"""

from dataclasses import dataclass, field
import asyncio
from typing import Any
import uuid

from fastapi import WebSocket

from app.common.responses import BusinessError


@dataclass(slots=True)
class ReverseSession:
    """单个 NapCat 反向 WebSocket 会话。"""

    connection_id: str
    self_id: str
    websocket: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)


class ReverseWebSocketManager:
    """管理 NapCat 连接并通过 echo 关联动作请求和响应。"""

    def __init__(self, action_timeout: int) -> None:
        self.action_timeout = action_timeout
        self._sessions: dict[str, ReverseSession] = {}
        self._lock = asyncio.Lock()

    @property
    def connection_count(self) -> int:
        """返回已连接的 NapCat 数量。"""
        return len(self._sessions)

    @property
    def connected_self_ids(self) -> list[str]:
        """返回当前连接的机器人 QQ 列表。"""
        return sorted({session.self_id for session in self._sessions.values() if session.self_id})

    async def register(self, websocket: WebSocket, self_id: str) -> ReverseSession:
        """注册新的 NapCat 连接。"""
        session = ReverseSession(uuid.uuid4().hex, self_id, websocket)
        async with self._lock:
            self._sessions[session.connection_id] = session
        return session

    async def unregister(self, session: ReverseSession) -> None:
        """注销连接并终止其全部待处理动作。"""
        async with self._lock:
            self._sessions.pop(session.connection_id, None)
        for future in tuple(session.pending.values()):
            if not future.done():
                future.set_exception(BusinessError("NapCat WebSocket 连接已断开", 50302))
        session.pending.clear()

    async def resolve_response(
        self, session: ReverseSession, payload: dict[str, Any]
    ) -> bool:
        """尝试将带 echo 的数据交给对应动作请求。"""
        echo = payload.get("echo")
        if echo is None:
            return False
        future = session.pending.pop(str(echo), None)
        if future is None:
            return False
        if not future.done():
            future.set_result(payload)
        return True

    async def call(
        self,
        action: str,
        params: dict[str, Any],
        self_id: str = "",
        session: ReverseSession | None = None,
    ) -> dict[str, Any]:
        """通过指定或可用 NapCat 连接调用 OneBot 动作。"""
        target = session or await self._select_session(self_id)
        if target is None:
            raise BusinessError("没有可用的 NapCat WebSocket 连接", 50301)
        echo = f"service:{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        target.pending[echo] = future
        try:
            async with target.send_lock:
                await target.websocket.send_json(
                    {"action": action, "params": params, "echo": echo}
                )
            return await asyncio.wait_for(future, timeout=self.action_timeout)
        except TimeoutError as exc:
            raise BusinessError("NapCat 动作调用超时", 50401) from exc
        finally:
            target.pending.pop(echo, None)

    async def _select_session(self, self_id: str) -> ReverseSession | None:
        """按机器人 QQ 选择连接，未指定时使用最新连接。"""
        async with self._lock:
            sessions = tuple(self._sessions.values())
        if self_id:
            sessions = tuple(item for item in sessions if item.self_id == self_id)
        return sessions[-1] if sessions else None

