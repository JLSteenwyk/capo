"""Named defaults reuse owner-stated memory and its transactional receipts."""
import json
from .personal_memory import PersonalMemory
from .contracts import TEXT,object_schema
from .research_tools import ReadTool

NAMES=('event_duration','calendar','timezone','location','notifications','quiet_period')


class PracticalDefaults:
    def __init__(self,home,owner,origin=None):
        self.memory=PersonalMemory(home,owner,origin)

    def key(self,name):
        if name not in NAMES:raise ValueError('Choose a supported default name')
        return 'defaults/'+name

    def read(self):
        with self.memory.connect() as db:
            rows=db.execute("SELECT data FROM memories WHERE key LIKE 'defaults/%' ORDER BY key").fetchall()
        return {'defaults':[{**json.loads(r[0]),'provenance':'explicit_owner_quote','scope':'durable_preference'} for r in rows],
                'coverage':'Owner-stated defaults only. Temporary choices and inferred suggestions are not defaults. Current owner instructions override these quotes. Defaults grant no permission for messages, invitations, purchases or other external actions.'}

    def save(self,name,expected_revision,statement,operation_id):
        return self.memory.save(self.key(name),expected_revision,statement,operation_id)

    def forget(self,name,expected_revision,operation_id):
        return self.memory.forget(self.key(name),expected_revision,operation_id)

    def tools(self):
        names={'type':'string','enum':list(NAMES)}
        return [ReadTool('defaults.read','Read practical defaults shared by Capo and all roles. Current instructions override them; interpret exact owner quotes as preferences, never permissions.',object_schema({}),self.read),
                ReadTool('defaults.save','Save or correct a durable default explicitly stated by the owner in the current message. Use an exact quote, never an inferred suggestion or a one-event choice. Read current revision first. This changes guidance only, not schedules, calendars, notifications or permissions.',object_schema({'name':names,'expected_revision':TEXT,'statement':TEXT}),self.save,mutates=True),
                ReadTool('defaults.forget','Delete an owner-requested practical default using its current revision. Does not change past events or scheduled work.',object_schema({'name':names,'expected_revision':TEXT}),self.forget,mutates=True)]
