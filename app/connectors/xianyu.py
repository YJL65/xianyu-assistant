from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import struct
import sys
import time
from contextlib import contextmanager, suppress
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..models import IncomingMessage, Listing, ManualSellerMessage
from .base import Connector

ROOT = Path(__file__).resolve().parents[2]
RECONNECT_MIN_SECONDS = 3
RECONNECT_MAX_SECONDS = 60


class XianyuConnectorUnavailable(RuntimeError):
    pass


class XianyuConnector(Connector):
    def __init__(self, cookies: str, vendor_path: Path, node_exe: str = "") -> None:
        if not cookies:
            raise XianyuConnectorUnavailable(
                "XIANYU_COOKIE is not configured. Run `python -m app xianyu-login` "
                "or paste a logged-in goofish.com cookie into .env.local."
            )
        cookie_dict = parse_cookie_string(cookies)
        if not cookie_dict.get("unb"):
            raise XianyuConnectorUnavailable(
                "XIANYU_COOKIE is present but does not include `unb`, so it is not a full "
                "logged-in Xianyu cookie. Run `python -m app xianyu-login` again, or paste "
                "a complete logged-in goofish.com browser cookie into .env.local."
            )
        self.cookies = cookies
        self.vendor_path = vendor_path
        self.node_exe = node_exe
        self.queue: asyncio.Queue[IncomingMessage | ManualSellerMessage] | None = None
        self.websocket = None
        self.peer_by_conversation: dict[str, str] = {}
        self.bot_sent_text_by_conversation: dict[str, list[str]] = {}
        self.manual_takeover_conversations: set[str] = set()
        self.started_at_ms = int(time.time() * 1000)
        self.history_since_ms = self.started_at_ms - 24 * 60 * 60 * 1000
        self._reported_stale_sync = False
        self._requested_recent_conversations = False
        self._recent_conversations_mid = ""
        self._history_request_by_mid: dict[str, str] = {}
        self._live = None
        self._make_text = None

    async def events(self) -> AsyncIterator[IncomingMessage]:
        self.queue = asyncio.Queue()
        self._ensure_live()
        task = asyncio.create_task(self._run_live_forever())
        try:
            while True:
                yield await self.queue.get()
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def send_text(self, conversation_id: str, buyer_id: str, text: str) -> None:
        self._ensure_live()
        if self.websocket is None:
            raise XianyuConnectorUnavailable("Xianyu websocket is not connected yet")
        peer_id = buyer_id or self.peer_by_conversation.get(conversation_id, "")
        if not peer_id:
            raise XianyuConnectorUnavailable("Cannot send reply without buyer id")
        await self._live.send_msg(
            self.websocket,
            conversation_id,
            peer_id,
            self._make_text(text),
        )
        self._remember_bot_sent_text(conversation_id, text)
        print(f"[xianyu -> buyer] conversation={conversation_id} text={text}")

    def _remember_bot_sent_text(self, conversation_id: str, text: str) -> None:
        entries = self.bot_sent_text_by_conversation.setdefault(conversation_id, [])
        entries.append(text)
        del entries[:-20]

    def _is_bot_sent_echo(self, conversation_id: str, text: str) -> bool:
        entries = self.bot_sent_text_by_conversation.get(conversation_id, [])
        if text in entries:
            entries.remove(text)
            return True
        return False

    def manual_takeover_enabled(self, conversation_id: str) -> bool:
        return conversation_id in self.manual_takeover_conversations

    async def fetch_listing(self, listing_id: str) -> Listing:
        self._ensure_live()
        if not listing_id:
            return Listing(item_id="", title="未识别商品")
        try:
            data = await asyncio.to_thread(self._live.xianyu.get_item_info, listing_id)
        except Exception:
            return Listing(item_id=listing_id, title=f"商品 {listing_id}")
        return extract_listing(data, listing_id)

    def _ensure_live(self) -> None:
        if self._live is not None:
            return
        if not self.vendor_path.exists():
            raise XianyuConnectorUnavailable(f"Vendor path does not exist: {self.vendor_path}")
        configure_node_path(self.node_exe)
        with temporary_cwd(self.vendor_path):
            if str(self.vendor_path) not in sys.path:
                sys.path.insert(0, str(self.vendor_path))
            from goofish_live import XianyuLive
            from message import make_text
            from utils.goofish_utils import generate_mid

        connector = self

        class AgentXianyuLive(XianyuLive):
            def user_alive(self):
                if getattr(self, "_agent_user_alive_running", False):
                    return
                self._agent_user_alive_running = True
                return super().user_alive()

            async def heart_beat(self, websocket):
                try:
                    await super().heart_beat(websocket)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(
                        f"[xianyu] heartbeat stopped: {type(exc).__name__}: {exc}; "
                        "closing websocket to reconnect"
                    )
                    connector.websocket = None
                    with suppress(Exception):
                        await websocket.close()

            async def handle_message(self, message, websocket):
                connector.websocket = websocket
                write_ws_event_debug(message)
                if await connector._handle_auxiliary_response(message, self.myid, websocket, generate_mid):
                    return
                await connector._request_recent_conversations_once(message, websocket, generate_mid)
                incoming = parse_incoming_message(message, self.myid)
                if incoming is None:
                    manual = parse_manual_seller_message(message, self.myid)
                    if manual is not None:
                        if connector._is_bot_sent_echo(manual.conversation_id, manual.text):
                            return
                        connector.manual_takeover_conversations.add(manual.conversation_id)
                        print(
                            f"[xianyu <- seller/manual] conversation={manual.conversation_id} "
                            f"text={manual.text}"
                        )
                        if connector.queue is not None:
                            await connector.queue.put(manual)
                        return
                    if is_sync_push(message):
                        write_unparsed_debug(message)
                        text = format_unparsed_sync_push(
                            message,
                            min_timestamp_ms=connector.started_at_ms - 60_000,
                        )
                        if text:
                            print(text)
                        elif not connector._reported_stale_sync:
                            print(format_stale_sync_summary(message, connector.started_at_ms))
                            connector._reported_stale_sync = True
                    return
                print(
                    f"[xianyu <- buyer] conversation={incoming.conversation_id} "
                    f"buyer={incoming.buyer_name} text={incoming.text}"
                )
                connector.peer_by_conversation[incoming.conversation_id] = incoming.buyer_id
                if connector.queue is not None:
                    await connector.queue.put(incoming)

        self._live = AgentXianyuLive(self.cookies)
        self._make_text = make_text

    async def _run_live_forever(self) -> None:
        delay = RECONNECT_MIN_SECONDS
        while True:
            self._ensure_live()
            self.websocket = None
            connected_at = time.monotonic()
            try:
                await self._live.main()
                reason = "closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
            finally:
                self.websocket = None

            reconnect_delay = delay
            if time.monotonic() - connected_at >= 60:
                reconnect_delay = RECONNECT_MIN_SECONDS
            print(f"[xianyu] websocket {reason}; reconnecting in {reconnect_delay}s")
            await asyncio.sleep(reconnect_delay)
            delay = min(reconnect_delay * 2, RECONNECT_MAX_SECONDS)

    async def _request_recent_conversations_once(self, message, websocket, generate_mid_fn) -> None:
        if self._requested_recent_conversations or message.get("lwp") != "/s/vulcan":
            return
        self._requested_recent_conversations = True
        mid = generate_mid_fn()
        self._recent_conversations_mid = mid
        request = {
            "lwp": "/r/Conversation/listNewest",
            "headers": {"mid": mid},
            "body": [0, 50],
        }
        await websocket.send(json.dumps(request))
        print("[xianyu] requested recent conversations for startup catch-up")

    async def _handle_auxiliary_response(self, message, myid: str, websocket, generate_mid_fn) -> bool:
        mid = str((message.get("headers") or {}).get("mid") or "")
        if mid and mid == self._recent_conversations_mid:
            await self._handle_recent_conversations_response(message, websocket, generate_mid_fn)
            return True
        if mid and mid in self._history_request_by_mid:
            cid = self._history_request_by_mid.pop(mid)
            await self._handle_history_response(message, cid, myid)
            return True
        return False

    async def _handle_recent_conversations_response(self, message, websocket, generate_mid_fn) -> None:
        write_json_debug("xianyu_recent_conversations.json", message)
        cids = extract_conversation_ids(message)
        if not cids:
            print("[xianyu] recent conversation list returned no conversation ids")
            return
        for cid in cids[:10]:
            request_mid = generate_mid_fn()
            self._history_request_by_mid[request_mid] = cid
            await websocket.send(
                json.dumps(
                    {
                        "lwp": "/r/MessageManager/listUserMessages",
                        "headers": {"mid": request_mid},
                        "body": [
                            f"{cid}@goofish" if "@" not in cid else cid,
                            False,
                            9007199254740991,
                            20,
                            False,
                        ],
                    }
                )
            )
        print(f"[xianyu] requested recent messages for {min(len(cids), 10)} conversations")

    async def _handle_history_response(self, message, cid: str, myid: str) -> None:
        write_json_debug(f"xianyu_history_{safe_filename(cid)}.json", message)
        history_events = prioritize_manual_history_events(
            parse_history_events(message, cid, myid, self.history_since_ms)
        )
        if not history_events:
            return
        for event in history_events:
            if isinstance(event, ManualSellerMessage):
                print(
                    f"[xianyu <- seller/history] conversation={event.conversation_id} "
                    f"text={event.text}"
                )
            else:
                print(
                    f"[xianyu <- buyer/history] conversation={event.conversation_id} "
                    f"buyer={event.buyer_name} text={event.text}"
                )
                self.peer_by_conversation[event.conversation_id] = event.buyer_id
            if self.queue is not None:
                await self.queue.put(event)


