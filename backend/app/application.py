"""FastAPI 应用装配、生命周期和全局异常处理。"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from sqlalchemy import text

from app.common.config import Settings, get_settings
from app.common.database import Database
from app.common.log_config import configure_logging
from app.common.responses import BusinessError, failure_response, success
from app.routes import (
    auth,
    animated_emojis,
    ai_trigger_keywords,
    auto_reply,
    ai_proactive_replies,
    bots,
    chat_records,
    dashboard,
    email_logs,
    forbidden_words,
    friends,
    ai_voice_characters,
    dictionaries,
    features,
    geetest,
    login_logs,
    onebot,
    plugin_admin,
    plugins,
    registry,
    scheduled_tasks,
    settings as settings_routes,
    users,
    welcome_messages,
)
from app.services.auto_reply_service import AutoReplyService
from app.services.animated_emoji_category_service import AnimatedEmojiCategoryService
from app.services.animated_emoji_service import AnimatedEmojiService
from app.services.ai_proactive_reply_service import AiProactiveReplyService
from app.services.ai_followup_service import AiFollowupService
from app.services.ai_message_reply_guard import AiMessageReplyGuard
from app.services.ai_trigger_keyword_service import AiTriggerKeywordService
from app.services.bot_ai_service import BotAiService
from app.services.bot_service import BotService
from app.services.chat_record_service import ChatRecordService
from app.services.geetest_service import GeetestService
from app.services.email_log_service import EmailLogService
from app.services.login_log_service import LoginLogService
from app.services.forbidden_word_service import ForbiddenWordService
from app.services.friend_service import FriendService
from app.services.ai_voice_character_rule_service import AiVoiceCharacterRuleService
from app.services.feature_service import FeatureService
from app.services.onebot_service import OneBotService
from app.services.plugin_hooks import PluginHooks
from app.services.plugin_install_service import PluginInstallService
from app.services.plugin_review_service import PluginReviewService
from app.services.plugin_repo_sync_service import PluginRepoSyncService
from app.services.plugin_runtime import build_plugin_runtime
from app.services.plugin_service import PluginService
from app.services.plugin_trust_service import PluginTrustService
from app.services.setting_service import SettingService
from app.services.user_service import UserService
from app.services.welcome_message_service import WelcomeMessageService
from app.services.scheduled_task_service import ScheduledTaskService


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ServiceContainer:
    """集中保存应用级配置、数据库和业务服务实例。"""

    settings: Settings
    database: Database
    geetest_service: GeetestService
    user_service: UserService
    auto_reply_service: AutoReplyService
    bot_service: BotService
    bot_ai_service: BotAiService
    ai_followup_service: AiFollowupService
    welcome_message_service: WelcomeMessageService
    forbidden_word_service: ForbiddenWordService
    ai_trigger_keyword_service: AiTriggerKeywordService
    ai_voice_character_rule_service: AiVoiceCharacterRuleService
    feature_service: FeatureService
    onebot_service: OneBotService
    ai_proactive_reply_service: AiProactiveReplyService
    chat_record_service: ChatRecordService
    friend_service: FriendService
    scheduled_task_service: ScheduledTaskService
    animated_emoji_service: AnimatedEmojiService
    animated_emoji_category_service: AnimatedEmojiCategoryService
    login_log_service: LoginLogService
    email_log_service: EmailLogService
    plugin_service: PluginService
    plugin_install_service: PluginInstallService
    plugin_review_service: PluginReviewService
    plugin_trust_service: PluginTrustService
    plugin_hooks: PluginHooks
    plugin_repo_sync_service: PluginRepoSyncService


def _validation_message(error: RequestValidationError) -> str:
    """将 Pydantic 校验错误转换为简洁中文提示。"""
    first = error.errors()[0] if error.errors() else {}
    location = first.get("loc", ())
    field_name = str(location[-1]) if location else "参数"
    error_type = str(first.get("type", ""))
    custom_message = str(first.get("ctx", {}).get("error", ""))
    if custom_message:
        return f"参数 {field_name} 不正确：{custom_message}"
    if error_type == "missing":
        return f"缺少必填参数：{field_name}"
    return f"参数 {field_name} 格式或取值不正确"


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """
    可选地把前端构建产物挂到后端，供「Linux 裸机部署」单端口运行。

    Docker 部署时前端由独立 Nginx 容器处理，``FRONTEND_DIST`` 为空直接跳过；
    裸机部署由 start.sh 先构建前端，再由这里托管，省去再装一个 Nginx。
    """
    raw_dir = str(settings.frontend_dist or "").strip()
    if not raw_dir:
        return
    dist_dir = Path(raw_dir).expanduser().resolve()
    if not (dist_dir / "index.html").is_file():
        logger.warning(
            "已配置 FRONTEND_DIST 但目录里没有 index.html，跳过静态托管：%s（请先执行 npm run build）",
            dist_dir,
        )
        return

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="frontend-assets",
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend_spa(full_path: str) -> FileResponse:
        """前端单页回退：命中静态文件就返回文件，否则交给 index.html。"""
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        target = (dist_dir / full_path).resolve()
        # 只允许返回 dist 目录内的文件，避免构造路径读到项目其他文件。
        if target.is_file() and str(target).startswith(str(dist_dir)):
            return FileResponse(target)
        return FileResponse(dist_dir / "index.html")

    logger.info("前端静态资源已由后端托管：%s", dist_dir)


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建可用于生产运行和自动化测试的 FastAPI 应用。"""
    app_settings = settings or get_settings()
    log_path = configure_logging(app_settings)
    logger.info("详细日志文件已启用，path=%s，level=%s", log_path, app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log_path = configure_logging(app_settings)
        logger.info("应用生命周期日志已启用，path=%s", log_path)
        database = Database(app_settings)
        geetest_service = GeetestService(app_settings)
        login_log_service = LoginLogService()
        email_log_service = EmailLogService()
        user_service = UserService(
            app_settings, geetest_service, login_log_service, email_log_service
        )
        auto_reply_service = AutoReplyService()
        bot_service = BotService()
        bot_ai_service = BotAiService(bot_service, database.session_factory)
        redis_client = (
            Redis.from_url(
                app_settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            if app_settings.redis_url
            else None
        )
        ai_followup_service = AiFollowupService(
            redis_client,
            ttl_seconds=app_settings.ai_followup_ttl,
        )
        ai_message_reply_guard = AiMessageReplyGuard(redis_client)
        welcome_message_service = WelcomeMessageService()
        forbidden_word_service = ForbiddenWordService()
        ai_trigger_keyword_service = AiTriggerKeywordService()
        animated_emoji_service = AnimatedEmojiService(
            database,
        )
        animated_emoji_category_service = AnimatedEmojiCategoryService()
        ai_voice_character_rule_service = AiVoiceCharacterRuleService(database.session_factory)
        feature_service = FeatureService()
        plugin_hooks = PluginHooks(app_settings, database)
        onebot_service = OneBotService(            app_settings,
            database,
            auto_reply_service,
            bot_service,
            bot_ai_service,
            ai_followup_service,
            welcome_message_service,
            forbidden_word_service,
            redis_client,
            ai_voice_character_rule_service,
            ai_trigger_keyword_service,
            animated_emoji_service,
            ai_message_reply_guard,
            plugin_hooks,
        )
        # 插件执行单元需要代调 OneBot 动作，因此必须在 OneBot 服务就绪后再接入。
        plugin_hooks.attach_runtime(
            build_plugin_runtime(app_settings, database, onebot_service.call_action)
        )
        chat_record_service = ChatRecordService()
        friend_service = FriendService(bot_service, onebot_service, database)
        ai_proactive_reply_service = AiProactiveReplyService(
            database,
            bot_service,
            bot_ai_service,
            onebot_service,
            redis_client,
            ai_message_reply_guard,
        )
        scheduled_task_service = ScheduledTaskService(
            database,
            onebot_service,
            animated_emoji_service,
        )
        plugin_service = PluginService(app_settings)
        plugin_install_service = PluginInstallService(app_settings)
        plugin_review_service = PluginReviewService(app_settings)
        plugin_trust_service = PluginTrustService(app_settings)
        # 仓库来源插件的自动同步：定时探测作者仓库的新 tag 并登记版本。
        plugin_repo_sync_service = PluginRepoSyncService(
            app_settings, database, plugin_service
        )
        app.state.container = ServiceContainer(
            app_settings,
            database,
            geetest_service,
            user_service,
            auto_reply_service,
            bot_service,
            bot_ai_service,
            ai_followup_service,
            welcome_message_service,
            forbidden_word_service,
            ai_trigger_keyword_service,
            ai_voice_character_rule_service,
            feature_service,
            onebot_service,
            ai_proactive_reply_service,
            chat_record_service,
            friend_service,
            scheduled_task_service,
            animated_emoji_service,
            animated_emoji_category_service,
            login_log_service,
            email_log_service,
            plugin_service,
            plugin_install_service,
            plugin_review_service,
            plugin_trust_service,
            plugin_hooks,
            plugin_repo_sync_service,
        )
        await database.initialize()
        async with database.session_factory() as session:
            await user_service.initialize_defaults(session)
            setting_service = SettingService(session)
            await setting_service.initialize_defaults()
            # Settings 是冻结 dataclass，用 object.__setattr__ 注入数据库中的令牌；
            # .env 已配置时以 .env 优先，避免后台误覆盖环境配置。
            if not app_settings.plugin_repo_token:
                github_settings = await setting_service.get_group(
                    "github", mask_sensitive=False
                )
                object.__setattr__(
                    app_settings,
                    "plugin_repo_token",
                    str(github_settings.get("plugin_repo_token") or "").strip(),
                )
            # 插件库接入令牌：.env 显式配置时优先，否则读取后台保存的值。
            if not app_settings.registry_token:
                registry_settings = await setting_service.get_group(
                    "registry", mask_sensitive=False
                )
                object.__setattr__(
                    app_settings,
                    "registry_token",
                    str(registry_settings.get("token") or "").strip(),
                )
            # 仓库插件自动同步的配置只以数据库为准（后台系统设置里维护），
            # 因此每次启动都把库里保存的值注入运行时，不受 .env 影响。
            sync_settings = await setting_service.get_group("plugin_sync")
            object.__setattr__(
                app_settings,
                "plugin_repo_sync_enabled",
                bool(sync_settings.get("enabled", True)),
            )
            object.__setattr__(
                app_settings,
                "plugin_repo_sync_interval",
                int(sync_settings.get("interval_seconds") or 21600),
            )
            object.__setattr__(
                app_settings,
                "plugin_repo_sync_batch",
                int(sync_settings.get("batch") or 20),
            )
            await feature_service.initialize_defaults(session)
            await scheduled_task_service.initialize_defaults(session)
            await plugin_service.initialize_defaults(session)
        await ai_proactive_reply_service.start()
        await scheduled_task_service.start()
        await plugin_repo_sync_service.start()
        try:
            yield
        finally:
            await plugin_repo_sync_service.close()
            await ai_proactive_reply_service.close()
            await scheduled_task_service.close()
            await plugin_hooks.close()
            await onebot_service.close()
            await bot_ai_service.close()
            await ai_followup_service.close()
            await geetest_service.close()
            await database.close()

    app = FastAPI(
        title="PlugHub 插件库",
        version="0.1.0",
        lifespan=lifespan,
    )
    wildcard_cors = "*" in app_settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.cors_origins),
        allow_credentials=not wildcard_cors,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(geetest.router)
    app.include_router(settings_routes.router)
    app.include_router(dictionaries.router)
    app.include_router(auto_reply.router)
    app.include_router(ai_proactive_replies.router)
    app.include_router(chat_records.router)
    app.include_router(dashboard.router)
    app.include_router(bots.router)
    app.include_router(welcome_messages.router)
    app.include_router(forbidden_words.router)
    app.include_router(friends.router)
    app.include_router(ai_trigger_keywords.router)
    app.include_router(ai_voice_characters.router)
    app.include_router(features.router)
    app.include_router(onebot.router)
    app.include_router(scheduled_tasks.router)
    app.include_router(animated_emojis.router)
    app.include_router(users.router)
    app.include_router(login_logs.router)
    app.include_router(email_logs.router)
    app.include_router(plugins.router)
    app.include_router(plugin_admin.router)
    app.include_router(registry.router)

    @app.get("/health", tags=["系统"])
    async def health(request: Request) -> dict:
        """检查数据库和 OneBot 连接状态。"""
        async with request.app.state.container.database.session_factory() as session:
            await session.execute(text("SELECT 1"))
        service = request.app.state.container.onebot_service
        return success(
            {
                "status": "ok",
                "database": "connected",
                "websocket_connections": service.reverse_manager.connection_count,
                "connected_bots": service.reverse_manager.connected_self_ids,
            },
            "服务正常",
        )

    @app.exception_handler(BusinessError)
    async def handle_business_error(
        _request: Request, error: BusinessError
    ):
        """业务异常统一以 HTTP 200 返回。"""
        return failure_response(error.message, error.code)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, error: RequestValidationError
    ):
        """请求参数异常统一转换为中文业务响应。"""
        return failure_response(_validation_message(error), 40002)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception):
        """记录未知错误上下文，不向前端暴露堆栈。"""
        logger.exception("接口处理异常，path=%s，错误=%s", request.url.path, error)
        return failure_response("服务处理失败，请稍后重试", 50000)

    # 裸机部署：配置了 FRONTEND_DIST 时由后端顺带托管前端构建产物，
    # 一个进程一个端口就能跑；Docker 部署走 Nginx，留空即可。
    _mount_frontend(app, app_settings)

    return app
