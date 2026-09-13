"""Git snapshots and validated file proposals for trusted local repositories."""

import subprocess
from pathlib import Path, PurePosixPath


PROTECTED = {".git", ".github", ".claude", ".codex", ".grok", ".boardroom",
             ".env", ".ssh", ".aws", "node_modules", ".venv", "__pycache__"}
CONFIG_NAMES = {"agents.md", "claude.md", "grok.md", ".mcp.json"}


def git(repo, *args, raw=False):
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git command failed")
    return result.stdout if raw else result.stdout.strip()


def safe_path(name):
    path = PurePosixPath(name)
    if (not name or "\\" in name or "\0" in name or path.is_absolute()
            or any(part in ("..", ".") for part in name.split("/"))
            or any(part.lower() in PROTECTED for part in path.parts)
            or path.name.lower() in CONFIG_NAMES
            or path.name.lower().startswith(".env")
            or path.suffix.lower() in (".pem", ".key", ".p12")):
        raise ValueError(f"Protected or invalid path: {name!r}")
    return path


def target_path(repo, name):
    path = safe_path(name)
    target = repo.joinpath(*path.parts)
    current = repo
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symlink cannot be used: {name}")
    if not target.resolve().is_relative_to(repo.resolve()):
        raise ValueError(f"Path escapes workspace: {name}")
    return target


def snapshot(repo, limit=120_000):
    names = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", raw=True).split("\0")
    files, omitted, size = {}, [], 0
    for name in sorted(set(names)):
        if not name:
            continue
        try:
            target = target_path(repo, name)
            if not target.is_file():
                continue
            if target.stat().st_size > 30_000:
                omitted.append(name)
                continue
            content = target.read_text()
            if "\0" in content or size + len(content) > limit:
                omitted.append(name)
                continue
            files[name] = content
            size += len(content)
        except (ValueError, UnicodeError, OSError):
            omitted.append(name)
    return {"files": files, "omitted": omitted}


def apply_changes(repo, changes):
    if len(changes) > 30:
        raise ValueError("At most 30 files may change in one task")
    validated, seen, size = [], set(), 0
    for change in changes:
        target = target_path(repo, change["path"])
        if target in seen or target.exists() and not target.is_file():
            raise ValueError(f"Duplicate or non-file target: {change['path']}")
        seen.add(target)
        size += len(change["content"].encode())
        if size > 500_000:
            raise ValueError("Proposed changes exceed 500 KB")
        validated.append((target, change))
    # Validate the entire batch before the first mutation.
    for target, change in validated:
        if change["delete"]:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change["content"])


def create_workspace(repo, workspace, base):
    # Separate .git and no remote: workers cannot accidentally push via origin.
    workspace.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", "--no-local",
                    str(repo), str(workspace)], check=True, capture_output=True, timeout=60)
    git(workspace, "remote", "remove", "origin")
    git(workspace, "checkout", "--detach", base)


def changed_diff(repo, base):
    # Stage to include newly created files in the review artifact.
    git(repo, "add", "--all")
    return git(repo, "diff", "--cached", "--no-ext-diff", "--no-textconv", base, "--", raw=True)