def configure_node_path(node_exe: str = "") -> None:
    candidates = []
    if node_exe:
        candidates.append(Path(node_exe))
    candidates.append(
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
        / "bin"
        / "node.exe"
    )
    for candidate in candidates:
        if candidate.exists():
            os.environ["PATH"] = str(candidate.parent) + os.pathsep + os.environ.get("PATH", "")
            return


def parse_cookie_string(cookies: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in cookies.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed


@contextmanager
def temporary_cwd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def parse_incoming_message(raw_message: dict[str, Any], myid: str) -> IncomingMessage | None:
    for decoded in decode_sync_payloads(raw_message):
        incoming = parse_legacy_reminder(decoded, myid)
        if incoming is not None:
            return incoming
        incoming = parse_push_message(decoded, myid)
        if incoming is not None:
            return incoming
    return None


def parse_manual_seller_message(raw_message: dict[str, Any], myid: str) -> ManualSellerMessage | None:
    for decoded in decode_sync_payloads(raw_message):
        manual = parse_legacy_seller_message(decoded, myid)
        if manual is not None:
            return manual
        manual = parse_push_seller_message(decoded, myid)
        if manual is not None:
            return manual
    return None


def parse_legacy_reminder(decoded: dict[str, Any], myid: str) -> IncomingMessage | None:
    try:
        body = decoded["1"]["10"]
        cid = str(decoded["1"]["2"]).split("@")[0]
    except Exception:
        return None

    buyer_id = str(body.get("senderUserId") or body.get("sender") or "")
    if not buyer_id or buyer_id == str(myid):
        return None

    text = str(body.get("reminderContent") or body.get("content") or "").strip()
    if not text:
        return None

    message_id = (
        str(body.get("messageId") or "")
        or json_field(body.get("extJson"), "messageId")
        or f"{cid}:{buyer_id}:{abs(hash(text))}"
    )
    listing_id = (
        extract_item_id(str(body.get("reminderUrl") or ""))
        or json_field(body.get("extJson"), "itemId")
        or ""
    )

    return IncomingMessage(
        conversation_id=cid,
        buyer_id=buyer_id,
        buyer_name=str(body.get("reminderTitle") or body.get("senderNick") or buyer_id),
        text=text,
        message_id=message_id,
        listing_id=listing_id,
    )


def parse_legacy_seller_message(decoded: dict[str, Any], myid: str) -> ManualSellerMessage | None:
    try:
        body = decoded["1"]["10"]
        cid = str(decoded["1"]["2"]).split("@")[0]
    except Exception:
        return None

    sender_id = str(body.get("senderUserId") or body.get("sender") or "")
    if sender_id != str(myid):
        return None

    text = str(body.get("reminderContent") or body.get("content") or "").strip()
    if not text:
        return None

    message_id = (
        str(body.get("messageId") or "")
        or json_field(body.get("extJson"), "messageId")
        or f"seller:{cid}:{sender_id}:{abs(hash(text))}"
    )
    listing_id = (
        extract_item_id(str(body.get("reminderUrl") or ""))
        or json_field(body.get("extJson"), "itemId")
        or ""
    )

    return ManualSellerMessage(
        conversation_id=cid,
        text=text,
        message_id=f"seller:{message_id}",
        listing_id=listing_id,
    )


def parse_push_message(decoded: dict[str, Any], myid: str) -> IncomingMessage | None:
    for message in iter_message_objects(decoded):
        sender = message.get("senderInfo") or {}
        session = message.get("sessionInfo") or {}
        reminder = message.get("reminder") or {}
        content = message.get("content") or {}

        buyer_id = str(sender.get("userId") or sender.get("uid") or "")
        if not buyer_id or buyer_id == str(myid):
            continue

        text = (
            str(reminder.get("content") or "").strip()
            or content_text(content)
            or str(message.get("summary") or "").strip()
        )
        if not text:
            continue

        cid = str(
            session.get("sessionId")
            or message.get("sessionId")
            or message.get("cid")
            or ""
        ).split("@")[0]
        if not cid:
            continue

        item_info = session.get("itemInfo") or message.get("itemInfo") or {}
        listing_id = (
            str(item_info.get("itemId") or "")
            or extract_item_id(str(reminder.get("url") or ""))
            or json_field(message.get("extJson"), "itemId")
            or ""
        )
        message_id = (
            str(message.get("messageId") or "")
            or json_field(message.get("extJson"), "messageId")
            or f"{cid}:{buyer_id}:{abs(hash(text))}"
        )
        buyer_name = str(
            reminder.get("title")
            or sender.get("nick")
            or sender.get("nickname")
            or sender.get("userNick")
            or buyer_id
        )
        return IncomingMessage(
            conversation_id=cid,
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            text=text,
            message_id=message_id,
            listing_id=listing_id,
        )
    return None


def parse_push_seller_message(decoded: dict[str, Any], myid: str) -> ManualSellerMessage | None:
    for message in iter_message_objects(decoded):
        sender = message.get("senderInfo") or {}
        session = message.get("sessionInfo") or {}
        reminder = message.get("reminder") or {}
        content = message.get("content") or {}

        sender_id = str(sender.get("userId") or sender.get("uid") or "")
        if sender_id != str(myid):
            continue

        text = (
            str(reminder.get("content") or "").strip()
            or content_text(content)
            or str(message.get("summary") or "").strip()
        )
        if not text:
            continue

        cid = str(
            session.get("sessionId")
            or message.get("sessionId")
            or message.get("cid")
            or ""
        ).split("@")[0]
        if not cid:
            continue

        item_info = session.get("itemInfo") or message.get("itemInfo") or {}
        listing_id = (
            str(item_info.get("itemId") or "")
            or extract_item_id(str(reminder.get("url") or ""))
            or json_field(message.get("extJson"), "itemId")
            or ""
        )
        message_id = (
            str(message.get("messageId") or "")
            or json_field(message.get("extJson"), "messageId")
            or f"{cid}:{sender_id}:{abs(hash(text))}"
        )
        return ManualSellerMessage(
            conversation_id=cid,
            text=text,
            message_id=f"seller:{message_id}",
            listing_id=listing_id,
        )
    return None


def parse_history_messages(
    raw_message: dict[str, Any],
    cid: str,
    myid: str,
    since_ms: int,
) -> list[IncomingMessage]:
    return [
        event
        for event in parse_history_events(raw_message, cid, myid, since_ms)
        if isinstance(event, IncomingMessage)
    ]


def parse_history_events(
    raw_message: dict[str, Any],
    cid: str,
    myid: str,
    since_ms: int,
) -> list[IncomingMessage | ManualSellerMessage]:
    events: list[tuple[int, IncomingMessage | ManualSellerMessage]] = []
    for model in iter_user_message_models(raw_message):
        message = model.get("message") or model
        if not isinstance(message, dict):
            continue
        extension = message.get("extension") or message.get("ext") or {}
        if not isinstance(extension, dict):
            extension = {}

        created_ms = numeric_timestamp(
            find_first(message, ["sendTime", "timeStamp", "timestamp", "createTime"])
        )
        if not created_ms or created_ms < since_ms:
            continue

        buyer_id = string_value(
            extension.get("senderUserId")
            or extension.get("sender")
            or find_first(message, ["senderUserId", "sender"])
        )
        if not buyer_id:
            continue

        text = content_text(message.get("content") or {}) or string_value(
            extension.get("reminderContent") or find_first(message, ["reminderContent"])
        )
        if not text:
            continue

        message_id = (
            string_value(message.get("messageId"))
            or string_value(extension.get("messageId"))
            or json_field(extension.get("extJson"), "messageId")
            or f"{cid}:{buyer_id}:{created_ms}:{abs(hash(text))}"
        )
        listing_id = (
            extract_item_id(string_value(extension.get("reminderUrl")))
            or json_field(extension.get("extJson"), "itemId")
            or string_value(find_first(message, ["itemId"]))
        )
        created_at = datetime.fromtimestamp(created_ms / 1000).isoformat()
        if buyer_id == str(myid):
            events.append(
                (
                    created_ms,
                    ManualSellerMessage(
                        conversation_id=str(cid).split("@")[0],
                        text=text,
                        message_id=f"seller:{message_id}",
                        listing_id=listing_id,
                        created_at=created_at,
                    ),
                )
            )
            continue

        events.append(
            (
                created_ms,
                IncomingMessage(
                    conversation_id=str(cid).split("@")[0],
                    buyer_id=buyer_id,
                    buyer_name=string_value(
                        extension.get("reminderTitle")
                        or extension.get("senderNick")
                        or find_first(message, ["senderNick", "nick"])
                        or buyer_id
                    ),
                    text=text,
                    message_id=message_id,
                    listing_id=listing_id,
                    created_at=created_at,
                ),
            )
        )
    return [event for _created_ms, event in sorted(events, key=lambda item: item[0])]


def prioritize_manual_history_events(
    events: list[IncomingMessage | ManualSellerMessage],
) -> list[IncomingMessage | ManualSellerMessage]:
    manual = [event for event in events if isinstance(event, ManualSellerMessage)]
    incoming = [event for event in events if isinstance(event, IncomingMessage)]
    return [*manual, *incoming]


def iter_user_message_models(value: Any):
    if isinstance(value, dict):
        message = value.get("message")
        if isinstance(message, dict) and (
            isinstance(message.get("content"), dict) or isinstance(message.get("extension"), dict)
        ):
            yield value
        for child in value.values():
            yield from iter_user_message_models(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_user_message_models(child)


def extract_conversation_ids(value: Any) -> list[str]:
    found: list[str] = []
    collect_conversation_ids(value, found)
    output = []
    seen = set()
    for cid in found:
        normalized = cid.split("@")[0].strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def collect_conversation_ids(value: Any, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"cid", "appCid", "sessionId", "conversationId"}:
                cid = string_value(item)
                if cid:
                    found.append(cid)
            collect_conversation_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            collect_conversation_ids(item, found)
    elif isinstance(value, str) and "@goofish" in value:
        found.append(value)


def decode_sync_payload(raw_message: dict[str, Any]) -> Any | None:
    payloads = decode_sync_payloads(raw_message)
    return payloads[0] if payloads else None


def decode_sync_payloads(raw_message: dict[str, Any]) -> list[Any]:
    decoded = []
    for raw_data in iter_sync_data(raw_message):
        payload, _errors = decode_sync_data(raw_data)
        if payload is not None:
            decoded.append(payload)
    return decoded


def iter_sync_data(raw_message: dict[str, Any]):
    try:
        entries = raw_message["body"]["syncPushPackage"]["data"]
    except Exception:
        return
    for entry in entries:
        if isinstance(entry, dict) and "data" in entry:
            yield entry["data"]
        else:
            yield entry


def decode_sync_data(raw_data: Any) -> tuple[Any | None, list[str]]:
    errors: list[str] = []
    if isinstance(raw_data, (dict, list)):
        return normalize_keys(raw_data), errors
    if not isinstance(raw_data, str):
        return None, [f"unsupported data type: {type(raw_data).__name__}"]

    try:
        return normalize_keys(json.loads(raw_data)), errors
    except Exception as exc:
        errors.append(f"json: {type(exc).__name__}: {exc}")

    try:
        return normalize_keys(json.loads(decode_base64_text(raw_data))), errors
    except Exception as exc:
        errors.append(f"base64 json: {type(exc).__name__}: {exc}")

    try:
        return normalize_keys(unpack_messagepack_base64(raw_data)), errors
    except Exception as exc:
        errors.append(f"messagepack: {type(exc).__name__}: {exc}")

    try:
        from utils.goofish_utils import decrypt
    except Exception as exc:
        errors.append(f"decrypt import: {type(exc).__name__}: {exc}")
        return None, errors

    try:
        return normalize_keys(json.loads(decrypt(raw_data))), errors
    except Exception as exc:
        errors.append(f"decrypt: {type(exc).__name__}: {exc}")
        return None, errors


def decode_base64_text(value: str) -> str:
    return decode_base64_bytes(value).decode("utf-8")


def decode_base64_bytes(value: str) -> bytes:
    cleaned = re.sub(r"[^A-Za-z0-9+/=]", "", value)
    if not cleaned:
        raise ValueError("empty base64 payload")
    missing_padding = (-len(cleaned)) % 4
    if missing_padding:
        cleaned += "=" * missing_padding
    return base64.b64decode(cleaned)


def unpack_messagepack_base64(value: str) -> Any:
    raw = decode_base64_bytes(value)
    unpacker = MessagePackUnpacker(raw)
    result = unpacker.unpack()
    if not unpacker.finished:
        raise ValueError("messagepack data has trailing bytes")
    return result


class MessagePackUnpacker:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.index = 0

    @property
    def finished(self) -> bool:
        return self.index == len(self.data)

    def read(self, size: int) -> bytes:
        end = self.index + size
        if end > len(self.data):
            raise ValueError("unexpected end of messagepack data")
        chunk = self.data[self.index:end]
        self.index = end
        return chunk

    def number(self, fmt: str, size: int) -> Any:
        return struct.unpack(fmt, self.read(size))[0]

    def unpack(self) -> Any:
        token = self.number(">B", 1)
        if token <= 0x7F:
            return token
        if token >= 0xE0:
            return token - 0x100
        if 0x80 <= token <= 0x8F:
            return self.map(token & 0x0F)
        if 0x90 <= token <= 0x9F:
            return self.array(token & 0x0F)
        if 0xA0 <= token <= 0xBF:
            return self.string(token & 0x1F)
        if token == 0xC0:
            return None
        if token == 0xC2:
            return False
        if token == 0xC3:
            return True
        if token == 0xC4:
            return self.binary(self.number(">B", 1))
        if token == 0xC5:
            return self.binary(self.number(">H", 2))
        if token == 0xC6:
            return self.binary(self.number(">I", 4))
        if token == 0xCA:
            return self.number(">f", 4)
        if token == 0xCB:
            return self.number(">d", 8)
        if token == 0xCC:
            return self.number(">B", 1)
        if token == 0xCD:
            return self.number(">H", 2)
        if token == 0xCE:
            return self.number(">I", 4)
        if token == 0xCF:
            return self.number(">Q", 8)
        if token == 0xD0:
            return self.number(">b", 1)
        if token == 0xD1:
            return self.number(">h", 2)
        if token == 0xD2:
            return self.number(">i", 4)
        if token == 0xD3:
            return self.number(">q", 8)
        if token == 0xD4:
            return self.ext(1)
        if token == 0xD5:
            return self.ext(2)
        if token == 0xD6:
            return self.ext(4)
        if token == 0xD7:
            return self.ext(8)
        if token == 0xD8:
            return self.ext(16)
        if token == 0xD9:
            return self.string(self.number(">B", 1))
        if token == 0xDA:
            return self.string(self.number(">H", 2))
        if token == 0xDB:
            return self.string(self.number(">I", 4))
        if token == 0xDC:
            return self.array(self.number(">H", 2))
        if token == 0xDD:
            return self.array(self.number(">I", 4))
        if token == 0xDE:
            return self.map(self.number(">H", 2))
        if token == 0xDF:
            return self.map(self.number(">I", 4))
        raise ValueError(f"unknown messagepack token 0x{token:02x}")

    def string(self, size: int) -> str:
        return self.read(size).decode("utf-8", errors="replace")

    def binary(self, size: int) -> str:
        data = self.read(size)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(data).decode("ascii")

    def array(self, size: int) -> list[Any]:
        return [self.unpack() for _ in range(size)]

    def map(self, size: int) -> dict[Any, Any]:
        return {self.unpack(): self.unpack() for _ in range(size)}

    def ext(self, size: int) -> dict[str, Any]:
        ext_type = self.number(">b", 1)
        data = self.read(size)
        return {"__ext_type__": ext_type, "__ext_data__": data.hex()}


def normalize_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): normalize_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_keys(item) for item in value]
    return value


