"""Owner-scoped personal tasks with transactional, replay-safe mutations."""
import calendar
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import TEXT, TEXTS, object_schema
from .research_tools import ReadTool

STATUSES = ['open', 'waiting', 'candidate', 'paused', 'dismissed', 'completed', 'cancelled']

FOLLOW_THROUGH = object_schema({
    'outcome': TEXT, 'completion_evidence': TEXTS, 'next_action': TEXT,
    'next_review_at': TEXT, 'decisions': TEXTS, 'uncertainties': TEXTS,
    'assignee': TEXT, 'original_conversation': TEXT,
})


FIELDS = object_schema({
    'title': TEXT, 'notes': TEXT, 'project': TEXT,
    'status': {'type': 'string', 'enum': STATUSES},
    'waiting_on': TEXT,
    'priority': {'type': 'string', 'enum': ['low', 'normal', 'high']},
    'due_at': TEXT, 'remind_at': TEXT, 'timezone': TEXT,
    'recurrence': {'type': 'string', 'enum': ['', 'daily', 'weekdays', 'weekly', 'monthly']},
    'dependencies': TEXTS, 'sources': TEXTS,
})


def instant(value, zone):
    """Require an explicit offset consistent with the named zone, including DST."""
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Time requires an explicit offset')
    local = dt.astimezone(ZoneInfo(zone))
    if local.replace(tzinfo=None) != dt.replace(tzinfo=None) or local.utcoffset() != dt.utcoffset():
        raise ValueError('Time does not exist in this timezone or has the wrong offset')
    return local


def next_occurrence(value, zone, recurrence):
    current = instant(value, zone)
    if recurrence == 'monthly':
        year, month = current.year + (current.month == 12), current.month % 12 + 1
        target = current.replace(year=year, month=month,
                                 day=min(current.day, calendar.monthrange(year, month)[1]))
    else:
        target = current + timedelta(days=7 if recurrence == 'weekly' else 1)
        while recurrence == 'weekdays' and target.weekday() >= 5:
            target += timedelta(days=1)
    # A recurring time in a spring-forward gap moves forward by the gap.
    target = target.astimezone(timezone.utc).astimezone(ZoneInfo(zone))
    return target.isoformat()


