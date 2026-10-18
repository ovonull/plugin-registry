"""视频解析插件测试：清单合规、链接识别、平台解析、缓存与消息拼装。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.services import plugin_sdk
from app.services.plugin_gate_runtime import (
    MAX_RESPONSE_CHARS,
    MAX_RESPONSE_CHARS_LIMIT,
    _response_chars,
)
from app.services.plugin_manifest import normalize_manifest


PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "video-parser"
PLUGIN_ID = "cn.ucbk.video"

DOUYIN_ITEM = {
    "aweme_id": "7069543727328398622",
    "desc": "一条测试作品",
    "author": {"nickname": "测试作者"},
    "video": {
        "duration": 15000,
        "play_addr": {"url_list": ["https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0200"]},
        "cover": {"url_list": ["http://p3.douyinpic.com/cover.jpg"]},
    },
    "statistics": {"digg_count": 12},
}

DOUYIN_SHARE_HTML = (
    "<html><head></head><body><script>window._ROUTER_DATA = "
    + json.dumps({"loaderData": {"video": {"videoInfoRes": {"item_list": [DOUYIN_ITEM]}}}}, ensure_ascii=False)
    + ";</script></body></html>"
)

XHS_NOTE_ID = "6573a1b2c3d4e5f60718293a"
XHS_NOTE = {
    "noteId": XHS_NOTE_ID,
    "title": "小红书测试笔记",
    "desc": "正文第一行\n第二行",
    "type": "video",
    "user": {"nickname": "笔记作者"},
    "video": {
        "capa": {"duration": 42},
        "media": {
            "stream": {
                "h264": [
                    {
                        "masterUrl": "https://sns-video-bd.xhscdn.com/stream/1.mp4",
                        "backupUrls": ["https://sns-video-hw.xhscdn.com/stream/1.mp4"],
                    }
                ]
            }
        },
        "image": {"urlDefault": "http://sns-img-bd.xhscdn.com/cover.jpg"},
    },
    "imageList": [],
}

# 页面把 JS 字面量 undefined 写进了状态，直接 json 解析会失败。
XHS_HTML = (
    "<html><body><script>window.__INITIAL_STATE__={\"note\":{\"noteDetailMap\":{\""
    + XHS_NOTE_ID
    + "\":{\"note\":"
    + json.dumps(XHS_NOTE, ensure_ascii=False)
    + ",\"comments\":{\"cursor\":undefined}}}}};</script></body></html>"
)


class FakeHost:
    """模拟宿主视图，记录发出去的消息与出网请求。"""

    def __init__(self, config: dict[str, Any] | None = None, granted: list[str] | None = None) -> None:
        """初始化配置、能力与响应表。"""
        self.plugin_id = PLUGIN_ID
        self.bot_qq = "10000"
        self.config = config or {}
        self.granted = granted if granted is not None else ["msg.send", "http.outbound"]
        self.logs: list[tuple[str, str]] = []
        self.sent: list[Any] = []
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[str, dict[str, Any]] = {}
        self.fail_video = False

    def has(self, capability: str) -> bool:
        """判断能力是否批准。"""
        return capability in self.granted

    def log(self, message: Any, level: str = "info") -> None:
        """记录插件日志。"""
        self.logs.append((level, str(message)))

    async def http_request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """按注册的响应表返回内容，未注册时回 404。"""
        self.calls.append({"method": method, "url": url, **kwargs})
        for prefix, response in self.responses.items():
            if url.startswith(prefix):
                return response
        return {"status": 404, "headers": {}, "text": ""}

    async def _deliver(self, message: Any) -> dict[str, Any]:
        """模拟发送动作，视频段在 fail_video 时抛出被拒异常。"""
        if self.fail_video and isinstance(message, list):
            if any(isinstance(seg, dict) and seg.get("type") == "video" for seg in message):
                raise plugin_sdk.PluginCallError("视频上传失败")
        self.sent.append(message)
        return {"message_id": 1}

    async def send_group_msg(self, group_id: Any, message: Any, auto_escape: bool = False) -> dict[str, Any]:
        """发送群消息。"""
        return await self._deliver(message)

    async def send_private_msg(self, user_id: Any, message: Any, auto_escape: bool = False) -> dict[str, Any]:
        """发送私聊消息。"""
        return await self._deliver(message)


def load_plugin() -> Any:
    """按宿主的加载方式导入插件入口，重复调用复用同一模块。"""
    name = "napcat_plugin_test_video"
    if name in sys.modules:
        return sys.modules[name]
    if str(PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR))
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / "plugin.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    plugin_sdk.begin_loading(PLUGIN_ID)
    try:
        spec.loader.exec_module(module)
    finally:
        plugin_sdk.end_loading()
    return module


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """每个用例前清空会话冷却与解析缓存，避免用例之间互相影响。"""
    plugin = load_plugin()
    plugin._last_reply.clear()
    plugin._media_cache.clear()


def run(coro: Any) -> Any:
    """在同步测试里跑一次协程，项目没有引入 pytest-asyncio。"""
    return asyncio.run(coro)


def sent_segments(host: FakeHost) -> list[dict[str, Any]]:
    """把发送记录拍平成一串消息段，忽略消息之间的边界。"""
    segments: list[dict[str, Any]] = []
    for message in host.sent:
        if isinstance(message, list):
            segments.extend(message)
        else:
            segments.append({"type": "text", "data": {"text": message}})
    return segments


def sent_text(host: FakeHost) -> str:
    """把发出去的所有文本拼成一段，方便断言关键词。"""
    return "\n".join(
        str(segment["data"].get("text") or "")
        for segment in sent_segments(host)
        if segment.get("type") == "text"
    )


def sent_videos(host: FakeHost) -> list[str]:
    """列出所有发出去的视频地址。"""
    return [
        str(segment["data"]["file"])
        for segment in sent_segments(host)
        if segment.get("type") == "video"
    ]


def build_event(text: str, *, group: bool = True, chat_id: int = 123) -> dict[str, Any]:
    """构造一条 OneBot 消息事件。"""
    event: dict[str, Any] = {
        "post_type": "message",
        "message_type": "group" if group else "private",
        "self_id": 999,
        "user_id": 456,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
    }
    if group:
        event["group_id"] = chat_id
    return event


def douyin_host(**config: Any) -> FakeHost:
    """构造能返回抖音分享页的宿主。"""
    host = FakeHost(config)
    host.responses["https://www.iesdouyin.com/share/video/"] = {
        "status": 200,
        "headers": {"content-type": "text/html"},
        "text": DOUYIN_SHARE_HTML,
    }
    return host


def xhs_host(**config: Any) -> FakeHost:
    """构造能返回小红书笔记页的宿主。"""
    settings = {"xhs_cookie": "web_session=abc; a1=def"}
    settings.update(config)
    host = FakeHost(settings)
    host.responses["https://www.xiaohongshu.com/explore/"] = {
        "status": 200,
        "headers": {"content-type": "text/html"},
        "text": XHS_HTML,
    }
    return host


def test_manifest_passes_strict_validation() -> None:
    """清单必须通过宿主的严格校验，字段一个都不能多。"""
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    normalized = normalize_manifest(manifest)
    assert normalized["id"] == PLUGIN_ID
    assert normalized["version"] == "1.1.3"
    assert normalized["entry"] == "plugin.py"
    assert "msg.send" in normalized["capabilities"]
    assert "http.outbound" in normalized["capabilities"]
    # 声明出网能力必须同时给出域名白名单。
    for domain in ("www.iesdouyin.com", "api.bilibili.com", "www.xiaohongshu.com", "xhslink.com"):
        assert domain in normalized["allowed_domains"], domain
    keys = {item["key"] for item in normalized["config_schema"]}
    assert {"cache_seconds", "xhs_cookie"} <= keys
    # Cookie 属于敏感项，必须在界面上按密码处理。
    sensitive = {item["key"] for item in normalized["config_schema"] if item["sensitive"]}
    assert {"douyin_cookie", "bilibili_cookie", "xhs_cookie"} <= sensitive


def test_plugin_entry_registers_handler() -> None:
    """入口文件用宿主 SDK 注册了消息处理函数。"""
    load_plugin()
    handlers = plugin_sdk.handlers_for(
        PLUGIN_ID, {"post_type": "message", "message_type": "group"}
    )
    assert handlers, "插件没有注册 on_message 处理函数"


def test_normalize_url_matches_supported_platforms() -> None:
    """各种形态的链接都能识别出平台。"""
    common = load_plugin().common
    cases = {
        "https://www.douyin.com/video/7069543727328398622": "douyin",
        "https://v.douyin.com/iRabcdef/": "douyin",
        "https://www.iesdouyin.com/share/video/7069543727328398622/": "douyin",
        "https://www.douyin.com/discover?modal_id=7069543727328398622": "douyin",
        "https://www.bilibili.com/video/BV1xx411c7mD?p=2": "bilibili",
        "https://b23.tv/abcdefg": "bilibili",
        f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}?xsec_token=abc": "xiaohongshu",
        f"https://www.xiaohongshu.com/discovery/item/{XHS_NOTE_ID}": "xiaohongshu",
        "http://xhslink.com/a/AbCdEf": "xiaohongshu",
        "v.douyin.com/iRabcdef": "douyin",
        f"xhslink.com/a/AbCdEf": "xiaohongshu",
    }
    for text, expected in cases.items():
        platform, url = common.normalize_url(f"看看这个 {text} 好笑")
        assert platform == expected, text
        assert url.startswith("http"), url
    assert common.normalize_url("今天天气不错") == ("", "")


def test_douyin_resolve_id_from_text() -> None:
    """链接里带作品号时不去请求短链。"""
    douyin = load_plugin().douyin
    assert douyin.extract_id("https://www.douyin.com/video/7069543727328398622") == "7069543727328398622"
    assert douyin.extract_id("https://www.douyin.com/?modal_id=123456789012345") == "123456789012345"
    assert douyin.extract_id("https://v.douyin.com/iRabcdef/") == ""


def test_douyin_build_strips_watermark() -> None:
    """playwm 地址会被替换成无水印的 play 地址。"""
    douyin = load_plugin().douyin
    media = douyin._build(DOUYIN_ITEM, "https://www.douyin.com/video/1")
    assert media["kind"] == "video"
    assert "playwm" not in media["play_url"]
    assert "/play/" in media["play_url"]
    assert media["duration"] == 15
    assert media["cover"].startswith("https://")
    assert media["title"] == "一条测试作品"
    # 抖音没有独立标题，整段文案既做标题也做描述，由插件侧去重。
    assert media["desc"] == "一条测试作品"
    assert media["stats"] == ["点赞 12"]


def test_douyin_build_images() -> None:
    """图文作品按图片返回，不当作视频。"""
    douyin = load_plugin().douyin
    item = {
        "aweme_id": "1",
        "desc": "图文",
        "author": {"nickname": "作者"},
        "images": [{"url_list": ["http://p3.douyinpic.com/1.jpg"]}, {"url_list": ["http://p3.douyinpic.com/2.jpg"]}],
        "video": {"duration": 0},
    }
    media = douyin._build(item, "https://www.douyin.com/note/1")
    assert media["kind"] == "images"
    assert len(media["images"]) == 2
    assert media["images"][0].startswith("https://")


def test_handler_sends_video_for_douyin_link() -> None:
    """群里贴抖音链接时先回描述卡片，再单独发视频。"""
    plugin = load_plugin()
    host = douyin_host()
    run(plugin.handle_video(build_event("看看 https://www.douyin.com/video/7069543727328398622"), host))

    assert len(host.sent) == 2, "描述与视频分两条发，避免文本段被混发丢掉"
    card = host.sent[0]
    assert isinstance(card, str)
    assert "一条测试作品" in card
    assert "@测试作者" in card
    assert "时长 00:15" in card
    assert "点赞 12" in card

    segments = host.sent[1]
    assert isinstance(segments, list)
    assert segments[0]["type"] == "video"
    assert segments[0]["data"]["file"].startswith("https://aweme.snssdk.com")


def test_describe_keeps_full_text_and_hides_repeated_title() -> None:
    """描述卡片保留正文，且不会把已作为标题的那一行再重复一遍。"""
    plugin = load_plugin()
    media = {
        "platform_name": "抖音",
        "title": "第一行标题",
        "desc": "第一行标题\n第二行正文\n第三行正文",
        "author": "作者",
        "duration": 90,
        "stats": ["点赞 1.2万", "评论 345"],
    }
    text = plugin._describe(media)
    assert text.splitlines()[0] == "【抖音】第一行标题"
    assert text.count("第一行标题") == 1, "标题那一行不应该在正文里重复"
    assert "第二行正文" in text
    assert "点赞 1.2万 · 评论 345" in text
    # 元素齐全时顺序应当是：标题 → 元信息 → 互动数据 → 空行 → 正文。
    assert text.splitlines()[1] == "@作者 · 时长 01:30"
    assert text.splitlines()[3] == ""


def test_describe_clips_long_description() -> None:
    """超长文案按上限截断，不会把上千字全塞进一条消息。"""
    plugin = load_plugin()
    media = {"platform_name": "B站", "title": "标题", "desc": "长" * 2000}
    text = plugin._describe(media)
    assert "…" in text
    assert len(text) < plugin.MAX_DESC_LENGTH + 120


def test_describe_skips_placeholder_description() -> None:
    """B站老视频的简介常是「-」「www」这类占位符，不能当成正文展示。"""
    plugin = load_plugin()
    for desc in ("-", "www", "-----", "。。。"):
        media = {"platform_name": "哔哩哔哩", "title": "标题", "desc": desc}
        assert plugin._describe(media).splitlines() == ["【哔哩哔哩】标题"], desc


def test_send_video_card_falls_back_to_link_when_rejected() -> None:
    """QQ 拒收视频时先保留描述，再补一条无水印直链。"""
    plugin = load_plugin()
    host = douyin_host()
    host.fail_video = True
    run(plugin.handle_video(build_event("看看 https://www.douyin.com/video/7069543727328398622"), host))

    assert len(host.sent) == 2
    assert "一条测试作品" in host.sent[0]
    assert "无水印直链" in host.sent[1]
    assert any(level == "warning" for level, _ in host.logs)


def test_handler_sends_video_when_duration_is_unlimited() -> None:
    """0 或留空表示不限时长：宿主默认写入的 0 不能让视频被误判超限。"""
    plugin = load_plugin()
    for index, config in enumerate(({"max_duration": 0}, {"max_duration": ""}, {})):
        host = douyin_host(**config)
        run(
            plugin.handle_video(
                build_event(
                    "https://www.douyin.com/video/7069543727328398622",
                    chat_id=200 + index,
                ),
                host,
            )
        )
        videos = sent_videos(host)
        assert len(videos) == 1, f"{config} 应直发视频，实际回了链接"
        assert videos[0].startswith("https://aweme.snssdk.com")


def test_handler_ignores_zero_image_count() -> None:
    """图文张数配置为 0 时按默认张数处理，不能压成 1 张。"""
    plugin = load_plugin()
    host = FakeHost({"max_images": 0})
    count = plugin._int_config(
        host,
        "max_images",
        plugin.DEFAULT_MAX_IMAGES,
        1,
        plugin.MAX_IMAGES_LIMIT,
        zero_as_default=True,
    )
    assert count == plugin.DEFAULT_MAX_IMAGES == 3


def test_handler_respects_duration_limit() -> None:
    """超过时长上限时不发视频，只回链接。"""
    plugin = load_plugin()
    host = douyin_host(max_duration=5)
    run(plugin.handle_video(build_event("https://www.douyin.com/video/7069543727328398622"), host))

    assert len(host.sent) == 1
    assert isinstance(host.sent[0], str)
    assert "超过上限" in host.sent[0]


def test_handler_ignores_private_when_scope_is_group_only() -> None:
    """生效范围为仅群聊时，私聊不处理。"""
    plugin = load_plugin()
    host = douyin_host(reply_scope="仅群聊")
    run(plugin.handle_video(build_event("https://www.douyin.com/video/1", group=False), host))
    assert host.sent == []


def test_handler_requires_trigger_word_in_command_mode() -> None:
    """仅指令模式下，没带触发词的消息不处理；带了但没链接时给用法提示。"""
    plugin = load_plugin()
    host = douyin_host(auto_parse="仅指令触发")
    run(plugin.handle_video(build_event("随便聊聊 https://www.douyin.com/video/7069543727328398622"), host))
    assert host.sent == []

    run(plugin.handle_video(build_event("解析"), host))
    assert len(host.sent) == 1
    assert "用法" in host.sent[0]
    assert "小红书" in host.sent[0]


def test_handler_reports_parse_failure() -> None:
    """抖音只回空壳页面时给出可执行的中文提示，而不是抛错。"""
    plugin = load_plugin()
    host = FakeHost()
    host.responses["https://www.iesdouyin.com/share/video/"] = {"status": 200, "headers": {}, "text": "<html></html>"}
    run(plugin.handle_video(build_event("https://www.douyin.com/video/7069543727328398622"), host))

    assert len(host.sent) == 1
    assert "解析失败" in host.sent[0]
    assert "Cookie" in host.sent[0]
    # 空壳页面会重试三次，不能无限请求。
    assert len(host.calls) == 3


def test_handler_blocks_without_outbound_capability() -> None:
    """没有出网能力时明确告知，而不是静默失败。"""
    plugin = load_plugin()
    host = FakeHost(granted=["msg.send"])
    run(plugin.handle_video(build_event("https://www.douyin.com/video/7069543727328398622"), host))

    assert len(host.sent) == 1
    assert "访问外网" in host.sent[0]
    assert host.calls == []


def test_handler_cooldown_blocks_same_link() -> None:
    """同一会话重复贴同一条链接会被冷却挡住。"""
    plugin = load_plugin()
    host = douyin_host()
    event = build_event("https://www.douyin.com/video/7069543727328398622")
    run(plugin.handle_video(event, host))
    run(plugin.handle_video(event, host))
    assert len(host.sent) == 2, "冷却期内只应回复一次（描述 + 视频）"


def test_handler_reuses_cache_across_chats() -> None:
    """同一条链接被另一个群粘贴时直接用缓存，不再请求平台。"""
    plugin = load_plugin()
    host = douyin_host()
    url = "https://www.douyin.com/video/7069543727328398622"
    run(plugin.handle_video(build_event(url, chat_id=111), host))
    run(plugin.handle_video(build_event(url, chat_id=222), host))

    assert len(host.sent) == 4, "第二个群也应该收到描述与视频"
    assert len(host.calls) == 1, "缓存命中时不应再次请求分享页"
    assert any("缓存" in message for _, message in host.logs)


def test_handler_cache_can_be_disabled() -> None:
    """缓存时间配成 -1 时关闭缓存，每次都重新解析。"""
    plugin = load_plugin()
    host = douyin_host(cache_seconds=-1)
    url = "https://www.douyin.com/video/7069543727328398622"
    run(plugin.handle_video(build_event(url, chat_id=111), host))
    run(plugin.handle_video(build_event(url, chat_id=222), host))

    assert len(host.sent) == 4
    assert len(host.calls) == 2


def test_handler_zero_means_default_not_disabled() -> None:
    """宿主默认写入的 0 按默认值处理，不能把冷却和缓存一起关掉。"""
    plugin = load_plugin()
    high_cache = plugin.MAX_CACHE_SECONDS
    host = FakeHost({"cooldown_seconds": 0, "cache_seconds": 0})
    assert plugin._seconds_config(host, "cooldown_seconds", 15, plugin.MAX_COOLDOWN) == 15
    assert plugin._seconds_config(host, "cache_seconds", 600, high_cache) == 600
    # 想显式关闭就填负数。
    host_off = FakeHost({"cache_seconds": -1, "cooldown_seconds": -1})
    assert plugin._seconds_config(host_off, "cache_seconds", 600, high_cache) == 0
    assert plugin._seconds_config(host_off, "cooldown_seconds", 15, plugin.MAX_COOLDOWN) == 0


def test_media_cache_expires_and_evicts(monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存按有效期过期，并遵守容量上限（最近最少使用的先淘汰）。"""
    load_plugin()
    cache_module = sys.modules["vparse.cache"]
    cache = cache_module.MediaCache(capacity=2)
    clock = [1000.0]
    monkeypatch.setattr(cache_module, "_now", lambda: clock[0])

    cache.put("a", {"title": "A"}, 10)
    assert cache.get("a") == {"title": "A"}

    clock[0] += 11
    assert cache.get("a") is None, "过期后不应再返回旧结果"

    cache.put("b", {"title": "B"}, 10)
    cache.put("c", {"title": "C"}, 10)
    cache.put("d", {"title": "D"}, 10)
    assert len(cache) == 2
    assert cache.get("b") is None, "容量满时应淘汰最早写入的条目"
    assert cache.get("c") is not None
    assert cache.get("d") is not None

    # ttl <= 0 表示不缓存。
    cache.put("e", {"title": "E"}, 0)
    assert cache.get("e") is None


