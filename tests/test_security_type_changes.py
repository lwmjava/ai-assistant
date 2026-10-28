"""Git object type changes must retain new-credential checks."""

import subprocess

import pytest

from scripts import security_checks as checks


@pytest.mark.parametrize("staged", [True, False])
def test_link_replaced_by_secret_file_is_scanned(tmp_path, staged):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args, text=None):
        return subprocess.run(["git", "-C", str(repo), *args], input=text, text=True,
                              capture_output=True, check=True, timeout=30).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "synthetic@example.invalid")
    old_blob = git("hash-object", "-w", "--stdin", text="safe.py")
    git("update-index", "--add", "--cacheinfo", f"120000,{old_blob},credential.py")
    git("commit", "-qm", "Initial link fixture")
    base = git("rev-parse", "HEAD")
    secret = "".join(("AKIA", "Q7W9E2R4T6Y8U3I5"))
    new_blob = git("hash-object", "-w", "--stdin", text=f"credential = '{secret}'\n")
    git("update-index", "--cacheinfo", f"100644,{new_blob},credential.py")
    assert git("diff", "--cached", "--name-status").startswith("T\t")
    if not staged:
        git("commit", "-qm", "Incoming type change fixture")
    result = checks.scan_secrets(repo, staged=staged, base_ref=base)
    assert result["status"] == "failed"
    assert result["files_checked"] == 1
    assert result["findings"] and secret not in str(result)