def is_sync_push(raw_message: dict[str, Any]) -> bool:
    try:
        raw_message["body"]["syncPushPackage"]["data"][0]["data"]
        return True
    except Exception:
        return False


def iter_message_objects(value: Any):
    if isinstance(value, dict):
        push = value.get("pushMessage")
        if isinstance(push, dict) and isinstance(push.get("message"), dict):
            yield push["message"]
        if isinstance(value.get("senderInfo"), dict) and isinstance(value.get("sessionInfo"), dict):
            yield value
        for child in value.values():
            yield from iter_message_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_message_objects(child)


def nested_text(value: Any) -> str:
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, dict):
            candidate = text.get("text")
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        if isinstance(text, str) and text.strip():
            return text.strip()
        for child in value.values():
            found = nested_text(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = nested_text(child)
            if found:
                return found
    return ""


def content_text(content: Any) -> str:
    text = nested_text(content)
    if text:
        return text
    if not isinstance(content, dict):
        return ""
    custom = content.get("custom")
    if not isinstance(custom, dict):
        return ""
    return custom_data_text(custom.get("data"))


def custom_data_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return nested_text(value)
    if not isinstance(value, str):
        return ""

    candidates = [value.strip()]
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
        candidates.append(decoded.strip())
    except Exception:
        pass

    for candidate in candidates:
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        text = nested_text(payload)
        if text:
            return text
    return ""


def write_unparsed_debug(raw_message: dict[str, Any]) -> None:
    try:
        entries = []
        for raw_data in iter_sync_data(raw_message):
            decoded, errors = decode_sync_data(raw_data)
            entries.append(
                {
                    "raw": summarize_sync_data(raw_data),
                    "decoded": decoded,
                    "errors": errors,
                }
            )
        debug_dir = ROOT / "data"
        debug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "raw_lwp": raw_message.get("lwp"),
            "raw_header_keys": sorted((raw_message.get("headers") or {}).keys()),
            "data_count": len(entries),
            "entries": entries,
        }
        (debug_dir / "xianyu_last_unparsed.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[xianyu] failed to write unparsed debug snapshot: {exc}")


