"""Bounded subprocess execution with parent-death supervision."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

class WorkerError(RuntimeError):
    pass


def run_process(argv, cwd, directory, timeout, stdin=None):
    """Persist output to files, bound duration, and terminate child process groups."""
    directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # Prefer existing CLI subscription authentication over ambient API keys.
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "XAI_API_KEY",
                "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        environment.pop(key, None)
    started = time.time()
    with (directory / "stdout.txt").open("w") as out, (directory / "stderr.txt").open("w") as err:
        read_fd, write_fd = os.pipe()
        try:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("watchdog.py")),
                 str(read_fd), str(timeout), *argv],
                cwd=cwd, env=environment, stdin=subprocess.PIPE, stdout=out, stderr=err,
                text=True, start_new_session=True, pass_fds=(read_fd,))
        except BaseException:
            os.close(write_fd)
            raise
        finally:
            os.close(read_fd)
        try:
            (directory / "process.json").write_text(json.dumps({
                "pid": process.pid, "started": started, "command": argv[0]}))
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
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            # The parent may exit on TERM while a child ignores it.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise
        finally:
            os.close(write_fd)
    # Background descendants are outside the bounded attempt contract.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    (directory / "exit.json").write_text(json.dumps({
        "returncode": process.returncode, "seconds": time.time() - started}))
    if sum((directory / name).stat().st_size for name in ("stdout.txt", "stderr.txt")) > 8_000_000:
        raise WorkerError(f"Attempt exceeded its 8 MB log limit; inspect {directory}")
    if (directory / "stdout.txt").stat().st_size > 4_000_000:
        raise WorkerError(f"Output exceeds 4 MB; inspect {directory}")
    output = (directory / "stdout.txt").read_text(errors="replace")
    if process.returncode:
        raise WorkerError(f"{Path(argv[0]).name} exited {process.returncode}; inspect {directory}")
    return output