def test_bilibili_extract_ids() -> None:
    """B站链接能取出 bvid、av 号与分P。"""
    bilibili = load_plugin().bilibili
    assert bilibili.extract_ids("https://www.bilibili.com/video/BV1xx411c7mD?p=3") == ("BV1xx411c7mD", "", 3)
    assert bilibili.extract_ids("https://www.bilibili.com/video/av170001") == ("", "170001", 1)


def test_bilibili_parse_prefers_single_file_mp4() -> None:
    """B站走 html5 单文件地址，清晰度按返回的 quality 标注。"""
    plugin = load_plugin()
    host = FakeHost()
    host.responses["https://api.bilibili.com/x/web-interface/view"] = {
        "status": 200,
        "headers": {"content-type": "application/json"},
        "json": {
            "code": 0,
            "data": {
                "bvid": "BV1xx411c7mD",
                "title": "测试视频",
                "pic": "http://i0.hdslb.com/cover.jpg",
                "duration": 2055,
                "owner": {"name": "UP主"},
                "cid": 62131,
            },
        },
    }
    host.responses["https://api.bilibili.com/x/player/playurl"] = {
        "status": 200,
        "headers": {"content-type": "application/json"},
        "json": {"code": 0, "data": {"quality": 80, "durl": [{"url": "https://upos.example/video.mp4"}]}},
    }
    media = run(plugin.bilibili.parse("https://www.bilibili.com/video/BV1xx411c7mD", "", host))

    assert media["play_url"] == "https://upos.example/video.mp4"
    assert media["quality"] == "1080P"
    assert media["duration"] == 2055
    assert media["cover"].startswith("https://")
    play_call = host.calls[-1]
    assert play_call["query"]["platform"] == "html5"
    assert play_call["query"]["high_quality"] == 1


