import copy
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from capo.digest import DEFAULTS,check_preferences,next_delivery,scope
from capo.digest_service import DigestManager
from capo.heartbeat import settings,due_slot,HeartbeatManager
from capo.team import roster,Specialist
from capo.specialist_tools import SpecialistTools


class AutomationDaysTests(unittest.TestCase):
    def test_local_weekends_and_next_monday(self):
        p=dict(copy.deepcopy(DEFAULTS),enabled=True,weekdays=list('01234'))
        for value in ['2026-09-19T17:00:00+00:00','2026-09-20T17:00:00+00:00']:
            now=datetime.fromisoformat(value)
            self.assertIsNone(due_slot(now,settings({'heartbeat':p})))
            self.assertEqual(next_delivery(now,p).isoformat(),'2026-09-21T07:00:00-07:00')
        # UTC Saturday is still Friday afternoon in Pacific time.
        self.assertIsNotNone(due_slot(datetime.fromisoformat('2026-09-19T00:00:00+00:00'),settings({'heartbeat':dict(p,end_hour=17)})))
        for invalid in ([],['7'],['0','0'],[0],['01'],'01234'):
            with self.assertRaises(ValueError):check_preferences(dict(p,weekdays=invalid))
        legacy=copy.deepcopy(DEFAULTS);legacy.pop('weekdays')
        check_preferences(legacy)

    def test_managers_do_not_generate_or_deliver_on_excluded_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            config={'team_id':'TTEST','channel_id':'CTEST','owner_user_id':'UTEST',
                    'heartbeat':{'enabled':True,'weekdays':list('01234')}}
            service=SimpleNamespace(store=SimpleNamespace(home=Path(tmp),list=lambda:[]),config=config,client=Mock())
            now=datetime(2026,9,19,17,tzinfo=timezone.utc)
            for cls in (DigestManager,HeartbeatManager):
                manager=cls(service)
                try:
                    manager.db.configure(scope(config),{'enabled':True,'weekdays':list('01234')})
                    manager.db.create(dict(key='pending',scope=scope(config),day='2026-09-19',status='sending',
                        deadline=now.timestamp()+3600,retry_at=0),now)
                    with patch.object(manager,'deliver') as deliver:
                        manager.tick(now)
                        deliver.assert_not_called()
                    self.assertEqual(manager.db.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],1)
                    service.client.chat_postMessage.assert_not_called()
                finally:manager.db.close()

    def test_retired_specialists_are_unavailable_but_other_roles_remain(self):
        config={'disabled_specialists':['shopping_assistant','style_assistant']}
        self.assertEqual(list(roster(config)['specialists']),['money_saver'])
        with tempfile.TemporaryDirectory() as tmp:
            tools=SpecialistTools(Path(tmp),'owner',config)
            self.assertEqual([s['role'] for s in tools.list()['specialists']],['money_saver'])
            for role in config['disabled_specialists']:
                with self.assertRaises(ValueError):tools.read(role)
                with self.assertRaises(ValueError):tools.remember(role,'test','receipt')
                with self.assertRaises(ValueError):Specialist(Path(tmp),role,'owner',config)
