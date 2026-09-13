"""Git snapshots and validated file proposals for trusted local repositories."""

import subprocess
import re
from pathlib import Path, PurePosixPath


PROTECTED = {".git", ".github", ".claude", ".codex", ".grok", ".capo", ".config",
             ".env", ".ssh", ".aws", "node_modules", ".venv", "__pycache__"}
CONFIG_NAMES = {"agents.md", "claude.md", "grok.md", ".mcp.json", ".netrc", ".npmrc",
                ".pypirc", ".git-credentials", "credentials.json", "slack.json"}


def git(repo, *args, raw=False, env=None):
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                            capture_output=True, text=True, timeout=60, env=env)
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
            or path.name.lower().endswith(".local.json")
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


def supporting_excerpt(target, terms, budget):
    """Select bounded line windows as evidence, never replacement file contents."""
    # Bound both disk reads and output. Large generated files are not context.
    if budget <= 0 or target.stat().st_size > 2_000_000:
        return []
    content = target.read_text()
    if "\0" in content:
        return []
    lines = content.splitlines(keepends=True)
    matches = []
    for index, line in enumerate(lines):
        words = set(re.findall(r"[a-z][a-z0-9]+", line.lower()))
        score = len(words & terms)
        if score:
            matches.append((-score, index))
    # Include a small header when the request supplies no matching vocabulary.
    centers = [index for _, index in sorted(matches)[:40]] or [0]
    selected, used = set(), 0
    for center in centers:
        window = set(range(max(0, center - 6), min(len(lines), center + 13))) - selected
        cost = sum(len(lines[index]) for index in window)
        if used + cost > budget:
            # A useful matching line can still fit when its surrounding block
            # does not. Its explicit range preserves the missing-context caveat.
            window = {center} - selected
            cost = sum(len(lines[index]) for index in window)
        if cost and used + cost <= budget:
            selected.update(window)
            used += cost
    ranges = []
    for index in sorted(selected):
        if ranges and ranges[-1]["end_line"] == index:
            ranges[-1]["end_line"] = index + 1
            ranges[-1]["content"] += lines[index]
        else:
            ranges.append({"start_line": index + 1, "end_line": index + 1,
                           "content": lines[index]})
    return ranges


def snapshot(repo, limit=120_000, focus=""):
    names = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", raw=True).split("\0")
    files, omitted, excerpts, size, excerpt_size = {}, [], {}, 0, 0
    # Put named task files and related filenames ahead of unrelated source.
    # Alphabetical truncation can otherwise omit every test in a growing repo.
    terms = set(re.findall(r"[a-z][a-z0-9]+", focus.lower())) - {"the", "and", "with", "for", "file", "files"}
    def priority(name):
        path = PurePosixPath(name)
        parts = set(re.findall(r"[a-z][a-z0-9]+", path.stem.lower()))
        named = bool(name and name.lower() in focus.lower())
        return (-100 * named - len(parts & terms), name)
    for name in sorted(set(names), key=priority):
        if not name:
            continue
        try:
            target = target_path(repo, name)
            if not target.is_file():
                continue
            if target.stat().st_size > 30_000:
                omitted.append(name)
                # Only exact task references earn read-only excerpts. A filename
                # substring (e.g. app.py inside other_app.py) is not a reference.
                named = re.search(r"(?<![\w./-])" + re.escape(name) + r"(?![\w/-]|\.[\w])",
                                  focus, re.IGNORECASE)
                if named:
                    path_terms = set(re.findall(r"[a-z][a-z0-9]+", name.lower()))
                    ranges = supporting_excerpt(target, terms - path_terms,
                                                min(12_000, 24_000 - excerpt_size,
                                                    max(0, limit // 4), limit - size))
                    if ranges:
                        amount = sum(len(item["content"]) for item in ranges)
                        excerpts[name] = {"read_only": True, "incomplete": True,
                                          "notice": "Supporting evidence only. Omitted lines may change "
                                          "the interpretation; do not edit or reconstruct this file.",
                                          "ranges": ranges}
                        size += amount
                        excerpt_size += amount
                continue
            content = target.read_text()
            if "\0" in content or size + len(content) > limit:
                omitted.append(name)
                continue
            files[name] = content
            size += len(content)
        except (ValueError, UnicodeError, OSError):
            omitted.append(name)
    return {"files": files, "omitted": omitted, "supporting_excerpts": excerpts}


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
