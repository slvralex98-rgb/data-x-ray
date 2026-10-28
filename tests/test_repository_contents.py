"""Publication policy is checked against a real, isolated Git index and archive."""
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import unquote, urlsplit

import pytest

from tools.check_repository import is_internal_path, tracked_internal_files


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repository(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("Git is needed for publication checks")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_ATTR_NOSYSTEM", "1")
    for name in (".gitignore", ".gitattributes"):
        shutil.copy2(ROOT / name, tmp_path / name)
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    return tmp_path


def git(root, *args, **kwargs):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, **kwargs)


@pytest.mark.parametrize("path", [
    "AGENTS.md", "sub/AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "GEMINI.md", "SKILL.md",
    ".agents/skills/task/SKILL.md", ".codex/settings.toml", ".cursor/rules/check.mdc",
    ".claude/commands/review.md", ".continue/config.yaml", ".junie/guidelines.md",
    ".aider.conf.yml", ".local-notes/todo.md", ".github/copilot-instructions.md",
    ".github/agents/reviewer.agent.md", ".github/instructions/code.instructions.md",
    ".github/prompts/task.prompt.md", ".github/skills/check/SKILL.md",
    "docs/ui-refinement-plan.md", "docs/v0.1.0-testing-report.md", "docs/new-handoff.md",
    "docs/tour-v010/step-04.png",
])
def test_internal_paths_are_ignored_by_git(repository, path):
    assert is_internal_path(path)
    assert git(repository, "check-ignore", "--no-index", "--quiet", path).returncode == 0


@pytest.mark.parametrize("path", [
    "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "pyproject.toml",
    ".github/workflows/tests.yml", "data_xray/engine.py", "tests/test_engine.py",
    "tools/check_repository.py", "docs/public/overview.png", "docs/public/guide.md",
    "examples/uniqueness_demo.csv", "examples/uniqueness_criteria.json",
])
def test_useful_source_and_documentation_remain_public(repository, path):
    assert not is_internal_path(path)
    assert git(repository, "check-ignore", "--no-index", "--quiet", path).returncode == 1


def test_forced_add_is_detected_and_archives_still_exclude_notes(repository):
    files = {"README.md": b"Read me", "AGENTS.md": b"local instructions",
             "docs/internal-plan.md": b"private work plan",
             "docs/public/overview.png": b"synthetic fixture",
             ".github/prompts/task.prompt.md": b"private prompt"}
    for name, content in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    assert git(repository, "add", "--force", ".").returncode == 0
    assert tracked_internal_files(repository) == [
        ".github/prompts/task.prompt.md", "AGENTS.md", "docs/internal-plan.md"]
    tree = git(repository, "write-tree")
    assert tree.returncode == 0, tree.stderr
    result = git(repository, "archive", "--format=tar", tree.stdout.decode().strip())
    assert result.returncode == 0, result.stderr
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        names = set(archive.getnames())
    assert "README.md" in names and "docs/public/overview.png" in names
    assert not {"AGENTS.md", "docs/internal-plan.md", ".github/prompts/task.prompt.md"} & names


def test_repository_check_cli_rejects_indexed_notes(repository):
    (repository / "tools").mkdir()
    script = repository / "tools/check_repository.py"
    shutil.copy2(ROOT / "tools/check_repository.py", script)
    completed = subprocess.run([sys.executable, str(script)], capture_output=True)
    assert completed.returncode == 0, completed.stderr
    (repository / "AGENTS.md").write_text("local only", encoding="utf-8")
    assert git(repository, "add", "--force", "AGENTS.md").returncode == 0
    completed = subprocess.run([sys.executable, str(script)], capture_output=True)
    assert completed.returncode == 1
    assert b"AGENTS.md" in completed.stderr


def test_public_documentation_links_exist_and_are_publishable(repository):
    documents = [ROOT / name for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md")]
    documents.extend((ROOT / "docs/public").rglob("*.md"))
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for match in re.finditer(r"!?\[[^\]]*\]\(([^)]+)\)", text):
            target = urlsplit(match.group(1).strip("<>"))
            if target.scheme or not target.path:
                continue
            path = (document.parent / unquote(target.path)).resolve()
            relative = path.relative_to(ROOT).as_posix()
            assert path.exists(), (document.name, relative)
            assert not is_internal_path(relative), (document.name, relative)
            assert git(repository, "check-ignore", "--no-index", "--quiet", relative).returncode == 1
