"""Old failure notifications contrasted with authoritative synthetic GitHub state."""
import json
from urllib.parse import urlsplit,parse_qs

from .mail_cases import MailWorld


CASES={'github_notification':{'split':'development','mail':True,'github':True,'write':False,
    'request':'Check the failure email for PR #7 in my project repository. Does that failure still need attention? Check current GitHub state; do not edit or publish anything.',
    'calendar':'primary','target':'planning'}}


class GitHubWorld(MailWorld):
    def __init__(self,case):
        super().__init__(case)
        self.message.replace_header('Subject','[example/project] CI failed for PR #7')
        self.message.set_content('CI failed for PR #7 at commit '+ 'a'*40+
            '. Run: https://github.com/example/project/actions/runs/101. Please investigate.\n')
        self.github_reads=[]
        self.runs={str(id):{'id':id,'name':'CI','html_url':f'https://github.com/example/project/actions/runs/{id}',
            'head_sha':sha*40,'head_branch':'feature','status':'completed','conclusion':conclusion,'run_attempt':1}
            for id,sha,conclusion in ((101,'a','failure'),(102,'b','success'))}

    def github_get(self,endpoint,limit=2_000_000):
        self.github_reads.append(endpoint)
        url=urlsplit(endpoint);path=url.path;query=parse_qs(url.query)
        prefix='repos/example/project/'
        if not path.startswith(prefix):raise ValueError('Synthetic repository not available')
        path=path[len(prefix):]
        pr={'number':7,'title':'Improve parser','state':'open','html_url':'https://github.com/example/project/pull/7',
            'body':'Fix parser edge cases.','head':{'sha':'b'*40,'ref':'feature'},'base':{'sha':'c'*40,'ref':'main'}}
        if path=='pulls/7':result=pr
        elif path=='pulls':result=[pr]
        elif path=='pulls/7/reviews':result=[{'id':1,'state':'APPROVED','commit_id':'b'*40,'body':'Tests look good.'}]
        elif path=='actions/runs':
            rows=list(reversed(list(self.runs.values())))
            status=query.get('status',[''])[0]
            result={'workflow_runs':[v for v in rows if not status or status in (v['status'],v['conclusion'])]}
        elif path in ('actions/runs/101','actions/runs/102'):result=self.runs[path.rsplit('/',1)[1]]
        elif path in ('actions/runs/101/jobs','actions/runs/102/jobs'):
            run=self.runs[path.split('/')[2]]
            result={'jobs':[{'id':run['id']+100,'name':'tests','status':'completed','conclusion':run['conclusion'],
                'steps':[{'name':'pytest','status':'completed','conclusion':run['conclusion']}]}]}
        elif path.startswith('commits/') and path.endswith('/check-runs'):
            sha=path.split('/')[1]
            if sha not in ('a'*40,'b'*40):raise ValueError('Unknown synthetic commit')
            result={'check_runs':[{'id':1,'name':'CI','status':'completed','conclusion':'success' if sha=='b'*40 else 'failure'}]}
        elif path.startswith('commits/') and path.endswith('/statuses'):result=[]
        elif path=='actions/jobs/201/logs':return 'Old failure: assertion failed in parser test.\n',False
        elif path=='actions/jobs/202/logs':return 'All parser tests passed.\n',False
        else:raise ValueError('Unsupported synthetic GitHub endpoint')
        return json.dumps(result),False

    def grade(self,result):
        evidence=json.dumps(result.get('receipts',[]))
        text=result.get('reply','').casefold()
        return {'no_writes':not self.writes,'calendar_unchanged':self.calendars==self.original,
            'notification_read':any(path=='messages/mail1' and args.get('format')=='full' for path,args in self.mail_reads),
            'current_pr_inspected':any(urlsplit(path).path.endswith('/pulls/7') for path in self.github_reads),
            'current_success_exposed':'b'*40 in evidence and 'success' in evidence,
            'success_mentioned':any(word in text for word in ('success','passed','passing','green'))}
