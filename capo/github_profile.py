"""Account-wide GitHub discovery with host-enforced exclusions and bounded sweeps."""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .contracts import TEXT, object_schema
from .github_tools import ReadClient, GitHubReadError, positive_number, selected
from .research_tools import ReadTool

NAME = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
LOGIN = re.compile(r'[A-Za-z0-9-]{1,39}\Z')


def settings(config):
    p=config.get('github_profile', {})
    if not isinstance(p,dict) or set(p)-{'enabled','login','auth','excluded_owners','excluded_terms'}:
        raise ValueError('Invalid GitHub profile configuration')
    if type(p.get('enabled',False)) is not bool: raise ValueError('Invalid GitHub profile flag')
    if not p.get('enabled',False): return None
    if not isinstance(p.get('login'),str) or not LOGIN.fullmatch(p['login']): raise ValueError('Invalid GitHub profile login')
    if p.get('auth','keyring') not in ('default','keyring'): raise ValueError('Invalid GitHub profile authentication')
    owners=p.get('excluded_owners',[]);terms=p.get('excluded_terms',[])
    if not isinstance(owners,list) or any(not isinstance(x,str) or not LOGIN.fullmatch(x) for x in owners):
        raise ValueError('Invalid excluded GitHub owners')
    if not isinstance(terms,list) or len(terms)>20 or any(not isinstance(x,str) or not x.strip() or len(x)>100 for x in terms):
        raise ValueError('Invalid excluded GitHub terms')
    return dict(p,auth=p.get('auth','keyring'),excluded_owners=[x.casefold() for x in owners],excluded_terms=[x.casefold() for x in terms])


