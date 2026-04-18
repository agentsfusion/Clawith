"""Shared utilities for Feishu/Lark brand-aware URL resolution."""

FEISHU_BASE_URL = "https://open.feishu.cn"
LARK_BASE_URL = "https://open.larksuite.com"


def resolve_base_url(brand: str | None) -> str:
    """Resolve the API base URL based on the brand.

    Args:
        brand: "lark" for international Lark, "feishu" or anything else for Feishu (China).

    Returns:
        The base URL for the corresponding platform API.
    """
    if brand == "lark":
        return LARK_BASE_URL
    return FEISHU_BASE_URL
