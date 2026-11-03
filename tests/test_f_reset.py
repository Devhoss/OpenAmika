"""No-network reset integration checks against temporary stores only."""
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'payload'))
from f_memory import MemoryRuntime
import f_reset
import test_f_bridge


class Request:
    headers={'X-Build-F-Action':'wipe-memory'}
    is_json=True
    def __init__(self,value=None):self.value={'confirmation':'WIPE'} if value is None else value
    def get_json(self,**kwargs):return self.value


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_f_bridge.BridgeTests()
        self.fixture.setUp()
        self.fixture.install()
        self.g=self.fixture.g
        self.root=self.fixture.root
        self.memory=self.g['F_MEMORY']
        self.meter=self.g['F_METER']
        self.context=self.root/'brain/memories'
        self.context.mkdir()
        for name in ('conversations.json','thinking_log.json','consolidated.json'):
            (self.context/name).write_text(json.dumps({'old':'Private old context'}),encoding='utf-8')
        self.g['CONVERSATION_HISTORY'].append({'turn_id':'old','user':'Old message'})
        self.g['CONSOLIDATED_MEMORIES']=[{'old':True}]
        self.g['THINKING_LOG']=[{'old':True}]
        self.g['_COMPLETED_CPS']={'old'}
        self.g['_pending_think']='old thought'
        self.memory.archive({'id':'old-event','time':'2026-09-26T01:00:00Z','user':'I like blue.','assistant':'Okay.','source_kind':'conversation'})
        self.seed=self.root/'config/seed.json'
        self.memory.model.persist(self.seed)
        self.g['request']=Request()
        self.route=self.g['app'].view_functions['/api/build_f/wipe']
        ident=self.meter.begin('anthropic','claude-sonnet-5','test',estimated_max_usd=.01)
        self.meter.finish(ident,{'input_tokens':10,'output_tokens':2})

    def tearDown(self):self.fixture.tearDown()

    def test_success_clears_context_retains_backup_ledger_and_stays_empty_after_restart(self):
        before=self.meter.summary()
        result,code=self.route()
        self.assertEqual(code,200,result)
        backup=self.root/'memory_f/backups'/result['backup_id']
        self.assertTrue((backup/'state.json').exists())
        self.assertEqual(json.loads((backup/'brain_context/conversations.json').read_text())['old'],'Private old context')
        self.assertEqual(self.memory.status()['events'],0)
        self.assertEqual(self.memory.status()['pending'],0)
        self.assertEqual(self.g['CONVERSATION_HISTORY'],[])
        self.assertEqual(self.g['THINKING_LOG'],[])
        self.assertIsNone(self.g['_pending_think'])
        self.assertEqual(before,self.meter.summary())
        reloaded=MemoryRuntime(self.memory.folder,seed_path=self.seed)
        self.assertEqual(reloaded.status()['events'],0)
        self.assertEqual(self.fixture.http.calls,[])

    def test_confirmation_content_and_origin_guards_do_not_touch_memory(self):
        for value in ({},{'confirmation':'wipe'},['WIPE']):
            self.g['request']=Request(value)
            self.assertEqual(self.route()[1],400)
        request=Request();request.headers={}
        self.g['request']=request;self.assertEqual(self.route()[1],403)
        request=Request();request.headers={'X-Build-F-Action':'wipe-memory','Origin':'https://attacker.invalid'}
        self.g['request']=request;self.assertEqual(self.route()[1],403)
        self.assertEqual(self.memory.status()['events'],1)
        self.assertFalse((self.root/'memory_f/backups').exists())

    def test_busy_turn_and_provider_request_prevent_reset(self):
        self.g['_ws_active']['turn']={}
        self.assertEqual(self.route()[1],409)
        self.g['_ws_active'].clear()
        self.meter.begin('anthropic','claude-sonnet-5','test',estimated_max_usd=.01)
        self.assertEqual(self.route()[1],409)
        self.assertEqual(self.memory.status()['events'],1)

    def test_interrupted_cross_store_reset_recovers_before_loading_old_context(self):
        original=f_reset._atomic
        def fail_context(path,value):
            if path.name=='conversations.json':raise OSError('simulated disk interruption')
            return original(path,value)
        with patch.object(f_reset,'_atomic',fail_context):
            result,code=self.route()
        self.assertEqual(code,503)
        self.assertEqual(self.memory.status()['events'],0)
        pending=self.root/'memory_f/reset_pending_live.json'
        self.assertTrue(pending.exists())
        reloaded=MemoryRuntime(self.memory.folder,seed_path=self.seed)
        f_reset.install_reset(self.g,reloaded,self.meter,threading.Lock(),threading.Event())
        self.assertFalse(pending.exists())
        self.assertEqual(json.loads((self.context/'conversations.json').read_text()),{'turns':[]})
        self.assertEqual(self.g['CONVERSATION_HISTORY'],[])
        self.assertEqual(reloaded.status()['events'],0)


if __name__=='__main__':unittest.main()
