import base64
import json
import unittest

from app.connectors.xianyu import (
    content_text,
    decode_sync_data,
    decode_sync_payload,
    extract_conversation_ids,
    extract_item_id,
    extract_listing,
    format_unparsed_sync_push,
    json_field,
    parse_history_messages,
    parse_history_events,
    parse_incoming_message,
    parse_manual_seller_message,
    prioritize_manual_history_events,
)


def pack_msgpack(value):
    if value is None:
        return b"\xc0"
    if value is False:
        return b"\xc2"
    if value is True:
        return b"\xc3"
    if isinstance(value, int):
        if 0 <= value <= 0x7F:
            return bytes([value])
        if -32 <= value < 0:
            return bytes([0x100 + value])
        if 0 <= value <= 0xFF:
            return b"\xcc" + value.to_bytes(1, "big")
        if 0 <= value <= 0xFFFF:
            return b"\xcd" + value.to_bytes(2, "big")
        return b"\xce" + value.to_bytes(4, "big")
    if isinstance(value, str):
        data = value.encode("utf-8")
        if len(data) < 32:
            return bytes([0xA0 + len(data)]) + data
        if len(data) <= 0xFF:
            return b"\xd9" + len(data).to_bytes(1, "big") + data
        return b"\xda" + len(data).to_bytes(2, "big") + data
    if isinstance(value, list):
        if len(value) < 16:
            prefix = bytes([0x90 + len(value)])
        else:
            prefix = b"\xdc" + len(value).to_bytes(2, "big")
        return prefix + b"".join(pack_msgpack(item) for item in value)
    if isinstance(value, dict):
        if len(value) < 16:
            prefix = bytes([0x80 + len(value)])
        else:
            prefix = b"\xde" + len(value).to_bytes(2, "big")
        return prefix + b"".join(
            pack_msgpack(key) + pack_msgpack(item) for key, item in value.items()
        )
    raise TypeError(type(value))