def test_bilibili_short_link_follows_location() -> None:
    """b23.tv 短链手工跟随跳转后再解析。"""
    plugin = load_plugin()
    host = FakeHost()
    host.responses["https://b23.tv/"] = {
        "status": 302,
        "headers": {"location": "https://www.bilibili.com/video/BV1xx411c7mD"},
        "text": "",
    }
    resolved = run(
        plugin.common.follow_short_link(
            host, "https://b23.tv/abcdefg", headers={"User-Agent": "x"}
        )
    )
    assert resolved == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_xiaohongshu_extract_id() -> None:
    """各种形态的小红书链接都能取出笔记号。"""
    xiaohongshu = load_plugin().xiaohongshu
    assert xiaohongshu.extract_id(f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}?xsec_token=x") == XHS_NOTE_ID
    assert xiaohongshu.extract_id(f"https://www.xiaohongshu.com/discovery/item/{XHS_NOTE_ID}") == XHS_NOTE_ID
    assert xiaohongshu.extract_id("http://xhslink.com/a/AbCdEf") == ""


def test_xiaohongshu_parse_state_tolerates_js_undefined() -> None:
    """状态里的 undefined 要能替换成 null 后解析，截断的 JSON 则判为失败。"""
    xiaohongshu = load_plugin().xiaohongshu
    state = xiaohongshu.parse_state(XHS_HTML)
    assert state is not None
    assert xiaohongshu.find_note(state)["noteId"] == XHS_NOTE_ID

    truncated = XHS_HTML[: XHS_HTML.index("\"comments\"")]
    assert xiaohongshu.parse_state(truncated) is None
    assert xiaohongshu.parse_state("<html>没有状态</html>") is None


