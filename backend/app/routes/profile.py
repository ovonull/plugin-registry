"""个人中心接口：目前只包含「接入令牌」的自助管理。

为什么要单开一组接口：插件库的索引与插件包下载需要鉴权，
系统设置里的那几枚令牌是管理员发放、全站下游共用的公共令牌；
多用户注册之后，每个账号也应该能拿自己的令牌去调接口，
于是把「个人令牌」的增删查放在这里。

与系统设置版的区别：

- 只认登录会话，不要求管理员身份；
- 严格按 ``owner_id`` 隔离，只能看到、吊销自己创建的令牌；
- 顺带返回下游该用的服务地址，前端把它显示成「API 调用地址」，
  避免用户自己拼错路径。
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session
from app.models.entities import User
from app.schemas import RegistryTokenCreateRequest
from app.services.registry_token_service import RegistryTokenService, token_item


router = APIRouter(prefix="/api/profile", tags=["个人中心"])


@router.get("/registry-tokens")
async def list_my_registry_tokens(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """列出当前账号自己的接入令牌，并返回可用的服务基址。"""
    user: User = await get_current_user(request, session)
    rows = await RegistryTokenService().list_tokens(session, user.id)
    return success(
        {
            "items": [token_item(row) for row in rows],
            # 只回显显式配置的对外地址。留空让前端用「当前站点域名」拼，
            # 那个地址一定能打开本页面，比后端按容器端口猜出来的更可靠；
            # 部署在域名/反代后面时在 .env 里设 PUBLIC_SERVICE_URL 即可覆盖。
            "endpoint": str(
                getattr(request.app.state.container.settings, "public_service_url", "")
                or ""
            ).rstrip("/"),
        }
    )


@router.post("/registry-tokens")
async def create_my_registry_token(
    payload: RegistryTokenCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """创建一枚归属当前账号的令牌，明文只在这条响应里返回一次。"""
    user: User = await get_current_user(request, session)
    row, plain = await RegistryTokenService().create(
        session, payload.name, user.id
    )
    return success(
        {"token": plain, "item": token_item(row)},
        "令牌已创建，请立即复制",
    )


@router.delete("/registry-tokens/{token_id}")
async def revoke_my_registry_token(
    token_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """吊销自己的令牌；别人的令牌一律按「不存在」处理。"""
    user: User = await get_current_user(request, session)
    await RegistryTokenService().revoke(session, token_id, user.id)
    return success({"id": token_id}, "令牌已吊销")
