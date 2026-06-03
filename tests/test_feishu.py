import unittest

from app.feishu import sign


class FeishuTests(unittest.TestCase):
    def test_sign_is_stable(self):
        self.assertEqual(sign("1234567890", "secret"), "ZfKVuj6L5hFYWbpNk/R//8s1lu9nDXiIbG0Fc4NaCEk=")


if __name__ == "__main__":
    unittest.main()
