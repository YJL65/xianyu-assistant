import unittest

from app.dialogue import availability_question_reply


class AvailabilityTests(unittest.TestCase):
    def test_replies_to_common_availability_question(self):
        reply = availability_question_reply("宝贝还有吗？")
        self.assertEqual(reply, "你有什么需求？")


if __name__ == "__main__":
    unittest.main()
