"""Real private Unix transport; fake QMP is explicit, no guest is started."""
import copy
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import threading
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import workflow_control as control

class Qmp:
    def __init__(self): self.commands=[]
    def execute(self,name,args=None):
        self.commands.append((name,args)); return {'running':True}

class Stream:
    def __init__(self):
        self.state={'schemaVersion':1,'bootId':'boot-one','sessionId':'session-one','revision':3,
            'phase':'ready','root':{'width':1280,'height':800,'presentedSequence':9},
            'apps':[{'app':1,'generation':1,'workerPid':20,'state':'ready','sourceSequence':5,
                     'presentedSourceSequence':5,'inputReady':True}],'error':None}
        self.plan=None; self.receipt=None
    def advance_plan(self,state,qmp):
        if self.plan: self.plan.advance(state,qmp,{'desktopPid':42,'logOffset':10,'receipt':self.receipt})

@unittest.skipUnless(os.name=='posix','Linux private Unix control')
class SocketTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='wf-'); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.output=self.root/'output'; self.output.mkdir()
        self.controller=control.Controller(self.root/'ctl',self.root,self.output)
        self.stream=Stream(); self.qmp=Qmp(); self.sequence=0
        self.addCleanup(lambda:self.controller.close(self.qmp))
        self.credentials=control.read_credentials(self.root/'ctl')

    def send(self,command,**changes):
        self.sequence+=1
        envelope={'schemaVersion':1,'token':self.credentials['token'],'instance':self.credentials['instance'],
            'sequence':self.sequence,'sessionId':'session-one','revision':self.stream.state['revision'],'command':command,
            'inputEpoch':self.controller.guard.input_epoch,'layoutSha256':control.layout_digest(self.stream.state)}
        envelope.update(changes)
        client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); client.settimeout(1)
        client.connect(str(self.root/'ctl/control.sock'))
        client.sendall(json.dumps(envelope).encode()+b'\n')
        self.controller.poll(self.stream,self.qmp)
        return client

    def read(self,client):
        with client: return json.loads(client.recv(262144))

    def test_real_socket_auth_and_capture_whitelist(self):
        self.assertEqual(self.read(self.send({'op':'status'}))['status'],'ready')
        self.assertEqual(self.read(self.send({'op':'capture'}))['capture'],'workflow-capture-0000.ppm')
        self.assertEqual(self.qmp.commands,[('screendump',{'filename':str(self.output/'workflow-capture-0000.ppm')})])
        for command,changes in [({'op':'status'},{'token':'bad'}),({'op':'qmp'},{}),
                                ({'op':'capture','path':'../bad'},{}),({'op':'capture'},{'sessionId':'old'})]:
            self.assertEqual(self.read(self.send(command,**changes))['status'],'rejected')
        self.assertNotIn(self.credentials['token'],(self.output/'workflow-control.jsonl').read_text())

    def test_status_then_command_survives_motion_frames(self):
        status=self.read(self.send({'op':'status'}))
        revision=status['state']['revision']
        self.stream.state=copy.deepcopy(self.stream.state)
        self.stream.state['revision']+=1
        self.stream.state['root']['presentedSequence']+=1
        self.stream.state['apps'][0]['sourceSequence']+=1
        self.stream.state['apps'][0]['presentedSourceSequence']+=1
        self.assertEqual(self.read(self.send({'op':'capture'},revision=revision,
            layoutSha256=status['layoutSha256'],inputEpoch=status['inputEpoch']))['status'],'captured')

    def test_status_layout_rejects_geometry_generation_prompt_and_input_epoch_changes(self):
        initial=copy.deepcopy(self.stream.state)
        for mutation in ('geometry','generation','prompt','input'):
            self.stream.state=copy.deepcopy(initial)
            status=self.read(self.send({'op':'status'}))
            if mutation=='geometry': self.stream.state['root']['width']-=1
            elif mutation=='generation': self.stream.state['apps'][0]['generation']+=1
            elif mutation=='prompt': self.stream.state['root']['storagePrompt']={'mode':1,'packageId':'notes','version':'0.1','hasAllow':True}
            else: self.controller.guard.input_epoch+=1
            self.assertEqual(self.read(self.send({'op':'capture'},revision=status['state']['revision'],
                layoutSha256=status['layoutSha256'],inputEpoch=status['inputEpoch']))['status'],'rejected')

    def test_actual_cli_request_round_trips_allow_advancing_motion(self):
        stopped=threading.Event(); advance=threading.Event(); advanced=threading.Event(); failures=[]
        def serve():
            try:
                while not stopped.is_set():
                    if advance.is_set():
                        value=copy.deepcopy(self.stream.state)
                        value['revision']+=10; value['root']['presentedSequence']+=100
                        for app in value['apps']:
                            app['sourceSequence']+=100; app['presentedSourceSequence']+=100
                        self.stream.state=value; advance.clear(); advanced.set()
                    self.controller.poll(self.stream,self.qmp)
                    time.sleep(.001)
            except Exception as error: failures.append(error)
        worker=threading.Thread(target=serve); worker.start()
        try:
            view=control.request(self.root/'ctl',{'op':'status'})
            advance.set(); self.assertTrue(advanced.wait(1))
            response=control.request(self.root/'ctl',{'op':'capture'},view)
            self.assertEqual(response['status'],'captured')
            self.assertGreater(response['state']['revision'],view['state']['revision'])
        finally:
            stopped.set(); worker.join(2)
        self.assertFalse(worker.is_alive()); self.assertEqual(failures,[])

    def test_pending_pointer_rejects_layout_change_before_buttons(self):
        client=self.send({'op':'click','x':150,'y':170})
        self.controller.poll(self.stream,self.qmp)
        self.stream.state=copy.deepcopy(self.stream.state)
        self.stream.state['apps'][0]['outer']={'x':30,'y':30,'width':500,'height':400}
        with self.assertRaisesRegex(ValueError,'target layout changed'):
            self.controller.poll(self.stream,self.qmp)
        events=[e for name,args in self.qmp.commands if name=='input-send-event' for e in args['events']]
        self.assertEqual([e['type'] for e in events],['abs','abs'])
        client.close()

    def test_click_waits_for_actual_matching_pointer_receipt(self):
        client=self.send({'op':'click','x':150,'y':170})
        self.controller.poll(self.stream,self.qmp)
        sent=lambda:[event for name,args in self.qmp.commands if name=='input-send-event' for event in args['events']]
        self.assertEqual([e['type'] for e in sent()],['abs','abs'])
        self.stream.receipt={'entered':True,'bootId':'boot-one','sessionId':'session-one','desktopPid':42,
            'logOffset':11,'pointer':1,'surface':1,'x':150,'y':170}
        self.controller.poll(self.stream,self.qmp)
        self.assertEqual([e['type'] for e in sent()],['abs','abs','btn','btn'])
        self.stream.state=copy.deepcopy(self.stream.state); self.stream.state['revision']+=1
        self.stream.state['root']['presentedSequence']+=1
        self.controller.poll(self.stream,self.qmp)
        self.assertEqual(self.read(client)['type'],'control-completed')
        self.assertIsNone(self.stream.plan)

    def test_power_completion_after_qemu_exit_needs_no_qmp(self):
        self.assertTrue(hasattr(self.controller,'finish'),'closed-QEMU completion hook is missing')
        client=self.send({'op':'power','x':1238,'y':12})
        self.stream.receipt={'entered':True,'bootId':'boot-one','sessionId':'session-one','desktopPid':42,
            'logOffset':11,'pointer':1,'surface':1,'x':1238,'y':12}
        self.controller.poll(self.stream,self.qmp)
        self.controller.poll(self.stream,self.qmp)
        self.stream.state=copy.deepcopy(self.stream.state); self.stream.state['revision']+=1
        self.stream.state['phase']='stopped'
        self.stream.advance_plan(self.stream.state,self.qmp)
        count=len(self.qmp.commands)
        self.controller.finish(self.stream)
        self.assertEqual(len(self.qmp.commands),count)
        self.assertEqual(self.read(client)['type'],'control-completed')

    def test_power_completed_by_guest_stream_is_acknowledged_on_next_poll(self):
        client=self.send({'op':'power','x':1238,'y':12})
        self.addCleanup(client.close)
        self.controller.poll(self.stream,self.qmp)
        self.stream.receipt={'entered':True,'bootId':'boot-one','sessionId':'session-one','desktopPid':42,
            'logOffset':11,'pointer':1,'surface':1,'x':1238,'y':12}
        self.controller.poll(self.stream,self.qmp)
        self.assertTrue(self.stream.plan.waiting)
        self.stream.state=copy.deepcopy(self.stream.state)
        self.stream.state.update(revision=4,phase='stopped')
        self.stream.state['apps'][0].update(state='closed',inputReady=False,outer=None)
        # GuestStream.poll advances the plan before Controller.poll in vm.main.
        self.stream.advance_plan(self.stream.state,self.qmp)
        self.assertTrue(self.stream.plan.complete)
        count=len(self.qmp.commands)
        try:
            self.controller.poll(self.stream,self.qmp)
        except ValueError as error:
            self.fail('completed POWER was rejected by the pre-dispatch guard: '+str(error))
        self.assertEqual(len(self.qmp.commands),count)
        self.assertIsNone(self.stream.plan)
        self.assertEqual(self.read(client)['state']['phase'],'stopped')

    def test_next_type_step_still_rejects_changed_layout_before_dispatch(self):
        client=self.send({'op':'type','text':'abcdefghijk'})
        self.addCleanup(client.close)
        self.controller.poll(self.stream,self.qmp)
        self.stream.state=copy.deepcopy(self.stream.state)
        self.stream.state['revision']+=1
        self.stream.state['root']['presentedSequence']+=1
        self.stream.state['apps'][0]['presentedSourceSequence']+=1
        self.stream.state['apps'][0]['outer']={'x':30,'y':30,'width':500,'height':400}
        self.stream.advance_plan(self.stream.state,self.qmp)
        self.assertFalse(self.stream.plan.complete)
        self.assertFalse(self.stream.plan.waiting)
        count=len(self.qmp.commands)
        with self.assertRaisesRegex(ValueError,'target layout changed'):
            self.controller.poll(self.stream,self.qmp)
        self.assertEqual(len(self.qmp.commands),count)

    def test_out_of_geometry_is_rejected_without_poisoning_active_owner(self):
        client=self.send({'op':'click','x':2000,'y':12})
        self.assertEqual(self.read(client)['status'],'rejected')
        self.assertIsNone(self.controller.active)
        self.assertIsNone(self.stream.plan)

    def test_noop_key_observes_new_state_without_requiring_a_frame(self):
        client=self.send({'op':'key','qcode':'end'})
        self.addCleanup(client.close)
        self.controller.poll(self.stream,self.qmp)
        self.assertTrue(self.stream.plan.waiting)
        self.assertEqual(self.stream.plan.held,{})
        self.controller.poll(self.stream,self.qmp)
        self.assertIsNotNone(self.controller.active)  # Same revision is insufficient.
        self.stream.state=copy.deepcopy(self.stream.state)
        self.stream.state['revision']+=1
        self.controller.poll(self.stream,self.qmp)
        self.assertIsNone(self.controller.active,'legal no-op key waited for an unrelated frame')
        result=self.read(client)
        self.assertEqual(result['completionScope'],'qmp-dispatched-desktop-state-observed')
        self.assertFalse(result['applicationEffectVerified'])
        events=[e for name,args in self.qmp.commands if name=='input-send-event' for e in args['events']]
        self.assertEqual([e['data']['down'] for e in events],[True,False])
        self.assertEqual([e['data']['key']['data'] for e in events],['end','end'])

    def test_noop_key_rejects_changed_boot_session_or_nonready_phase(self):
        client=self.send({'op':'key','qcode':'a','modifiers':['ctrl']})
        self.addCleanup(client.close)
        self.controller.poll(self.stream,self.qmp)
        baseline=copy.deepcopy(self.stream.state)
        for field,value in [('bootId','other-boot'),('sessionId','other-session'),('phase','stopped')]:
            self.stream.state=copy.deepcopy(baseline)
            self.stream.state['revision']+=1;self.stream.state[field]=value
            self.controller.poll(self.stream,self.qmp)
            self.assertIsNotNone(self.controller.active)
        self.stream.state=copy.deepcopy(baseline);self.stream.state['revision']+=1
        self.controller.poll(self.stream,self.qmp)
        self.assertIsNone(self.controller.active)
        self.assertFalse(self.read(client)['applicationEffectVerified'])

    def test_key_qmp_failure_cannot_emit_completion(self):
        client=self.send({'op':'key','qcode':'end'})
        def fail(name,args=None):
            if name=='input-send-event':raise RuntimeError('QMP refused input')
            return {'running':True}
        self.qmp.execute=fail
        try:
            with self.assertRaisesRegex(RuntimeError,'QMP refused'):
                self.controller.poll(self.stream,self.qmp)
            rows=[json.loads(line) for line in (self.output/'workflow-control.jsonl').read_text().splitlines()]
            self.assertFalse(any(row['type']=='control-completed' for row in rows))
        finally:client.close()

    def test_noop_key_waits_through_stale_revisions_and_starting_phase(self):
        client=self.send({'op':'key','qcode':'home'})
        self.addCleanup(client.close)
        self.controller.poll(self.stream,self.qmp)
        baseline=copy.deepcopy(self.stream.state)
        for revision,phase in [(2,'ready'),(3,'ready'),(4,'starting')]:
            self.stream.state=copy.deepcopy(baseline)
            self.stream.state.update(revision=revision,phase=phase)
            self.controller.poll(self.stream,self.qmp)
            self.assertIsNotNone(self.controller.active)
        self.stream.state=copy.deepcopy(baseline)
        self.stream.state['revision']=5
        self.controller.poll(self.stream,self.qmp)
        self.assertIsNone(self.controller.active)
        self.assertEqual(self.read(client)['completionScope'],'qmp-dispatched-desktop-state-observed')

    def test_ctrl_noop_releases_modifier_in_exact_order_before_completion(self):
        client=self.send({'op':'key','qcode':'a','modifiers':['ctrl']})
        self.addCleanup(client.close)
        self.controller.poll(self.stream,self.qmp)
        events=[e for name,args in self.qmp.commands if name=='input-send-event' for e in args['events']]
        self.assertEqual([(e['data']['key']['data'],e['data']['down']) for e in events],
                         [('ctrl',True),('a',True),('a',False),('ctrl',False)])
        self.assertEqual(self.stream.plan.held,{})
        self.stream.state=copy.deepcopy(self.stream.state)
        self.stream.state['revision']+=1
        self.controller.poll(self.stream,self.qmp)
        self.assertIsNone(self.controller.active)
        self.assertFalse(self.read(client)['applicationEffectVerified'])

    def test_modifier_release_qmp_failure_is_not_a_completed_key(self):
        client=self.send({'op':'key','qcode':'a','modifiers':['ctrl']})
        self.addCleanup(client.close)
        original=self.qmp.execute
        def refuse_release(name,args=None):
            if name=='input-send-event' and any(e['data']['key']['data']=='ctrl' and not e['data']['down'] for e in args['events']):
                raise RuntimeError('modifier release refused')
            return original(name,args)
        self.qmp.execute=refuse_release
        try:
            with self.assertRaisesRegex(RuntimeError,'modifier release refused'):
                self.controller.poll(self.stream,self.qmp)
            self.assertFalse(self.stream.plan.waiting)
            self.assertFalse(self.stream.plan.complete)
            self.assertTrue(self.stream.plan.held)
            rows=[json.loads(line) for line in (self.output/'workflow-control.jsonl').read_text().splitlines()]
            self.assertFalse(any(row['type']=='control-completed' for row in rows))
        finally:
            self.qmp.execute=original
        # Releasing the held modifier during shutdown is cleanup, not success.
        self.stream.plan.release(self.qmp)
        self.assertEqual(self.stream.plan.held,{})
        self.assertFalse(self.stream.plan.complete)

if __name__=='__main__': unittest.main()