def write_ws_event_debug(raw_message: dict[str, Any]) -> None:
    try:
        debug_dir = ROOT / "data"
        debug_dir.mkdir(parents=True, exist_ok=True)
        body = raw_message.get("body")
        payload = {
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "lwp": raw_message.get("lwp"),
            "header_keys": sorted((raw_message.get("headers") or {}).keys()),
            "body_type": type(body).__name__,
            "body_keys": sorted(body.keys()) if isinstance(body, dict) else [],
            "sync_data_count": len(list(iter_sync_data(raw_message))) if is_sync_push(raw_message) else 0,
        }
        (debug_dir / "xianyu_last_ws_event.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with (debug_dir / "xianyu_ws_events.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def write_json_debug(filename: str, value: Any) -> None:
    try:
        debug_dir = ROOT / "data"
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / filename).write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return cleaned[:80] or "unknown"


def summarize_sync_data(raw_data: Any) -> dict[str, Any]:
    if not isinstance(raw_data, str):
        return {"type": type(raw_data).__name__}
    summary: dict[str, Any] = {
        "type": "str",
        "length": len(raw_data),
        "prefix": raw_data[:160],
        "suffix": raw_data[-160:] if len(raw_data) > 160 else "",
    }
    if len(raw_data) <= 20000:
        summary["value"] = raw_data
    return summary


