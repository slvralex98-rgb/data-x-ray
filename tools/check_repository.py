"""Reject assistant configuration and internal notes in the Git index."""
from pathlib import Path, PurePosixPath
import subprocess
import sys


ASSISTANT_FILES = frozenset({
    "agents.md", "agents.override.md", "claude.md", "gemini.md", "skill.md",
    ".cursorrules", ".windsurfrules",
})
LOCAL_DIRECTORIES = frozenset({
    ".agents", ".codex", ".claude", ".cursor", ".windsurf", ".junie", ".continue", ".local-notes",
})


def is_internal_path(path):
    """Only docs/public is curated for readers; other docs are working material."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    lowered = tuple(part.casefold() for part in parts)
    if not lowered:
        return False
    return (lowered[-1] in ASSISTANT_FILES
            or any(part in LOCAL_DIRECTORIES or part.startswith(".aider") for part in lowered)
            or (lowered[0] == ".github" and len(lowered) > 1 and lowered[1] in {
                "copilot-instructions.md", "instructions", "prompts", "agents", "skills"})
            or (lowered[0] == "docs" and (len(lowered) < 2 or lowered[1] != "public")))


def tracked_internal_files(root):
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "-z"],
        capture_output=True, check=False,
    )
    if completed.returncode:
        raise RuntimeError("Не удалось прочитать индекс Git. Запустите проверку в Git-репозитории.")
    paths = completed.stdout.decode("utf-8").split("\0")
    return sorted({path for path in paths if path and is_internal_path(path)})


def main():
    root = Path(__file__).resolve().parents[1]
    try:
        forbidden = tracked_internal_files(root)
    except (OSError, RuntimeError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if forbidden:
        print("В индекс Git попали локальные служебные файлы:", file=sys.stderr)
        for path in forbidden:
            print(f"  {path}", file=sys.stderr)
        return 1
    print("Состав Git проверен: локальные служебные материалы не добавлены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
