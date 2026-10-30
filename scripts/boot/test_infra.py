import json
import tempfile
import unittest
from pathlib import Path
from infra import contained, regular_file, validate_state, state_ready, input_events, classify_guest, validate_cold_pair, InputPlan

class InfraTests(unittest.TestCase):
    def test_paths_reject_escape_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("../disk", "/dev/sda", "C:/disk", "a\\b", "a,b"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    contained(root, name)
            (root / "plain").write_bytes(b"data")
            regular_file(root / "plain")
            try:
                (root / "link").symlink_to(root / "plain")
            except OSError:
                return
            with self.assertRaises(ValueError):
                regular_file(root / "link")

    def state(self):
        return {"schemaVersion": 1, "bootId": "boot-1", "sessionId": "session-1", "revision": 3,
                "phase": "ready", "root": {"width": 1280, "height": 800, "presentedSequence": 9},
                "apps": [{"app": 1, "generation": 1, "workerPid": 20, "state": "ready",
                          "sourceSequence": 5, "presentedSourceSequence": 5, "inputReady": True}], "error": None}

    def test_state_binds_boot_session_revision(self):
        s = self.state()
        validate_state(s, "boot-1", "session-1", 2)
        for boot, session, rev in (("old", "session-1", 2), ("boot-1", "old", 2), ("boot-1", "session-1", 4)):
            with self.subTest(boot=boot, session=session, rev=rev), self.assertRaises(ValueError):
                validate_state(s, boot, session, rev)

    def test_initial_null_root_is_valid_but_cannot_enable_input(self):
        initial=self.state();initial['phase']='starting';initial['root']=None
        try: validate_state(initial,'boot-1')
        except ValueError as error: self.fail('host initial null root must be accepted: '+str(error))
        self.assertFalse(state_ready(initial,[1]))
        initial['phase']='ready'
        with self.assertRaises(ValueError): validate_state(initial,'boot-1')

    def test_actual_host_running_closing_minimized_states_are_supported(self):
        state=self.state()
        for status in ('starting','running','closing','minimized','closed','failed'):
            state['apps'][0]['state']=status
            try: validate_state(state,'boot-1')
            except ValueError as error:self.fail('actual host status rejected '+status+': '+str(error))
            self.assertEqual(state_ready(state,[1]),status=='running')
        state['apps'][0]['state']='running';state['apps'][0]['minimized']=True
        self.assertFalse(state_ready(state,[1]))
        state['apps'][0]['state']='unknown'
        with self.assertRaises(ValueError):validate_state(state,'boot-1')

    def test_ready_requires_presented_root_and_source(self):
        s = self.state()
        self.assertTrue(state_ready(s, [1]))
        self.assertFalse(state_ready(s, [1, 2]))
        s["apps"][0]["presentedSourceSequence"] = 0
        self.assertFalse(state_ready(s, [1]))
        s = self.state(); s["apps"][0]["inputReady"] = False
        self.assertFalse(state_ready(s, [1]))
        s = self.state(); s["apps"][0]["minimized"] = True
        self.assertFalse(state_ready(s, [1]))
        s = self.state(); s["root"]["presentedSequence"] = 0
        self.assertFalse(state_ready(s, [1]))

    def test_input_is_vm_protocol_only_and_checks_geometry(self):
        s = self.state()
        events = input_events([{"kind": "pointer", "x": 1279, "y": 799},
                               {"kind": "key", "qcode": "esc", "down": True}], s)
        self.assertEqual(events[0], {"type": "abs", "data": {"axis": "x", "value": 32767}})
        self.assertEqual(events[1]["data"]["value"], 32767)
        self.assertEqual(events[2]["data"]["key"]["data"], "esc")
        for event in ({"kind": "pointer", "x": 1280, "y": 0},
                      {"kind": "shell", "command": "anything"},
                      {"kind": "key", "qcode": "esc", "down": 1}):
            with self.subTest(event=event), self.assertRaises(ValueError):
                input_events([event], s)

    def test_timeout_and_abnormal_exit_never_classify_normal(self):
        good = [{"type": "guest-result", "status": "normal", "desktopExitCode": 0,
                 "dataSynced": True, "poweroffRequested": True}]
        self.assertEqual(classify_guest(good, False), "normal-desktop-poweroff")
        self.assertEqual(classify_guest(good, True), "timeout-unaccepted")
        bad = [dict(good[0], desktopExitCode=1)]
        self.assertEqual(classify_guest(bad, False), "guest-failed")
        self.assertEqual(classify_guest([], False), "guest-exited-unverified")
        self.assertEqual(classify_guest([{"type":"guest-error"}]+good, False), "guest-failed")

    def test_input_plan_waits_for_input_ready_and_presentation_ack(self):
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args): self.calls.append((command, args)); return {}
        with tempfile.TemporaryDirectory() as work:
            qmp = Transport()
            spec = {"schemaVersion":1,"requireApps":[1],"steps":[{"name":"tap", "events":[{"kind":"key","qcode":"a","down":True},{"kind":"key","qcode":"a","down":False}],"expect":{"rootAdvance":True}}]}
            plan = InputPlan(spec, Path(work)/"inputs.jsonl", 10)
            state = self.state();state["apps"][0]["inputReady"] = False
            plan.advance(state,qmp);self.assertEqual(qmp.calls,[])
            state = self.state();plan.advance(state,qmp);self.assertEqual(len(qmp.calls),1)
            self.assertFalse(plan.complete)
            next_state = self.state();next_state["revision"] += 1
            plan.advance(next_state,qmp);self.assertFalse(plan.complete)
            next_state["root"]["presentedSequence"] += 1
            plan.advance(next_state,qmp);self.assertTrue(plan.complete)
            self.assertEqual(len(qmp.calls),1)
            self.assertEqual(len((Path(work)/"inputs.jsonl").read_text().splitlines()),2)

    def test_confirmed_input_scanout_precedes_next_dispatch(self):
        import json
        from vm import GuestStream
        class Transport:
            def __init__(self): self.calls=[]
            def execute(self, command, args): self.calls.append((command,args)); return {}
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/'guest.jsonl'; qmp=Transport()
            steps=[{'name':name,'events':[{'kind':'key','qcode':'a','down':False}], 'expect':{'rootAdvance':True}} for name in ('type','power')]
            plan=InputPlan({'schemaVersion':1,'requireApps':[1],'steps':steps},Path(work)/'inputs.jsonl',10)
            stream=GuestStream(path,plan)
            first=self.state();second=self.state();second['revision']+=1;second['root']['presentedSequence']+=1
            third=dict(second,revision=second['revision']+1)
            records=[{'type':'guest-start','bootId':'boot-1'}]+[{'type':'desktop-state','state':state} for state in (first,second,third)]
            path.write_text(''.join(json.dumps(record)+'\n' for record in records))
            stream.poll(qmp)
            self.assertEqual([command for command,_ in qmp.calls],['input-send-event','screendump','input-send-event'])

    def test_diagnostic_split_preserves_events_with_separate_button_transactions(self):
        class Transport:
            def __init__(self):self.calls=[]
            def execute(self,command,args=None):self.calls.append((command,args));return [{'name':'virtual','current':True}] if command=='query-mice' else {}
        with tempfile.TemporaryDirectory() as work:
            events=[{'kind':'pointer','x':120,'y':200},{'kind':'button','button':'left','down':True},{'kind':'button','button':'left','down':False}]
            spec={'schemaVersion':1,'requireApps':[1],'steps':[{'name':'same-click','events':events,'expect':{'sourceAdvance':[1]}}]}
            try:plan=InputPlan(spec,Path(work)/'inputs.jsonl',10,split_transactions=True)
            except TypeError as error:self.fail('diagnostic split missing: '+str(error))
            qmp=Transport();plan.advance(self.state(),qmp)
            self.assertEqual(qmp.calls[0][0],'query-mice')
            batches=[args['events'] for command,args in qmp.calls if command=='input-send-event']
            self.assertEqual([len(batch) for batch in batches],[2,1,1])
            self.assertEqual([event for batch in batches for event in batch],input_events(events,self.state()))
            self.assertFalse(plan.complete)

    def test_virtual_evdev_decoder_has_x86_64_record_boundary(self):
        import importlib.util,struct
        path=Path(__file__).with_name('evdev_observer.py')
        self.assertTrue(path.exists(),'guest virtual evdev observer missing')
        spec=importlib.util.spec_from_file_location('observer_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.EVENT.size,24)
        sample=struct.pack('<qqHHi',3,42,1,272,1)
        self.assertEqual(module.decode(sample)[0],{'seconds':3,'microseconds':42,'type':1,'code':272,'value':1})
        with self.assertRaises(ValueError):module.decode(sample[:-1])

    def test_virtual_evdev_identity_accepts_current_data_disk_generation(self):
        import importlib.util
        path=Path(__file__).with_name('evdev_observer.py')
        spec=importlib.util.spec_from_file_location('observer_identity_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertTrue(module.task_owned_cmdline('console=ttyS0 rdinit=/init mingos.data_uuid=b4ee0135-c979-4bb9-aace-2b7aae417a5f mingos.data_serial=MINGDATAb4ee0135c979'))
        self.assertTrue(module.task_owned_cmdline('rdinit=/init mingos.data_uuid=6472be15-4656-4898-afac-95c1531a8a72 mingos.data_serial=MINGDATA6472be154656'))
        self.assertFalse(module.task_owned_cmdline('console=ttyS0 rdinit=/init'))

    def test_virtual_evdev_bound_stops_across_multiple_ready_fds(self):
        import importlib.util,time
        from unittest import mock
        path=Path(__file__).with_name('evdev_observer.py')
        spec=importlib.util.spec_from_file_location('observer_bound_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        event=module.EVENT.pack(1,2,1,272,1)
        records=[];descriptors={10:{'device':'ten'},11:{'device':'eleven'}}
        with mock.patch.object(module.select,'select',return_value=([10,11],[],[])),mock.patch.object(module.os,'read',side_effect=lambda fd,size:event*(128 if fd==10 else 1)):
            count=module.capture(descriptors,records.append,time.monotonic()+5)
        self.assertEqual(count,4096)
        self.assertEqual(len(records),4096)
        self.assertEqual(records[-1]['device'],'ten')

    def test_pointer_barrier_waits_for_matching_client_receipt_without_replaying_abs(self):
        class Transport:
            def __init__(self):self.calls=[]
            def execute(self,command,args=None):self.calls.append((command,args));return [] if command=='query-mice' else {}
        with tempfile.TemporaryDirectory() as work:
            events=[{'kind':'pointer','x':120,'y':200},{'kind':'button','button':'left','down':True},{'kind':'button','button':'left','down':False}]
            spec={'schemaVersion':1,'requireApps':[1],'steps':[{'name':'same-click','events':events,'expect':{'sourceAdvance':[1]}}]}
            try:plan=InputPlan(spec,Path(work)/'inputs.jsonl',10,split_transactions=True,pointer_handshake=True)
            except TypeError as error:self.fail('pointer receipt barrier missing: '+str(error))
            qmp=Transport();state=self.state();context={'desktopPid':66,'logOffset':100,'receipt':None};plan.advance(state,qmp,context)
            self.assertEqual(len([x for x in qmp.calls if x[0]=='input-send-event']),1)
            for receipt in (None,{'entered':False,'x':120,'y':200},{'entered':True,'x':0,'y':0}):plan.advance(state,qmp,dict(context,receipt=receipt))
            self.assertEqual(len([x for x in qmp.calls if x[0]=='input-send-event']),1)
            receipt={'bootId':'boot-1','sessionId':'session-1','desktopPid':66,'entered':True,'x':120.078,'y':200.242,'pointer':36,'surface':15,'sequence':1,'logOffset':101}
            for bad in (dict(receipt,logOffset=100),dict(receipt,desktopPid=67),dict(receipt,sessionId='old'),dict(receipt,bootId='old')):plan.advance(state,qmp,dict(context,receipt=bad))
            self.assertEqual(len([x for x in qmp.calls if x[0]=='input-send-event']),1)
            plan.advance(state,qmp,dict(context,receipt=receipt))
            sends=[args['events'] for command,args in qmp.calls if command=='input-send-event']
            self.assertEqual([len(batch) for batch in sends],[2,1,1]);self.assertFalse(plan.complete)
            self.assertEqual([e for batch in sends for e in batch],input_events(events,state))

    def test_guest_stream_binds_current_desktop_receipt_and_completes_click(self):
        import json
        from vm import GuestStream
        class Transport:
            def __init__(self):self.calls=[]
            def execute(self,command,args=None):self.calls.append((command,args));return [] if command=='query-mice' else {}
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);qmp=Transport();events=[{'kind':'pointer','x':120,'y':200},{'kind':'button','button':'left','down':True},{'kind':'button','button':'left','down':False}]
            plan=InputPlan({'schemaVersion':1,'requireApps':[1],'steps':[{'name':'click','events':events,'expect':{'sourceAdvance':[1]}}]},root/'inputs.jsonl',10,split_transactions=True,pointer_handshake=True)
            stream=GuestStream(root/'guest.jsonl',plan);state=self.state();later=self.state();later['revision']+=1;later['apps'][0]['sourceSequence']+=1;later['apps'][0]['presentedSourceSequence']+=1
            rows=[{'type':'guest-start','bootId':'boot-1'},{'type':'process-start','name':'desktop','pid':50},
                {'type':'desktop-state','state':state,'desktopPid':50,'diagnosticStderrOffset':100},
                {'type':'desktop-pointer-receipt','bootId':'boot-1','sessionId':'session-1','desktopPid':50,'receipt':{'pointer':36,'surface':15,'entered':True,'x':120.1,'y':200.2,'sequence':1,'logOffset':101}},
                {'type':'desktop-state','state':later,'desktopPid':50,'diagnosticStderrOffset':101}]
            stream.path.write_text(''.join(json.dumps(row)+'\n' for row in rows));stream.poll(qmp)
            self.assertTrue(plan.complete)
            self.assertEqual([len(args['events']) for command,args in qmp.calls if command=='input-send-event'],[2,1,1])

    def test_guest_stream_requires_fault_receipt_to_match_bundle_configuration(self):
        from vm import GuestStream
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            config = {"schemaVersion": 1, "profile": "strict-worker-sigkill-v1", "app": 2,
                      "generation": 1, "signal": "SIGKILL", "maxWaitSeconds": 60}
            receipt = dict(config, pid=71, cgroup="/mingos/app-2-g-1", revision=7,
                           status="sent", monotonicNs=123456)
            stream = GuestStream(root / "guest.jsonl", None, fault_config=config)
            stream.path.write_text("\n".join(json.dumps(row) for row in (
                {"type": "guest-start", "bootId": "boot-1"},
                {"type": "fault-injection", "receipt": receipt},
            )) + "\n")
            stream.poll(None)
            self.assertEqual(stream.fault_receipt, receipt)
            bad = GuestStream(root / "bad.jsonl", None, fault_config=config)
            bad.path.write_text(json.dumps({"type": "fault-injection", "receipt": dict(receipt, app=1)}) + "\n")
            with self.assertRaisesRegex(ValueError, "fault receipt"):
                bad.poll(None)

    def test_fault_profile_blocks_input_until_receipt_and_failed_worker_state(self):
        from vm import GuestStream
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args=None): self.calls.append((command, args)); return {}
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            config = {"schemaVersion": 1, "profile": "strict-worker-sigkill-v1", "app": 2,
                      "generation": 1, "signal": "SIGKILL", "maxWaitSeconds": 60}
            receipt = dict(config, pid=71, cgroup="/mingos/app-2-g-1", revision=7,
                           status="sent", monotonicNs=123456)
            plan = InputPlan({"schemaVersion": 1, "requireApps": [1], "steps": [{
                "name": "dock", "events": [{"kind": "key", "qcode": "a", "down": False}], "expect": {}
            }]}, root / "inputs.jsonl", 10)
            running = self.state()
            failed = self.state(); failed["revision"] += 1
            failed["apps"].append({"app": 2, "generation": 1, "workerPid": 71, "state": "failed",
                                   "sourceSequence": 3, "presentedSourceSequence": 3, "inputReady": False})
            stream = GuestStream(root / "guest.jsonl", plan, fault_config=config)
            stream.path.write_text("\n".join(json.dumps(row) for row in (
                {"type": "guest-start", "bootId": "boot-1"},
                {"type": "desktop-state", "state": running},
                {"type": "fault-injection", "receipt": receipt},
                {"type": "desktop-state", "state": failed},
            )) + "\n")
            qmp = Transport(); stream.poll(qmp)
            self.assertEqual([call[0] for call in qmp.calls], ["input-send-event"])
            self.assertEqual(stream.fault_failure_state, failed)

    def test_fault_profile_pointer_receipt_cannot_bypass_input_gate(self):
        from vm import GuestStream
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args=None): self.calls.append((command, args)); return {}
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            config = {"schemaVersion": 1, "profile": "strict-worker-sigkill-v1", "app": 2,
                      "generation": 1, "signal": "SIGKILL", "maxWaitSeconds": 60}
            plan = InputPlan({"schemaVersion": 1, "requireApps": [1], "steps": [{
                "name": "dock", "events": [{"kind": "pointer", "x": 120, "y": 200}], "expect": {}
            }]}, root / "inputs.jsonl", 10, split_transactions=True, pointer_handshake=True)
            state = self.state()
            stream = GuestStream(root / "guest.jsonl", plan, fault_config=config)
            stream.path.write_text("\n".join(json.dumps(row) for row in (
                {"type": "guest-start", "bootId": "boot-1"},
                {"type": "process-start", "name": "desktop", "pid": 50},
                {"type": "desktop-state", "state": state, "desktopPid": 50, "diagnosticStderrOffset": 100},
                {"type": "desktop-pointer-receipt", "bootId": "boot-1", "sessionId": "session-1", "desktopPid": 50,
                 "receipt": {"pointer": 36, "surface": 15, "entered": True, "x": 120.1, "y": 200.2, "sequence": 1, "logOffset": 101}},
            )) + "\n")
            qmp = Transport(); stream.poll(qmp)
            self.assertEqual(qmp.calls, [])

    def test_fault_profile_requires_valid_receipt_before_normal_result(self):
        from vm import fault_result
        config = {"schemaVersion": 1, "profile": "strict-worker-sigkill-v1", "app": 2,
                  "generation": 1, "signal": "SIGKILL", "maxWaitSeconds": 60}
        receipt = dict(config, pid=71, cgroup="/mingos/app-2-g-1", revision=7,
                       status="sent", monotonicNs=123456)
        failure = self.state(); failure["apps"].append({"app": 2, "generation": 1, "workerPid": 71,
                                                         "state": "failed", "sourceSequence": 3,
                                                         "presentedSourceSequence": 3, "inputReady": False})
        self.assertIsNone(fault_result(None, None))
        self.assertEqual(fault_result(config, receipt, failure), receipt)
        with self.assertRaisesRegex(ValueError, "missing fault receipt"):
            fault_result(config, None, failure)
        with self.assertRaisesRegex(ValueError, "missing failed worker state"):
            fault_result(config, receipt, None)

    def test_cold_boot_checks_identity_disk_and_previous_marker(self):
        first = {"status": "normal-desktop-poweroff", "bootId": "one", "bundleIdentity": "same",
                 "diskUuid": "uuid", "marker": {"bootId": "one", "counter": 1}}
        second = {"status": "normal-desktop-poweroff", "bootId": "two", "bundleIdentity": "same",
                  "diskUuid": "uuid", "previousMarker": first["marker"],
                  "marker": {"bootId": "two", "counter": 2}}
        validate_cold_pair(first, second)
        for change in ({"bootId": "one"}, {"diskUuid": "other"}, {"bundleIdentity": "changed"},
                       {"previousMarker": None}, {"status": "timeout-unaccepted"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_cold_pair(first, dict(second, **change))

if __name__ == "__main__":
    unittest.main()
