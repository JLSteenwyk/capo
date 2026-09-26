"""Replace persistently disconnected Socket Mode clients without restarting work."""
import threading
import time
from pathlib import Path

from .conversation import _write


class ConnectionWatchdog:
    def __init__(self, factory, home, clock=time.monotonic, grace=30, maximum_delay=300):
        self.factory, self.clock = factory, clock
        self.path = Path(home)/'slack-connection.json'
        self.grace, self.maximum_delay = grace, maximum_delay
        self.handler = None
        self.down_since = None
        self.up_since = None
        self.next_attempt = 0
        self.failures = 0
        self.stopped = threading.Event()
        self.thread = None

    def connected(self):
        handler = self.handler
        try:
            return bool(handler and handler.client.is_connected())
        except Exception:
            return False

    def record(self, status, delay=0):
        # Never persist exception text, connection URLs, credentials or SDK objects.
        _write(self.path, {'status': status, 'at': time.time(),
                          'consecutive_attempts': self.failures, 'retry_in_seconds': delay})

    def step(self):
        if self.stopped.is_set(): return
        now = self.clock()
        if self.connected():
            if self.up_since is None:
                self.up_since = now
                self.record('connected')
            self.down_since = None
            if now-self.up_since >= 60:
                self.failures = 0
                self.next_attempt = 0
            return
        self.up_since = None
        if self.down_since is None:
            self.down_since = now
            self.record('disconnected')
        # Allow the SDK's own reconnect loop to repair short interruptions first.
        if self.handler is not None and now-self.down_since < self.grace: return
        if now < self.next_attempt: return
        self.failures += 1
        delay = min(self.maximum_delay, self.grace * 2**min(self.failures-1, 10))
        self.next_attempt = now+delay
        self.record('reconnecting', delay)
        old = self.handler
        if old is not None:
            # Do not open a second client if the old one cannot be closed.
            old.close()
            self.handler = None
        if self.stopped.is_set(): return
        self.handler = self.factory()
        self.handler.connect()
        # Mark recovery only after the SDK confirms an active connection.
        if self.connected():
            self.up_since = self.clock()
            self.down_since = None
            self.record('connected')

    def run(self):
        try:
            while not self.stopped.is_set():
                try:
                    self.step()
                except Exception:
                    self.record('reconnect_failed', max(0, self.next_attempt-self.clock()))
                self.stopped.wait(5)
        finally:
            if self.handler is not None:
                try: self.handler.close()
                except Exception: pass

    def start(self):
        self.thread = threading.Thread(target=self.run, name='slack-connection-watchdog', daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.thread is not None: self.thread.join(timeout=5)
