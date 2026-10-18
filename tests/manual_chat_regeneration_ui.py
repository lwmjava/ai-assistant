"""仅本机的重新生成页面夹具；合成模型不访问外部服务。"""

from pathlib import Path

from tests.manual_sse_ui import main as run_fixture


def main() -> None:
    def provider_factory():
        import asyncio

        from app.agents.prompts import SYSTEM_RESPOND
        from app.llm.mock import MockLLMProvider

        class RegenerationFixture(MockLLMProvider):
            responses = 0

            async def chat(self, messages, options=None):
                await asyncio.sleep(0.12)
                if Path("data/chat006-evidence/ui-failure.flag").exists():
                    raise RuntimeError("synthetic generation failure")
                if messages and messages[0].content.startswith(SYSTEM_RESPOND):
                    self.responses += 1
                    return f"隔离合成回复第 {self.responses} 次：已通过真实对话管线生成并持久化，不代表模型质量。"
                return await super().chat(messages, options)

        return RegenerationFixture()

    run_fixture(provider_factory, port=18806)


if __name__ == "__main__":
    main()
