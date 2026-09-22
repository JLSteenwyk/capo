import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from capo.contracts import object_schema
from capo.research_tools import ReadTool,ReadTools,research,ToolInputError
from capo.gmail import GmailReadTools


def tool(name,args):return dict(action='tool',tool=name,arguments_json=json.dumps(args),reply='',document='',document_title='')
def finish(status='partial',evidence=None):
 return dict(action='finish',tool='',reply='Source check result.',document='',document_title='',arguments_json=json.dumps({'outcomes':[dict(requirement='Check sources',kind='answer',status=status,evidence=evidence or [],next_step='Inspect the source.' if status!='complete' else '')]}))

class InspectionRepairs(unittest.TestCase):
 def test_unattempted_source_failure_is_corrected_through_host_tool(self):
  source=Mock(return_value={'findings':[]});p=Mock();p.call.side_effect=[tool('clock.now',{}),finish(),tool('source.read',{}),finish('complete',['2'])]
  tools=ReadTools([ReadTool('clock.now','Time',object_schema({}),lambda:{}),ReadTool('source.read','Read',object_schema({}),source)])
  with tempfile.TemporaryDirectory() as tmp:r=research(p,tools,{},Path(tmp),max_calls=4,require_source_inspection=True)
  source.assert_called_once();self.assertEqual(r['status'],'reported_complete');self.assertTrue(r['receipts'][1]['source_feedback'])
 def test_unsubstantiated_failures_do_not_reach_user_as_claimed_tool_outages(self):
  p=Mock();p.call.side_effect=[finish(),finish(),finish()]
  tools=ReadTools([ReadTool('source.read','Read',object_schema({}),Mock())])
  with tempfile.TemporaryDirectory() as tmp:r=research(p,tools,{},Path(tmp),max_calls=4,require_source_inspection=True)
  self.assertEqual(r['stop_reason'],'source_inspection_missing');self.assertNotIn('unavailable',r['reply'])
 def test_direct_completed_answer_does_not_require_unnecessary_tool(self):
  p=Mock();p.call.return_value=finish('complete')
  with tempfile.TemporaryDirectory() as tmp:r=research(p,ReadTools([]),{},Path(tmp),require_source_inspection=True)
  self.assertEqual(r['status'],'reported_complete')
 def test_metadata_triage_composes_with_selected_body_read_and_preserves_budgets(self):
  client=Mock();client.get.side_effect=[{'messages':[{'id':'a'},{'id':'b'}]},
   {'payload':{'headers':[{'name':'Subject','value':'Receipt'}]},'snippet':'Subscription renewal'},
   {'payload':{'headers':[{'name':'Subject','value':'Promotion'}]},'snippet':'Offer'}, {'payload':{}}]
  mail=GmailReadTools(client);mail.search('newer_than:30d','2','');r=mail.metadata(['a','b'])
  self.assertEqual(len(r['messages']),2);self.assertEqual(mail.read_attempts,0);self.assertEqual(mail.inspected_ids,set())
  mail.read(['a'],False);state=mail.research_state();resumed=GmailReadTools(client);resumed.restore_research_state(state)
  self.assertEqual(resumed.metadata_reads,2);self.assertEqual(resumed.read_attempts,1);self.assertEqual(mail.next_scan_state()['known_ids'],['b'])
 def test_metadata_limit_survives_restart(self):
  mail=GmailReadTools(Mock());mail.known_ids.add('a');mail.metadata_reads=100
  resumed=GmailReadTools(Mock());resumed.restore_research_state(mail.research_state())
  self.assertIn('mail.metadata',resumed.unavailable());self.assertEqual(resumed.metadata(['a'])['messages'],[])
  resumed.client.get.assert_not_called()
 def test_invalid_source_reference_has_safe_schema_feedback(self):
  execute=Mock();tools=ReadTools([ReadTool('record','Record',object_schema({'ref':{'type':'string','enum':['allowed']}}),execute)])
  with self.assertRaises(ToolInputError) as error:tools.call('record',{'ref':'private-unknown'})
  self.assertIn('result.ref',str(error.exception));self.assertNotIn('private-unknown',str(error.exception));execute.assert_not_called()
 def test_search_results_explain_calendar_restrictions(self):
  from capo.calendar_tools import CalendarTools
  row={'id':'e','etag':'v','organizer':{'self':True},'attendees':[{'email':'guest@example.test'}]}
  result=CalendarTools('UTC').describe('primary',row)
  self.assertFalse(result['editable_by_capo']);self.assertIn('without guests',result['edit_policy'])

 def test_current_automation_status_supersedes_old_failure_and_has_stable_identity(self):
  from datetime import datetime,timezone
  from capo.heartbeat import evidence
  from capo.digest import DigestStore,scope
  config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123'}
  now=datetime(2030,1,3,18,tzinfo=timezone.utc)
  check={'schedule_id':'weekly','title':'Weekly plan','status':'running','next_action':''}
  with tempfile.TemporaryDirectory() as tmp:
   home=Path(tmp);db=DigestStore(home/'scheduled')
   try:db.save({'key':'old','scope':scope(config),'status':'failed','schedule_id':'weekly','title':'Weekly plan','day':'2030-01-03'},now)
   finally:db.close()
   with patch('capo.health.Health.automations',return_value=[check]):
    self.assertEqual(evidence(config,[],now,home),[])
    check.update(status='needs_attention',next_action='First wording')
    first=evidence(config,[],now,home)
    check['next_action']='Different wording'
    second=evidence(config,[],now,home)
   self.assertEqual(len(first),1);self.assertEqual(first[0]['id'],second[0]['id'])
 def test_rejected_tool_arguments_are_not_source_inspections(self):
  execute=Mock();p=Mock();p.call.side_effect=[tool('source.read',{'unexpected':'value'}),finish(),finish(),finish()]
  tools=ReadTools([ReadTool('source.read','Read',object_schema({}),execute)])
  with tempfile.TemporaryDirectory() as tmp:r=research(p,tools,{},Path(tmp),max_calls=4,require_source_inspection=True)
  self.assertEqual(r['stop_reason'],'source_inspection_missing');execute.assert_not_called()