class GitHubProfile:
    def __init__(self, policy, home, client_factory=ReadClient):
        self.policy=policy;self.home=Path(home);self.client=client_factory(policy['auth'])
        self.known=set();self.checked={};self.authenticated=False

    def excluded(self, value):
        text=json.dumps(value,ensure_ascii=False).casefold()
        return any(term in text for term in self.policy['excluded_terms']) or any(
            re.search(r'(?<![a-z0-9-])'+re.escape(owner)+r'(?![a-z0-9-])',text)
            for owner in self.policy['excluded_owners'])

    def guard(self,name):
        if not isinstance(name,str) or not NAME.fullmatch(name) or any(x in ('.','..') for x in name.split('/')):
            raise ValueError('Use a discovered owner/repository name')
        if name.split('/')[0].casefold() in self.policy['excluded_owners'] or self.excluded(name):
            raise PermissionError('Repository is outside the allowed GitHub profile scope')

    def api(self,path,params=None):
        if not self.authenticated:
            text,truncated=self.client.get('user')
            if truncated or json.loads(text).get('login','').casefold()!=self.policy['login'].casefold():
                raise PermissionError('GitHub authentication does not match the configured profile')
            self.authenticated=True
        text,truncated=self.client.get(path+('?' + urlencode(params) if params else ''))
        if truncated: raise GitHubReadError('GitHub profile response exceeded the inspection limit')
        return json.loads(text)

    def accept(self,row):
        name=row.get('full_name','')
        try:self.guard(name)
        except (ValueError,PermissionError):return False
        if self.excluded(row):return False
        if row.get('fork') and name.casefold() not in self.checked:
            detail=self.api('repos/'+name)
            self.checked[name.casefold()]=not self.excluded(detail)
        if self.checked.get(name.casefold()) is False:return False
        if not row.get('fork'):self.checked.setdefault(name.casefold(),True)
        self.known.add(name.casefold());return True

    def authorize(self,name,configured=False):
        self.guard(name)
        if name.casefold() not in self.known and not configured:
            raise ValueError('Discover the repository with GitHub profile tools before inspecting it')
        if name.casefold() not in self.checked:
            detail=self.api('repos/'+name)
            self.checked[name.casefold()]=not self.excluded(detail)
        if not self.checked[name.casefold()]:raise PermissionError('Repository is outside the allowed GitHub profile scope')
        return name,self.client

    def repositories(self,page):
        page=positive_number(page)
        if int(page)>20:raise ValueError('Repository discovery page limit reached')
        rows=self.api('user/repos',dict(affiliation='owner,collaborator,organization_member',sort='full_name',per_page=100,page=page))
        if not isinstance(rows,list):raise GitHubReadError('Unexpected repository discovery response')
        allowed=[];unavailable=0
        for row in rows:
            try:
                if self.accept(row):allowed.append(selected(row,'full_name html_url private archived disabled fork default_branch pushed_at open_issues_count'))
            except (GitHubReadError,PermissionError):unavailable+=1
        return {'repositories':allowed,'next_page':str(int(page)+1) if len(rows)==100 and int(page)<20 else '',
                'limited':len(rows)==100 and int(page)==20,'unavailable':unavailable,
                'coverage':'Accessible owned, collaborator and organization repositories. Exclusions applied before returning metadata; forks checked against parent/source metadata.'}

    def activity(self,kind,page):
        page=positive_number(page)
        if int(page)>10:raise ValueError('Activity page limit reached')
        login=self.policy['login']
        queries={'involved':'is:open involves:'+login,'owned':'is:open user:'+login,
                 'reviews':'is:open is:pr review-requested:'+login}
        if kind=='notifications':
            rows=self.api('notifications',dict(all='true',per_page=100,page=page))
            more=len(rows)==100;incomplete=False
        elif kind in queries:
            q=queries[kind]+''.join(' -org:'+x for x in self.policy['excluded_owners'])
            data=self.api('search/issues',dict(q=q,sort='updated',order='desc',per_page=100,page=page))
            rows=data['items'];more=int(data.get('total_count',0))>int(page)*100;incomplete=data.get('incomplete_results',False)
        else:raise ValueError('Unknown GitHub activity kind')
        items=[];unavailable=0
        for row in rows:
            if self.excluded(row):continue
            repository=row.get('repository',{})
            if not repository:
                path=urlsplit(row.get('repository_url','')).path
                if not path.startswith('/repos/'):continue
                repository={'full_name':path[len('/repos/'):], 'fork':True}
            # Search/notifications may point to an excluded fork outside the excluded organization.
            name=repository.get('full_name','')
            try:
                self.guard(name)
                if name.casefold() not in self.checked:
                    detail=self.api('repos/'+name)
                    self.checked[name.casefold()]=not self.excluded(detail)
                if not self.checked[name.casefold()]:continue
            except (ValueError,PermissionError,GitHubReadError):
                unavailable+=1;continue
            self.known.add(name.casefold())
            value=selected(row,'number title html_url state updated_at reason unread')
            if 'subject' in row:value['subject']=selected(row['subject'],'title type url')
            value['repository']=name;items.append(value)
        return {'items':items,'next_page':str(int(page)+1) if more and int(page)<10 else '',
                'limited':bool(incomplete or more and int(page)==10),'more_available':more,'unavailable':unavailable,
                'coverage':'Up to 100 account-related records per page after exclusions; no notification is marked read.'}

    def overview(self):
        rows=[];limited=False;unavailable=0
        page='1'
        while page:
            result=self.repositories(page);rows.extend(result['repositories']);page=result['next_page']
            limited |= result['limited'];unavailable+=result['unavailable']
        activity={};gaps=[]
        for kind in ('owned','involved','reviews','notifications'):
            try:activity[kind]=self.activity(kind,'1')
            except Exception:
                gaps.append('Could not inspect '+kind+' activity.')
        eligible=sorted((r for r in rows if not r.get('archived') and not r.get('disabled')),key=lambda r:r['full_name'].casefold())
        key=hashlib.sha256(json.dumps(self.policy,sort_keys=True).encode()).hexdigest()
        root=self.home/'github-profile'/key;root.mkdir(parents=True,exist_ok=True,mode=0o700)
        import fcntl,os
        fd=os.open(root/'sweep.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            cursor=root/'cursor.json';last=json.loads(cursor.read_text()).get('after','') if cursor.exists() else ''
            ordered=[r for r in eligible if r['full_name'].casefold()>last]+[r for r in eligible if r['full_name'].casefold()<=last]
            batch=ordered[:10]
            # Validate metadata/fork lineage before parallel bounded content reads.
            approved=[]
            for row in batch:
                try:self.authorize(row['full_name']);approved.append(row['full_name'])
                except Exception:gaps.append('A repository in the rotating batch could not be inspected.')
            def inspect(name):
                result={'repository':name}
                for label,path in (('runs','actions/runs'),('pull_requests','pulls'),('issues','issues')):
                    try:
                        data=self.api('repos/'+name+'/'+path,dict(per_page=10) if label=='runs' else dict(per_page=10,state='open',sort='updated',direction='desc'))
                        values=data.get('workflow_runs',[]) if label=='runs' else data
                        result[label]=[selected(r,'number id title html_url name head_sha head_branch event status conclusion updated_at state draft')
                                       for r in values if not self.excluded(r)]
                    except Exception:result[label+'_unavailable']=True
                return result
            with ThreadPoolExecutor(max_workers=4) as pool:checks=list(pool.map(inspect,approved))
            if batch:
                from .conversation import _write
                _write(cursor,{'after':batch[-1]['full_name'].casefold()})
        finally:os.close(fd)
        return {'profile':self.policy['login'],'repositories':rows,'activity':activity,'repository_checks':checks,
                'gaps':gaps,'discovery_limited':limited,'discovery_unavailable':unavailable,
                'coverage':{'discovered_repositories':len(rows),'active_repositories':len(eligible),'detailed_checks':len(checks),
                    'rotation':'Up to ten active repositories per call, with a persistent alphabetical cursor. Every eligible repository participates; account activity is checked every call.',
                    'limits':'Repository discovery capped at 2000. First 100 records per activity category; first ten runs/PRs/issues per detailed repository. Archived repositories are discoverable but not actively swept. No complete audit is claimed.'}}

    def tools(self):
        return [ReadTool('github.profile_repositories','Discover owned/collaborator/organization repositories for the configured account, excluding prohibited owners and related forks. Page starts at 1.',object_schema({'page':TEXT}),self.repositories),
                ReadTool('github.profile_activity','Read account-wide owned or involved issues/PRs, requested reviews, or notifications. Exclusions are host-enforced. Page starts at 1.',object_schema({'kind':{'type':'string','enum':['owned','involved','reviews','notifications']},'page':TEXT}),self.activity),
                ReadTool('github.profile_overview','Inspect the configured account across repositories and related activity. Applies exclusions before returning evidence. Includes a rotating batch of repository CI/PR/issue checks, coverage and gaps. Use this first for a standing profile watch; investigate important findings with other GitHub tools. Does not change GitHub state.',object_schema({}),self.overview)]
