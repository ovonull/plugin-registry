"""
ghub：GitHub 仓库动态插件的实现包。

模块划分：
- store：状态文件读写（监控仓库、每仓库配置、全局凭证、已见条目）；
- client：GitHub REST API 客户端，统一经宿主出网；
- formatter：把接口数据格式化成推送消息；
- drafts：Issue 草稿状态机（起草、修改、确认、撤销、还原、删除）；
- commands：群聊指令解析与每仓库设置解析；
- poller：后台轮询任务。

入口在仓库根目录的 plugin.py；除 poller 依赖宿主 SDK 外，其余模块都可独立单测。
"""

from ghub.client import GitHubClient, GitHubError
from ghub.commands import (
    Command,
    parse_bool,
    parse_branch,
    parse_command,
    parse_credential,
    parse_display_name,
    parse_qq_list,
    parse_repo,
    parse_seconds,
    parse_settings,
    parse_threshold,
    parse_watch,
    repo_key,
    split_issue_args,
)
from ghub.drafts import (
    STATUS_TEXT,
    cancel_draft,
    confirm_draft,
    create_draft,
    delete_draft,
    edit_draft,
    get_draft,
    list_drafts,
    refresh_drafts,
    restore_draft,
)
from ghub.formatter import beijing_time, clip, format_item, format_star, mask_name
from ghub.store import (
    WATCH_KINDS,
    credential_of,
    diff_items,
    display_of,
    ensure_entry_defaults,
    load_state,
    record_failure,
    record_success,
    save_state,
    state_file,
    state_root,
    watch_of,
)

__all__ = [
    "GitHubClient",
    "GitHubError",
    "Command",
    "parse_bool",
    "parse_branch",
    "parse_command",
    "parse_credential",
    "parse_display_name",
    "parse_qq_list",
    "parse_repo",
    "parse_seconds",
    "parse_settings",
    "parse_threshold",
    "parse_watch",
    "repo_key",
    "split_issue_args",
    "STATUS_TEXT",
    "cancel_draft",
    "confirm_draft",
    "create_draft",
    "delete_draft",
    "edit_draft",
    "get_draft",
    "list_drafts",
    "refresh_drafts",
    "restore_draft",
    "beijing_time",
    "clip",
    "format_item",
    "format_star",
    "mask_name",
    "WATCH_KINDS",
    "credential_of",
    "diff_items",
    "display_of",
    "ensure_entry_defaults",
    "load_state",
    "record_failure",
    "record_success",
    "save_state",
    "state_file",
    "state_root",
    "watch_of",
]
