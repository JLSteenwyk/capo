"""Standalone Linux worker supervisor, sent to the guest over Lima's SSH channel.

Only stdlib imports: this file also runs independently with ``python3 -c``.
An open, heartbeating stdin lease is required for the child to remain alive.
"""

import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LEASE_SECONDS = 10
MAX_OUTPUT = 4_000_000


def root_dir():
    root = Path.home() / ".local/share/capo-worker"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def terminate(process, grace=2):
    if process is None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
    process.wait()


def run(run_id, timeout, binary):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id) or not 0 < timeout <= 86400:
        raise ValueError("Invalid worker identity or deadline")
    os.umask(0o077)
    directory = root_dir() / run_id
    directory.mkdir()  # Never reuse an uncertain attempt.
    process = None
    status = {"state": "starting", "deadline": time.time() + timeout}
    status_path = directory / "status.json"

    def save():
        temporary = directory / "status.tmp"
        temporary.write_text(json.dumps(status))
        temporary.replace(status_path)

    def interrupted(*_):
        raise InterruptedError("Remote worker interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    save()
    try:
        # Read one bounded line without buffering heartbeats ahead of select().
        request_bytes = bytearray()
        deadline = time.monotonic() + timeout
        while not request_bytes.endswith(b"\n"):
            if time.monotonic() >= deadline or (directory / "cancel").exists():
                raise InterruptedError("Task staging cancelled or deadline exceeded")
            if not select.select([sys.stdin], [], [], min(LEASE_SECONDS, max(0, deadline - time.monotonic())))[0]:
                raise TimeoutError("Host lease expired before task staging")
            chunk = os.read(sys.stdin.fileno(), 1)
            if not chunk:
                raise InterruptedError("Host disconnected before task staging")
            request_bytes.extend(chunk)
            if len(request_bytes) > MAX_OUTPUT:
                raise ValueError("Task input exceeds 4 MB")
        request = json.loads(request_bytes)
        prompt = directory / "prompt.txt"
        prompt.write_text(request["prompt"])
        schema = json.dumps(request["schema"])
        work = directory / "workspace"
        work.mkdir()
        environment = {"HOME": str(Path.home()), "PATH": "/usr/local/bin:/usr/bin:/bin",
                       "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        executable = str(Path(binary).expanduser())
        argv = [executable, "--prompt-file", str(prompt), "--output-format", "json",
                "--json-schema", schema, "--tools", "", "--no-subagents",
                "--sandbox", "read-only", "--permission-mode", "dontAsk"]
        stdout, stderr = directory / "stdout.txt", directory / "stderr.txt"
        if (directory / "cancel").exists() or time.monotonic() >= deadline:
            raise InterruptedError("Task cancelled before worker startup")
        with stdout.open("w") as out, stderr.open("w") as err:
            process = subprocess.Popen(argv, cwd=work, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=True)
            status.update(state="running", pid=process.pid)
            save()
            heartbeat = time.monotonic()
            while process.poll() is None:
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutError("Remote worker deadline exceeded")
                if now - heartbeat >= LEASE_SECONDS:
                    raise TimeoutError("Host heartbeat lease expired")
                if (directory / "cancel").exists():
                    raise InterruptedError("Remote cancellation requested")
                if stdout.stat().st_size + stderr.stat().st_size > 2 * MAX_OUTPUT:
                    raise ValueError("Remote worker log limit exceeded")
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    data = os.read(sys.stdin.fileno(), 4096)
                    if not data:
                        raise InterruptedError("Host disconnected")
                    heartbeat = time.monotonic()
        terminate(process)
        if stdout.stat().st_size > MAX_OUTPUT or stderr.stat().st_size > MAX_OUTPUT:
            raise ValueError("Remote output exceeds 4 MB")
        if process.returncode:
            sys.stderr.write(stderr.read_text(errors="replace")[-8000:])
            raise RuntimeError(f"Grok exited {process.returncode}; check guest login and sandbox dependencies")
        sys.stdout.write(stdout.read_text())
        status.update(state="finished", returncode=0)
    except BaseException as exc:
        status.update(state="failed", error=str(exc))
        raise
    finally:
        # EOF/deadline and SSH shutdown can arrive together. A second signal must
        # not interrupt process-group cleanup or leave a nonterminal receipt.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        terminate(process)
        status["finished"] = time.time()
        # Keep only a small reconciliation receipt, never prompts or model logs.
        for path in directory.iterdir():
            if path.name == "status.json":
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        save()


def control(mode, run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid worker identity")
    directory = root_dir() / run_id
    if mode == "cancel":
        try:
            directory.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            created = False
        (directory / "cancel").touch(mode=0o600)
        if created:
            (directory / "status.json").write_text(json.dumps({
                "state": "failed", "error": "Cancelled before startup"}))
    path = directory / "status.json"
    print(path.read_text() if path.exists() else json.dumps({"state": "starting" if directory.exists() else "missing"}))


def main():
    mode = sys.argv[1]
    if mode == "run":
        run(sys.argv[2], float(sys.argv[3]), sys.argv[4])
    elif mode in ("status", "cancel"):
        control(mode, sys.argv[2])
    elif mode == "health":
        binary = str(Path(sys.argv[2]).expanduser())
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
        print(json.dumps({"platform": sys.platform, "binary_ok": version.returncode == 0,
                          "version": version.stdout.strip(), "bubblewrap": bool(shutil.which("bwrap")),
                          "login_file_present": (Path.home() / ".grok/auth.json").is_file()}))
    else:
        raise ValueError("Unknown worker operation")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"capo guest: {exc}", file=sys.stderr)
        sys.exit(1)