def test_xiaohongshu_build_video_and_images() -> None:
    """视频取 masterUrl，图文取原图，封面统一升级成 https。"""
    xiaohongshu = load_plugin().xiaohongshu
    media = xiaohongshu.build(XHS_NOTE, "https://www.xiaohongshu.com/explore/1")
    assert media["kind"] == "video"
    assert media["play_url"] == "https://sns-video-bd.xhscdn.com/stream/1.mp4"
    assert media["duration"] == 42
    assert media["author"] == "笔记作者"
    assert media["cover"].startswith("https://")

    image_note = {
        "noteId": "1",
        "title": "",
        "desc": "第一行正文\n第二行",
        "user": {"nickname": "作者"},
        "imageList": [
            {"urlDefault": "http://sns-img-bd.xhscdn.com/1.jpg"},
            {"infoList": [{"url": "http://sns-img-bd.xhscdn.com/2.jpg"}]},
        ],
    }
    images = xiaohongshu.build(image_note, "https://www.xiaohongshu.com/explore/1")
    assert images["kind"] == "images"
    assert images["images"] == [
        "https://sns-img-bd.xhscdn.com/1.jpg",
        "https://sns-img-bd.xhscdn.com/2.jpg",
    ]
    # 没有标题时用正文第一行兜底。
    assert images["title"] == "第一行正文"


