import unittest

from app.dialogue import IntakeAssistant, extract_budget
from app.llm import OpenAICompatibleClient
from app.models import Listing, NeedState


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.assistant = IntakeAssistant(OpenAICompatibleClient("", "", ""))
        self.listing = Listing(
            item_id="1",
            title="小程序开发服务",
            description="根据需求定制开发。",
        )

    def test_asks_for_missing_fields(self):
        state = NeedState()
        decision = self.assistant.handle(
            self.listing,
            [("buyer", "你好，我想做一个校园二手交易小程序")],
            state,
        )
        self.assertFalse(decision.should_notify)
        self.assertIn("方便", decision.reply)

    def test_completes_and_notifies(self):
        state = NeedState(
            goal="校园二手交易",
            deliverable="小程序前后端和论文材料",
            deadline="月底",
            budget="两千左右",
            delivery_method="线上沟通",
            materials="有需求文档和参考图",
        )
        decision = self.assistant.handle(
            self.listing,
            [("buyer", "资料都有，线上沟通就行")],
            state,
        )
        self.assertTrue(decision.should_notify)
        self.assertIn("记录", decision.reply)

    def test_extracts_chinese_budget(self):
        self.assertEqual(extract_budget("预算两千左右"), "预算两千左右")


if __name__ == "__main__":
    unittest.main()
