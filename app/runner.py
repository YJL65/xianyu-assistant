from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone

from .cleanup import AUTO_CLEANUP_INTERVAL_SECONDS, cleanup_data
from .config import Settings
from .connectors.base import Connector
from .feishu import FeishuNotifier
from .llm import LlmError, OpenAICompatibleClient
from .models import AssistantDecision, Listing, ManualSellerMessage, NeedState, utc_now_iso
from .storage import Store

FIRST_REPLY = "\u4f60\u6709\u4ec0\u4e48\u9700\u6c42\uff1f"
NEW_CONSULTATION_GAP_SECONDS = 30 * 60


class AssistantRunner:
    def __init__(
        self,
        settings: Settings,
        connector: Connector,
    ) -> None:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        settings.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.connector = connector
        self.store = Store(settings.db_path)
        self.notifier = FeishuNotifier(
            settings.feishu_webhook_url,
            settings.feishu_webhook_secret,
        )
        self.llm = OpenAICompatibleClient(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )

    async def run_forever(self) -> None:
        cleanup_task = asyncio.create_task(self._run_cleanup_forever())
        try:
            async for message in self.connector.events():
                await self.handle_message(message)
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task

    async def _run_cleanup_forever(self) -> None:
        while True:
            self._run_cleanup_once()
            await asyncio.sleep(AUTO_CLEANUP_INTERVAL_SECONDS)

    def _run_cleanup_once(self) -> None:
        try:
            result = cleanup_data(self.settings)
        except Exception as exc:
            print(f"[cleanup] failed: {type(exc).__name__}: {exc}")
            return
        if (
            result.deleted_messages
            or result.deleted_states
            or result.deleted_debug_files
        ):
            print(result.report())

    async def handle_message(self, message) -> None:
        if isinstance(message, ManualSellerMessage):
            self._handle_manual_seller_message(message)
            return

        if self.store.has_message(message.message_id):
            return
        self.store.add_incoming(message)
        state = self.store.state(message.conversation_id)

        listing = await self._listing(message.listing_id)
        history, is_new_consultation = current_consultation_history(
            self.store.history_records(message.conversation_id)
        )
        if is_new_consultation:
            state = NeedState()
        if state.manual_takeover:
            self.store.save_state(message.conversation_id, state)
            return
        if self.llm.enabled:
            await self._handle_ai_customer_service(message, listing, history, state)
        else:
            await self._handle_fixed_fallback(message, listing, history, state)

        self.store.save_state(message.conversation_id, state)

    def _handle_manual_seller_message(self, message: ManualSellerMessage) -> None:
        if self.store.has_message(message.message_id):
            return
        if self._is_recorded_assistant_echo(message.conversation_id, message.text):
            self._clear_connector_manual_takeover(message.conversation_id)
            return
        self.store.add_seller_message(message)
        state = self.store.state(message.conversation_id)
        state.manual_takeover = True
        state.notified = True
        self.store.save_state(message.conversation_id, state)
        print(f"[assistant] manual takeover enabled conversation={message.conversation_id}")

    def _is_recorded_assistant_echo(self, conversation_id: str, text: str) -> bool:
        normalized = text.strip()
        if not normalized:
            return False
        return any(
            role == "assistant" and previous_text.strip() == normalized
            for role, previous_text in self.store.history(conversation_id)[-30:]
        )

    def _clear_connector_manual_takeover(self, conversation_id: str) -> None:
        conversations = getattr(self.connector, "manual_takeover_conversations", None)
        if conversations is not None:
            conversations.discard(conversation_id)

    async def _handle_ai_customer_service(
        self,
        message,
        listing: Listing,
        history: list[tuple[str, str]],
        state,
    ) -> None:
        decision = self._customer_service_turn(listing, history, message.text)

        reply = decision.reply or FIRST_REPLY
        if self._manual_takeover_enabled(message.conversation_id):
            state.manual_takeover = True
            state.notified = True
            print(
                f"[assistant] skipped AI reply before send because manual takeover is active "
                f"conversation={message.conversation_id}"
            )
            return

        await self.connector.send_text(
            message.conversation_id,
            message.buyer_id,
            reply,
        )
        self.store.add_assistant_reply(message.conversation_id, reply, utc_now_iso())

        if decision.should_notify:
            self._notify_customer_service_summary(
                conversation_id=message.conversation_id,
                buyer_name=message.buyer_name,
                listing=listing,
                latest_message=message.text,
                summary=decision.summary,
                state=state,
            )

    def _manual_takeover_enabled(self, conversation_id: str) -> bool:
        if self.store.state(conversation_id).manual_takeover:
            return True
        checker = getattr(self.connector, "manual_takeover_enabled", None)
        if callable(checker):
            return bool(checker(conversation_id))
        conversations = getattr(self.connector, "manual_takeover_conversations", None)
        return conversation_id in conversations if conversations is not None else False

    def _notify_customer_service_summary(
        self,
        conversation_id: str,
        buyer_name: str,
        listing: Listing,
        latest_message: str,
        summary: str,
        state: NeedState,
    ) -> None:
        if state.notified:
            return
        history_records = self.store.history_records(conversation_id)
        history, _is_new_consultation = current_consultation_history(history_records)
        self.notifier.send_customer_service_summary(
            buyer_name=buyer_name,
            listing_title=listing.title,
            latest_message=latest_message,
            summary=summary or fallback_summary_from_history(history),
            raw_dialogue=format_dialogue(history),
        )
        state.completed = True
        state.initial_notified = True
        state.notified = True

    def _notify_first_ai_exchange(
        self,
        conversation_id: str,
        buyer_name: str,
        listing: Listing,
        state,
    ) -> None:
        if state.initial_notified or state.notified:
            return
        history_records = self.store.history_records(conversation_id)
        history, _is_new_consultation = current_consultation_history(history_records)
        dialogue = first_two_assistant_rounds(history)
        if dialogue is None:
            return
        self.notifier.send_first_ai_exchange(
            buyer_name=buyer_name,
            listing_title=listing.title,
            dialogue=dialogue,
        )
        state.initial_notified = True
        state.notified = True

    async def _handle_fixed_fallback(
        self,
        message,
        listing: Listing,
        history: list[tuple[str, str]],
        state,
    ) -> None:
        await self.connector.send_text(
            message.conversation_id,
            message.buyer_id,
            FIRST_REPLY,
        )
        self.store.add_assistant_reply(message.conversation_id, FIRST_REPLY, utc_now_iso())
        self._notify_first_ai_exchange(message.conversation_id, message.buyer_name, listing, state)

    def _customer_service_turn(
        self,
        listing: Listing,
        history: list[tuple[str, str]],
        latest_message: str,
    ) -> AssistantDecision:
        try:
            return self.llm.customer_service_turn(listing, history, latest_message)
        except LlmError as exc:
            return AssistantDecision(
                reply=FIRST_REPLY,
                should_notify=True,
                summary=(
                    f"AI\u5ba2\u670d\u751f\u6210\u5931\u8d25\uff1a{exc}\n"
                    "\u5df2\u6536\u5230\u4e70\u5bb6\u6d88\u606f\uff0c\u8bf7\u4eba\u5de5\u5904\u7406\u3002"
                ),
            )

    async def _listing(self, listing_id: str) -> Listing:
        if not listing_id:
            return Listing(item_id="", title="未识别商品")
        return await self.connector.fetch_listing(listing_id)


