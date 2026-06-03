import asyncio
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.connectors.base import Connector
from app.models import AssistantDecision, IncomingMessage, Listing, ManualSellerMessage, NeedState
from app.runner import AssistantRunner


class FakeConnector(Connector):
    def __init__(self):
        self.sent = []
        self.manual_takeover_conversations = set()

    async def events(self):
        if False:
            yield None

    async def send_text(self, conversation_id: str, buyer_id: str, text: str) -> None:
        self.sent.append((conversation_id, buyer_id, text))

    async def fetch_listing(self, listing_id: str) -> Listing:
        return Listing(item_id=listing_id, title="服务商品")

    def manual_takeover_enabled(self, conversation_id: str) -> bool:
        return conversation_id in self.manual_takeover_conversations


class FakeNotifier:
    def __init__(self):
        self.new_inquiries = []
        self.first_exchanges = []
        self.buyer_messages = []
        self.summaries = []
        self.customer_summaries = []

    def send_new_inquiry(self, **kwargs):
        self.new_inquiries.append(kwargs)

    def send_first_ai_exchange(self, **kwargs):
        self.first_exchanges.append(kwargs)

    def send_intake_summary(self, **kwargs):
        self.summaries.append(kwargs)

    def send_buyer_message(self, **kwargs):
        self.buyer_messages.append(kwargs)

    def send_customer_service_summary(self, **kwargs):
        self.customer_summaries.append(kwargs)


class FakeCustomerServiceLlm:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.calls = 0

    @property
    def enabled(self):
        return True

    def customer_service_turn(self, listing, history, latest_message):
        self.calls += 1
        if self.decisions:
            return self.decisions.pop(0)
        return AssistantDecision(
            reply="\u53ef\u4ee5\u7684\uff0c\u8bf7\u628a\u4efb\u52a1\u5185\u5bb9\u548c\u622a\u6b62\u65f6\u95f4\u53d1\u6211\u3002",
            should_notify=False,
        )


class TakeoverDuringLlm:
    def __init__(self, connector: FakeConnector, conversation_id: str):
        self.connector = connector
        self.conversation_id = conversation_id
        self.calls = 0

    @property
    def enabled(self):
        return True

    def customer_service_turn(self, listing, history, latest_message):
        self.calls += 1
        self.connector.manual_takeover_conversations.add(self.conversation_id)
        return AssistantDecision(reply="这句应该被发送前检查拦截。", should_notify=True)


