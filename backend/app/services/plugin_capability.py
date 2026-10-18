"""
插件能力白名单与风险等级定义。

功能：
1. 集中定义插件可以申请的全部能力，是审核面板与运行期放行的唯一依据；
2. 为每项能力标注风险等级，高风险能力必须由管理员在审核时显式勾选才会生效；
3. 维护能力与 OneBot 动作的映射，供运行期能力闸门按动作反查所需能力。

约定：
- 能力码即数据字典 plugin_capability 的 item_code，中文名称沿用本文件，禁止前端硬编码；
- 不提供任何「万能能力」，避免插件全部声明同一项导致审核失去意义；
- 不提供数据库读写能力，插件自己的状态只能走 plugin.storage 或 cache.kv。
"""

from typing import Any


RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"


CAPABILITY_SPECS: tuple[dict[str, Any], ...] = (
    {
        "code": "plugin.storage",
        "name": "插件数据目录",
        "risk_level": RISK_LOW,
        "sort_order": 10,
        "description": "仅允许读写插件自己的 data/plugins/{id} 与 temp/plugins/{id} 目录。",
        "actions": (),
    },
    {
        "code": "cache.kv",
        "name": "键值缓存",
        "risk_level": RISK_LOW,
        "sort_order": 20,
        "description": "使用带插件命名空间前缀的键值缓存，不得访问其他插件的键。",
        "actions": (),
    },
    {
        "code": "bot.info",
        "name": "机器人自身信息",
        "risk_level": RISK_LOW,
        "sort_order": 30,
        "description": "读取机器人自己的登录信息、运行状态与版本信息。",
        "actions": ("get_login_info", "get_status", "get_version_info"),
    },
    {
        "code": "group.info",
        "name": "读取群信息",
        "risk_level": RISK_LOW,
        "sort_order": 40,
        "description": "读取群资料、群列表和群成员列表。",
        "actions": (
            "get_group_info",
            "get_group_list",
            "get_group_member_info",
            "get_group_member_list",
        ),
    },
    {
        "code": "friend.info",
        "name": "读取好友信息",
        "risk_level": RISK_MEDIUM,
        "sort_order": 50,
        "description": "读取好友列表与陌生人资料，涉及他人隐私。",
        "actions": ("get_friend_list", "get_stranger_info"),
    },
    {
        "code": "msg.send",
        "name": "发送消息",
        "risk_level": RISK_MEDIUM,
        "sort_order": 60,
        "description": "主动发送私聊消息、群消息与合并转发消息。",
        "actions": (
            "send_private_msg",
            "send_group_msg",
            "send_msg",
            "send_group_forward_msg",
            "send_private_forward_msg",
            "forward_group_single_msg",
        ),
    },
    {
        "code": "msg.recall",
        "name": "撤回消息",
        "risk_level": RISK_MEDIUM,
        "sort_order": 70,
        "description": "撤回群聊或私聊中的指定消息。",
        "actions": ("delete_msg",),
    },
    {
        "code": "msg.history",
        "name": "读取历史消息",
        "risk_level": RISK_MEDIUM,
        "sort_order": 80,
        "description": "按消息 ID 或会话读取历史消息内容，涉及聊天隐私。",
        "actions": ("get_msg", "get_group_msg_history", "get_friend_msg_history"),
    },
    {
        "code": "msg.media",
        "name": "消息媒体处理",
        "risk_level": RISK_MEDIUM,
        "sort_order": 90,
        "description": "获取与下载消息中的图片、语音和文件。",
        "actions": ("get_image", "get_record", "get_file", "download_file"),
    },
    {
        "code": "llm.generate",
        "name": "调用机器人 AI",
        "risk_level": RISK_MEDIUM,
        "sort_order": 100,
        "description": "使用机器人当前 AI 配置生成文本，会消耗用户的模型额度。",
        "actions": (),
    },
    {
        "code": "scheduler.register",
        "name": "注册定时任务",
        "risk_level": RISK_MEDIUM,
        "sort_order": 110,
        "description": "在系统定时任务中注册由插件负责执行的周期性任务。",
        "actions": (),
    },
    {
        "code": "group.mute",
        "name": "群成员禁言",
        "risk_level": RISK_HIGH,
        "sort_order": 120,
        "description": "禁言指定群成员或开启全员禁言，会直接影响真实群成员。",
        "actions": ("set_group_ban", "set_group_whole_ban"),
    },
    {
        "code": "group.kick",
        "name": "踢出群成员",
        "risk_level": RISK_HIGH,
        "sort_order": 130,
        "description": "将群成员移出群聊，属于不可自动撤销的高风险操作。",
        "actions": ("set_group_kick",),
    },
    {
        "code": "group.settings",
        "name": "修改群设置",
        "risk_level": RISK_HIGH,
        "sort_order": 140,
        "description": "修改群名称、成员名片、专属头衔、群公告，或让机器人退群。",
        "actions": (
            "set_group_name",
            "set_group_card",
            "set_group_leave",
            "set_group_special_title",
            "_send_group_notice",
        ),
    },
    {
        "code": "friend.handle",
        "name": "好友与加群请求处理",
        "risk_level": RISK_HIGH,
        "sort_order": 150,
        "description": "通过或拒绝好友请求、加群请求，会影响机器人的好友与群关系。",
        "actions": ("set_friend_add_request", "set_group_add_request"),
    },
    {
        "code": "http.outbound",
        "name": "访问外网",
        "risk_level": RISK_HIGH,
        "sort_order": 160,
        "description": "主动发起外部 HTTP 请求，必须同时声明域名白名单，否则可能外传数据。",
        "actions": (),
    },
)


