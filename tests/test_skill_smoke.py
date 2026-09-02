"""Smoke test for skill system."""
import sys
from pathlib import Path

# Ensure project root is on sys.path for direct execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.skills import SkillManager, SkillMode, TriggerType

# Test 1: Load skills
mgr = SkillManager()
count = mgr.load()
print("Test 1: Loaded", count, "skills")
for s in mgr.list_all():
    print(f"  - {s.name} (v{s.version}): {s.description} [{s.mode.value}]")
assert count == 2, f"Expected 2 skills, got {count}"

# Test 2: Match code_review
matches = mgr.match("帮我审查这段代码")
print("\nTest 2: Match for '帮我审查这段代码':")
for m in matches:
    print(f"  - {m.skill.name}: confidence={m.confidence:.2f}, reason={m.trigger_reason}")
assert len(matches) > 0, "Expected code_review match"

# Test 3: No match
matches = mgr.match("今天天气怎么样")
print("\nTest 3: Match for '今天天气怎么样':")
print(f"  Matches: {len(matches)} (expected 0)")
assert len(matches) == 0, f"Expected 0 matches, got {len(matches)}"

# Test 4: Activate
matches = mgr.match("帮我翻译这段文字")
ctx = mgr.activate(matches)
print("\nTest 4: Activate translator:")
print(f"  skill_name={ctx.skill_name}")
print(f"  confidence={ctx.confidence:.2f}")
assert ctx.skill_name == "translator"

# Test 5: Inject prompt
base = "You are a helpful assistant."
injected = mgr.inject_prompt(ctx, base)
print("\nTest 5: Inject prompt:")
print(f"  base length={len(base)}, injected length={len(injected)}")
assert len(injected) > len(base)

# Test 6: Describe
print("\nTest 6: Describe skills:")
desc = mgr.describe()
print(desc)
assert "code_review" in desc
assert "translator" in desc

# Test 7: Dynamic register
mgr.register_from_yaml("""
name: test_skill
version: "1.0"
description: "A test skill"
mode: prompt_injection
trigger:
  type: keyword
  keywords: ["test"]
system_prompt: "This is a test skill."
""")
assert mgr.get("test_skill") is not None
print("\nTest 7: Dynamic register OK")

# Test 8: Unregister
mgr.unregister("test_skill")
assert mgr.get("test_skill") is None
print("Test 8: Unregister OK")

# Test 9: Global singleton
from app.agents.skills import get_skill_manager, reset_skill_manager
reset_skill_manager()
mgr2 = get_skill_manager()
assert mgr2 is not None
assert len(mgr2.list_all()) == 2
print("Test 9: Global singleton OK")

print("\nAll tests passed!")