class RunnerTests(unittest.TestCase):
    def test_first_message_forwards_to_feishu_and_asks_fixed_question_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                feishu_webhook_url="",
                feishu_webhook_secret="",
                openai_base_url="",
                openai_api_key="",
                openai_model="",
                db_path=root / "assistant.sqlite3",
                log_path=root / "assistant.log",
                mode="test",
                xianyu_cookie="",
                xianyu_vendor_path=root,
                node_exe="",
            )
            connector = FakeConnector()
            notifier = FakeNotifier()
            runner = AssistantRunner(settings, connector)
            runner.notifier = notifier

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-1",
                        buyer_id="buyer-1",
                        buyer_name="买家A",
                        text="宝贝还有吗？",
                        message_id="msg-1",
                        listing_id="item-1",
                    )
                )
            )

            self.assertEqual(len(connector.sent), 1)
            self.assertEqual(connector.sent[0][2], "你有什么需求？")
            self.assertEqual(len(notifier.first_exchanges), 1)
            self.assertEqual(notifier.first_exchanges[0]["buyer_message"], "宝贝还有吗？")
            self.assertEqual(notifier.first_exchanges[0]["assistant_reply"], "你有什么需求？")
            self.assertEqual(notifier.buyer_messages, [])
            self.assertEqual(notifier.summaries, [])
            self.assertEqual(notifier.customer_summaries, [])

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-1",
                        buyer_id="buyer-1",
                        buyer_name="买家A",
                        text="我想做一个小程序，明天要",
                        message_id="msg-2",
                        listing_id="item-1",
                    )
                )
            )

            self.assertEqual(len(notifier.first_exchanges), 1)
            self.assertEqual(notifier.buyer_messages, [])
            self.assertEqual(len(connector.sent), 2)
            self.assertEqual(connector.sent[1][2], "你有什么需求？")

    def test_ai_mode_replies_to_xianyu_and_notifies_first_inquiry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                feishu_webhook_url="",
                feishu_webhook_secret="",
                openai_base_url="https://example.test/v1",
                openai_api_key="key",
                openai_model="model",
                db_path=root / "assistant.sqlite3",
                log_path=root / "assistant.log",
                mode="test",
                xianyu_cookie="",
                xianyu_vendor_path=root,
                node_exe="",
            )
            connector = FakeConnector()
            notifier = FakeNotifier()
            runner = AssistantRunner(settings, connector)
            runner.notifier = notifier
            runner.llm = FakeCustomerServiceLlm(
                AssistantDecision(
                    reply="有的，你想代问什么内容？截止时间大概是什么时候？",
                    should_notify=False,
                )
            )

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-2",
                        buyer_id="buyer-2",
                        buyer_name="买家B",
                        text="宝贝还有吗？",
                        message_id="msg-ai-1",
                        listing_id="item-2",
                    )
                )
            )

            self.assertEqual(len(connector.sent), 1)
            self.assertEqual(connector.sent[0][2], "有的，你想代问什么内容？截止时间大概是什么时候？")
            self.assertEqual(notifier.new_inquiries, [])
            self.assertEqual(len(notifier.first_exchanges), 1)
            self.assertEqual(notifier.first_exchanges[0]["buyer_message"], "宝贝还有吗？")
            self.assertEqual(
                notifier.first_exchanges[0]["assistant_reply"],
                "有的，你想代问什么内容？截止时间大概是什么时候？",
            )
            self.assertEqual(notifier.buyer_messages, [])
            self.assertEqual(notifier.customer_summaries, [])

    def test_ai_mode_only_sends_first_exchange_even_when_need_is_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                feishu_webhook_url="",
                feishu_webhook_secret="",
                openai_base_url="https://example.test/v1",
                openai_api_key="key",
                openai_model="model",
                db_path=root / "assistant.sqlite3",
                log_path=root / "assistant.log",
                mode="test",
                xianyu_cookie="",
                xianyu_vendor_path=root,
                node_exe="",
            )
            connector = FakeConnector()
            notifier = FakeNotifier()
            runner = AssistantRunner(settings, connector)
            runner.notifier = notifier
            runner.llm = FakeCustomerServiceLlm(
                AssistantDecision(
                    reply="收到，我先帮你同步给卖家确认。",
                    should_notify=True,
                    summary="买家需要代问Claude，材料已齐，希望今晚完成。",
                )
            )

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-3",
                        buyer_id="buyer-3",
                        buyer_name="买家C",
                        text="我要代问Claude，材料都齐，今晚要",
                        message_id="msg-ai-2",
                        listing_id="item-3",
                    )
                )
            )

            self.assertEqual(len(connector.sent), 1)
            self.assertEqual(connector.sent[0][2], "收到，我先帮你同步给卖家确认。")
            self.assertEqual(len(notifier.first_exchanges), 1)
            self.assertEqual(notifier.first_exchanges[0]["buyer_message"], "我要代问Claude，材料都齐，今晚要")
            self.assertEqual(notifier.first_exchanges[0]["assistant_reply"], "收到，我先帮你同步给卖家确认。")
            self.assertEqual(notifier.customer_summaries, [])
            self.assertEqual(notifier.buyer_messages, [])

    def test_ai_mode_keeps_replying_until_manual_takeover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                feishu_webhook_url="",
                feishu_webhook_secret="",
                openai_base_url="https://example.test/v1",
                openai_api_key="key",
                openai_model="model",
                db_path=root / "assistant.sqlite3",
                log_path=root / "assistant.log",
                mode="test",
                xianyu_cookie="",
                xianyu_vendor_path=root,
                node_exe="",
            )
            connector = FakeConnector()
            notifier = FakeNotifier()
            runner = AssistantRunner(settings, connector)
            runner.notifier = notifier
            runner.llm = FakeCustomerServiceLlm(
                AssistantDecision(
                    reply="可以的，把参考资料和提示词发我这边就行。",
                    should_notify=False,
                )
            )

            runner.store.add_incoming(
                IncomingMessage(
                    conversation_id="cid-5",
                    buyer_id="buyer-5",
                    buyer_name="买家E",
                    text="你好，在吗？",
                    message_id="msg-ai-6",
                    listing_id="item-5",
                    created_at="2026-06-02T00:00:01+00:00",
                )
            )
            runner.store.add_assistant_reply("cid-5", "请问您需要处理什么任务？", "2026-06-02T00:00:02+00:00")

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-5",
                        buyer_id="buyer-5",
                        buyer_name="买家E",
                        text="我想处理ppt，今天之内完成",
                        message_id="msg-ai-7",
                        listing_id="item-5",
                        created_at="2026-06-02T00:00:03+00:00",
                    )
                )
            )

            self.assertEqual(len(connector.sent), 1)
            self.assertEqual(connector.sent[0][2], "可以的，把参考资料和提示词发我这边就行。")
            self.assertEqual(notifier.customer_summaries, [])
            self.assertEqual(len(notifier.first_exchanges), 1)
            self.assertEqual(notifier.first_exchanges[0]["buyer_message"], "我想处理ppt，今天之内完成")
            self.assertEqual(notifier.buyer_messages, [])

    def test_ai_mode_resets_reply_count_after_consultation_gap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                feishu_webhook_url="",
                feishu_webhook_secret="",
                openai_base_url="https://example.test/v1",
                openai_api_key="key",
                openai_model="model",
                db_path=root / "assistant.sqlite3",
                log_path=root / "assistant.log",
                mode="test",
                xianyu_cookie="",
                xianyu_vendor_path=root,
                node_exe="",
            )
            connector = FakeConnector()
            notifier = FakeNotifier()
            runner = AssistantRunner(settings, connector)
            runner.notifier = notifier
            runner.llm = FakeCustomerServiceLlm(
                AssistantDecision(
                    reply="在的，请问您这次需要处理什么内容？",
                    should_notify=False,
                )
            )

            runner.store.add_incoming(
                IncomingMessage(
                    conversation_id="cid-6",
                    buyer_id="buyer-6",
                    buyer_name="买家F",
                    text="上午咨询",
                    message_id="msg-ai-8",
                    listing_id="item-6",
                    created_at="2026-06-02T00:00:01+00:00",
                )
            )
            runner.store.add_assistant_reply("cid-6", "旧回复一", "2026-06-02T00:00:02+00:00")
            runner.store.add_assistant_reply("cid-6", "旧回复二", "2026-06-02T00:00:03+00:00")
            runner.store.save_state("cid-6", NeedState(notified=True, completed=True))

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-6",
                        buyer_id="buyer-6",
                        buyer_name="买家F",
                        text="你好",
                        message_id="msg-ai-9",
                        listing_id="item-6",
                        created_at="2026-06-02T01:00:00+00:00",
                    )
                )
            )

            self.assertEqual(len(connector.sent), 1)
            self.assertEqual(connector.sent[0][2], "在的，请问您这次需要处理什么内容？")
            self.assertEqual(notifier.customer_summaries, [])
            self.assertEqual(len(notifier.first_exchanges), 1)
            self.assertEqual(notifier.first_exchanges[0]["buyer_message"], "你好")
            self.assertEqual(notifier.buyer_messages, [])

    def test_manual_seller_reply_pauses_ai_replies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                feishu_webhook_url="",
                feishu_webhook_secret="",
                openai_base_url="https://example.test/v1",
                openai_api_key="key",
                openai_model="model",
                db_path=root / "assistant.sqlite3",
                log_path=root / "assistant.log",
                mode="test",
                xianyu_cookie="",
                xianyu_vendor_path=root,
                node_exe="",
            )
            connector = FakeConnector()
            notifier = FakeNotifier()
            runner = AssistantRunner(settings, connector)
            runner.notifier = notifier
            fake_llm = FakeCustomerServiceLlm(
                AssistantDecision(
                    reply="这句不应该发送。",
                    should_notify=False,
                )
            )
            runner.llm = fake_llm

            runner._handle_manual_seller_message(
                ManualSellerMessage(
                    conversation_id="cid-manual",
                    text="我来处理。",
                    message_id="seller-msg-1",
                    listing_id="item-manual",
                    created_at="2026-06-02T00:00:01+00:00",
                )
            )

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-manual",
                        buyer_id="buyer-manual",
                        buyer_name="买家G",
                        text="那我发你资料",
                        message_id="buyer-after-manual",
                        listing_id="item-manual",
                        created_at="2026-06-02T00:00:02+00:00",
                    )
                )
            )

            self.assertEqual(fake_llm.calls, 0)
            self.assertEqual(connector.sent, [])
            self.assertEqual(notifier.buyer_messages, [])
            self.assertEqual(notifier.customer_summaries, [])

    def test_manual_takeover_survives_existing_history_gap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                feishu_webhook_url="",
                feishu_webhook_secret="",
                openai_base_url="https://example.test/v1",
                openai_api_key="key",
                openai_model="model",
                db_path=root / "assistant.sqlite3",
                log_path=root / "assistant.log",
                mode="test",
                xianyu_cookie="",
                xianyu_vendor_path=root,
                node_exe="",
            )
            connector = FakeConnector()
            notifier = FakeNotifier()
            runner = AssistantRunner(settings, connector)
            runner.notifier = notifier
            fake_llm = FakeCustomerServiceLlm(
                AssistantDecision(reply="不应该自动回复。", should_notify=False)
            )
            runner.llm = fake_llm

            runner.store.add_incoming(
                IncomingMessage(
                    conversation_id="cid-gap-manual",
                    buyer_id="buyer-gap",
                    buyer_name="买家H",
                    text="很久以前的咨询",
                    message_id="old-gap-msg",
                    listing_id="item-gap",
                    created_at="2026-06-02T00:00:01+00:00",
                )
            )
            runner.store.add_assistant_reply("cid-gap-manual", "旧回复", "2026-06-02T00:00:02+00:00")
            runner.store.add_incoming(
                IncomingMessage(
                    conversation_id="cid-gap-manual",
                    buyer_id="buyer-gap",
                    buyer_name="买家H",
                    text="新一轮咨询",
                    message_id="new-gap-msg",
                    listing_id="item-gap",
                    created_at="2026-06-02T01:00:00+00:00",
                )
            )
            runner._handle_manual_seller_message(
                ManualSellerMessage(
                    conversation_id="cid-gap-manual",
                    text="我人工接一下。",
                    message_id="seller-gap-msg",
                    listing_id="item-gap",
                    created_at="2026-06-02T01:00:01+00:00",
                )
            )

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-gap-manual",
                        buyer_id="buyer-gap",
                        buyer_name="买家H",
                        text="那我继续说需求",
                        message_id="buyer-gap-after-manual",
                        listing_id="item-gap",
                        created_at="2026-06-02T01:00:02+00:00",
                    )
                )
            )

            self.assertEqual(fake_llm.calls, 0)
            self.assertEqual(connector.sent, [])
            self.assertEqual(notifier.buyer_messages, [])
            self.assertEqual(notifier.customer_summaries, [])

    def test_recorded_assistant_echo_does_not_trigger_manual_takeover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                feishu_webhook_url="",
                feishu_webhook_secret="",
                openai_base_url="https://example.test/v1",
                openai_api_key="key",
                openai_model="model",
                db_path=root / "assistant.sqlite3",
                log_path=root / "assistant.log",
                mode="test",
                xianyu_cookie="",
                xianyu_vendor_path=root,
                node_exe="",
            )
            connector = FakeConnector()
            notifier = FakeNotifier()
            runner = AssistantRunner(settings, connector)
            runner.notifier = notifier
            fake_llm = FakeCustomerServiceLlm(
                AssistantDecision(
                    reply="继续正常回复。",
                    should_notify=False,
                )
            )
            runner.llm = fake_llm

            runner.store.add_assistant_reply(
                "cid-echo",
                "可以的，把参考资料和提示词发我这边就行。",
                "2026-06-02T00:00:01+00:00",
            )
            runner._handle_manual_seller_message(
                ManualSellerMessage(
                    conversation_id="cid-echo",
                    text="可以的，把参考资料和提示词发我这边就行。",
                    message_id="seller-history-echo",
                    listing_id="item-echo",
                    created_at="2026-06-02T00:00:02+00:00",
                )
            )

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-echo",
                        buyer_id="buyer-echo",
                        buyer_name="买家I",
                        text="我发过去了",
                        message_id="buyer-after-echo",
                        listing_id="item-echo",
                        created_at="2026-06-02T00:00:03+00:00",
                    )
                )
            )

            self.assertEqual(fake_llm.calls, 1)
            self.assertEqual(len(connector.sent), 1)
            self.assertEqual(connector.sent[0][2], "继续正常回复。")
            self.assertFalse(runner.store.state("cid-echo").manual_takeover)

    def test_ai_reply_is_skipped_if_manual_takeover_appears_before_send(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                feishu_webhook_url="",
                feishu_webhook_secret="",
                openai_base_url="https://example.test/v1",
                openai_api_key="key",
                openai_model="model",
                db_path=root / "assistant.sqlite3",
                log_path=root / "assistant.log",
                mode="test",
                xianyu_cookie="",
                xianyu_vendor_path=root,
                node_exe="",
            )
            connector = FakeConnector()
            notifier = FakeNotifier()
            runner = AssistantRunner(settings, connector)
            runner.notifier = notifier
            runner.llm = TakeoverDuringLlm(connector, "cid-race")

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-race",
                        buyer_id="buyer-race",
                        buyer_name="买家J",
                        text="还是只要给个主题？",
                        message_id="buyer-race-msg",
                        listing_id="item-race",
                        created_at="2026-06-02T00:00:01+00:00",
                    )
                )
            )

            self.assertEqual(runner.llm.calls, 1)
            self.assertEqual(connector.sent, [])
            self.assertEqual(notifier.new_inquiries, [])
            self.assertEqual(notifier.first_exchanges, [])
            self.assertEqual(notifier.buyer_messages, [])
            self.assertEqual(notifier.customer_summaries, [])
            self.assertTrue(runner.store.state("cid-race").manual_takeover)

    def test_ai_mode_keeps_replying_after_many_assistant_replies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                feishu_webhook_url="",
                feishu_webhook_secret="",
                openai_base_url="https://example.test/v1",
                openai_api_key="key",
                openai_model="model",
                db_path=root / "assistant.sqlite3",
                log_path=root / "assistant.log",
                mode="test",
                xianyu_cookie="",
                xianyu_vendor_path=root,
                node_exe="",
            )
            connector = FakeConnector()
            notifier = FakeNotifier()
            runner = AssistantRunner(settings, connector)
            runner.notifier = notifier
            fake_llm = FakeCustomerServiceLlm(
                AssistantDecision(
                    reply="还需要再问一句。",
                    should_notify=False,
                )
            )
            runner.llm = fake_llm

            runner.store.add_incoming(
                IncomingMessage(
                    conversation_id="cid-4",
                    buyer_id="buyer-4",
                    buyer_name="买家D",
                    text="你好，在吗？",
                    message_id="msg-ai-3",
                    listing_id="item-4",
                    created_at="2026-06-02T00:00:01+00:00",
                )
            )
            runner.store.add_assistant_reply("cid-4", "请问您需要处理什么任务？", "2026-06-02T00:00:02+00:00")
            runner.store.add_incoming(
                IncomingMessage(
                    conversation_id="cid-4",
                    buyer_id="buyer-4",
                    buyer_name="买家D",
                    text="我想处理ppt，今天之内完成",
                    message_id="msg-ai-4",
                    listing_id="item-4",
                    created_at="2026-06-02T00:00:03+00:00",
                )
            )
            runner.store.add_assistant_reply("cid-4", "可以的，把参考资料和提示词发我这边就行。", "2026-06-02T00:00:04+00:00")
            runner.store.save_state("cid-4", NeedState(initial_notified=True))

            asyncio.run(
                runner.handle_message(
                    IncomingMessage(
                        conversation_id="cid-4",
                        buyer_id="buyer-4",
                        buyer_name="买家D",
                        text="论文答辩，具体的我已经写好了。",
                        message_id="msg-ai-5",
                        listing_id="item-4",
                        created_at="2026-06-02T00:00:05+00:00",
                    )
                )
            )

            self.assertEqual(fake_llm.calls, 1)
            self.assertEqual(len(connector.sent), 1)
            self.assertEqual(connector.sent[0][2], "还需要再问一句。")
            self.assertEqual(notifier.customer_summaries, [])
            self.assertEqual(notifier.new_inquiries, [])
            self.assertEqual(notifier.first_exchanges, [])
            self.assertEqual(notifier.buyer_messages, [])


if __name__ == "__main__":
    unittest.main()
