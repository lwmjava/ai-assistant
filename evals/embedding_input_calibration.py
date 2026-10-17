"""Calibrate the approved conservative counter with synthetic input only.

The config reader deliberately selects only required keys and never prints
credentials, response bodies, document content or exception messages.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.rag.embeddings.input_limits import resolve_input_policy

SAMPLES = {
    "cjk": "这是合成的切分输入校准样本。" * 40,
    "ascii_code": 'def example(value):\n    return {"result": value + 1}\n' * 20,
    "unicode_box": "┌──────────┐\n│ 合成流程 │\n└──────────┘\n" * 20,
    "markdown_table": "| 字段 | 值 |\n| --- | --- |\n| 示例 | 123 |\n" * 20,
    "emoji_combining": "示例🙂 e\u0301 👩‍💻\n" * 40,
    "near_byte_threshold": "aZ09+-_=" * 1010,
}


async def calibrate(config_path: Path) -> dict:
    wanted = {"EMBEDDING_BASE_URL", "EMBEDDING_API_KEY", "EMBEDDING_MODEL"}
    config = {}
    for line in config_path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in wanted:
            config[key.strip()] = value.strip().strip('"').strip("'")
    endpoint = config.get("EMBEDDING_BASE_URL", "")
    model = config.get("EMBEDDING_MODEL", "")
    policy = resolve_input_policy(endpoint, model)
    if policy is None or not config.get("EMBEDDING_API_KEY"):
        raise ValueError("calibration_config_unavailable")
    results = []
    async with httpx.AsyncClient(timeout=60) as client:
        for name, text in SAMPLES.items():
            if policy.check(text):
                raise ValueError("synthetic_sample_over_limit")
            response = await client.post(
                endpoint.rstrip("/") + "/embeddings",
                headers={"Authorization": "Bearer " + config["EMBEDDING_API_KEY"]},
                json={"model": model, "input": [text]},
            )
            response.raise_for_status()
            usage = response.json()["usage"]
            tokens = int(usage.get("prompt_tokens", usage["total_tokens"]))
            estimate = len(text.encode("utf-8")) + policy.safety_margin
            results.append({"sample": name, "utf8_bytes_plus_margin": estimate,
                            "provider_tokens": tokens, "estimate_covers_observed": estimate >= tokens})
    return {
        "version": "embedding-input-calibration-v0.1", "model": model, "provider": "dashscope",
        "created_at": datetime.now(UTC).isoformat(), "synthetic": True, "data_tier": "Smoke",
        "policy": policy.metadata(), "samples": results,
        "all_samples_covered": all(row["estimate_covers_observed"] for row in results),
        "limitations": (
            "Finite synthetic calibration; not exact tokenization or a universal bound. No retrieval metrics."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = asyncio.run(calibrate(args.config))
    except Exception as exc:
        print("calibration_failed exception_type=" + type(exc).__name__)
        raise SystemExit(1) from None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("calibration_complete samples=" + str(len(report["samples"])) +
          " covered=" + str(report["all_samples_covered"]))
    if not report["all_samples_covered"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