CAPABILITY_CODES: tuple[str, ...] = tuple(spec["code"] for spec in CAPABILITY_SPECS)

CAPABILITY_CODE_SET = frozenset(CAPABILITY_CODES)

CAPABILITY_LABELS: dict[str, str] = {
    spec["code"]: spec["name"] for spec in CAPABILITY_SPECS
}

CAPABILITY_RISK_LEVELS: dict[str, str] = {
    spec["code"]: spec["risk_level"] for spec in CAPABILITY_SPECS
}

HIGH_RISK_CAPABILITY_CODES = frozenset(
    spec["code"] for spec in CAPABILITY_SPECS if spec["risk_level"] == RISK_HIGH
)

_LOW_AND_MEDIUM_CAPABILITY_CODES = frozenset(
    spec["code"] for spec in CAPABILITY_SPECS if spec["risk_level"] != RISK_HIGH
)

# 动作名 → 能力码，供运行期能力闸门按动作反查所需能力。
ACTION_CAPABILITY_MAP: dict[str, str] = {
    action: spec["code"] for spec in CAPABILITY_SPECS for action in spec["actions"]
}


def capability_label(code: str) -> str:
    """返回能力的中文名称，未登记的能力返回能力码本身。"""
    return CAPABILITY_LABELS.get(code, code)


def capability_risk_level(code: str) -> str:
    """返回能力的风险等级，未登记的能力按高风险处理。"""
    return CAPABILITY_RISK_LEVELS.get(code, RISK_HIGH)


def is_high_risk_capability(code: str) -> bool:
    """判断能力是否属于高风险，高风险必须由管理员显式勾选才生效。"""
    return code in HIGH_RISK_CAPABILITY_CODES


def capability_for_action(action_name: str) -> str | None:
    """按 OneBot 动作名反查所需能力，未纳入插件体系的动作返回 None。"""
    return ACTION_CAPABILITY_MAP.get(str(action_name or "").strip())


def default_granted_capabilities(declared: list[str]) -> list[str]:
    """计算默认批准的能力：非高风险能力随审核通过生效，高风险能力默认不批准。"""
    return [code for code in declared if code in _LOW_AND_MEDIUM_CAPABILITY_CODES]


def capability_catalog() -> list[dict[str, Any]]:
    """返回能力清单，供审核面板按风险等级分组展示。"""
    return [
        {
            "code": spec["code"],
            "name": spec["name"],
            "risk_level": spec["risk_level"],
            "description": spec["description"],
            "actions": list(spec["actions"]),
        }
        for spec in CAPABILITY_SPECS
    ]
