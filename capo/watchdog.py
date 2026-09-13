"""Private subprocess supervisor; EOF from the owning process cancels its worker."""

import os
import select
import signal
import subprocess
import sys
import time


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
        else:
            if not confirmed:
                # Reaping can remove the last process-group member. Probe again
                # so a transient denial on an exited child is not a false alarm.
                try:
                    os.killpg(worker.pid, 0)
                except ProcessLookupError:
                    confirmed = True
                except PermissionError:
                    pass
    finally:
        os.close(parent_fd)
    return confirmed


def main():
    parent_fd, timeout = int(sys.argv[1]), float(sys.argv[2])
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker = subprocess.Popen(sys.argv[3:], start_new_session=True)
    deadline = time.monotonic() + timeout
    result = 125
    try:
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
        if not cleanup(worker, parent_fd):
            print("Worker process-group cleanup could not be confirmed; inspect local processes before retrying.", file=sys.stderr)
            result = 126
    return result


if __name__ == "__main__":
    raise SystemExit(main())
