import json
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from capo import guest
from capo.providers import Providers, WorkerError
from capo.transport import command, inspect_vm, reconcile, run_grok, validate_config


class TransportCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"HOME": str(self.root)})
        self.environment.start()
        self.binary = self.root / "fake-grok"
        self.binary.write_text('#!' + sys.executable + '\nimport json\nprint(json.dumps({"structuredOutput":{"ok":True},"stopReason":"end_turn"}))\n')
        self.binary.chmod(0o700)
        self.config = {"transport": "lima", "vm": "test-vm", "binary": str(self.binary)}

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def local_command(self, vm, *args):
        return [sys.executable, str(Path(guest.__file__)), *map(str, args)]

    def test_round_trip_and_private_staging_cleanup(self):
        with patch("capo.transport.command", side_effect=self.local_command), patch("capo.transport.health",
                   return_value={"binary_ok": True, "bubblewrap": True, "login_file_present": True}):
            result = Providers(5, {"grok": self.config}).call("grok", "synthetic task", {}, self.root, self.root / "attempt")
        self.assertEqual(result, {"ok": True})
        record = json.loads((self.root / "attempt/remote.json").read_text())
        directory = guest.root_dir() / record["run_id"]
        self.assertEqual({p.name for p in directory.iterdir()}, {"status.json", "cancel"})
        self.assertEqual(json.loads((directory / "status.json").read_text())["state"], "finished")
        self.assertTrue((self.root / "attempt/remote-exit.json").exists())

    def test_timeout_reconciles_remote_process(self):
        self.binary.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(60)\n')
        with patch("capo.transport.command", side_effect=self.local_command), patch("capo.transport.health",
                   return_value={"binary_ok": True, "bubblewrap": True, "login_file_present": True}):
            with self.assertRaises((subprocess.TimeoutExpired, WorkerError)):
                run_grok(self.config, "synthetic", {}, self.root / "attempt", 0.4)
        status = json.loads((self.root / "attempt/remote-exit.json").read_text())
        self.assertEqual(status["state"], "failed")
        with self.assertRaises(ProcessLookupError):
            os.kill(status["pid"], 0)

    def test_disconnection_kills_guest_worker(self):
        self.binary.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(60)\n')
        run_id = uuid.uuid4().hex
        process = subprocess.Popen(self.local_command("test", "run", run_id, 30, self.binary),
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            process.stdin.write(b'{"prompt":"synthetic","schema":{}}\n')
            process.stdin.flush()
            receipt = guest.root_dir() / run_id / "status.json"
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if receipt.exists() and json.loads(receipt.read_text()).get("state") == "running":
                    break
                time.sleep(0.02)
            else:
                self.fail("Guest did not start")
            pid = json.loads(receipt.read_text())["pid"]
            process.stdin.close()
            process.wait(timeout=5)
            self.assertEqual(json.loads(receipt.read_text())["state"], "failed")
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_cancel_before_delayed_start_fences_attempt(self):
        run_id = uuid.uuid4().hex
        cancel = subprocess.run(self.local_command("test", "cancel", run_id), capture_output=True, text=True)
        self.assertEqual(json.loads(cancel.stdout)["state"], "failed")
        delayed = subprocess.run(self.local_command("test", "run", run_id, 2, self.binary),
                                 input='{"prompt":"task","schema":{}}\n', capture_output=True, text=True)
        self.assertNotEqual(delayed.returncode, 0)
        status = json.loads((guest.root_dir() / run_id / "status.json").read_text())
        self.assertNotIn("pid", status)

    def test_interrupted_error_handler_still_records_terminal_receipt(self):
        run_id = uuid.uuid4().hex
        # Deterministically reproduce a second interruption just before the
        # exception handler records failure; cleanup must normalize the state.
        script = '''import sys
from unittest.mock import patch
from capo import guest
def interrupt(frame, event, arg):
    if (frame.f_code.co_name == "run" and event == "line"
            and isinstance(frame.f_locals.get("exc"), RuntimeError)):
        sys.settrace(None)
        raise KeyboardInterrupt()
    return interrupt
with patch("subprocess.Popen", side_effect=RuntimeError("synthetic startup failure")):
    sys.settrace(interrupt)
    try:
        guest.run(sys.argv[1], 2, "/unused")
    except BaseException:
        pass
    finally:
        sys.settrace(None)
'''
        result = subprocess.run([sys.executable, "-c", script, run_id],
                                input='{"prompt":"task","schema":{}}\n', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads((guest.root_dir() / run_id / "status.json").read_text())
        self.assertEqual(status["state"], "failed")
        self.assertIn("finished", status)

    def test_missing_auth_does_not_dispatch(self):
        with patch("capo.transport.health", return_value={"binary_ok": True, "bubblewrap": True,
                                                          "login_file_present": False}), patch("subprocess.Popen") as start:
            with self.assertRaisesRegex(WorkerError, "not signed in"):
                run_grok(self.config, "task", {}, self.root / "attempt", 5)
            start.assert_not_called()

    def test_small_bootstrap_executes_original_guest_health(self):
        argv = command("test-vm", "health", self.binary)
        self.assertLess(len(argv[6]), 7000)
        result = subprocess.run([sys.executable, "-c", *argv[6:]], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["binary_ok"])

    def test_guest_reboot_invalidates_running_receipt_without_killing_reused_pid(self):
        run_id = uuid.uuid4().hex
        directory = guest.root_dir() / run_id
        directory.mkdir()
        (directory / "status.json").write_text(json.dumps({"state": "running", "boot_id": "old-boot", "pid": os.getpid()}))
        with patch("capo.guest.boot_id", return_value="new-boot"), contextlib.redirect_stdout(io.StringIO()) as out:
            guest.control("cancel", run_id)
        self.assertEqual(json.loads(out.getvalue())["state"], "failed")
        self.assertEqual(json.loads((directory / "status.json").read_text())["state"], "failed")

    def test_configuration_rejects_unknown_modes_and_shell_names(self):
        for config in [{"grok": {"transport": "api"}}, {"grok": {"transport": "lima", "vm": "x; sh"}},
                       {"grok": {"transport": "lima", "vm": "x", "token": "fake"}}]:
            with self.assertRaises(ValueError):
                validate_config(config)

    def test_vm_policy_rejects_host_mounts(self):
        fake = subprocess.CompletedProcess([], 0, json.dumps({"status": "Running", "config": {
            "mounts": [{"location": "~"}], "propagateProxyEnv": False}}), "")
        with patch("subprocess.run", return_value=fake), self.assertRaisesRegex(ValueError, "shared folders"):
            inspect_vm("test")

    def test_recovery_does_not_accept_active_or_unreachable_guest(self):
        record = self.root / "remote.json"
        record.write_text(json.dumps({"vm": "test", "run_id": uuid.uuid4().hex}))
        with patch("capo.transport.remote_status", return_value={"state": "running"}):
            with self.assertRaisesRegex(ValueError, "still be active"):
                reconcile(record)
        with patch("capo.transport.remote_status", side_effect=ValueError("disconnected")):
            with self.assertRaisesRegex(ValueError, "disconnected"):
                reconcile(record)


if __name__ == "__main__":
    unittest.main()