def format_unparsed_sync_push(
    raw_message: dict[str, Any],
    max_entries: int = 1,
    min_timestamp_ms: int = 0,
) -> str:
    entries = []
    seen = set()
    for index, raw_data in enumerate(iter_sync_data(raw_message), start=1):
        decoded, errors = decode_sync_data(raw_data)
        entry = summarize_decoded_sync_entry(index, raw_data, decoded, errors)
        key = (
            entry.get("session_id"),
            entry.get("item_id"),
            tuple(entry.get("texts", [])[:5]),
            tuple(entry.get("errors", [])[:2]),
        )
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)

    fresh_entries = [
        entry
        for entry in entries
        if not min_timestamp_ms or (entry.get("timestamp") or 0) >= min_timestamp_ms
    ]
    if min_timestamp_ms and not fresh_entries:
        write_readable_sync_snapshots(raw_message, entries, [])
        return ""

    latest_entries = latest_sync_entries(fresh_entries, max_entries)
    omitted = max(0, len(entries) - len(latest_entries))
    readable = render_sync_entries(raw_message, latest_entries, omitted, latest_only=True)
    readable_all = render_sync_entries(raw_message, entries, 0, latest_only=False)
    write_readable_sync_snapshots(raw_message, entries, latest_entries, readable, readable_all)
    return readable


