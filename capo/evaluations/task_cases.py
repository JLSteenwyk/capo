"""Correspondence-to-task evaluation using an isolated real task database."""
from .mail_cases import MailWorld

CASES={'commitment_notes':{'split':'development','mail':True,'tasks':True,'write':False,
    'calendar':'primary','target':'planning',
    'request':'Read my Notes email and track the firm commitment, possible idea, and item awaiting a reply as separate tasks. Preserve their different levels of commitment. Do not send mail or change my calendar.'}}


class TaskWorld(MailWorld):
    def __init__(self,case):
        super().__init__(case)
        self.message.replace_header('Subject','Notes')
        self.message.replace_header('From','Owner <owner@example.invalid>')
        self.message.set_content('I will send the budget report. I am considering a workshop, but have not committed. I am waiting for Morgan to reply with the venue address. No deadlines or reminders yet.\n')
        self.task_rows=[]

    def grade(self,result):
        matches={name:[t for t in self.task_rows if name in (t['title']+' '+t['notes']).casefold()]
                 for name in ('budget','workshop','venue')}
        checks={'source_inspected':any(path in ('messages/mail1','threads/thread1') and args.get('format')=='full'
                                      for path,args in self.mail_reads),
                'three_tasks':len(self.task_rows)==3,
                'no_external_writes':not self.writes,'calendar_unchanged':self.calendars==self.original,
                'no_invented_schedule':all(not t['due_at'] and not t['remind_at'] and not t['recurrence'] for t in self.task_rows)}
        for name,status in (('budget','open'),('workshop','candidate'),('venue','waiting')):
            checks[name+'_status']=len(matches[name])==1 and matches[name][0]['status']==status
        checks['waiting_person']=len(matches['venue'])==1 and 'morgan' in matches['venue'][0]['waiting_on'].casefold()
        checks['source_links']=len(self.task_rows)==3 and all(t['sources'] for t in self.task_rows)
        return checks
