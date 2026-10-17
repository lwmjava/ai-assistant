"""Run the actual CI endpoint command with a noisy dependency import."""
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_ci_endpoint_capture_is_only_digest_under_import_noise(tmp_path):
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["agent-baseline"]["steps"]
    run = next(step["run"] for step in steps if step.get("name") == "Agent report gate and regression probes")
    inline = re.search(r'endpoint_hash=\$\(python -c "([^"\n]+)"\)', run)
    if inline:
        code = inline.group(1)
    else:
        code = run.split("endpoint_hash=$(python - <<'PY'\n", 1)[1].split("\nPY\n)", 1)[0]
    (tmp_path / "sitecustomize.py").write_text(
        "import builtins\n"
        "_original_import = builtins.__import__\n"
        "def _noisy_import(name, *args, **kwargs):\n"
        "    if name == 'scripts.run_agent_chain_eval':\n"
        "        print('controlled dependency import warning')\n"
        "    return _original_import(name, *args, **kwargs)\n"
        "builtins.__import__ = _noisy_import\n", encoding="utf-8")
    environment = dict(os.environ)
    environment.update(PYTHONPATH=os.pathsep.join([str(tmp_path), str(ROOT)]),
                       LLM_BASE_URL="https://api.deepseek.com/v1", LLM_API_KEY="",
                       LLM_INTENT_API_KEY="", LLM_FALLBACK_API_KEY="", RAG_BACKEND="native")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=environment,
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0
    assert result.stdout.strip() == "12b8deaccc34b32757dbb1497e029da0c2e7b26ffa86b9c926c08cb4692f4508"
    assert "controlled dependency import warning" in result.stderr
