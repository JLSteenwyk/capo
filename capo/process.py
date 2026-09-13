"""Bounded subprocess execution with parent-death supervision."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .watchdog import save_receipt

class WorkerError(RuntimeError):
    pass


class CleanupUncertain(WorkerError):
    """Execution must stop until the recorded worker group is proven absent."""


def reconcile_local(directory):
    """Read-only process probes; never signal an identity recovered from disk."""
    record = json.loads((directory / "process.json").read_text())
    receipt = directory / "worker.json"
    exit_path = directory / "exit.json"
    if receipt.exists():
        worker = json.loads(receipt.read_text())
        if worker.get("cleanup_confirmed") is True:
            return
        try:
            os.killpg(worker["pgid"], 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        raise CleanupUncertain("Worker cleanup could not be confirmed; inspect local processes before retrying")
    if record.get("supervision") == "watchdog-v2":
        # Workers cannot execute until their group receipt is durably recorded.
        # The parent also records the watchdog before releasing its start gate.
        pid = record.get("pid")
        if pid is None:
            return
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        raise CleanupUncertain("Worker startup or cleanup remains active; inspect local processes before retrying")
    if exit_path.exists():
        if json.loads(exit_path.read_text()).get("returncode") == 126:
            raise CleanupUncertain("Legacy worker cleanup is unconfirmed and its group identity is unavailable; inspect before retrying")
        return
    pid = record["pid"]
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # Older local watchdogs did not persist their separate worker group.
        if record.get("command") and record["command"] != "limactl":
            raise CleanupUncertain("Interrupted legacy worker has no cleanup receipt; inspect before retrying")
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
    raise CleanupUncertain(f"Process {pid} may still be active; inspect it before recovery or retry")


def run_process(argv, cwd, directory, timeout, stdin=None):
    """Persist output to files, bound duration, and terminate child process groups."""
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "process.json").exists():
        reconcile_local(directory)
    # Never let a prior invocation's successful receipt authorize this one.
    for name in ("worker.json", "exit.json"):
        (directory / name).unlink(missing_ok=True)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # Prefer existing CLI subscription authentication over ambient API keys.
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "XAI_API_KEY",
                "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        environment.pop(key, None)
    started = time.time()
    save_receipt(directory / "process.json", {
        "pid": None, "started": started, "command": argv[0], "supervision": "watchdog-v2"})
    with (directory / "stdout.txt").open("w") as out, (directory / "stderr.txt").open("w") as err:
        read_fd, write_fd = os.pipe()
        try:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("watchdog.py")),
                 str(read_fd), str(timeout), str(directory.resolve()), *argv],
                cwd=cwd, env=environment, stdin=subprocess.PIPE, stdout=out, stderr=err,
                text=True, start_new_session=True, pass_fds=(read_fd,))
        except BaseException:
            os.close(write_fd)
            raise
        finally:
            os.close(read_fd)
        try:
            save_receipt(directory / "process.json", {
                "pid": process.pid, "started": started, "command": argv[0], "supervision": "watchdog-v2"})
            os.write(write_fd, b"1")
            deadline = time.monotonic() + timeout
            pending_input = stdin
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                if sum((directory / name).stat().st_size for name in ("stdout.txt", "stderr.txt")) > 8_000_000:
                    raise WorkerError(f"Attempt exceeded its 8 MB log limit; inspect {directory}")
                try:
                    process.communicate(pending_input, timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pending_input = None
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    raise CleanupUncertain("Worker supervisor termination could not be confirmed") from None
            if process.returncode == 126:
                raise CleanupUncertain(f"Worker cleanup could not be confirmed; inspect {directory} before retrying") from None
            reconcile_local(directory)
            raise
        finally:
            os.close(write_fd)
    (directory / "exit.json").write_text(json.dumps({
        "returncode": process.returncode, "seconds": time.time() - started}))
    if process.returncode == 126:
        raise CleanupUncertain(f"Worker cleanup could not be confirmed; inspect {directory} before retrying")
    reconcile_local(directory)
    if sum((directory / name).stat().st_size for name in ("stdout.txt", "stderr.txt")) > 8_000_000:
        raise WorkerError(f"Attempt exceeded its 8 MB log limit; inspect {directory}")
    if (directory / "stdout.txt").stat().st_size > 4_000_000:
        raise WorkerError(f"Output exceeds 4 MB; inspect {directory}")
    output = (directory / "stdout.txt").read_text(errors="replace")
    if process.returncode:
        raise WorkerError(f"{Path(argv[0]).name} exited {process.returncode}; inspect {directory}")
    return output
