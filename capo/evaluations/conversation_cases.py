"""Synthetic multi-turn requests with grades based on resulting service state."""
import copy
from .mail_cases import MailWorld
from .research_cases import ResearchWorld

CASES={
 'conversation_sessions':{'split':'development','conversation':True,'calendar':'primary','target':'planning','write':False,
  'request':'Add a separate calendar event for every session below on November 4, 2030, Pacific time. Keep my existing events. Summit: Breakfast 8–9 a.m.; Keynote 10–11 a.m.; Lunch noon–1 p.m.',
  'followups':['Please do that again—make sure all of them are there.']},
 'conversation_move':{'scenario':'move','split':'development','conversation':True,'calendar':'primary','target':'planning','write':False,
  'request':'Find my Planning appointment on my Work calendar on November 4, 2030. Tell me when it is; do not change it yet.',
  'followups':['Move that event to 2–3 p.m. Pacific. Keep the other details.']},
 'conversation_replied':{'split':'development','conversation':True,'calendar':'primary','target':'planning','write':False,'mail':True,'tasks':True,
  'request':'Read Morgan’s Planning email and track a task for me to reply. Do not send anything or draft a reply.',
  'followups':['I already replied. Please update my task list so it stops showing as something I need to do.']},
}

CASES['conversation_recurrence']={
 'split':'development','conversation':True,'series':True,'calendar':'primary','target':'planning','write':False,
 'request':'Create a personal Focus event every Monday for six occurrences, starting November 4, 2030, 2–3 p.m. Pacific. No guests. Keep my other events.',
 'followups':['Please check that the weekly series is there; do not add duplicates.']}

class ConversationWorld(MailWorld,ResearchWorld):
    def client(self,calendar_id='primary'):
        return ResearchWorld.client(self,calendar_id)

    def advance(self):
        self.before_followup=copy.deepcopy(self.calendars)
        self.writes_before_followup=len(self.writes)

    def grade(self,result):
        checks={'nonempty_answer':bool(result.get('reply','').strip()),'no_drafts':not self.drafts,'completion_report_accepted':result.get('status')=='reported_complete'}
        if self.case.get('series'):
            from capo.calendar_recurrence import equivalent_rules
            new=[row for id,row in self.calendars['primary'].items() if id not in self.original['primary']]
            checks.update(one_series=len(new)==1 and len(self.writes)==1,
                unchanged_followup=self.calendars==self.before_followup,
                correct_rule=len(new)==1 and equivalent_rules(new[0].get('recurrence',[]),['RRULE:FREQ=WEEKLY;COUNT=6'],'2030-11-04'),
                correct_time=len(new)==1 and new[0]['start']=={'dateTime':'2030-11-04T14:00:00-08:00','timeZone':'America/Los_Angeles'}
                    and new[0]['end']=={'dateTime':'2030-11-04T15:00:00-08:00','timeZone':'America/Los_Angeles'},
                others_preserved=all(self.calendars[c].get(id)==row for c,rows in self.original.items() for id,row in rows.items()))
        elif self.case.get('tasks'):
            rows=self.task_rows
            before=self.tasks_before_followup
            checks.update(calendar_unchanged=self.calendars==self.original,no_external_writes=not self.writes,
                one_task=len(rows)==1,initial_task_open=len(before)==1 and before[0]['status']=='open',
                same_task=len(rows)==len(before)==1 and rows[0]['id']==before[0]['id'],reply_task_closed=len(rows)==1 and rows[0]['status']=='completed',
                source_inspected=any(path in ('messages/mail1','threads/thread1') and args.get('format')=='full'
                    for path,args in self.mail_reads))
        elif self.case.get('scenario')=='move':
            expected=copy.deepcopy(self.original)
            row=expected['work']['planning']
            row.update(start={'dateTime':'2030-11-04T14:00:00-08:00','timeZone':'America/Los_Angeles'},
                end={'dateTime':'2030-11-04T15:00:00-08:00','timeZone':'America/Los_Angeles'},etag='"updated"')
            checks.update(correct_event_only=self.calendars==expected,one_write=len(self.writes)==1,
                no_early_write=self.writes_before_followup==0)
        else:
            new=[(c,row) for c,rows in self.calendars.items() for id,row in rows.items() if id not in self.original[c]]
            checks.update(existing_preserved=all(self.calendars[c].get(id)==row for c,rows in self.original.items() for id,row in rows.items()),
                three_events=len(new)==3,three_writes=len(self.writes)==3,
                retry_unchanged=self.calendars==self.before_followup,
                completed_first_turn=self.writes_before_followup==3)
            for name,hour in [('breakfast',8),('keynote',10),('lunch',12)]:
                matches=[(c,row) for c,row in new if name in row['summary'].casefold()]
                checks[name]=len(matches)==1 and matches[0][0]=='primary' and all(
                    matches[0][1][part].get('dateTime')==f'2030-11-04T{h:02}:00:00-08:00'
                    for part,h in [('start',hour),('end',hour+1)])
        return checks
