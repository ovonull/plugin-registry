"""
异步数据库连接与启动自检。

提供连接重试、自动建表、缺失字段与索引补齐和统一会话工厂，路由层不直接操作 SQL。
默认使用 SQLite（单文件、无需额外服务），连接建立时统一下发一组 PRAGMA
提升并发读与写入稳定性；配置成 MySQL 时按 MySQL 的连接参数处理。
"""

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.common.config import Settings
from app.models.base import Base


logger = logging.getLogger(__name__)

SQLITE_PREFIXES = ("sqlite+aiosqlite://", "sqlite://")

# SQLite 连接级 PRAGMA：
# - WAL 允许读写并行，读不再被写锁阻塞；
# - NORMAL 只在高并发下牺牲极小安全性，换来明显更少的 fsync；
# - busy_timeout 让写冲突等待而不是立即抛 database is locked；
# - 临时表与缓存走内存，减少磁盘抖动。
SQLITE_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("busy_timeout", "15000"),
    ("temp_store", "MEMORY"),
    ("cache_size", "-16000"),
    ("mmap_size", "134217728"),
    ("wal_autocheckpoint", "1000"),
)


def is_sqlite_url(database_url: str) -> bool:
    """判断连接串是否指向 SQLite。"""
    return database_url.startswith(SQLITE_PREFIXES)


class Database:
    """管理异步引擎、会话和数据库结构初始化。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.is_sqlite = is_sqlite_url(settings.database_url)
        self._ensure_sqlite_directory(settings.database_url)

        connect_args: dict[str, object] = {}
        engine_kwargs: dict[str, object] = {}
        if self.is_sqlite:
            # aiosqlite 的 timeout 就是 SQLite 的 busy timeout，建连时先给一个兜底值。
            connect_args["timeout"] = 15
            if ":memory:" in settings.database_url:
                # 内存库每个连接都是独立数据库，必须复用同一个连接。
                engine_kwargs["poolclass"] = StaticPool
            else:
                engine_kwargs.update(
                    pool_size=5,
                    max_overflow=5,
                    pool_timeout=30,
                    pool_recycle=3600,
                )
            # 本地文件连接不会像 TCP 那样悄悄断开，省掉每次取连接的 ping。
            engine_kwargs["pool_pre_ping"] = False
        else:
            engine_kwargs["pool_pre_ping"] = True
            if settings.database_url.startswith("mysql+aiomysql://"):
                # aiomysql 默认可能长时间等待 TCP 连接，必须显式限制启动等待时间。
                connect_args["connect_timeout"] = settings.database_connect_timeout

        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            connect_args=connect_args,
            **engine_kwargs,
        )
        if self.is_sqlite:
            event.listen(self.engine.sync_engine, "connect", self._apply_sqlite_pragmas)
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    @staticmethod
    def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        """每条新连接都下发一次 PRAGMA，保证连接池里的连接行为一致。"""
        cursor = dbapi_connection.cursor()
        try:
            for pragma, value in SQLITE_PRAGMAS:
                try:
                    cursor.execute(f"PRAGMA {pragma}={value}")
                except Exception as exc:  # noqa: BLE001 - 个别 PRAGMA 在不支持的存储上会失败
                    logger.warning(
                        "SQLite PRAGMA 设置失败，已跳过：%s=%s，原因=%s",
                        pragma,
                        value,
                        exc,
                    )
        finally:
            cursor.close()

    @staticmethod
    def _ensure_sqlite_directory(database_url: str) -> None:
        """为本地 SQLite 数据库预先创建父目录。"""
        if not is_sqlite_url(database_url):
            return
        prefix = "sqlite+aiosqlite:///" if database_url.startswith("sqlite+aiosqlite://") else "sqlite:///"
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
                if self.is_sqlite:
                    # SQLite 没有网络握手，慢盘上首次建表可能超过连接超时；
                    # 用 wait_for 强杀会留下半成品结构，因此这里只靠重试兜底。
                    await self._initialize_once()
                else:
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
