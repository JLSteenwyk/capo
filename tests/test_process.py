import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from capo.process import run_process


class ProcessCase(unittest.TestCase):
    def test_stdin_and_output_survive_supervision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(run_process([sys.executable, "-c", "print(input().upper())"],
                                         root, root / "attempt", 10, "hello\n"), "HELLO\n")

    def test_hard_supervisor_death_terminates_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            worker_pid = root / "worker.pid"
            worker = ("import os,time; from pathlib import Path; "
                      f"Path({str(worker_pid)!r}).write_text(str(os.getpid())); time.sleep(60)")
            parent = subprocess.Popen([sys.executable, "-c",
                "from capo.process import run_process; from pathlib import Path; "
                f"run_process({[sys.executable, '-c', worker]!r}, Path({str(root)!r}), "
                f"Path({str(root / 'attempt')!r}), 60)"], start_new_session=True)
            pid = None
            try:
                deadline = time.monotonic() + 10
                while not worker_pid.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(worker_pid.exists(), "worker failed to start")
                pid = int(worker_pid.read_text())
                parent.kill()
                parent.wait(timeout=5)
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(.05)
                else:
                    self.fail("worker survived supervisor SIGKILL")
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait()
                if pid:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
