import pytest

from app.services.feishu_utils import resolve_base_url, FEISHU_BASE_URL, LARK_BASE_URL


def test_resolve_base_url_feishu():
    assert resolve_base_url("feishu") == FEISHU_BASE_URL
    assert resolve_base_url("feishu") == "https://open.feishu.cn"


def test_resolve_base_url_lark():
    assert resolve_base_url("lark") == LARK_BASE_URL
    assert resolve_base_url("lark") == "https://open.larksuite.com"


def test_resolve_base_url_default():
    assert resolve_base_url(None) == FEISHU_BASE_URL
    assert resolve_base_url("") == FEISHU_BASE_URL
    assert resolve_base_url("unknown") == FEISHU_BASE_URL
