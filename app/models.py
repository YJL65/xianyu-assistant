from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Listing:
    item_id: str
    title: str
    description: str = ""
    url: str = ""


@dataclass
class IncomingMessage:
    conversation_id: str
    buyer_id: str
    buyer_name: str
    text: str
    message_id: str
    listing_id: str = ""
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class ManualSellerMessage:
    conversation_id: str
    text: str
    message_id: str
    listing_id: str = ""
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class NeedState:
    goal: str = ""
    deliverable: str = ""
    deadline: str = ""
    budget: str = ""
    delivery_method: str = ""
    materials: str = ""
    notes: str = ""
    completed: bool = False
    notified: bool = False
    initial_notified: bool = False
    manual_takeover: bool = False

    def missing_fields(self) -> list[str]:
        required = [
            "goal",
            "deliverable",
            "deadline",
            "budget",
            "delivery_method",
            "materials",
        ]
        return [field_name for field_name in required if not getattr(self, field_name)]

    def merge(self, updates: dict[str, Any]) -> None:
        for key in (
            "goal",
            "deliverable",
            "deadline",
            "budget",
            "delivery_method",
            "materials",
            "notes",
        ):
            value = updates.get(key)
            if isinstance(value, str) and value.strip():
                setattr(self, key, value.strip())
        if not self.missing_fields():
            self.completed = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "NeedState":
        if not data:
            return cls()
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass
class AssistantDecision:
    reply: str
    should_notify: bool
    summary: str = ""
