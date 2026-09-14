"""Private subprocess supervisor; EOF from the owning process cancels its worker."""

import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path


def cleanup(worker, parent_fd):
    """Attempt every cleanup step, even when the OS denies a group probe.

    EPERM is not evidence that a process group disappeared. Retain failure while
    attempting direct-child termination and reaping; callers must not report a
    successful attempt when group cleanup could not be confirmed.
    """
    confirmed = True
    try:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(worker.pid, sig)
            except ProcessLookupError:
                # A later ESRCH positively establishes that the group is gone,
                # including macOS races where an earlier zero-signal probe gave EPERM.
                if sig == signal.SIGKILL:
                    confirmed = True
            except PermissionError:
                confirmed = False
                try:
                    worker.send_signal(sig)
                except (ProcessLookupError, PermissionError):
                    pass
            if sig == signal.SIGTERM:
                until = time.monotonic() + 2
                while time.monotonic() < until:
                    try:
                        os.killpg(worker.pid, 0)
                    except ProcessLookupError:
                        break
                    except PermissionError:
                        confirmed = False
                        break
                    worker.poll()
                    time.sleep(0.05)
        try:
            worker.wait(timeout=2)
        except subprocess.TimeoutExpired:
            confirmed = False
        # A successful kill call alone does not establish group termination.
        until = time.monotonic() + 2
        confirmed = False
        while True:
            try:
                os.killpg(worker.pid, 0)
            except ProcessLookupError:
                confirmed = True
                break
            except PermissionError:
                break
            if time.monotonic() >= until:
                break
            time.sleep(0.05)
    finally:
        os.close(parent_fd)
    return confirmed


def save_receipt(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main():
    if sys.argv[1] == "--worker":
        gate = int(sys.argv[2])
        ready = os.read(gate, 1)
        os.close(gate)
        if ready != b"1":
            return 125
        os.execvp(sys.argv[3], sys.argv[3:])
    parent_fd, timeout = int(sys.argv[1]), float(sys.argv[2])
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if os.read(parent_fd, 1) != b"1":
        os.close(parent_fd)
        return 125
    directory = Path(sys.argv[3])
    gate_read, gate_write = os.pipe()
    try:
        worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                   "--worker", str(gate_read), *sys.argv[4:]],
                                  start_new_session=True, pass_fds=(gate_read,))
    except BaseException:
        os.close(gate_write)
        os.close(parent_fd)
        raise
    finally:
        os.close(gate_read)
    receipt = {"pid": worker.pid, "pgid": worker.pid, "cleanup_confirmed": False}
    deadline = time.monotonic() + timeout
    result = 125
    try:
        save_receipt(directory / "worker.json", receipt)
        os.write(gate_write, b"1")
        os.close(gate_write)
        gate_write = None
        while worker.poll() is None:
            if stopped or time.monotonic() >= deadline:
                result = 124
                break
            readable, _, _ = select.select([parent_fd], [], [], 0.05)
            if readable and not os.read(parent_fd, 1):
                break
        else:
            result = worker.returncode
    finally:
        if gate_write is not None:
            os.close(gate_write)
        receipt["cleanup_confirmed"] = cleanup(worker, parent_fd)
        save_receipt(directory / "worker.json", receipt)
        if not receipt["cleanup_confirmed"]:
            print("Worker process-group cleanup could not be confirmed; inspect local processes before retrying.", file=sys.stderr)
            result = 126
    return result


if __name__ == "__main__":
    raise SystemExit(main())
