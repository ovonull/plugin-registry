"""
违禁消息禁言时长递增服务。

使用 Redis 按机器人、群和用户记录 24 小时内的禁言次数，并将次数映射到
QQ 支持的禁言时长。Redis 状态过期后，下一次命中从首轮时长重新计算。
"""

import logging
from bisect import bisect_left
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError


logger = logging.getLogger("uvicorn.error")

MUTE_WINDOW_SECONDS = 24 * 60 * 60
QQ_MUTE_DURATIONS = (5 * 60, 10 * 60, 60 * 60, 12 * 60 * 60, 24 * 60 * 60)


class ForbiddenMuteService:
    """管理同一群成员的 24 小时禁言递增状态。"""

    _INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

    def __init__(self, redis: Redis | None) -> None:
        self.redis = redis

    async def next_duration(
        self,
        event: dict[str, Any],
        bot_qq: str,
        fallback_duration: int = QQ_MUTE_DURATIONS[0],
    ) -> int:
        """
        记录一次违禁命中并返回本次禁言时长。

        Args:
            event: OneBot 群消息事件。
            bot_qq: 执行动作的机器人 QQ。
            fallback_duration: Redis 不可用时使用的首轮时长。
        Returns:
            当前 24 小时窗口内对应的禁言秒数。
        """
        first_duration = self._normalise_fallback_duration(fallback_duration)
        key = self._key(event, bot_qq)
        if not key or self.redis is None:
            return first_duration
        try:
            count = int(
                await self.redis.eval(
                    self._INCREMENT_SCRIPT,
                    1,
                    key,
                    MUTE_WINDOW_SECONDS,
                )
            )
        except RedisError as exc:
            logger.error(
                "违禁词禁言次数写入 Redis 失败，按首轮时长处理，key=%s，错误=%s",
                key,
                exc,
            )
            return first_duration
        except (TypeError, ValueError) as exc:
            logger.error(
                "违禁词禁言次数返回格式不正确，按首轮时长处理，key=%s，错误=%s",
                key,
                exc,
            )
            return first_duration
        except Exception as exc:
            logger.exception(
                "违禁词禁言次数读取异常，按首轮时长处理，key=%s，错误=%s",
                key,
                exc,
            )
            return first_duration
        return self._duration_for_count(count, first_duration)

    @classmethod
    def _duration_for_count(cls, count: int, first_duration: int) -> int:
        """按当前次数选择递增档位，超过最后一档时保持 1 天。"""
        durations = sorted(set(QQ_MUTE_DURATIONS + (first_duration,)))
        start_index = bisect_left(durations, first_duration)
        index = min(start_index + max(count, 1) - 1, len(durations) - 1)
        return durations[index]

    @staticmethod
    def _normalise_fallback_duration(duration: int) -> int:
        """校验首轮时长，非法值统一回退为 5 分钟。"""
        try:
            value = int(duration)
        except (TypeError, ValueError):
            return QQ_MUTE_DURATIONS[0]
        return value if value > 0 else QQ_MUTE_DURATIONS[0]

    @classmethod
    def _key(cls, event: dict[str, Any], bot_qq: str) -> str:
        """按机器人、群和用户生成隔离键，仅对群消息启用。"""
        if event.get("message_type") != "group":
            return ""
        group_id = event.get("group_id")
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        user_id = str(event.get("user_id") or sender.get("user_id") or "")
        bot_text = str(bot_qq or "")
        if group_id is None or not user_id or not bot_text:
            return ""
        return f"napcat:forbidden_mute:{bot_text}:{group_id}:{user_id}"
