"""Independent macOS release supervisor. Never modifies the developer checkout."""
import argparse
import fcntl
import json
import os
import plistlib
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from .conversation import _write

# The pinned supervisor cannot replace its own rules or shared dependencies.
MANUAL_PATHS={'capo/updater.py','capo/update_runtime.py','capo/store.py','capo/effects.py',
              'pyproject.toml','setup.py','setup.cfg','requirements.txt','uv.lock','poetry.lock'}
SHA=re.compile(r'[a-f0-9]{40}\Z')


class UpdateError(RuntimeError):pass


def read(path,default=None):
    return json.loads(path.read_text()) if path.exists() else ({} if default is None else default)


def write_private_bytes(path, data):
    temp=path.with_suffix('.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'wb') as handle:
        handle.write(data);handle.flush();os.fsync(handle.fileno())
    temp.chmod(0o600);os.replace(temp,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def command(args, *, cwd=None, timeout=60, env=None):
    result=subprocess.run(args,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          text=True,timeout=timeout)
    if result.returncode:raise UpdateError('External deployment command failed')
    return result.stdout.strip()


def git_env():
    env=os.environ.copy()
    for key in ('GH_TOKEN','GITHUB_TOKEN','GIT_DIR','GIT_WORK_TREE','PYTHONPATH'):env.pop(key,None)
    env['GIT_TERMINAL_PROMPT']='0'
    return env


def checks_pass(rows, sha, required):
    latest={}
    for row in rows:
        if row.get('head_sha')!=sha or row.get('app',{}).get('slug')!='github-actions':continue
        name=row.get('name')
        if name not in latest or row.get('id',0)>latest[name].get('id',0):latest[name]=row
    return bool(required) and all(name in latest and latest[name].get('status')=='completed'
        and latest[name].get('conclusion')=='success' for name in required)


def settings(path):
    c=read(path)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',c.get('repository','')):raise UpdateError('Invalid repository')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',c.get('branch','')):raise UpdateError('Invalid branch')
    for key in ('home','service_plist','python'):
        if not Path(c.get(key,'')).is_absolute():raise UpdateError('Use absolute private deployment paths')
    if not SHA.fullmatch(c.get('initial_revision','')):raise UpdateError('Pin an initial revision')
    if not isinstance(c.get('required_checks'),list) or not c['required_checks'] or any(not isinstance(x,str) or not x for x in c['required_checks']):raise UpdateError('Pin required GitHub checks')
    return c


class MacService:
    def __init__(self, config, root):
        self.config=config;self.root=root;self.path=Path(config['service_plist'])
        self.label=plistlib.loads(self.path.read_bytes())['Label']
        self.domain='gui/'+str(os.getuid())

    def pid(self):
        for line in command(['launchctl','list']).splitlines():
            values=line.split()
            if len(values)==3 and values[-1]==self.label:
                return int(values[0]) if values[0].isdigit() else None
        return None

    def write_plist(self, value):
        write_private_bytes(self.path,plistlib.dumps(value))

    def restart(self):
        result=subprocess.run(['launchctl','bootout',self.domain+'/'+self.label],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if result.returncode and self.pid():raise UpdateError('Could not stop service')
        # launchd unload completes asynchronously even after bootout returns.
        for attempt in range(10):
            result=subprocess.run(['launchctl','bootstrap',self.domain,str(self.path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if result.returncode==0:return
            time.sleep(1)
        raise UpdateError('Service bootstrap failed after waiting for launchd')

    def switch(self, release, sha):
        value=plistlib.loads(self.path.read_bytes());args=value['ProgramArguments']
        if args[1:3]==['-m','capo']:tail=args[3:]
        elif Path(args[0]).name=='capo':tail=args[1:]
        else:raise UpdateError('Unsupported service launch command')
        value['ProgramArguments']=[self.config['python'],'-m','capo']+tail
        value['WorkingDirectory']=str(release)
        value.setdefault('EnvironmentVariables',{})['CAPO_RELEASE']=sha
        value['EnvironmentVariables'].pop('PYTHONPATH',None)
        self.write_plist(value);self.restart()

    def restore(self, backup):
        self.write_plist(plistlib.loads(backup.read_bytes()));self.restart()


class Updater:
    def __init__(self, config, service=None):
        self.config=config;self.home=Path(config['home']);self.root=self.home/'updates'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.state_path=self.root/'state.json';self.state=read(self.state_path)
        self.service=service or MacService(config,self.root)

    def save(self, **changes):
        self.state.update(changes);_write(self.state_path,self.state)

    def git(self,*args,cwd=None):
        return command(['git','-c','credential.helper=','-c','credential.helper=!gh auth git-credential',*args],
                       cwd=cwd or self.root/'mirror',env=git_env(),timeout=120)

    def target(self):
        mirror=self.root/'mirror';url='https://github.com/'+self.config['repository']+'.git'
        if not mirror.exists():self.git('clone','--no-checkout',url,str(mirror),cwd=self.root)
        if self.git('remote','get-url','origin')!=url:raise UpdateError('Deployment remote changed')
        self.git('fetch','origin',self.config['branch'])
        sha=self.git('rev-parse','FETCH_HEAD')
        if not SHA.fullmatch(sha):raise UpdateError('Invalid revision')
        return sha

    def ci(self, sha):
        rows=[]
        for page in range(1,11):
            value=json.loads(command(['gh','api',f"repos/{self.config['repository']}/commits/{sha}/check-runs?per_page=100&page={page}"],env=git_env()))
            rows.extend(value['check_runs'])
            if len(value['check_runs'])<100:return checks_pass(rows,sha,self.config['required_checks'])
        return False

    def stage(self, sha):
        active=self.state.get('active_revision',self.config['initial_revision'])
        self.git('merge-base','--is-ancestor',active,sha)
        changed=set(self.git('diff','--name-only',active,sha).splitlines())
        if changed & MANUAL_PATHS:raise UpdateError('This revision changes updater, storage, or dependencies; manual deployment required')
        release=self.root/'releases'/sha;release.parent.mkdir(exist_ok=True,mode=0o700)
        if not release.exists():self.git('worktree','add','--detach',str(release),sha)
        if self.git('rev-parse','HEAD',cwd=release)!=sha or self.git('status','--porcelain',cwd=release):raise UpdateError('Release checkout is not clean')
        env={k:os.environ[k] for k in ('HOME','PATH','TMPDIR','LANG') if k in os.environ}
        env.update(PYTHONNOUSERSITE='1',GIT_CONFIG_GLOBAL=os.devnull)
        log=self.root/('tests-'+sha+'.log')
        with log.open('w') as output:
            log.chmod(0o600)
            result=subprocess.run([self.config['python'],'-m','unittest','discover','-s','tests','-q'],
                cwd=release,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=600)
        if result.returncode:raise UpdateError('Candidate regression tests failed')
        if self.git('status','--porcelain',cwd=release):raise UpdateError('Tests changed the release checkout')
        return release

    def receipt(self, sha=None, nonce=None, probation=False):
        h=read(self.root/'health.json');pid=self.service.pid()
        return bool(pid and h.get('pid')==pid and h.get('connected') and time.time()-h.get('at',0)<10
            and (sha is None or h.get('release')==sha)
            and (nonce is None or h.get('nonce')==nonce and h.get('drained'))
            and (not probation or h.get('probation')))

    def wait(self, predicate, seconds):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if predicate():return True
            time.sleep(1)
        return False

    def stable_health(self, sha, nonce):
        started=None
        def check():
            nonlocal started
            if not self.receipt(sha,nonce,probation=True):
                started=None
                return False
            if started is None:started=time.monotonic()
            return time.monotonic()-started>=10
        return self.wait(check,60)

    def clear_handoff(self):
        for name in ('drain.json','probation.json'):(self.root/name).unlink(missing_ok=True)

    def rollback(self):
        backup=self.root/'previous.plist'
        if not backup.exists():raise UpdateError('Rollback backup is missing')
        # Keep the candidate paused until the old launch configuration is restored.
        try:self.service.restore(backup)
        except Exception:
            self.save(phase='recovery_pending',pending=True,message='Restoring the previous service; supervisor will retry')
            return False
        self.clear_handoff()
        expected=self.state.get('previous_release')
        ok=self.wait(lambda:self.receipt(expected),60)
        self.save(phase='rolled_back' if ok else 'rollback_failed',failed_revision=self.state.get('target'),
                  message='Previous service restored' if ok else 'Rollback needs local inspection',pending=False)
        return ok

    def deploy(self, sha, release):
        nonce=uuid.uuid4().hex
        _write(self.root/'drain.json',dict(nonce=nonce,expires=time.time()+300))
        if not self.wait(lambda:self.receipt(nonce=nonce),60):
            self.clear_handoff();self.save(phase='deferred',message='Waiting for idle service');return
        old=read(self.root/'health.json');backup=self.root/'previous.plist'
        write_private_bytes(backup,Path(self.config['service_plist']).read_bytes())
        self.save(phase='switching',pending=True,target=sha,previous_release=old['release'])
        _write(self.root/'probation.json',dict(release=sha,nonce=nonce))
        try:
            self.service.switch(release,sha)
            if not self.stable_health(sha,nonce):raise UpdateError('New service failed its Slack health check')
            # No queued work executes during probation. Promotion precedes unpause.
            self.save(phase='active',active_revision=sha,previous_revision=self.state.get('active_revision',self.config['initial_revision']),
                      pending=False,message='Update healthy; Slack connected')
            self.clear_handoff()
        except Exception:
            self.rollback()

    def run(self, redeploy=False):
        with (self.root/'supervisor.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            if self.state.get('pending'):self.rollback();return
            if self.state.get('phase')=='rollback_failed':return
            if self.state.get('phase')=='active' and (self.root/'probation.json').exists():
                if self.receipt(self.state.get('active_revision')):self.clear_handoff()
                else:return
            try:
                sha=self.target()
                if sha==self.state.get('failed_revision'):return
                if sha==self.state.get('active_revision') and not redeploy:return
                self.save(phase='checking',target=sha,message='Checking branch and CI')
                if not self.ci(sha):self.save(phase='waiting_ci',message='Waiting for required checks');return
                release=self.stage(sha)
                if self.target()!=sha:self.save(phase='deferred',message='Branch advanced during verification');return
                if not self.ci(sha):self.save(phase='waiting_ci',message='Checks changed during verification');return
                self.deploy(sha,release)
            except (UpdateError,subprocess.TimeoutExpired,OSError,ValueError,KeyError) as exc:
                if self.state.get('pending'):self.rollback()
                else:self.save(phase='blocked',message=str(exc) if isinstance(exc,UpdateError) else 'Update verification failed; current service retained')


def status(home):
    state=read(Path(home)/'updates/state.json')
    return {key:state.get(key) for key in ('phase','active_revision','target','failed_revision','message')}


def install_watchdog(config, config_path, bootstrap):
    if sys.platform!='darwin':raise UpdateError('Automatic service updates currently support macOS launchd')
    bootstrap=Path(bootstrap).resolve()
    if command(['git','rev-parse','HEAD'],cwd=bootstrap)!=config['initial_revision']:
        raise UpdateError('Watchdog must use the pinned initial release')
    root=Path(config['home'])/'updates';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=Path.home()/'Library/LaunchAgents/org.capo.updater.plist'
    value={'Label':'org.capo.updater','ProgramArguments':[config['python'],'-m','capo.updater','--config',str(config_path.resolve())],
           'WorkingDirectory':str(bootstrap),'RunAtLoad':True,'StartInterval':300,
           'EnvironmentVariables':{'PATH':os.environ.get('PATH','/opt/homebrew/bin:/usr/bin:/bin')},
           'StandardOutPath':str(root/'supervisor.log'),'StandardErrorPath':str(root/'supervisor-error.log')}
    for key in ('StandardOutPath','StandardErrorPath'):
        log=Path(value[key]);log.touch(exist_ok=True);log.chmod(0o600)
    write_private_bytes(path,plistlib.dumps(value))
    MacService(dict(config,service_plist=str(path)),root).restart()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--install-watchdog',type=Path,metavar='PINNED_RELEASE')
    parser.add_argument('--redeploy',action='store_true',help='Revalidate and redeploy the current branch revision')
    args=parser.parse_args();config=settings(args.config)
    if args.install_watchdog:install_watchdog(config,args.config,args.install_watchdog)
    elif not args.status:Updater(config).run(args.redeploy)
    print(json.dumps(status(config['home'])))


if __name__=='__main__':main()
