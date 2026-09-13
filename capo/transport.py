"""Explicit Lima transport with bounded staging, host leases, and reconciliation."""

import json
import os
import re
import select
import signal
import subprocess
import time
import uuid
from pathlib import Path

from . import guest


def validate_config(config):
    if not isinstance(config, dict) or set(config) - {"grok"}:
        raise ValueError("Provider configuration must contain only a grok object")
    grok = config.get("grok", {"transport": "local"})
    if not isinstance(grok, dict) or set(grok) - {"transport", "vm", "binary"}:
        raise ValueError("Unsupported Grok transport configuration")
    if grok.get("transport") not in ("local", "lima"):
        raise ValueError("Grok transport must be local or lima")
    if grok["transport"] == "lima":
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", grok.get("vm", "")):
            raise ValueError("Lima transport requires a valid VM name")
        binary = grok.get("binary", "~/.grok/bin/grok")
        if not isinstance(binary, str) or not binary.startswith(("/", "~/")) or any(ord(c) < 32 for c in binary):
            raise ValueError("Guest Grok binary must be an absolute or home-relative path")
    return config


def load_config(path=None):
    path = path or os.environ.get("CAPO_PROVIDERS_CONFIG")
    if path is None:
        return {"grok": {"transport": "local"}}
    return validate_config(json.loads(Path(path).expanduser().read_text()))


def command(vm, *args):
    return ["limactl", "shell", "--workdir=/tmp", vm, "python3", "-c",
            Path(guest.__file__).read_text(), *map(str, args)]


def inspect_vm(vm):
    result = subprocess.run(["limactl", "list", vm, "--json"], capture_output=True, text=True, timeout=10)
    if result.returncode or not result.stdout.strip():
        raise ValueError(f"Lima VM {vm} is unavailable; create it using integrations/grok/lima.yaml")
    data = json.loads(result.stdout)
    if data.get("status") != "Running":
        raise ValueError(f"Lima VM {vm} is stopped; run limactl start {vm}")
    config = data.get("config", {})
    ssh = config.get("ssh", {})
    if (config.get("mounts") or config.get("additionalDisks")
            or ssh.get("forwardAgent") or ssh.get("forwardX11")
            or config.get("copyToHost") or config.get("propagateProxyEnv", True)
            or config.get("env")):
        raise ValueError("Lima worker must have no shared folders/disks, forwarded agents, or inherited environment")
    rules = config.get("portForwards", [])
    if any(rule.get("guestSocket") or rule.get("hostSocket") or not rule.get("ignore") for rule in rules):
        raise ValueError("Lima worker must not forward sockets or application ports")
    if not any(rule.get("proto") == "any" and rule.get("guestPortRange") == [1, 65535]
               and rule.get("ignore") for rule in rules):
        raise ValueError("Lima worker needs an explicit deny-all application port-forwarding rule")
    return data


def health(config):
    validate_config({"grok": config})
    inspect_vm(config["vm"])
    result = subprocess.run(command(config["vm"], "health", config.get("binary", "~/.grok/bin/grok")),
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError("Grok guest health check failed; install Python 3 and Grok in the configured VM")
    return json.loads(result.stdout)


def remote_status(record, cancel=False):
    """Never infer completion from a dropped SSH connection or an elapsed deadline."""
    result = subprocess.run(command(record["vm"], "cancel" if cancel else "status", record["run_id"]),
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError("Cannot reconcile Grok guest; restore its connection before recovery")
    return json.loads(result.stdout)


def reconcile(record_path):
    record = json.loads(Path(record_path).read_text())
    status = remote_status(record, cancel=True)
    if status.get("state") not in ("finished", "failed", "missing"):
        raise ValueError("Grok guest attempt may still be active; wait for its lease or cancel it before recovery")
    return status


def run_grok(config, prompt, schema, directory, timeout):
    from .providers import WorkerError

    state = health(config)
    if not state.get("binary_ok") or not state.get("bubblewrap"):
        raise WorkerError("Grok guest needs its executable and bubblewrap installed")
    if not state.get("login_file_present"):
        raise WorkerError("Grok guest is not signed in; run grok login --device-code inside the VM")
    if not 0 < timeout <= 86400:
        raise ValueError("VM request timeout must be between 0 and 86400 seconds")
    request = (json.dumps({"prompt": prompt, "schema": schema}) + "\n").encode()
    if len(request) > guest.MAX_OUTPUT:
        raise ValueError("VM request exceeds 4 MB")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {"vm": config["vm"], "run_id": uuid.uuid4().hex, "deadline": time.time() + timeout}
    (directory / "remote.json").write_text(json.dumps(record))
    argv = command(config["vm"], "run", record["run_id"], timeout,
                   config.get("binary", "~/.grok/bin/grok"))
    started = time.time()
    process = None
    with (directory / "stdout.txt").open("wb") as out, (directory / "stderr.txt").open("wb") as err:
        try:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                       start_new_session=True)
            (directory / "process.json").write_text(json.dumps({
                "pid": process.pid, "started": started, "command": "limactl"}))
            os.set_blocking(process.stdin.fileno(), False)
            pending = memoryview(request)
            heartbeat = time.monotonic()
            deadline = heartbeat + timeout
            while process.poll() is None:
                now = time.monotonic()
                if now >= deadline:
                    raise subprocess.TimeoutExpired(["limactl", "grok"], timeout)
                if sum((directory / name).stat().st_size for name in ("stdout.txt", "stderr.txt")) > 8_000_000:
                    raise WorkerError("VM transport log limit exceeded")
                if not pending and now >= heartbeat:
                    pending = memoryview(b"heartbeat\n")
                    heartbeat = now + 2
                if pending and select.select([], [process.stdin], [], 0.1)[1]:
                    try:
                        count = os.write(process.stdin.fileno(), pending[:65536])
                        pending = pending[count:]
                    except BrokenPipeError:
                        process.wait(timeout=5)
                        break
                else:
                    time.sleep(0.05)
            if process.returncode:
                raise WorkerError(f"Grok VM request failed; inspect {directory / 'stderr.txt'}")
        finally:
            if process is not None:
                process.stdin.close()  # EOF ends the guest's lease, including on SIGTERM.
                guest.terminate(process, grace=5)
            # Reach a terminal remote receipt before marking the local attempt done.
            # If disconnected, retain remote.json without a receipt for recovery.
            try:
                status = remote_status(record, cancel=True)
                until = time.monotonic() + guest.LEASE_SECONDS + 3
                while status.get("state") in ("starting", "running") and time.monotonic() < until:
                    time.sleep(0.2)
                    status = remote_status(record)
                if status.get("state") in ("finished", "failed", "missing"):
                    (directory / "remote-exit.json").write_text(json.dumps(status))
            except (ValueError, OSError, subprocess.SubprocessError):
                pass
            (directory / "exit.json").write_text(json.dumps({
                "returncode": process.returncode if process else None, "seconds": time.time() - started}))
    if not (directory / "remote-exit.json").exists():
        raise WorkerError("Remote completion is uncertain; reconcile the guest before retrying")
    if (directory / "stdout.txt").stat().st_size > guest.MAX_OUTPUT:
        raise WorkerError("VM response exceeds 4 MB")
    return (directory / "stdout.txt").read_text()
