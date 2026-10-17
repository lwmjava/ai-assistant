"""Execute only after explicit task-specific approval; synthetic data, no tools."""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from app.agents.skills.base import SkillManifest, SkillTrigger
from app.agents.skills.manager import SkillManager
from app.core.config import settings
from app.llm.openai_compatible import OpenAICompatibleProvider


async def run(output: Path) -> None:
    if output.exists():
        raise ValueError("preserve_existing_report")
    cases_path = Path("docs/evaluations/skill-semantic-match-v1-cases.json")
    data = json.loads(cases_path.read_text(encoding="utf-8"))
    if not settings.LLM_API_KEY or not settings.LLM_CAPABILITY_GUARD_ENABLED:
        raise ValueError("real_credentials_and_guard_required")
    settings.LLM_RESPONSE_CACHE_ENABLED = False
    provider = OpenAICompatibleProvider(settings.LLM_BASE_URL, settings.LLM_API_KEY,
                                        settings.LLM_DEFAULT_MODEL, timeout=5)
    if provider._effective_capability() is None:
        raise ValueError("approved_capability_required")

    class RecordingProvider(OpenAICompatibleProvider):
        def __init__(self):
            super().__init__(provider.base_url, provider.api_key, provider.model, timeout=5)
            self.count = 0
            self.completed = False
            self.reply = None

        async def chat(self, messages, options=None):
            if self.count >= 6:
                raise ValueError("request_limit")
            self.count += 1
            self.completed = False
            self.reply = None
            reply = await super().chat(messages, options)
            self.completed = True
            self.reply = reply
            return reply

    recording = RecordingProvider()
    manager = SkillManager()
    for skill in data["skills"]:
        manager.register(SkillManifest(name=skill["name"], description=skill["description"],
                                       trigger=SkillTrigger(keywords=[skill["keyword"]])))
    rows = []
    for case in data["cases"]:
        assert manager.match(case["request"]) == []
        selected = await manager.match_semantic(case["request"], recording, max_results=1)
        actual = selected[0].skill.name if selected else None
        rows.append({"case_id": case["id"], "expected": case["expected"], "actual": actual,
                     "transport_completed": recording.completed,
                     "raw_reply": recording.reply,
                     "passed": recording.completed and actual == case["expected"]})
    report = {"version": data["version"], "evidence": "E1-real-model-synthetic-smoke-not-Gold",
              "created_at": datetime.now(UTC).isoformat(), "requested_model": recording.model,
              "endpoint_hash": hashlib.sha256(recording.base_url.encode()).hexdigest(),
              "model_calls": recording.count, "cache_enabled": False, "retry": 0,
              "timeout_seconds": 5, "max_output_tokens": 128,
              "dataset_hash": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
              "source_hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                [Path("app/agents/skills/manager.py"), Path("app/services/chat_service.py")]},
              "cases": rows, "passed": sum(r["passed"] for r in rows), "total": len(rows),
              "overall_pass": all(r["passed"] for r in rows)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": report["total"], "calls": recording.count}))
    if not report["overall_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.out))
