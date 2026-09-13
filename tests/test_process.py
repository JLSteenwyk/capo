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


class WatchdogCleanupCase(unittest.TestCase):
    def test_permission_denied_probe_does_not_skip_kill_reap_or_close(self):
        from unittest.mock import Mock, patch, call
        from capo.watchdog import cleanup
        worker = Mock(pid=12345)
        with patch('capo.watchdog.os.killpg', side_effect=[None, PermissionError(), None, PermissionError()]) as killpg, \
                patch('capo.watchdog.os.close') as close:
            self.assertFalse(cleanup(worker, 99))
        self.assertEqual(killpg.call_args_list, [call(12345, signal.SIGTERM), call(12345, 0), call(12345, signal.SIGKILL), call(12345, 0)])
        worker.wait.assert_called_once_with(timeout=2)
        close.assert_called_once_with(99)

    def test_denied_group_signals_attempt_direct_child_cleanup(self):
        from unittest.mock import Mock, patch, call
        from capo.watchdog import cleanup
        worker = Mock(pid=12345)
        with patch('capo.watchdog.os.killpg', side_effect=PermissionError()), patch('capo.watchdog.os.close'):
            self.assertFalse(cleanup(worker, 99))
        self.assertEqual(worker.send_signal.call_args_list, [call(signal.SIGTERM), call(signal.SIGKILL)])
        worker.wait.assert_called_once_with(timeout=2)

    def test_uncertain_cleanup_does_not_report_successful_cancellation(self):
        from unittest.mock import Mock, patch
        from capo.process import WorkerError, run_process
        worker = Mock(pid=12345, returncode=126)
        worker.communicate.side_effect = KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('capo.process.subprocess.Popen', return_value=worker), patch('capo.process.os.killpg'):
                with self.assertRaisesRegex(WorkerError, 'cleanup could not be confirmed'):
                    run_process(['synthetic-worker'], root, root / 'attempt', 10)

    def test_later_absent_group_resolves_denied_probe(self):
        from unittest.mock import Mock, patch
        from capo.watchdog import cleanup
        worker = Mock(pid=12345)
        with patch('capo.watchdog.os.killpg', side_effect=[None, PermissionError(), ProcessLookupError()]), \
                patch('capo.watchdog.os.close'):
            self.assertTrue(cleanup(worker, 99))
        worker.wait.assert_called_once_with(timeout=2)

    def test_reaping_resolves_transient_group_denial(self):
        from unittest.mock import Mock, patch
        from capo.watchdog import cleanup
        worker = Mock(pid=12345)
        with patch('capo.watchdog.os.killpg', side_effect=[None, PermissionError(), PermissionError(), ProcessLookupError()]), \
                patch('capo.watchdog.os.close'):
            self.assertTrue(cleanup(worker, 99))
        worker.wait.assert_called_once_with(timeout=2)