def write_readable_sync_snapshots(
    raw_message: dict[str, Any],
    all_entries: list[dict[str, Any]],
    visible_entries: list[dict[str, Any]],
    readable: str | None = None,
    readable_all: str | None = None,
) -> None:
    if readable is None:
        readable = render_sync_entries(raw_message, visible_entries, 0, latest_only=True)
    if readable_all is None:
        readable_all = render_sync_entries(raw_message, all_entries, 0, latest_only=False)
    try:
        debug_dir = ROOT / "data"
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "xianyu_last_unparsed_readable.txt").write_text(
            readable,
            encoding="utf-8",
        )
        (debug_dir / "xianyu_last_unparsed_readable_all.txt").write_text(
            readable_all,
            encoding="utf-8",
        )
    except Exception:
        pass


def summarize_decoded_sync_entry(
    index: int,
    raw_data: Any,
    decoded: Any,
    errors: list[str],
) -> dict[str, Any]:
    texts = unique_texts(
        collect_values(decoded, ["chatScrip", "reminderContent", "content", "text"])
        if decoded is not None
        else []
    )
    return {
        "index": index,
        "timestamp": numeric_timestamp(find_first(decoded, ["arouseTimeStamp", "createTime", "timeStamp", "timestamp"]))
        if decoded is not None
        else 0,
        "session_id": string_value(find_first(decoded, ["sessionId"])) if decoded is not None else "",
        "item_id": string_value(find_first(decoded, ["itemId"])) if decoded is not None else "",
        "item_title": string_value(find_first(decoded, ["itemTitle"])) if decoded is not None else "",
        "chat_type": string_value(find_first(decoded, ["chatType"])) if decoded is not None else "",
        "increment_type": string_value(find_first(decoded, ["incrementType"])) if decoded is not None else "",
        "content_type": string_value(find_first(decoded, ["contentType"])) if decoded is not None else "",
        "texts": texts[:12],
        "raw": summarize_sync_data(raw_data),
        "decoded": decoded,
        "errors": errors,
    }


