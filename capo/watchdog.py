"""Private subprocess supervisor; EOF from the owning process cancels its worker."""

import os
import select
import signal
import subprocess
import sys
import time


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
    try:
        while worker.poll() is None:
            if stopped or time.monotonic() >= deadline:
                return 124
            readable, _, _ = select.select([parent_fd], [], [], 0.05)
            if readable and not os.read(parent_fd, 1):
                return 125
        return worker.returncode
    finally:
        try:
            os.killpg(worker.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        until = time.monotonic() + 2
        while time.monotonic() < until:
            try:
                os.killpg(worker.pid, 0)
            except ProcessLookupError:
                break
            worker.poll()
            time.sleep(0.05)
        try:
            os.killpg(worker.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        worker.wait()
        os.close(parent_fd)


if __name__ == "__main__":
    raise SystemExit(main())
