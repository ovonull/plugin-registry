"""合并转发消息解析与 OneBot 内容读取测试。"""

import asyncio

from app.services.ai_image_service import AiImageService
from app.services.auto_reply_service import AutoReplyService
from app.services.onebot_service import OneBotService


def test_forward_message_content_is_resolved_for_text_and_images() -> None:
    """读取 get_forward_msg 后，转发文本和嵌套图片都应进入审核事件。"""
    service = object.__new__(OneBotService)
    calls: list[tuple[str, dict, str]] = []

    async def fake_call_action(
        action: str, params: dict, self_id: str = ""
    ) -> dict:
        calls.append((action, params, self_id))
        return {
            "status": "ok",
            "retcode": 0,
            "data": {
                "messages": [
                    {
                        "sender": {"user_id": 778899, "nickname": "发送者"},
                        "content": [
                            {"type": "text", "data": {"text": "转发中的违规文本"}},
                            {
                                "type": "image",
                                "data": {"url": "https://image.example/test.jpg"},
                            },
                        ],
                    }
                ]
            },
        }

    service.call_action = fake_call_action
    event = {
        "message": [{"type": "forward", "data": {"id": "forward-1"}}],
        "raw_message": "[CQ:forward,id=forward-1]",
    }
    resolved = asyncio.run(service._resolve_forward_content(event, "123456"))

    assert calls == [("get_forward_msg", {"message_id": "forward-1"}, "123456")]
    assert resolved["_forward_message_text"] == "转发中的违规文本"
    assert AutoReplyService.extract_message_text(resolved) == "转发中的违规文本"
    assert AiImageService.has_candidates(resolved) is True


def test_forward_message_ids_support_cq_and_segment_formats() -> None:
    """同一转发同时出现在 CQ 码和消息段时应去重。"""
    event = {
        "message": [
            {"type": "forward", "data": {"id": "forward-1"}},
            {"type": "forward", "data": {"message_id": "forward-2"}},
        ],
        "raw_message": "[CQ:forward,id=forward-1][CQ:forward,id=forward-3]",
    }

    assert OneBotService._forward_message_ids(event) == [
        "forward-1",
        "forward-2",
        "forward-3",
    ]


def test_failed_forward_lookup_does_not_expose_cq_placeholder_as_text() -> None:
    """转发查询失败时，审核文本不应把转发 ID 当成消息正文。"""
    event = {
        "raw_message": "[CQ:forward,id=forward-1]",
        "_forward_message_unavailable": True,
    }

    assert AutoReplyService.extract_message_text(event) == ""
    assert OneBotService._extract_ai_text(event) == ""
