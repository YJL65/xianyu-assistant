from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import requests


def load_goofish_apis():
    root = Path(__file__).resolve().parents[1]
    vendor_dir = root / "vendor" / "XianYuApis"
    if not (vendor_dir / "goofish_apis.py").exists():
        raise unittest.SkipTest("vendor/XianYuApis is not available")
    if str(vendor_dir) not in sys.path:
        sys.path.insert(0, str(vendor_dir))
    previous = Path.cwd()
    try:
        os.chdir(vendor_dir)
        return importlib.import_module("goofish_apis")
    finally:
        os.chdir(previous)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.cookies = requests.cookies.RequestsCookieJar()

    def json(self):
        return self._payload


class XianyuVendorCookieTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.goofish_apis = load_goofish_apis()

    def make_api(self):
        api = self.goofish_apis.XianyuApis({}, "device-id")
        jar = requests.cookies.RequestsCookieJar()
        jar.set("_m_h5_tk", "root-token_111", domain="", path="/")
        jar.set("_m_h5_tk", "goofish-token_222", domain=".goofish.com", path="/")
        api.session.cookies = jar
        return api

    def test_mtop_token_prefers_goofish_domain_when_duplicate_names_exist(self):
        api = self.make_api()
        self.assertEqual(self.goofish_apis._mtop_token(api.session), "goofish-token")

    def test_refresh_token_handles_duplicate_mtop_cookie_names(self):
        api = self.make_api()
        response = FakeResponse({"ret": ["SUCCESS::调用成功"]})
        with mock.patch.object(self.goofish_apis, "generate_sign", return_value="signed") as sign_mock:
            with mock.patch.object(api.session, "post", return_value=response):
                result = api.refresh_token()
        self.assertEqual(result["ret"][0], "SUCCESS::调用成功")
        self.assertEqual(sign_mock.call_args[0][1], "goofish-token")

    def test_get_token_handles_duplicate_mtop_cookie_names(self):
        api = self.make_api()
        response = FakeResponse({"ret": ["SUCCESS::调用成功"], "data": {}})
        with mock.patch.object(self.goofish_apis, "generate_sign", return_value="signed") as sign_mock:
            with mock.patch.object(api.session, "post", return_value=response):
                result = api.get_token()
        self.assertEqual(result["ret"][0], "SUCCESS::调用成功")
        self.assertEqual(sign_mock.call_args[0][1], "goofish-token")