def test_xiaohongshu_requires_cookie() -> None:
    """没配 Cookie 时直接给提示，不发起任何出网请求。"""
    plugin = load_plugin()
    host = FakeHost()
    run(plugin.handle_video(build_event(f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}"), host))

    assert len(host.sent) == 1
    assert "Cookie" in host.sent[0]
    assert host.calls == []


def test_xiaohongshu_login_redirect_is_reported() -> None:
    """Cookie 失效被跳到登录页时，提示要更新 Cookie。"""
    plugin = load_plugin()
    host = FakeHost({"xhs_cookie": "web_session=expired"})
    host.responses["https://www.xiaohongshu.com/explore/"] = {
        "status": 302,
        "headers": {"location": "https://www.xiaohongshu.com/login?redirectPath=x"},
        "text": "",
    }
    run(plugin.handle_video(build_event(f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}"), host))

    assert len(host.sent) == 1
    assert "登录" in host.sent[0]
    assert "Cookie" in host.sent[0]


def test_xiaohongshu_guest_redirect_is_reported() -> None:
    """未登录被跳到 404 页时（实测行为），提示补有效 Cookie。"""
    plugin = load_plugin()
    host = FakeHost({"xhs_cookie": "a1=x"})
    host.responses["https://www.xiaohongshu.com/explore/"] = {
        "status": 302,
        "headers": {"location": "https://www.xiaohongshu.com/404/sec_abc?redirectPath=x"},
        "text": "",
    }
    run(plugin.handle_video(build_event(f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}"), host))

    assert len(host.sent) == 1
    assert "404" in host.sent[0]
    assert "Cookie" in host.sent[0]


def test_handler_sends_video_for_xiaohongshu_link() -> None:
    """配了 Cookie 后小红书笔记先回描述卡片，再发视频。"""
    plugin = load_plugin()
    host = xhs_host()
    run(plugin.handle_video(build_event(f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}"), host))

    assert len(host.sent) == 2
    card = host.sent[0]
    assert "小红书测试笔记" in card
    assert "正文第一行" in card, "描述里应带上笔记正文"
    segments = host.sent[1]
    assert segments[0]["type"] == "video"
    assert segments[0]["data"]["file"] == "https://sns-video-bd.xhscdn.com/stream/1.mp4"
    # 申请了更大的响应长度，否则几百 KB 的笔记页会被截断。
    assert host.calls[0]["max_chars"] >= 100000


def test_response_chars_is_bounded() -> None:
    """插件申请的响应长度有默认值与硬上限，不能无限制索取。"""
    assert _response_chars(None) == MAX_RESPONSE_CHARS
    assert _response_chars("abc") == MAX_RESPONSE_CHARS
    assert _response_chars(120000) == 120000
    assert _response_chars(10) == 1000
    assert _response_chars(10**9) == MAX_RESPONSE_CHARS_LIMIT


def load_pack_module() -> Any:
    """导入 plugins/pack.py，复用与发布时一致的打包逻辑。"""
    name = "napcat_plugin_pack"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR.parent / "pack.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_package_installs_through_plugin_api(tmp_path: Path) -> None:
    """真实插件包能走完上传、审核、上架、安装、启用的全流程。"""
    from test_plugins import build_plugin_settings, submit_package
    from test_service import login

    zip_path = load_pack_module().build("video-parser", tmp_path)
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))

    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": "10001", "group_mode": "all", "is_enabled": True},
        ).json()
        assert created["success"] is True, created

        submitted = submit_package(client, token, zip_path.read_bytes())
        assert submitted["success"] is True, submitted

        # http.outbound 属高风险，必须在审核时显式勾选才会写入已批准能力。
        approved = client.post(
            "/api/plugin_admin/versions/approve",
            headers=headers,
            json={
                "plugin_id": PLUGIN_ID,
                "version": manifest["version"],
                "granted_capabilities": ["msg.send", "http.outbound"],
                "comment": "视频解析插件测试审核",
            },
        ).json()
        assert approved["success"] is True, approved
        version = approved["data"]["version"]
        assert sorted(version["granted_capabilities"]) == ["http.outbound", "msg.send"]
        assert version["is_current"] is True

        published = client.post(
            "/api/plugin_admin/publish",
            headers=headers,
            json={"plugin_id": PLUGIN_ID},
        ).json()
        assert published["success"] is True, published

        installed = client.post(
            "/api/plugins/installations",
            headers=headers,
            json={"plugin_id": PLUGIN_ID, "bot_qqs": ["10001"]},
        ).json()
        assert installed["success"] is True, installed
        assert installed["data"]["is_enabled"] is False
        assert "http.outbound" in installed["data"]["granted_capabilities"]

        enabled = client.put(
            f"/api/plugins/installations/{installed['data']['id']}",
            headers=headers,
            json={"is_enabled": True},
        ).json()
        assert enabled["success"] is True, enabled
        assert enabled["data"]["is_enabled"] is True
