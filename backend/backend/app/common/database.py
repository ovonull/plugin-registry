"""
异步数据库连接与启动自检。

提供连接重试、自动建表、缺失字段与索引补齐和统一会话工厂，路由层不直接操作 SQL。
"""

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.common.config import Settings
from app.models.base import Base


logger = logging.getLogger(__name__)


class Database:
    """管理异步引擎、会话和数据库结构初始化。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ensure_sqlite_directory(settings.database_url)
        connect_args = {}
        if settings.database_url.startswith("mysql+aiomysql://"):
            # aiomysql 默认可能长时间等待 TCP 连接，必须显式限制启动等待时间。
            connect_args["connect_timeout"] = settings.database_connect_timeout
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    @staticmethod
    def _ensure_sqlite_directory(database_url: str) -> None:
        """为本地 SQLite 数据库预先创建父目录。"""
        prefix = "sqlite+aiosqlite:///"
        if not database_url.startswith(prefix):
            return
        database_path = database_url.removeprefix(prefix)
        if database_path == ":memory:":
            return
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """连接数据库并幂等创建缺失表、字段和索引，失败时按配置重试。"""
        last_error: Exception | None = None
        for attempt in range(1, self.settings.database_retry_count + 1):
            logger.info(
                "数据库初始化开始，第 %s/%s 次尝试，连接超时=%s 秒，地址=%s",
                attempt,
                self.settings.database_retry_count,
                self.settings.database_connect_timeout,
                self.settings.database_url,
            )
            try:
                await asyncio.wait_for(
                    self._initialize_once(),
                    timeout=self.settings.database_connect_timeout,
                )
                logger.info("数据库结构自检完成")
                return
            except Exception as exc:  # 启动阶段必须覆盖驱动和网络异常
                last_error = exc
                logger.error(
                    "数据库初始化失败，第 %s/%s 次重试：类型=%s，错误=%s，地址=%s",
                    attempt,
                    self.settings.database_retry_count,
                    type(exc).__name__,
                    exc,
                    self.settings.database_url,
                )
                if attempt < self.settings.database_retry_count:
                    await asyncio.sleep(self.settings.database_retry_interval)
        raise RuntimeError("数据库初始化失败，已达到最大重试次数") from last_error

    async def _initialize_once(self) -> None:
        """执行一次数据库连接和结构自检，供初始化超时控制复用。"""
        async with self.engine.begin() as connection:
            await connection.run_sync(self._create_and_repair_schema)

    @staticmethod
    def _create_and_repair_schema(connection) -> None:
        """同步执行建表并补充模型中新增的缺失字段和索引。"""
        Base.metadata.create_all(connection)
        inspector = inspect(connection)
        quote = connection.dialect.identifier_preparer.quote
        for table_name, table in Base.metadata.tables.items():
            existing_columns = {
                item["name"] for item in inspector.get_columns(table_name)
            }
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                type_sql = column.type.compile(dialect=connection.dialect)
                # 新字段先允许 NULL，避免补列影响历史数据；业务写入会提供完整值。
                statement = (
                    f"ALTER TABLE {quote(table_name)} ADD COLUMN "
                    f"{quote(column.name)} {type_sql} NULL"
                )
                connection.exec_driver_sql(statement)
                # 新增的同步禁言开关对历史规则默认开启，避免旧记录因 NULL 意外跳过禁言。
                if table_name == "forbidden_word_rules" and column.name == "mute_enabled":
                    connection.exec_driver_sql(
                        f"UPDATE {quote(table_name)} SET {quote(column.name)} = 1 "
                        f"WHERE {quote(column.name)} IS NULL"
                    )
                logger.info("数据库补充字段：%s.%s", table_name, column.name)
            existing_indexes = {
                str(item.get("name") or "")
                for item in inspector.get_indexes(table_name)
            }
            for index in table.indexes:
                if not index.name or index.name in existing_indexes:
                    continue
                index.create(connection, checkfirst=True)
                logger.info("数据库补充索引：%s.%s", table_name, index.name)

    async def close(self) -> None:
        """释放数据库连接池。"""
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        """生成一次请求范围内的数据库会话。"""
        async with self.session_factory() as session:
            yield session
