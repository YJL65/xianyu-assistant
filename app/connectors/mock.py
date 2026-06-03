from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..models import IncomingMessage, Listing
from .base import Connector


class MockConnector(Connector):
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.listing = Listing(
            item_id="mock-listing-1",
            title="毕业论文/小程序/网站定制开发服务",
            description=(
                "提供软件项目需求梳理、程序开发、论文材料整理、部署协助。"
                "具体价格和周期需要根据需求确认。"
            ),
            url="https://example.invalid/xianyu/mock-listing-1",
        )
        self.messages = [
            IncomingMessage(
                conversation_id="mock-conversation-1",
                buyer_id="buyer-001",
                buyer_name="测试买家",
                text="你好，我想做一个小程序，能咨询下吗？",
                message_id="mock-1",
                listing_id=self.listing.item_id,
            ),
            IncomingMessage(
                conversation_id="mock-conversation-1",
                buyer_id="buyer-001",
                buyer_name="测试买家",
                text="主要是校园二手交易，想要前后端和论文材料，月底前，预算两千左右，线上沟通，我有需求文档和参考图。",
                message_id="mock-2",
                listing_id=self.listing.item_id,
            ),
        ]

    async def events(self) -> AsyncIterator[IncomingMessage]:
        for message in self.messages:
            await asyncio.sleep(0.1)
            yield message

    async def send_text(self, conversation_id: str, buyer_id: str, text: str) -> None:
        self.sent.append((conversation_id, buyer_id, text))
        print(f"[mock -> buyer] {text}")

    async def fetch_listing(self, listing_id: str) -> Listing:
        return self.listing