class XianyuConnectorTests(unittest.TestCase):
    def test_extracts_item_id_from_url(self):
        self.assertEqual(
            extract_item_id("fleamarket://message_chat?itemId=900052644277&peerUserId=1"),
            "900052644277",
        )

    def test_reads_json_field(self):
        self.assertEqual(json_field('{"messageId":"abc","itemId":"123"}', "itemId"), "123")

    def test_extracts_listing_from_nested_data(self):
        listing = extract_listing(
            {"data": {"itemDO": {"title": "服务标题", "desc": "服务说明"}}},
            "123",
        )
        self.assertEqual(listing.title, "服务标题")
        self.assertEqual(listing.description, "服务说明")

    def test_parses_legacy_sync_reminder(self):
        decoded = {
            "1": {
                "2": "cid-1@goofish",
                "10": {
                    "senderUserId": "buyer-1",
                    "reminderTitle": "买家A",
                    "reminderContent": "宝贝还有吗？",
                    "reminderUrl": "fleamarket://message_chat?itemId=900052644277",
                    "extJson": '{"messageId":"msg-1"}',
                },
            }
        }
        raw = {
            "body": {
                "syncPushPackage": {
                    "data": [
                        {
                            "data": json.dumps(decoded, ensure_ascii=False),
                        }
                    ],
                }
            }
        }

        incoming = parse_incoming_message(raw, "seller-1")

        self.assertIsNotNone(incoming)
        self.assertEqual(incoming.conversation_id, "cid-1")
        self.assertEqual(incoming.buyer_id, "buyer-1")
        self.assertEqual(incoming.text, "宝贝还有吗？")
        self.assertEqual(incoming.listing_id, "900052644277")

    def test_parses_push_message_reminder(self):
        decoded = {
            "pushMessage": {
                "message": {
                    "messageId": "msg-2",
                    "senderInfo": {"userId": "buyer-2", "nick": "买家B"},
                    "sessionInfo": {
                        "sessionId": "cid-2@goofish",
                        "itemInfo": {"itemId": "900052644278"},
                    },
                    "reminder": {
                        "title": "买家B",
                        "content": "宝贝还有吗？",
                    },
                },
            },
        }
        raw = {
            "body": {
                "syncPushPackage": {
                    "data": [
                        {
                            "data": json.dumps(decoded, ensure_ascii=False),
                        }
                    ],
                }
            }
        }

        incoming = parse_incoming_message(raw, "seller-1")

        self.assertIsNotNone(incoming)
        self.assertEqual(incoming.conversation_id, "cid-2")
        self.assertEqual(incoming.buyer_id, "buyer-2")
        self.assertEqual(incoming.text, "宝贝还有吗？")
        self.assertEqual(incoming.listing_id, "900052644278")

    def test_parses_manual_seller_push_message(self):
        decoded = {
            "pushMessage": {
                "message": {
                    "messageId": "msg-seller-1",
                    "senderInfo": {"userId": "seller-1", "nick": "卖家"},
                    "sessionInfo": {
                        "sessionId": "cid-seller@goofish",
                        "itemInfo": {"itemId": "900052644280"},
                    },
                    "content": {"contentType": 1, "text": {"text": "我人工回复一下。"}},
                },
            },
        }
        raw = {
            "body": {
                "syncPushPackage": {
                    "data": [
                        {
                            "data": json.dumps(decoded, ensure_ascii=False),
                        }
                    ],
                }
            }
        }

        manual = parse_manual_seller_message(raw, "seller-1")

        self.assertIsNotNone(manual)
        self.assertEqual(manual.conversation_id, "cid-seller")
        self.assertEqual(manual.text, "我人工回复一下。")
        self.assertEqual(manual.listing_id, "900052644280")

    def test_reads_base64_custom_text_content(self):
        payload = {"contentType": 1, "text": {"text": "宝贝还有吗？"}}
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")

        text = content_text(
            {
                "contentType": 101,
                "custom": {
                    "data": encoded,
                },
            }
        )

        self.assertEqual(text, "宝贝还有吗？")

    def test_parses_messagepack_sync_reminder(self):
        decoded = {
            1: {
                2: "cid-3@goofish",
                10: {
                    "senderUserId": "buyer-3",
                    "reminderTitle": "买家C",
                    "reminderContent": "宝贝还有吗？",
                    "reminderUrl": "fleamarket://message_chat?itemId=900052644279",
                    "extJson": '{"messageId":"msg-3"}',
                },
            }
        }
        raw = {
            "body": {
                "syncPushPackage": {
                    "data": [
                        {
                            "data": base64.b64encode(pack_msgpack(decoded)).decode("ascii"),
                        }
                    ],
                }
            }
        }

        incoming = parse_incoming_message(raw, "seller-1")

        self.assertIsNotNone(incoming)
        self.assertEqual(incoming.conversation_id, "cid-3")
        self.assertEqual(incoming.buyer_id, "buyer-3")
        self.assertEqual(incoming.text, "宝贝还有吗？")
        self.assertEqual(incoming.listing_id, "900052644279")

    def test_decodes_compact_messagepack_payload(self):
        raw = {
            "body": {
                "syncPushPackage": {
                    "data": [
                        {
                            "data": (
                                "hAGzNDc5ODMzODkwOTZAZ29vZmlzaAIBA4KrcmVkUmVtaW5kZX"
                                "Ky562J5b6F5Lmw5a625LuY5qy+sHJlZFJlbWluZGVyU3R5bGWhMQTPAAABlbMlNng="
                            ),
                        }
                    ],
                }
            }
        }

        payload = decode_sync_payload(raw)

        self.assertEqual(payload["1"], "47983389096@goofish")
        self.assertEqual(payload["3"]["redReminderStyle"], "1")

    def test_decodes_base64_json_session_arouse(self):
        payload = {
            "chatType": 1,
            "incrementType": 1,
            "operation": {
                "content": {
                    "contentType": 8,
                    "sessionArouse": {
                        "sessionArouseInfo": {
                            "arouseChatScriptInfo": [
                                {"chatScrip": "单独账号吗？"},
                                {"chatScrip": "API可以使用吗？"},
                            ]
                        }
                    },
                },
                "sessionInfo": {
                    "extensions": {
                        "itemTitle": "Gemini学生认证代订阅",
                        "itemId": "1006624829907",
                    },
                    "sessionId": "57027900443",
                },
            },
            "sessionId": "57027900443",
        }
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

        decoded, errors = decode_sync_data(encoded)

        self.assertEqual(decoded["sessionId"], "57027900443")
        self.assertEqual(decoded["operation"]["sessionInfo"]["extensions"]["itemId"], "1006624829907")
        self.assertTrue(errors)

    def test_formats_unparsed_session_arouse_for_terminal(self):
        payload = {
            "chatType": 1,
            "incrementType": 1,
            "operation": {
                "content": {
                    "contentType": 8,
                    "sessionArouse": {
                        "sessionArouseInfo": {
                            "arouseChatScriptInfo": [
                                {"chatScrip": "单独账号吗？"},
                            ]
                        }
                    },
                },
                "sessionInfo": {
                    "extensions": {
                        "itemTitle": "Gemini学生认证代订阅",
                        "itemId": "1006624829907",
                    },
                    "sessionId": "57027900443",
                },
            },
            "sessionId": "57027900443",
        }
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        raw = {
            "lwp": "/s/vulcan",
            "body": {
                "syncPushPackage": {
                    "data": [{"data": encoded}],
                }
            },
        }

        text = format_unparsed_sync_push(raw)

        self.assertIn("Gemini学生认证代订阅", text)
        self.assertIn("单独账号吗？", text)
        self.assertIn("57027900443", text)

    def test_formats_only_latest_unparsed_entry_for_terminal(self):
        older = {
            "operation": {
                "content": {
                    "contentType": 8,
                    "sessionArouse": {
                        "sessionArouseInfo": {
                            "arouseTimeStamp": 1000,
                            "arouseChatScriptInfo": [{"chatScrip": "旧问题？"}],
                        }
                    },
                },
                "sessionInfo": {
                    "extensions": {"itemTitle": "旧商品", "itemId": "1"},
                    "sessionId": "old",
                },
            },
            "sessionId": "old",
        }
        newer = {
            "operation": {
                "content": {
                    "contentType": 8,
                    "sessionArouse": {
                        "sessionArouseInfo": {
                            "arouseTimeStamp": 2000,
                            "arouseChatScriptInfo": [{"chatScrip": "新问题？"}],
                        }
                    },
                },
                "sessionInfo": {
                    "extensions": {"itemTitle": "新商品", "itemId": "2"},
                    "sessionId": "new",
                },
            },
            "sessionId": "new",
        }
        raw = {
            "lwp": "/s/vulcan",
            "body": {
                "syncPushPackage": {
                    "data": [
                        {"data": base64.b64encode(json.dumps(older).encode("utf-8")).decode("ascii")},
                        {"data": base64.b64encode(json.dumps(newer).encode("utf-8")).decode("ascii")},
                    ],
                }
            },
        }

        text = format_unparsed_sync_push(raw)

        self.assertIn("新商品", text)
        self.assertIn("新问题？", text)
        self.assertNotIn("旧商品", text)
        self.assertNotIn("旧问题？", text)
        self.assertIn("older unique entries omitted", text)

    def test_extracts_conversation_ids_from_recent_conversation_response(self):
        response = {
            "body": [
                {"singleChatUserConversation": {"cid": "123@goofish"}},
                {"sessionInfo": {"sessionId": "456"}},
                {"conversationId": "789@goofish"},
            ]
        }

        self.assertEqual(extract_conversation_ids(response), ["123", "456", "789"])

    def test_parses_recent_history_buyer_message(self):
        content = {
            "contentType": 1,
            "text": {"text": "宝贝还有吗？"},
        }
        response = {
            "body": {
                "userMessageModels": [
                    {
                        "message": {
                            "messageId": "msg-today",
                            "sendTime": 1780369200000,
                            "extension": {
                                "senderUserId": "buyer-1",
                                "reminderTitle": "买家A",
                                "reminderUrl": "fleamarket://message_chat?itemId=10001",
                            },
                            "content": {
                                "custom": {
                                    "data": base64.b64encode(
                                        json.dumps(content, ensure_ascii=False).encode("utf-8")
                                    ).decode("ascii")
                                }
                            },
                        }
                    }
                ]
            }
        }

        incoming = parse_history_messages(response, "cid-1", "seller-1", 1780360000000)

        self.assertEqual(len(incoming), 1)
        self.assertEqual(incoming[0].text, "宝贝还有吗？")
        self.assertEqual(incoming[0].buyer_id, "buyer-1")
        self.assertEqual(incoming[0].listing_id, "10001")

    def test_parses_recent_history_seller_message_and_prioritizes_manual(self):
        buyer_content = {
            "contentType": 1,
            "text": {"text": "现在还能做PPT吗？"},
        }
        seller_content = {
            "contentType": 1,
            "text": {"text": "可以的，你发资料。"},
        }
        response = {
            "body": {
                "userMessageModels": [
                    {
                        "message": {
                            "messageId": "msg-buyer-history",
                            "sendTime": 1780369200000,
                            "extension": {
                                "senderUserId": "buyer-1",
                                "reminderTitle": "买家A",
                                "reminderUrl": "fleamarket://message_chat?itemId=10001",
                            },
                            "content": {
                                "custom": {
                                    "data": base64.b64encode(
                                        json.dumps(buyer_content, ensure_ascii=False).encode("utf-8")
                                    ).decode("ascii")
                                }
                            },
                        }
                    },
                    {
                        "message": {
                            "messageId": "msg-seller-history",
                            "sendTime": 1780369210000,
                            "extension": {
                                "senderUserId": "seller-1",
                                "reminderTitle": "卖家",
                                "reminderUrl": "fleamarket://message_chat?itemId=10001",
                            },
                            "content": {
                                "custom": {
                                    "data": base64.b64encode(
                                        json.dumps(seller_content, ensure_ascii=False).encode("utf-8")
                                    ).decode("ascii")
                                }
                            },
                        }
                    },
                ]
            }
        }

        events = parse_history_events(response, "cid-1", "seller-1", 1780360000000)
        prioritized = prioritize_manual_history_events(events)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].text, "现在还能做PPT吗？")
        self.assertEqual(events[1].text, "可以的，你发资料。")
        self.assertEqual(prioritized[0].text, "可以的，你发资料。")
        self.assertEqual(prioritized[0].conversation_id, "cid-1")
        self.assertEqual(prioritized[1].text, "现在还能做PPT吗？")


if __name__ == "__main__":
    unittest.main()
