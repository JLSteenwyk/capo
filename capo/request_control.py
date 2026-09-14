"""Thread-scoped cancellation of owner-authorized conversational work."""
import hashlib
import json
from pathlib import Path

from .conversation import _write

STOPPED = 'Stopped this request. Any completed actions and saved progress are preserved.'


class Requests:
    def __init__(self, home, owner, thread):
        self.home = Path(home)
        self.owner = owner
        self.thread = thread

    def active(self):
        owner_hash = hashlib.sha256(self.owner.encode()).hexdigest()
        roots = [self.home/'capabilities'/owner_hash, self.home/'team'/owner_hash,
                 self.home/'conversation']
        found = []
        for root in roots:
            for marker in root.rglob('started.json'):
                directory = marker.parent
                if (directory/'outcome.json').exists() or (directory/'cancelled.json').exists():
                    continue
                context = json.loads(marker.read_text()).get('context', {})
                if context.get('request_thread') != self.thread:
                    continue
                if root == self.home/'conversation' and context.get('owner_scope') != self.owner:
                    continue
                found.append(directory)
        return found

    def cancel(self, event_id):
        directories = self.active()
        for directory in directories:
            _write(directory/'cancelled.json', {'owner_event':event_id})
        return len(directories)
