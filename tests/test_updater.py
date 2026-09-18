import json
import plistlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from capo.updater import Updater,MacService,checks_pass,UpdateError,read
from capo.update_runtime import health,idle_reason

OLD='a'*40
NEW='b'*40


class ChecksTests(unittest.TestCase):
    def test_checks_must_match_sha_app_and_latest_success(self):
        row=dict(id=1,name='Tests',head_sha=NEW,app={'slug':'github-actions'},status='completed',conclusion='success')
        self.assertTrue(checks_pass([row],NEW,['Tests']))
        for rows in ([],[dict(row,head_sha=OLD)],[dict(row,status='in_progress')],
                     [dict(row,app={'slug':'other'})],[row,dict(row,id=2,conclusion='failure')]):
            self.assertFalse(checks_pass(rows,NEW,['Tests']))
        self.assertFalse(checks_pass([row],NEW,['Tests','Missing']))


class FakeService:
    def __init__(self,root,path):self.root=root;self.path=path;self.current_pid=1;self.fail=False;self.switches=0;self.restores=0
    def pid(self):return self.current_pid
    def switch(self,release,sha):
        self.switches+=1;self.current_pid+=1
        self.path.write_bytes(b'candidate launch')
        if self.fail:raise UpdateError('Synthetic startup failure')
    def restore(self,backup):
        self.restores+=1;self.current_pid+=1;self.path.write_bytes(backup.read_bytes())


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name)
        self.path=self.home/'service.plist';self.path.write_bytes(b'original launch')
        self.config=dict(home=str(self.home),service_plist=str(self.path),initial_revision=OLD,repository='example/project',branch='main',python='/synthetic/python',required_checks=['Tests'])
        self.service=FakeService(self.home/'updates',self.path);self.u=Updater(self.config,self.service)
        self.u.save(active_revision=OLD)
        (self.u.root/'health.json').write_text(json.dumps(dict(release=OLD)))
        self.u.wait=Mock(return_value=True);self.u.stable_health=Mock(return_value=True)

    def test_success_promotes_after_health_and_preserves_previous_launch(self):
        self.u.deploy(NEW,self.home/'candidate')
        self.assertEqual(self.u.state['active_revision'],NEW)
        self.assertFalse(self.u.state['pending'])
        self.assertEqual((self.u.root/'previous.plist').read_bytes(),b'original launch')
        self.assertFalse((self.u.root/'probation.json').exists())
        self.u.stable_health.assert_called_once()

    def test_busy_service_is_never_restarted(self):
        self.u.wait.return_value=False
        self.u.deploy(NEW,self.home/'candidate')
        self.assertEqual(self.service.switches,0)
        self.assertEqual(self.u.state['phase'],'deferred')
        self.assertFalse((self.u.root/'drain.json').exists())

    def test_startup_or_health_failure_rolls_back_and_suppresses_same_revision(self):
        self.u.stable_health.return_value=False
        self.u.deploy(NEW,self.home/'candidate')
        self.assertEqual(self.path.read_bytes(),b'original launch')
        self.assertEqual(self.u.state['phase'],'rolled_back')
        self.assertEqual(self.u.state['active_revision'],OLD)
        self.assertEqual(self.u.state['failed_revision'],NEW)
        with patch.object(self.u,'target',return_value=NEW),patch.object(self.u,'ci') as ci:
            self.u.run();ci.assert_not_called()
        self.assertEqual(self.service.restores,1)

    def test_restart_recovers_interrupted_switch_before_fetching(self):
        (self.u.root/'previous.plist').write_bytes(b'original launch')
        self.u.save(pending=True,target=NEW,previous_release=OLD)
        with patch.object(self.u,'target',side_effect=AssertionError('Do not fetch during recovery')):
            self.u.run()
        self.assertEqual(self.service.restores,1)
        self.assertFalse(self.u.state['pending'])

    def test_failed_restore_is_retried_on_next_supervisor_run(self):
        (self.u.root/'previous.plist').write_bytes(b'original launch')
        self.u.save(pending=True,target=NEW,previous_release=OLD)
        with patch.object(self.service,'restore',side_effect=UpdateError('Temporary launchd failure')):
            self.u.run()
        self.assertEqual(self.u.state['phase'],'recovery_pending')
        self.assertTrue(self.u.state['pending'])
        self.u.run()
        self.assertEqual(self.u.state['phase'],'rolled_back')
        self.assertFalse(self.u.state['pending'])

    def test_launchd_bootstrap_retries_asynchronous_unload(self):
        self.path.write_bytes(plistlib.dumps({'Label':'test.capo'}))
        service=MacService(self.config,self.u.root)
        with patch('capo.updater.subprocess.run',side_effect=[Mock(returncode=0),Mock(returncode=5),Mock(returncode=0)]) as run,patch('capo.updater.time.sleep'):
            service.restart()
        self.assertEqual(run.call_count,3)
        self.assertEqual(run.call_args_list[-1].args[0][1],'bootstrap')

    def test_crash_after_promotion_releases_probation_on_next_run(self):
        self.u.save(phase='active',active_revision=NEW,pending=False)
        (self.u.root/'probation.json').write_text(json.dumps({'release':NEW}))
        with patch.object(self.u,'receipt',return_value=True),patch.object(self.u,'target',return_value=NEW):self.u.run()
        self.assertFalse((self.u.root/'probation.json').exists())

    def test_branch_or_ci_changes_do_not_restart_service(self):
        with patch.object(self.u,'target',side_effect=[NEW,OLD]),patch.object(self.u,'ci',return_value=True),patch.object(self.u,'stage',return_value=self.home/'candidate'):
            self.u.run()
        self.assertEqual(self.service.switches,0)
        with patch.object(self.u,'target',return_value=NEW),patch.object(self.u,'ci',return_value=False),patch.object(self.u,'stage') as stage:
            self.u.run();stage.assert_not_called()

    def test_updater_and_dependency_changes_require_manual_rollout(self):
        for name in ('capo/updater.py','capo/update_runtime.py','pyproject.toml','capo/store.py'):
            with self.subTest(name=name),patch.object(self.u,'git',side_effect=['',name]):
                with self.assertRaisesRegex(UpdateError,'manual deployment'):self.u.stage(NEW)

    def test_stale_or_wrong_process_health_is_rejected(self):
        row=dict(pid=1,release=NEW,connected=True,at=time.time(),drained=True,nonce='test',probation=True)
        p=self.u.root/'health.json';p.write_text(json.dumps(row))
        self.assertTrue(self.u.receipt(NEW,'test',True))
        for changes in ({'pid':999},{'at':0},{'release':OLD},{'connected':False},{'nonce':'wrong'},{'probation':False}):
            p.write_text(json.dumps(dict(row,**changes)))
            self.assertFalse(self.u.receipt(NEW,'test',True))

    def test_mac_switch_preserves_settings_and_uses_candidate_module(self):
        value={'Label':'test.capo','ProgramArguments':['/synthetic/bin/capo','--config','/private/config'],
               'EnvironmentVariables':{'EXISTING':'preserved'},'KeepAlive':True}
        self.path.write_bytes(plistlib.dumps(value));service=MacService(self.config,self.u.root)
        with patch.object(service,'restart'):service.switch(self.home/'candidate',NEW)
        after=plistlib.loads(self.path.read_bytes())
        self.assertEqual(after['ProgramArguments'],['/synthetic/python','-m','capo','--config','/private/config'])
        self.assertEqual(after['EnvironmentVariables']['EXISTING'],'preserved')
        self.assertTrue(after['KeepAlive'])


class RuntimeTests(unittest.TestCase):
    def test_drain_requires_idle_and_probation_holds_new_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);root=home/'updates';root.mkdir()
            (root/'drain.json').write_text(json.dumps(dict(nonce='test',expires=time.time()+60)))
            with patch('capo.update_runtime.idle_reason',return_value='active worker'):
                self.assertFalse(health(home,True,OLD))
            (root/'probation.json').write_text(json.dumps(dict(release=NEW)))
            with patch('capo.update_runtime.idle_reason',side_effect=AssertionError('Candidate must stay paused')):
                self.assertTrue(health(home,True,NEW))
            receipt=read(root/'health.json');self.assertTrue(receipt['probation']);self.assertEqual(receipt['nonce'],'test')
            (root/'drain.json').unlink()
            self.assertTrue(health(home,True,NEW))
            (root/'probation.json').unlink()
            self.assertFalse(health(home,True,NEW,True))

    def test_busy_objective_and_message_are_detected(self):
        from capo.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);store=Store(home)
            try:
                self.assertEqual(idle_reason(home),'')
                store.enqueue_slack('test',{'event':{'text':'work'}})
                self.assertEqual(idle_reason(home),'pending messages')
            finally:store.db.close()
