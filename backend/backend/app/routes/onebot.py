"""NapCat HTTP/反向 WebSocket 接入及业务动作代理路由。"""

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import BusinessError, onebot_error
from app.common.security import secure_equals
from app.dependencies import get_session, verify_service_token
from app.services.onebot_catalog import GROUP_SCOPED_ACTIONS


router = APIRouter(tags=["OneBot"])
logger = logging.getLogger("uvicorn.error")


def _format_log_data(payload: Any) -> str:
    """完整格式化日志数据，不限制字符串或集合长度。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _log_http_event_request(request: Request, raw_body: bytes) -> None:
    """完整打印 OneBot HTTP 事件请求的协议、地址、头和原始正文。"""
    try:
        body: Any = raw_body.decode("utf-8")
        body_encoding = "utf-8"
    except UnicodeDecodeError:
        body = raw_body.hex()
        body_encoding = "hex"
    headers = [
        {
            "name": name.decode("latin-1"),
            "value": value.decode("latin-1"),
        }
        for name, value in request.headers.raw
    ]
    client = request.client
    request_data = {
        "method": request.method,
        "url": str(request.url),
        "scheme": request.url.scheme,
        "path": request.url.path,
        "query_string": request.scope.get("query_string", b"").decode("latin-1"),
        "http_version": request.scope.get("http_version", ""),
        "client": (
            {"host": client.host, "port": client.port} if client else None
        ),
        "headers": headers,
        "body_bytes_length": len(raw_body),
        "body_encoding": body_encoding,
        "body": body,
    }
    logger.info(
        "[OneBot HTTP] 收到完整请求：\n%s",
        _format_log_data(request_data),
    )


def _log_http_event_response(response: dict[str, Any]) -> dict[str, Any]:
    """完整打印 OneBot HTTP 事件接口返回值。"""
    logger.info(
        "[OneBot HTTP] 完整返回：\n%s",
        _format_log_data({"http_status": 200, "body": response}),
    )
    return response


def _verify_http_signature(raw_body: bytes, token: str, signature: str) -> bool:
    """校验 NapCat HTTP Client 使用的 HMAC-SHA1 签名。"""
    if not token:
        return True
    expected = "sha1=" + hmac.new(
        token.encode("utf-8"), raw_body, hashlib.sha1
    ).hexdigest()
    return bool(signature) and secure_equals(signature, expected)


def _websocket_token(websocket: WebSocket) -> str:
    """从 WebSocket 查询参数或 Authorization 头提取令牌。"""
    query_token = websocket.query_params.get("access_token", "")
    if query_token:
        return query_token
    authorization = websocket.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


@router.post("/onebot/v11/events")
@router.post("/onebot/events")
async def receive_http_event(request: Request) -> dict[str, Any]:
    """接收 NapCat HTTP POST 事件并返回可能的快速回复。"""
    raw_body = await request.body()
    _log_http_event_request(request, raw_body)
    container = request.app.state.container
    if not _verify_http_signature(
        raw_body,
        container.settings.napcat_token,
        request.headers.get("x-signature", ""),
    ):
        return _log_http_event_response(
            onebot_error("NapCat HTTP 签名验证失败", 1403)
        )
    try:
        event = json.loads(raw_body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _log_http_event_response(
            onebot_error("事件 JSON 格式不正确", 1400)
        )
    if not isinstance(event, dict):
        return _log_http_event_response(
            onebot_error("事件数据必须是 JSON 对象", 1400)
        )
    logger.info("[OneBot HTTP] 解析后的完整事件：\n%s", _format_log_data(event))
    response = await container.onebot_service.handle_http_event(
        event, request.headers.get("x-self-id", "")
    )
    return _log_http_event_response(response)


@router.websocket("/onebot/v11/ws")
@router.websocket("/onebot/ws")
async def napcat_reverse_websocket(websocket: WebSocket) -> None:
    """接受 NapCat WebSocket Client 反向连接。"""
    container = websocket.app.state.container
    expected = container.settings.napcat_token
    supplied = _websocket_token(websocket)
    if expected and (not supplied or not secure_equals(supplied, expected)):
        await websocket.close(code=1008, reason="NapCat 令牌无效")
        return
    await websocket.accept()
    session = await container.onebot_service.reverse_manager.register(
        websocket, websocket.headers.get("x-self-id", "")
    )
    event_tasks: set[asyncio.Task] = set()

    def on_event_done(task: asyncio.Task) -> None:
        """回收事件任务并记录未处理异常。"""
        event_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "[OneBot WebSocket] 事件处理异常：%s",
                error,
                exc_info=(type(error), error, error.__traceback__),
            )

    try:
        while True:
            payload = await websocket.receive_json()
            if not isinstance(payload, dict):
                await websocket.send_json(onebot_error("数据必须是 JSON 对象", 1400))
                continue
            if await container.onebot_service.reverse_manager.resolve_response(
                session, payload
            ):
                continue
            task = asyncio.create_task(
                container.onebot_service.handle_websocket_event(payload, session)
            )
            event_tasks.add(task)
            task.add_done_callback(on_event_done)
    except WebSocketDisconnect:
        pass
    finally:
        await container.onebot_service.reverse_manager.unregister(session)
        for task in tuple(event_tasks):
            task.cancel()
        if event_tasks:
            await asyncio.gather(*event_tasks, return_exceptions=True)


@router.api_route(
    "/onebot/api/{action}", methods=["GET", "POST"], name="onebot_action_proxy"
)
async def proxy_action(
    action: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """通过 HTTP 调用任意 NapCat 支持的 OneBot 动作。"""
    try:
        verify_service_token(request)
    except BusinessError as exc:
        return onebot_error(exc.message, exc.code)
    if request.method == "GET":
        payload: dict[str, Any] = dict(request.query_params)
    else:
        try:
            parsed = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return onebot_error("请求 JSON 格式不正确", 1400)
        if not isinstance(parsed, dict):
            return onebot_error("动作参数必须是 JSON 对象", 1400)
        payload = dict(parsed)
        payload.update(dict(request.query_params))
    payload.pop("access_token", None)
    echo = payload.pop("echo", "")
    self_id = request.headers.get("x-self-id", "")
    group_id = payload.get("group_id")
    try:
        if action in GROUP_SCOPED_ACTIONS:
            if not self_id:
                raise BusinessError("群动作必须指定机器人 X-Self-ID", 40035)
            if group_id is None:
                raise BusinessError("群动作必须指定 group_id", 40036)
        if self_id and group_id is not None:
            await request.app.state.container.bot_service.ensure_group_allowed(
                session, self_id, str(group_id)
            )
        return await request.app.state.container.onebot_service.call_action(
            action, payload, echo, self_id
        )
    except BusinessError as exc:
        return onebot_error(exc.message, exc.code, echo)


@router.websocket("/ws")
async def business_websocket(websocket: WebSocket) -> None:
    """向业务客户端提供 OneBot 事件订阅和动作双工接口。"""
    container = websocket.app.state.container
    expected = container.settings.service_token
    supplied = _websocket_token(websocket)
    if expected and (not supplied or not secure_equals(supplied, expected)):
        await websocket.close(code=1008, reason="服务令牌无效")
        return
    await websocket.accept()
    send_lock = asyncio.Lock()
    action_tasks: set[asyncio.Task] = set()

    async def send_json(payload: dict[str, Any]) -> None:
        """串行发送事件和动作响应。"""
        async with send_lock:
            await websocket.send_json(payload)

    async def forward_events(queue: asyncio.Queue) -> None:
        """持续向当前业务客户端推送 OneBot 事件。"""
        while True:
            await send_json(await queue.get())

    async def handle_action(payload: dict[str, Any]) -> None:
        """处理业务 WebSocket 发来的单个动作。"""
        action = payload.get("action")
        echo = payload.get("echo", "")
        params = payload.get("params") or {}
        if not isinstance(action, str) or not isinstance(params, dict):
            await send_json(onebot_error("动作请求格式不正确", 1400, echo))
            return
        self_id = str(payload.get("self_id", ""))
        group_id = params.get("group_id")
        try:
            if action in GROUP_SCOPED_ACTIONS and not group_id:
                raise BusinessError("群动作必须指定 group_id", 40036)
            if group_id is not None:
                if not self_id:
                    raise BusinessError("群动作必须指定机器人 self_id", 40035)
                async with container.database.session_factory() as session:
                    await container.bot_service.ensure_group_allowed(
                        session, self_id, str(group_id)
                    )
            result = await container.onebot_service.call_action(
                action, params, echo, self_id
            )
        except BusinessError as exc:
            result = onebot_error(exc.message, exc.code, echo)
        await send_json(result)

    async with container.onebot_service.broker.subscribe() as queue:
        event_task = asyncio.create_task(forward_events(queue))
        try:
            while True:
                payload = await websocket.receive_json()
                if not isinstance(payload, dict):
                    await send_json(onebot_error("数据必须是 JSON 对象", 1400))
                    continue
                task = asyncio.create_task(handle_action(payload))
                action_tasks.add(task)
                task.add_done_callback(action_tasks.discard)
        except WebSocketDisconnect:
            pass
        finally:
            event_task.cancel()
            for task in tuple(action_tasks):
                task.cancel()
            await asyncio.gather(event_task, *action_tasks, return_exceptions=True)
