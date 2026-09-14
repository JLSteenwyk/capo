"""Run Capo improvements as ordinary objectives with frozen regression checks."""

import hashlib
import sys
from pathlib import Path

from .repository import git


BASELINE_RUNNER = '''import os
import sys
import unittest
from pathlib import Path
tests = Path(__file__).resolve().parent / "tests"
sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(tests))
suite = unittest.defaultTestLoader.discover(str(tests))
if suite.countTestCases() == 0:
    raise SystemExit("Frozen regression suite contains no tests")
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


def fingerprint(directory):
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Regression baseline cannot contain symlinks")
        if path.is_file():
            files[str(path.relative_to(directory))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def verify_baseline(objective):
    baseline = objective.get("regression_baseline")
    if objective.get("kind") == "self_improvement" and not baseline:
        raise ValueError("Self-improvement requires a frozen regression baseline")
    if baseline and fingerprint(Path(baseline["path"])) != baseline["files"]:
        raise ValueError("Frozen regression baseline changed; refusing to accept the candidate")


def add_improvement(store, args):
    from .cli import add_objective

    repo = args.repo.expanduser().resolve()
    if not (repo / "capo/runtime.py").is_file() or not (repo / "tests").is_dir():
        raise ValueError("Self-improvement target must be a Capo checkout with its test suite")
    # An ordinary objective guarantees a clean, committed source and isolated workspace.
    args.check = [f'"{sys.executable}" -m unittest discover -s tests -q']
    objective = add_objective(store, args, args.request + "\n\n"
        "This is an improvement to Capo itself. Preserve existing behavior and compatibility. "
        "Do not weaken permissions, credential handling, resource limits, review gates, or regression checks. "
        "Implement a bounded improvement and explain evidence. Changes run in a separate candidate clone; "
        "the running supervisor is not replaced.", source=getattr(args, "source", None), kind="self_improvement")
    if objective.get("regression_baseline"):
        verify_baseline(objective)
        return objective
    directory = store.home / "baselines" / objective["id"]
    try:
        directory.mkdir(parents=True, exist_ok=False)
        names = git(repo, "ls-files", "-z", "--", "tests", raw=True).split("\0")
        count = 0
        for name in names:
            if not name:
                continue
            source = repo / name
            if source.is_symlink() or not source.is_file():
                raise ValueError("Frozen test suite must contain regular tracked files")
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            count += name.endswith(".py")
        if count == 0:
            raise ValueError("No tracked Python regression tests found")
        (directory / "run.py").write_text(BASELINE_RUNNER)
        objective["regression_baseline"] = {"path": str(directory), "files": fingerprint(directory),
                                             "source_commit": objective["base"]}
        objective["checks"].insert(0, [sys.executable, "-I", "-B", str(directory / "run.py")])
        objective["kind"] = "self_improvement"
        store.save(objective, "improvement_queued")
    except Exception:
        objective["status"] = "blocked"
        objective["error"] = "Could not freeze the regression baseline; create a new improvement objective"
        store.save(objective, "baseline_failed")
        raise
    return objective


CORE_ENFORCEMENT = frozenset({
    "capo/runtime.py", "capo/repository.py", "capo/improvement.py", "capo/providers.py",
    "capo/process.py", "capo/watchdog.py", "capo/transport.py", "capo/guest.py",
    "capo/digest_cli.py", "capo/digest.py", "capo/digest_sources.py", "capo/digest_service.py", "capo/digest_feedback.py", "capo/gmail.py", "capo/team.py", "capo/slack_images.py", "capo/calendar.py", "capo/browser.py", "capo/browser_slack.py", "capo/finalize.py", "capo/github.py", "capo/store.py", "capo/slack.py", "capo/cli.py", "capo/contracts.py", "capo/conversation.py", "capo/routine.py", "capo/delivery.py", "capo/communication.py",
    "capo/__init__.py", "capo/__main__.py", "pyproject.toml", "setup.py", "setup.cfg",
    "sitecustomize.py", "usercustomize.py",
})


def verify_governance_changes(objective, changes):
    """Keep autonomous proposals outside existing Capo enforcement modules.

    Detect the pinned source tree as well as the objective kind so using `add`
    instead of `improve` cannot turn off this boundary.
    """
    is_capo = objective.get("kind") == "self_improvement"
    if not is_capo:
        repo = objective.get("workspace") or objective["repo"]
        names = git(repo, "ls-tree", "-r", "--name-only", objective["base"], "--",
                    "capo/runtime.py", "tests").splitlines()
        is_capo = "capo/runtime.py" in names and any(name.startswith("tests/") for name in names)
    if not is_capo:
        return
    from pathlib import PurePosixPath
    for change in changes:
        name = str(PurePosixPath(change["path"])).lower()
        if any(name == protected or protected.startswith(name + "/")
               or (protected.endswith(".py") and name.startswith(protected[:-3] + "/"))
               for protected in CORE_ENFORCEMENT):
            raise ValueError("Core enforcement changes require owner review outside autonomous apply: " + name)