def format_stale_sync_summary(raw_message: dict[str, Any], started_at_ms: int) -> str:
    entries = []
    for index, raw_data in enumerate(iter_sync_data(raw_message), start=1):
        decoded, errors = decode_sync_data(raw_data)
        entries.append(summarize_decoded_sync_entry(index, raw_data, decoded, errors))
    latest = latest_sync_entries(entries, 1)
    latest_time = format_timestamp(latest[0]["timestamp"]) if latest and latest[0].get("timestamp") else "unknown"
    started = format_timestamp(started_at_ms)
    return (
        "[xianyu] ignored startup history sync; waiting for new messages\n"
        f"lwp={raw_message.get('lwp')} data_count={len(list(iter_sync_data(raw_message)))} "
        f"latest_history_time={latest_time} listener_started={started}\n"
        f"event log: {ROOT / 'data' / 'xianyu_ws_events.log'}"
    )


def render_sync_entries(
    raw_message: dict[str, Any],
    entries: list[dict[str, Any]],
    omitted: int = 0,
    latest_only: bool = False,
) -> str:
    lines = [
        "[xianyu] received an unparsed sync push",
        f"lwp={raw_message.get('lwp')} data_count={len(list(iter_sync_data(raw_message)))}",
    ]
    if latest_only:
        lines.append("showing latest entry only")
    if not entries:
        lines.append("No sync data entries were found.")
        return "\n".join(lines)

    for entry in entries:
        headline = f"#{entry['index']} session={entry.get('session_id') or '-'}"
        if entry.get("item_title"):
            headline += f" item={entry['item_title']}"
        if entry.get("item_id"):
            headline += f" itemId={entry['item_id']}"
        meta = []
        if entry.get("chat_type"):
            meta.append(f"chatType={entry['chat_type']}")
        if entry.get("increment_type"):
            meta.append(f"incrementType={entry['increment_type']}")
        if entry.get("content_type"):
            meta.append(f"contentType={entry['content_type']}")
        if meta:
            headline += " " + " ".join(meta)
        lines.append(headline)
        if entry.get("timestamp"):
            lines.append(f"  time: {format_timestamp(entry['timestamp'])}")

        texts = entry.get("texts") or []
        if texts:
            lines.append("  text: " + " | ".join(texts[:8]))
        elif entry.get("decoded") is not None:
            lines.append("  decoded: " + compact_json(entry["decoded"], 420))
        else:
            errors = "; ".join(entry.get("errors") or [])
            lines.append("  decode errors: " + errors[:420])
            raw = entry.get("raw") or {}
            lines.append(f"  raw prefix: {raw.get('prefix', '')}")

    if omitted:
        lines.append(f"... {omitted} older unique entries omitted")
    lines.append(f"readable snapshot: {ROOT / 'data' / 'xianyu_last_unparsed_readable.txt'}")
    if latest_only:
        lines.append(f"all entries snapshot: {ROOT / 'data' / 'xianyu_last_unparsed_readable_all.txt'}")
    lines.append(f"json snapshot: {ROOT / 'data' / 'xianyu_last_unparsed.json'}")
    return "\n".join(lines)


