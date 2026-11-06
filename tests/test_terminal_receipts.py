import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from capo.capabilities import CapabilityConversation
from capo.contracts import object_schema
from capo.research_tools import ReadTool, ReadTools


class TerminalReceiptTests(unittest.TestCase):
    def test_provider_failure_preserves_verified_effect_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);router=CapabilityConversation(root,{})
            directory=root/'attempt';directory.mkdir()
            receipts=[{'tool':'calendar.change','result':{'changed':True,'verified':True,'reply':'Event created.'}},
                      {'tool':'','error':'Completion validation failed'}]
            (directory/'receipts.json').write_text(json.dumps(receipts))
            tools=ReadTools([ReadTool('calendar.change','Change',object_schema({}),Mock(),True)])
            fd=os.open(directory/'lock',os.O_CREAT|os.O_RDWR,0o600)
            with patch('capo.capabilities.shared_tools',return_value=tools),patch('capo.capabilities.research',side_effect=TimeoutError()):
                router._run(directory,{}, {},fd)
            result=json.loads((directory/'outcome.json').read_text())
            self.assertEqual(result['status'],'failed')
            self.assertIn('Event created.',result['route']['reply'])
            self.assertIn('changes are confirmed',result['route']['reply'])
            self.assertNotIn('The  tool',result['route']['reply'])
            tools.tools['calendar.change'].execute.assert_not_called()

    def test_uncertain_effect_is_never_claimed_confirmed(self):
        from capo.request_outcomes import completion_failure_reply
        tools=ReadTools([ReadTool('calendar.change','Change',object_schema({}),Mock(),True)])
        result=completion_failure_reply([{'tool':'calendar.change','uncertain':True,'result':{'verified':True,'reply':'Event created.'}}],tools)
        self.assertNotIn('Event created.',result)
