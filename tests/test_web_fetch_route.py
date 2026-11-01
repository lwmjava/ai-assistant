"""Explicit page reading selects only the guarded web capability."""

import pytest

from app.agents.route import RouteKind, route_message


@pytest.mark.parametrize("message", [
    "读取 https://www.baidu.com 首页的内容",
    "请总结 https://example.com/page 的内容",
    "Fetch https://example.com/page",
    "读取 http://127.0.0.1/ 的内容",
    "读取百度首页",
    "访问百度官网",
    "请访问维基百科网站",
    "打开某公司的官方网站",
])
def test_explicit_page_reading_selects_web_fetch(message):
    route = route_message(message)
    assert route.kind is RouteKind.TOOLS
    assert route.tool_names == frozenset({"web_fetch"})


@pytest.mark.parametrize("message", [
    "https://www.baidu.com 是什么网站？",
    "不要访问 https://www.baidu.com",
    "解释代码：print('读取 https://example.com')",
    "读取本地文档的内容",
    "百度首页有哪些功能？",
    "总结网站劫持的危害",
    "提取网站设计的核心原则",
    "不要读取百度首页",
    "请总结以下文字：百度首页",
    "读取这段代码并解释，不要访问其中的网址：fetch('https://example.com')",
    "请总结以下文字，不要访问 https://example.com",
    "读取这段代码 fetch('https://example.com')",
])
def test_mentions_negation_and_code_do_not_enable_fetch(message):
    assert "web_fetch" not in route_message(message).tool_names