def latest_sync_entries(entries: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if not entries:
        return []
    text_entries = [entry for entry in entries if entry.get("texts")]
    candidates = text_entries or entries
    return sorted(
        candidates,
        key=lambda entry: (
            entry.get("timestamp") or 0,
            1 if entry.get("texts") else 0,
            entry.get("index") or 0,
        ),
        reverse=True,
    )[:count]


def numeric_timestamp(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def format_timestamp(value: int) -> str:
    seconds = value / 1000 if value > 10_000_000_000 else value
    try:
        return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def collect_values(value: Any, keys: list[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys:
                if isinstance(item, str) and item.strip():
                    found.append(item.strip())
                elif isinstance(item, (int, float)):
                    found.append(str(item))
                elif isinstance(item, dict):
                    nested = item.get("text") or item.get("content")
                    if isinstance(nested, str) and nested.strip():
                        found.append(nested.strip())
            found.extend(collect_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_values(item, keys))
    return found


def unique_texts(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        text = re.sub(r"\s+", " ", value).strip()
        if not text or text in seen:
            continue
        if len(text) > 100:
            continue
        seen.add(text)
        output.append(text)
    return output


def compact_json(value: Any, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= max_chars else text[:max_chars] + "..."


def string_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def extract_item_id(text: str) -> str:
    if not text:
        return ""
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    for key in ("itemId", "item_id", "id"):
        value = query.get(key)
        if value:
            return value[0]
    match = re.search(r"(?:itemId|item_id|id)=([0-9]+)", text)
    return match.group(1) if match else ""


def json_field(value: Any, key: str) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ""
    if isinstance(value, dict):
        found = find_first(value, [key])
        return str(found) if found is not None else ""
    return ""


def extract_listing(data: dict[str, Any], item_id: str) -> Listing:
    title = find_first(data, ["title", "itemTitle", "subject"]) or f"商品 {item_id}"
    description = find_first(data, ["desc", "description", "itemDesc", "subtitle"]) or ""
    return Listing(
        item_id=item_id,
        title=str(title),
        description=str(description),
        url=f"https://www.goofish.com/item?id={item_id}",
    )


def find_first(value: Any, keys: list[str]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, (str, int, float)) and str(item).strip():
                return item
        for item in value.values():
            found = find_first(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_first(item, keys)
            if found is not None:
                return found
    return None
