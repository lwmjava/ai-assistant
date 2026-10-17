"""Offline error presentation fixture. Synthetic transport is NOT business E2E evidence.

Open /__fixture and choose scenarios through visible links; use only the documented
placeholder login form. No browser storage scripting or real credentials are needed.
"""

import html
import json
from pathlib import Path

from tests.manual_sse_ui import main as run_fixture

SCENARIOS = {
    "session_expired": "登录后发送消息：401 + 刷新失败，显示过期提示并回登录页",
    "token_revoked": "登录后发送消息：凭证撤销，显示管理员重置提示并回登录页",
    "user_disabled": "点击登录：显示禁用账号提示（表单只是占位，无真实口令）",
    "tenant_suspended": "登录后发送消息：显示租户停用全屏提示，确认后退出",
    "rate_limited": "登录后发送消息：429，发送按钮显示 8 秒倒计时",
    "quota_exceeded": "登录后发送消息：429，显示配额不足横幅",
    "model_unavailable": "登录后发送消息：隔离模型抛类型化故障，走真实 SSE 错误链",
    "file_too_large": "登录后选择 oversized.py：真实附件选择器拒绝 1 MiB + 1 字节",
    "silent_refresh": "登录后发送消息：首请求合成401，真实刷新一次，然后成功完成",
}


def main() -> None:
    assets = Path("data/pytest-tmp/error-ui-assets")
    assets.mkdir(parents=True, exist_ok=True)
    oversized = assets / "oversized.py"
    oversized.write_bytes(b"x" * 1048577)
    print("ERROR_UI_OVERSIZED_ASSET=" + str(oversized.resolve()), flush=True)

    def provider_factory():
        # Shared initializer disables dotenv, assigns a temporary DB and blocks external
        # DNS/sockets BEFORE this factory imports the real application modules.
        from fastapi import Request
        from fastapi.responses import HTMLResponse, RedirectResponse

        from app.api.errors import public_response
        from app.llm.mock import MockLLMProvider
        from app.llm.routing import LLMUnavailableError
        from app.main import app

        state = {"scenario": "silent_refresh", "chat_attempts": 0, "refresh_attempts": 0,
                 "login_attempts": 0, "synthetic_responses": 0}

        def failure(code, status, **kwargs):
            state["synthetic_responses"] += 1
            return public_response(status, code, **kwargs)

        @app.middleware("http")
        async def error_fixture(request: Request, call_next):
            path = request.url.path
            if path == "/__fixture":
                choices = "".join(
                    f'<li><a href="/__fixture/select?scenario={name}">{name}</a>：{html.escape(label)}</li>'
                    for name, label in SCENARIOS.items()
                )
                return HTMLResponse(
                    '<html lang="zh"><meta charset="utf-8"><title>错误场景隔离菜单</title>'
                    '<body style="font:18px system-ui;max-width:1000px;margin:40px auto">'
                    '<h1>九种错误验收场景</h1><p>这是隔离 UI fixture。合成 transport '
                    '只证明页面展示，不代表真实身份、限流、配额或远端模型业务验收。</p>'
                    '<p>切换前先在真实控制台点击退出登录，再回来选场景。若已经自动退出可直接选择。'
                    '登录名 ui-fixture，密码字段任意占位文本（如 fixture），不是凭据。</p>'
                    f'<ul>{choices}</ul><p>当前记录：</p>'
                    f'<pre>{html.escape(json.dumps(state, ensure_ascii=False))}</pre>'
                    '<p><a href="/chat">进入真实控制台</a> · <a href="/__fixture">刷新记录</a></p>'
                    f'<p>超限文件：{html.escape(str(oversized.resolve()))}</p></body></html>'
                )
            if path == "/__fixture/select":
                selected = request.query_params.get("scenario")
                if selected not in SCENARIOS:
                    return HTMLResponse("未知场景", status_code=400)
                state.update(scenario=selected, chat_attempts=0, refresh_attempts=0,
                             login_attempts=0, synthetic_responses=0)
                response = RedirectResponse("/login", status_code=303)
                response.set_cookie("error-ui-scenario", selected, httponly=True, samesite="strict")
                return response
            scenario = request.cookies.get("error-ui-scenario", state["scenario"])
            if path == "/api/v1/auth/login" and request.method == "POST":
                state["login_attempts"] += 1
                if scenario == "user_disabled":
                    return failure("user_disabled", 403)
            if path == "/api/v1/auth/refresh" and request.method == "POST":
                state["refresh_attempts"] += 1
                if scenario == "session_expired":
                    return failure("session_expired", 401)
            if path in {"/api/v1/chat", "/api/v1/chat/stream"} and request.method == "POST":
                state["chat_attempts"] += 1
                if scenario == "silent_refresh" and state["chat_attempts"] == 1:
                    return failure("session_expired", 401)
                if scenario in {"session_expired", "token_revoked"}:
                    return failure(scenario, 401)
                if scenario in {"user_disabled", "tenant_suspended"}:
                    return failure(scenario, 403)
                if scenario == "rate_limited":
                    return failure("rate_limited", 429, retry_after=8,
                                   headers={"Retry-After": "8"},
                                   legacy={"retry_after_seconds": 8})
                if scenario == "quota_exceeded":
                    return failure("quota_exceeded", 429, legacy={
                        "resource": "messages", "limit": 1, "used": 1,
                        "requested": 1, "quota_type": "message_count",
                    })
            return await call_next(request)

        class FixtureModel(MockLLMProvider):
            async def chat(self, messages, options=None):
                if state["scenario"] == "model_unavailable":
                    raise LLMUnavailableError()
                return await super().chat(messages, options)

            async def stream_chat(self, messages, options=None):
                if state["scenario"] == "model_unavailable":
                    raise LLMUnavailableError()
                yield "隔离验证成功：静默刷新后的消息已完成。"

        return FixtureModel()

    run_fixture(provider_factory, port=18767)


if __name__ == "__main__":
    main()