class Tasks:
    def __init__(self, home, owner, origin=None, allowed_actions=()):
        self.owner = owner
        self.origin = origin
        self.allowed_actions = tuple(allowed_actions)
        self.root = Path(home) / 'tasks' / hashlib.sha256(owner.encode()).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.path = self.root / 'tasks.sqlite3'
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_authority(task_id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, request TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history(task_id TEXT, revision INTEGER, data TEXT NOT NULL,
                    PRIMARY KEY(task_id, revision));
            ''')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            indexed = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_links'").fetchone()
            if not indexed:
                db.execute('CREATE TABLE task_links(source TEXT, title TEXT, task_id TEXT, PRIMARY KEY(source,title,task_id))')
                for (data,) in db.execute('SELECT data FROM tasks UNION ALL SELECT data FROM history'):
                    prior = json.loads(data)
                    db.executemany('INSERT OR IGNORE INTO task_links VALUES (?,?,?)',
                        [(source, prior['title'].strip().casefold(), prior['id']) for source in prior.get('sources', [])])
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _get(db, id):
        row = db.execute('SELECT data FROM tasks WHERE id=?', (id,)).fetchone()
        if not row:
            raise ValueError('Task not found')
        return json.loads(row[0])

    def get(self, id):
        with self.connection() as db:
            return self._view(db, self._get(db, id))

    @staticmethod
    def _view(db, task):
        value = dict(task)
        value['blocked_by'] = [dep for dep in task['dependencies']
                               if Tasks._get(db, dep)['status'] != 'completed']
        value['ready'] = task['status'] in ('open', 'waiting') and not value['blocked_by'] and not task['waiting_on']
        return value

    def search(self, query='', status='active', cursor=''):
        if len(query) > 500 or len(cursor) > 64:
            raise ValueError('Invalid task search')
        if status not in ('active', 'all', *STATUSES):
            raise ValueError('Invalid task status')
        with self.connection() as db:
            # JSON fields stay private; no user-provided SQL or wildcard interpretation.
            matches = []
            for (data,) in db.execute('SELECT data FROM tasks WHERE id>? ORDER BY id', (cursor,)):
                task = json.loads(data)
                if status == 'active' and task['status'] not in ('open', 'waiting'):
                    continue
                if status not in ('active', 'all') and task['status'] != status:
                    continue
                if query.casefold() not in '\n'.join(task[k] for k in ('title', 'notes', 'project', 'waiting_on')).casefold():
                    continue
                matches.append(self._view(db, task))
                if len(matches) == 101:
                    break
        return {'tasks': matches[:100], 'cursor': matches[99]['id'] if len(matches) > 100 else ''}

    def _validate(self, db, fields, id):
        from .contracts import validate
        validate(fields, FIELDS)
        if not fields['title'].strip() or len(fields['title']) > 200:
            raise ValueError('Task needs a short title')
        if any(len(fields[k]) > 4000 for k in ('notes', 'project', 'waiting_on')):
            raise ValueError('Task text too long')
        ZoneInfo(fields['timezone'])
        for key in ('due_at', 'remind_at'):
            if fields[key]:
                instant(fields[key], fields['timezone'])
        if fields['recurrence'] and not (fields['due_at'] or fields['remind_at']):
            raise ValueError('Recurring tasks need a deadline or reminder')
        if len(fields['dependencies']) > 30 or len(set(fields['dependencies'])) != len(fields['dependencies']):
            raise ValueError('Invalid dependencies')
        if len(fields['sources']) > 30 or any(len(s) > 1000 for s in fields['sources']):
            raise ValueError('Invalid source references')
        pending = list(fields['dependencies']); visited = set()
        while pending:
            dep = pending.pop()
            if dep == id:
                raise ValueError('Task dependencies must not form a cycle')
            if dep in visited:
                continue
            visited.add(dep)
            pending.extend(self._get(db, dep)['dependencies'])
        if fields['status'] == 'completed':
            if any(self._get(db, dep)['status'] != 'completed' for dep in fields['dependencies']):
                raise ValueError('Complete dependencies first')

    def save(self, id, expected_revision, fields, operation_id):
        if not operation_id or len(operation_id) > 2000:
            raise ValueError('Missing host action receipt')
        request = json.dumps([id, expected_revision, fields], sort_keys=True)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt = db.execute('SELECT request,result FROM receipts WHERE id=?', (operation_id,)).fetchone()
            if receipt:
                if receipt[0] != request:
                    raise ValueError('Action receipt cannot be reused for a different change')
                return json.loads(receipt[1])
            if id:
                previous = self._get(db, id)
                db.execute('INSERT OR IGNORE INTO history VALUES (?,?,?)', (id, previous['revision'], json.dumps(previous)))
                if str(previous['revision']) != expected_revision:
                    raise ValueError('Task changed; read it again before editing')
            else:
                if expected_revision:
                    raise ValueError('New tasks have no revision')
                id = hashlib.sha256(operation_id.encode()).hexdigest()
                previous = None
            self._validate(db, fields, id)
            if previous is None and fields['status'] in ('open','waiting','candidate'):
                if fields['sources']:
                    placeholders = ','.join('?' for _ in fields['sources'])
                    linked = db.execute('SELECT task_id FROM task_links WHERE title=? AND source IN ('+placeholders+') ORDER BY rowid LIMIT 1',
                                        [fields['title'].strip().casefold(), *fields['sources']]).fetchone()
                    if linked:
                        existing = self._get(db, linked[0])
                        result = {'task':existing, 'saved':False, 'already_exists':True}
                        db.execute('INSERT INTO receipts VALUES (?,?,?)', (operation_id, request, json.dumps(result)))
                        return result
                for row in db.execute('SELECT data FROM tasks'):
                    existing=json.loads(row[0])
                    if (all(existing.get(key)==value for key,value in fields.items())
                            or (existing['title'].strip().casefold() == fields['title'].strip().casefold()
                                and set(existing['sources']) & set(fields['sources']))):
                        result={'task':existing,'saved':False,'already_exists':True}
                        db.execute('INSERT INTO receipts VALUES (?,?,?)',(operation_id,request,json.dumps(result)))
                        return result
            now = datetime.now(timezone.utc).isoformat()
            task = dict(previous or {}, **fields)
            task.update(id=id, revision=previous['revision']+1 if previous else 1,
                        created_at=previous['created_at'] if previous else now, updated_at=now)
            # Preserve one task identity as a recurring commitment advances.
            if fields['status'] == 'completed' and fields['recurrence']:
                task['last_completed_at'] = now
                task['status'] = 'open'
                for key in ('due_at', 'remind_at'):
                    if task[key]:
                        task[key] = next_occurrence(task[key], task['timezone'], task['recurrence'])
            task.setdefault('follow_through', {key: [] if schema['type'] == 'array' else ''
                                             for key, schema in FOLLOW_THROUGH['properties'].items()})
            from .delegation import record_owner_request
            record_owner_request(db, task, self.origin, self.allowed_actions)
            result = {'task': task, 'saved': True}
            db.execute('INSERT INTO history VALUES (?,?,?)', (id, task['revision'], json.dumps(task)))
            db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?)', (id, json.dumps(task)))
            db.executemany('INSERT OR IGNORE INTO task_links VALUES (?,?,?)',
                           [(source, task['title'].strip().casefold(), id) for source in task['sources']])
            db.execute('INSERT INTO receipts VALUES (?,?,?)', (operation_id, request, json.dumps(result)))
            return result

    def history(self, id, cursor=''):
        if cursor and (not cursor.isdigit() or len(cursor) > 12):
            raise ValueError('Invalid history cursor')
        with self.connection() as db:
            self._get(db, id)
            rows = db.execute('SELECT data FROM history WHERE task_id=? AND revision>? ORDER BY revision LIMIT 21',
                              (id, int(cursor or 0))).fetchall()
        values = [json.loads(row[0]) for row in rows]
        return {'history': values[:20], 'cursor': str(values[19]['revision']) if len(values) > 20 else ''}

    def follow_through(self, id, expected_revision, details, operation_id):
        from .contracts import validate
        validate(details, FOLLOW_THROUGH)
        if not operation_id or len(operation_id) > 2000:
            raise ValueError('Missing host action receipt')
        if len(json.dumps(details)) > 16000:
            raise ValueError('Follow-through details too long')
        request = json.dumps(['follow_through', id, expected_revision, details], sort_keys=True)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt = db.execute('SELECT request,result FROM receipts WHERE id=?', (operation_id,)).fetchone()
            if receipt:
                if receipt[0] != request:
                    raise ValueError('Action receipt changed')
                return json.loads(receipt[1])
            task = self._get(db, id)
            if str(task['revision']) != expected_revision:
                raise ValueError('Task changed; read it again before editing')
            if details['next_review_at']:
                instant(details['next_review_at'], task['timezone'])
            # Old tasks acquire history on their first change; migrations need no model calls.
            db.execute('INSERT OR IGNORE INTO history VALUES (?,?,?)', (id, task['revision'], json.dumps(task)))
            task.update(follow_through=details, revision=task['revision'] + 1,
                        updated_at=datetime.now(timezone.utc).isoformat())
            from .delegation import record_owner_request
            record_owner_request(db, task, self.origin, self.allowed_actions)
            db.execute('INSERT INTO history VALUES (?,?,?)', (id, task['revision'], json.dumps(task)))
            db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(task), id))
            result = {'task': task, 'saved': True}
            db.execute('INSERT INTO receipts VALUES (?,?,?)', (operation_id, request, json.dumps(result)))
            return result

    def tools(self):
        return [
            ReadTool('tasks.search', 'Find personal tasks and reminders; active includes open and waiting. Use returned cursor for more.',
                     object_schema({'query': TEXT, 'status': {'type':'string','enum':['active','all',*STATUSES]}, 'cursor': TEXT}), self.search),
            ReadTool('tasks.history', 'Read earlier decisions, corrections and task states; paginated by revision.',
                     object_schema({'id': TEXT, 'cursor': TEXT}), self.history),
            ReadTool('tasks.follow_through', 'Update outcome, evidence, next action and review time on an existing task. These notes NEVER grant permission to act. Preserve prior decisions and original conversation. Empty values mean unset.',
                     object_schema({'id': TEXT, 'expected_revision': TEXT, 'details': FOLLOW_THROUGH}), self.follow_through, mutates=True),
            ReadTool('tasks.get', 'Read a task and its current revision before editing.', object_schema({'id': TEXT}), self.get),
            ReadTool('tasks.save', 'Create or edit an owner-requested personal task/reminder. Empty id/revision creates; edits require current revision as a string and all fields. Empty strings/lists mean unset. Dates require explicit local offset and timezone; ask when ambiguous. Use candidate for uncertain possibilities, paused to suspend, dismissed to ignore, and completed/cancelled to close. Candidates and paused/dismissed tasks never trigger reminders. Complete/cancel using status. Recurring completion advances dates while preserving ID. Sources link email, calendar or project evidence; never duplicate an existing linked task.',
                     object_schema({'id': TEXT, 'expected_revision': TEXT, 'fields': FIELDS}), self.save, mutates=True),
        ]
