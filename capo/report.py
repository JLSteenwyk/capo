"""Print a concise objective report without updating the ledger."""

import argparse
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path


MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
SNAPSHOT_ATTEMPTS = 5
RETRY_DELAY = 0.02


class ReportError(ValueError):
    """An intentionally public, fixed diagnostic."""


class SnapshotChanged(Exception):
    """Source files changed during capture; retry without opening SQLite."""


def _error(message):
    print(f"capo-report: {message}", file=sys.stderr)
    return 1


def _metadata(info):
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _stat_file(path, optional=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return None
        raise SnapshotChanged() from None
    # Reject symlinks and special files rather than following an unexpected
    # source or blocking on a pipe. Recheck the opened descriptor below.
    if not stat.S_ISREG(info.st_mode):
        raise ReportError("no readable Capo state found")
    return _metadata(info)


def _reject_journal(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise ReportError("stale rollback journal")


def _read_file(path, expected):
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise SnapshotChanged() from None
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or _metadata(info) != expected:
            raise SnapshotChanged()
        # Read at most the captured size plus one byte, even if a writer grows
        # the file continuously. The aggregate captured size is checked first.
        content = handle.read(expected[2] + 1)
        if len(content) != expected[2] or _metadata(os.fstat(handle.fileno())) != expected:
            raise SnapshotChanged()
    return content


def _snapshot(db_path):
    wal_path = db_path.with_name(db_path.name + "-wal")
    journal_path = db_path.with_name(db_path.name + "-journal")
    for attempt in range(SNAPSHOT_ATTEMPTS):
        try:
            _reject_journal(journal_path)
            before = (_stat_file(db_path), _stat_file(wal_path, optional=True))
            _reject_journal(journal_path)
            if sum(item[2] for item in before if item is not None) > MAX_SNAPSHOT_BYTES:
                raise ReportError("state exceeds snapshot size limit")
            database = _read_file(db_path, before[0])
            wal = _read_file(wal_path, before[1]) if before[1] is not None else None
            after = (_stat_file(db_path), _stat_file(wal_path, optional=True))
            _reject_journal(journal_path)
            if before != after:
                raise SnapshotChanged()
            return database, wal
        except SnapshotChanged:
            if attempt + 1 < SNAPSHOT_ATTEMPTS:
                time.sleep(RETRY_DELAY)
    raise ReportError("could not obtain a stable state snapshot")


def _temporary_parent(home):
    # Do not call gettempdir(): its writable-directory probes may create files
    # under --home when TMPDIR points there. Capo supports macOS and Linux.
    # Resolve before creating anything so /tmp symlinks are handled correctly.
    for candidate in (Path("/tmp"), Path("/var/tmp")):
        parent = candidate.resolve()
        if not parent.is_relative_to(home) and parent.is_dir():
            return parent
    raise ReportError("no temporary directory outside state available")


def _read_row(home, identifier):
    if re.fullmatch(r"[a-f0-9]{16}", identifier) is None:
        raise ReportError("invalid objective id")
    home = Path(home).expanduser().resolve()
    db_path = home / "capo.sqlite3"
    if not home.is_dir() or not db_path.is_file():
        raise ReportError("no Capo state found")
    database, wal = _snapshot(db_path)
    parent = _temporary_parent(home)
    with tempfile.TemporaryDirectory(prefix="capo-report-", dir=parent) as directory:
        copy_path = Path(directory) / "capo.sqlite3"
        copy_path.write_bytes(database)
        if wal is not None:
            copy_path.with_name(copy_path.name + "-wal").write_bytes(wal)
        # Never copy SHM or connect to the source. SQLite may initialize SHM
        # only inside this private, disposable directory. immutable=1 would
        # ignore committed WAL data and must not be used here.
        with closing(sqlite3.connect(copy_path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ReportError("invalid state snapshot")
            return db.execute("SELECT data FROM objectives WHERE id = ?",
                              (identifier,)).fetchone()


def _report(objective, identifier):
    """Select public summary fields; never serialize whole stored objects."""
    if not isinstance(objective, dict) or objective.get("id") != identifier:
        raise ValueError("Invalid objective")
    report = {"id": identifier}
    for key in ("status", "team_name", "active_stage"):
        default = "SPARKITscience" if key == "team_name" else None
        value = objective.get(key, default)
        if not isinstance(value, str) and value is not None:
            raise ValueError("Invalid summary field")
        report[key] = value
    for key in ("calls", "max_calls", "round", "max_rounds"):
        value = objective.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("Invalid count")
        report[key] = value

    verification = objective.get("verification", {})
    if not isinstance(verification, dict):
        raise ValueError("Invalid verification")
    checks = verification.get("checks", [])
    reviews = verification.get("reviews", [])
    if not isinstance(checks, list) or not isinstance(reviews, list):
        raise ValueError("Invalid verification entries")
    for check in checks:
        if not isinstance(check, dict) or type(check.get("passed")) is not bool:
            raise ValueError("Invalid check")
    reviewers = []
    for review in reviews:
        if (not isinstance(review, dict)
                or not isinstance(review.get("provider"), str)
                or type(review.get("approved")) is not bool):
            raise ValueError("Invalid review")
        reviewers.append({"provider": review["provider"],
                          "approved": review["approved"]})
    report["verification"] = {
        "checks_passed": sum(check["passed"] for check in checks),
        "checks_total": len(checks),
        "reviews_approved": sum(review["approved"] for review in reviewers),
        "reviews_total": len(reviewers),
    }
    report["reviewers"] = reviewers
    publication = objective.get("publication", {})
    if not isinstance(publication, dict):
        raise ValueError("Invalid publication")
    pr = publication.get("pr", {})
    if not isinstance(pr, dict):
        raise ValueError("Invalid publication PR")
    url = pr.get("url")
    if url is not None and not isinstance(url, str):
        raise ValueError("Invalid PR URL")
    report["pr_url"] = url
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="capo-report", description=__doc__)
    parser.add_argument("id", help="Objective ID (16 lowercase hexadecimal characters)")
    parser.add_argument("--home", type=Path, required=True,
                        help="Existing Capo state directory")
    args = parser.parse_args(argv)
    if re.fullmatch(r"[a-f0-9]{16}", args.id) is None:
        return _error("invalid objective id")

    try:
        row = _read_row(args.home, args.id)
    except ReportError as exc:
        return _error(str(exc))
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        return _error("no readable Capo state found")

    if row is None:
        return _error(f"unknown objective: {args.id}")
    try:
        report = _report(json.loads(row[0]), args.id)
        output = json.dumps(report, indent=2)
    except (ValueError, TypeError, RuntimeError):
        return _error("invalid objective state")
    try:
        print(output)
    except OSError:
        return _error("could not write report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
