from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from ..models import IncomingMessage, Listing


class Connector(ABC):
    @abstractmethod
    async def events(self) -> AsyncIterator[IncomingMessage]:
        raise NotImplementedError

    @abstractmethod
    async def send_text(self, conversation_id: str, buyer_id: str, text: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def fetch_listing(self, listing_id: str) -> Listing:
        raise NotImplementedError