def format_dialogue(history: list[tuple[str, str]], max_chars: int = 4000) -> str:
    lines = []
    for role, text in history[-20:]:
        if role == "buyer":
            label = "买家"
        elif role == "seller":
            label = "卖家"
        else:
            label = "助手"
        lines.append(f"{label}: {text}")
    return "\n".join(lines)[-max_chars:]


def first_two_assistant_rounds(history: list[tuple[str, str]]) -> str | None:
    selected: list[tuple[str, str]] = []
    assistant_count = 0
    for role, text in history:
        selected.append((role, text))
        if role == "assistant":
            assistant_count += 1
            if assistant_count >= 2:
                break
    if assistant_count < 2:
        return None
    return format_dialogue(selected, max_chars=2000)


def current_consultation_history(
    records: list[tuple[str, str, str]],
    gap_seconds: int = NEW_CONSULTATION_GAP_SECONDS,
) -> tuple[list[tuple[str, str]], bool]:
    if not records:
        return [], False

    start_index = 0
    for index in range(1, len(records)):
        previous_time = parse_iso_datetime(records[index - 1][2])
        current_time = parse_iso_datetime(records[index][2])
        if (
            previous_time is not None
            and current_time is not None
            and (current_time - previous_time).total_seconds() > gap_seconds
        ):
            start_index = index

    latest_started_new_consultation = start_index == len(records) - 1
    return (
        [(role, text) for role, text, _created_at in records[start_index:]],
        latest_started_new_consultation,
    )


def parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fallback_summary_from_history(history: list[tuple[str, str]], max_chars: int = 1200) -> str:
    buyer_messages = [text for role, text in history if role == "buyer"]
    if not buyer_messages:
        return "\u4e70\u5bb6\u5df2\u54a8\u8be2\uff0c\u4f46\u6682\u65f6\u6ca1\u6709\u8bc6\u522b\u5230\u660e\u786e\u9700\u6c42\uff0c\u5efa\u8bae\u4eba\u5de5\u63a5\u624b\u786e\u8ba4\u3002"

    latest = "\n".join(f"- {text}" for text in buyer_messages[-5:])
    return (
        "\u4e70\u5bb6\u6700\u8fd1\u8868\u8fbe\uff1a\n"
        f"{latest}\n"
        "\u5efa\u8bae\u4eba\u5de5\u63a5\u624b\u786e\u8ba4\u4efb\u52a1\u7ec6\u8282\u3001\u65f6\u95f4\u8981\u6c42\u548c\u8d39\u7528\u3002"
    )[-max_chars:]


def run(coro) -> None:
    asyncio.run(coro)
