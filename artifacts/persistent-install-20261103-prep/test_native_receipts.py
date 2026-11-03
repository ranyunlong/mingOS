"""Synthetic producer controls only; no test output qualifies a real VM."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('native_audit',HERE/'verify-persistent-install-evidence.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

def record(app,pid,ready=True):
    return dict(app=app,generation=1,window=app,title='fixture',workerPid=pid,status='running',completed=False,
        firstFrameMs=1,firstReadyMs=2 if ready else None,framesSubmitted=1,lastSourceSequence=1,
        presentedSourceSequence=1,presentedViewport=[1280,800],packageId='dev.ming.install-demo' if app==2 else 'dev.ming.text-entry-demo',
        requests=0,inputCounts=[0]*9,events=[],gpuStats=None,error=None)

def state(revision,history,phase='ready'):
    return dict(schemaVersion=1,bootId='boot-2',sessionId='session-2',revision=revision,phase=phase,
        root=dict(width=1280,height=800,presentedSequence=10+revision,storagePrompt=None,storagePromptPresentedSequence=None),
        apps=[dict(app=r['app'],generation=1,window=r['window'],workerPid=r['workerPid'],state='running',sourceSequence=1,
            presentedSourceSequence=1,firstReadyMs=r['firstReadyMs'],inputReady=r['firstReadyMs'] is not None,minimized=False,
            outer=dict(x=0,y=0,width=100,height=100)) for r in history],error=None)

def anchor(revision,history,count):
    return dict(state=state(revision,history),history=copy.deepcopy(history),traceCount=count,droppedTrace=0,droppedInputs=0)

class NativeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.context=dict(bootId='boot-2',sessionId='session-2',bootNumber=2)
        self.spec=json.loads((HERE/'input-boot2.json').read_text());one=[record(1,101)];both=one+[record(2,202)]
        self.trace=[dict(atMs=0,app=1,window=1,generation=1,kind='launch',detail=''),
                    dict(atMs=1,app=1,window=1,generation=1,kind='ready',detail=''),
                    dict(atMs=2,app=2,window=2,generation=1,kind='launch',detail=''),
                    dict(atMs=3,app=2,window=2,generation=1,kind='ready',detail='')]
        self.outcome=dict(trace=self.trace,instances=both,droppedTrace=0,droppedInputs=0)
        def edge(seq,point,before,after,action='other',target=None):
            return dict(schemaVersion=1,sequence=seq,pressSequence=seq if seq%2 else seq-1,
                edge='down' if seq%2 else 'up',x=point[0],y=point[1],action=action,targetApp=target,
                before=before,after=after,trace=copy.deepcopy(self.trace[before['traceCount']:after['traceCount']]))
        down=edge(1,(680,770),anchor(1,one,2),anchor(2,both,3),'launch',2)
        up=edge(2,(680,770),anchor(5,both,4),anchor(6,both,4))
        powerdown=edge(3,(1250,12),anchor(7,both,4),anchor(8,both,4))
        powerup=edge(4,(1250,12),anchor(9,both,4),anchor(10,both,4))
        self.records=[('native-input',down),('native-launch',dict(schemaVersion=1,downSequence=1,phase='worker-started',app=2,generation=1,window=2,workerPid=202,anchor=anchor(3,both,3))),
            ('native-launch',dict(schemaVersion=1,downSequence=1,phase='ready',app=2,generation=1,window=2,workerPid=202,anchor=anchor(4,both,4))),('native-input',up),('native-input',powerdown),('native-input',powerup)]
        self.progress=[dict(type='input-routing',state=state(1,one))]
        for index,planned in enumerate(self.spec['steps']):
            baseline=state(1,one) if index==0 else state(7,both)
            after=state(6,both) if index==0 else state(10,both)
            point=planned['events'][0]
            for kind in ['input-pointer-dispatched','input-pointer-ready','input-dispatched','input-expectation']:
                self.progress.append(dict(type=kind,step=planned['name'],state=after if kind=='input-expectation' else baseline,
                    receipt=dict(pointer=36,surface=15,entered=True,x=point['x'],y=point['y'],logOffset=0)))
    def check(self):
        data=bytearray(b'wl_seat#2.get_pointer(new id wl_pointer#36)\n')
        emitted=[];serial=0;buttons=set()
        def button(r):
            nonlocal serial
            if r['sequence'] in buttons:return
            buttons.add(r['sequence']);serial+=1
            if r['edge']=='down':
                data.extend(f'wl_pointer#36.enter(1, wl_surface#15, {r["x"]}, {r["y"]})\nwl_pointer#36.frame()\n'.encode())
                step=(r['sequence']-1)//2;self.progress[2+step*4]['receipt']['logOffset']=len(data)
            data.extend(f'wl_pointer#36.button({serial}, {100+serial}, 272, {1 if r["edge"]=="down" else 0})\n'.encode())
        for kind,r in self.records:
            if kind=='persistent-install-commit' and getattr(self,'allow_button_before_commit',False):
                button(self.allow_up_button)
            if kind=='native-input':button(r)
            wrapper=dict(kind=kind,bootId='boot-2',sessionId='session-2',desktopPid=44,receipt=r)
            start=len(data);data.extend(b'MING_PERSISTENT_INSTALL '+json.dumps(wrapper).encode()+b'\n')
            emitted.append(dict(type='desktop-persistent-receipt',bootId='boot-2',sessionId='session-2',desktopPid=44,
                logStartOffset=start,logEndOffset=len(data),record=wrapper))
        events=[dict(type='log',name='desktop-wayland.stderr.log',encoding='base64',offset=0,data=base64.b64encode(data).decode())]+emitted
        events += [dict(type='desktop-state',state=copy.deepcopy(row['state'])) for row in self.progress]
        if hasattr(self,'mutate_events'):self.mutate_events(events)
        self.last_events=events
        return audit.audit_native_receipts(events,self.context,44,self.outcome,self.progress,self.spec)
    def test_complete_actual_down_launch_ready_and_all_left_sequence(self):
        result=self.check();self.assertEqual(result['dockDownSequence'],1);self.assertEqual(result['workerPid'],202)
    def test_live_history_package_id_is_null_until_final_worker_result(self):
        for kind,r in self.records:
            for value in ([r['before'],r['after']] if kind=='native-input' else [r['anchor']]):
                for item in value['history']:item['packageId']=None
        self.assertEqual(self.check()['workerPid'],202)
    def first_boot(self):
        self.context['bootNumber']=1
        self.spec=json.loads((HERE/'input-boot1.json').read_text())
        prefix=[];one=[record(1,101)];progress=[dict(type='input-routing',state=state(1,one))]
        for seq in range(1,5):
            point=self.spec['steps'][(seq-1)//2]['events'][0];before=anchor(seq*2-1,one,2);after=anchor(seq*2,one,2)
            prefix.append(('native-input',dict(schemaVersion=1,sequence=seq,pressSequence=seq if seq%2 else seq-1,
                edge='down' if seq%2 else 'up',x=point['x'],y=point['y'],action='other',targetApp=None,before=before,after=after,trace=[])))
        for index,planned in enumerate(self.spec['steps'][:2]):
            for kind in ['input-pointer-dispatched','input-pointer-ready','input-dispatched','input-expectation']:
                point=planned['events'][0]
                progress.append(dict(type=kind,step=planned['name'],state=state(index*4+4 if kind=='input-expectation' else index*4+1,one),
                    receipt=dict(pointer=36,surface=15,entered=True,x=point['x'],y=point['y'],logOffset=0)))
        for kind,r in self.records:
            if kind=='native-input':
                r['sequence']+=4;r['pressSequence']+=4
                for label in ['before','after']:r[label]['state']['revision']+=8
            else:r['downSequence']+=4;r['anchor']['state']['revision']+=8
        for row in self.progress[1:]:
            row['state']=copy.deepcopy(row['state']);row['state']['revision']+=8
        self.progress=progress+self.progress[1:];self.records=prefix+self.records

    def test_first_boot_all_eight_edges_preserve_actual_dock_sequence_five(self):
        self.first_boot()
        self.assertEqual(self.check()['dockDownSequence'],5)

    def allow_mid_handler(self):
        self.first_boot()
        self.allow_button_before_commit=True
        snapshot_spec=importlib.util.spec_from_file_location('mid_handler_fixtures',HERE/'test_persistent_install_evidence.py')
        module=importlib.util.module_from_spec(snapshot_spec);snapshot_spec.loader.exec_module(module)
        content=module.content();commit=dict(content=content)
        self.trace[2:2]=[dict(atMs=2,app=0,window=0,generation=0,kind='persistent-install-commit',detail=json.dumps(commit)),
                        dict(atMs=2,app=0,window=0,generation=0,kind='installed',detail='dev.ming.install-demo')]
        self.records[3][1]['after']['state']=copy.deepcopy(self.records[4][1]['before']['state'])
        for kind,r in self.records:
            for label in (['before','after'] if kind=='native-input' else ['anchor']):
                value=r[label]
                if value['state']['revision']>=9:value['traceCount']+=2
            if kind=='native-input':r['trace']=copy.deepcopy(self.trace[r['before']['traceCount']:r['after']['traceCount']])
        self.records[3:3]=[('persistent-install-commit',commit),('persistent-install-installed',dict(schemaVersion=1,
            registryRoot=audit.ROOT,catalogPath=audit.ROOT+'/catalog.json',stateRevision=8,content=content))]
        self.allow_up_button=copy.deepcopy(self.records[5][1])
        self.progress[8]['state']=state(8,[record(1,101)],'starting')

    def test_allow_confirmation_can_sample_installed_revision_inside_up_handler(self):
        self.allow_mid_handler()
        self.assertEqual(self.records[5][1]['before']['state']['revision'],7)
        self.assertEqual(self.progress[8]['state']['revision'],8)
        self.assertEqual(self.records[5][1]['after']['state']['revision'],9)
        result=self.check();self.assertEqual(result['dockDownSequence'],5)
        rows=audit.persistent_receipts(self.last_events,self.context,44)
        transaction=next(row for row in rows if row['record']['kind']=='persistent-install-commit')
        snapshot={'post-desktop':{'content':transaction['record']['receipt']['content']}}
        bound=dict(receipt=transaction['record']['receipt'],offset=transaction['offset'],endOffset=transaction['endOffset'])
        self.assertEqual(audit.audit_installed_snapshot(rows,self.context,bound,snapshot,result)['stateRevision'],8)

    def test_allow_mid_handler_requires_installed_next_step_and_complete_order(self):
        for mutation in ['missing-up','missing-installed','duplicate-installed','installed-after-confirmation',
                         'next-armed-before-after','next-baseline-before-after','up-after-next-pointer','installed-after-up',
                         'other-edge-not-enclosed','wrong-boot-role','commit-before-wayland-up']:
            with self.subTest(mutation=mutation):
                self.setUp();self.allow_mid_handler()
                if mutation=='missing-up':self.records.pop(5)
                elif mutation=='missing-installed':self.records.pop(4)
                elif mutation=='duplicate-installed':self.records.insert(4,copy.deepcopy(self.records[4]))
                elif mutation=='installed-after-confirmation':self.records[4][1]['stateRevision']=9
                elif mutation=='next-armed-before-after':self.progress[9]['state']=state(8,[record(1,101)])
                elif mutation=='next-baseline-before-after':self.progress[11]['state']=state(8,[record(1,101)])
                elif mutation=='installed-after-up':self.records[4],self.records[5]=self.records[5],self.records[4]
                elif mutation=='other-edge-not-enclosed':self.progress[-1]['state']=state(17,[record(1,101),record(2,202)])
                elif mutation=='wrong-boot-role':self.context['bootNumber']=2
                elif mutation=='commit-before-wayland-up':self.allow_button_before_commit=False
                else:
                    def mutate(events):
                        up=next(event for event in events if event.get('type')=='desktop-persistent-receipt'
                            and event['record']['kind']=='native-input' and event['record']['receipt']['sequence']==4)
                        self.progress[10]['receipt']['logOffset']=up['logEndOffset']-1
                    self.mutate_events=mutate
                with self.assertRaises(ValueError):self.check()
                if hasattr(self,'mutate_events'):del self.mutate_events
    def test_missing_reordered_duplicate_identity_and_trace_pseudoproofs_rejected(self):
        for mutation in ['missing-up','missing-ready','duplicate','sequence-gap','wrong-press','wrong-pid','wrong-cause',
                         'wrong-target','wrong-coordinate','dropped-inputs','dropped-trace','trace-drift','unknown','ready-without-input','before-autostart','before-closed-history',
                         'live-wrong-package','completed-null-package','final-wrong-package']:
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation=='missing-up':self.records.pop(3)
                elif mutation=='missing-ready':self.records.pop(2)
                elif mutation=='duplicate':self.records.insert(1,copy.deepcopy(self.records[0]))
                elif mutation=='sequence-gap':self.records[3][1]['sequence']=9
                elif mutation=='wrong-press':self.records[3][1]['pressSequence']=2
                elif mutation=='wrong-pid':self.records[2][1]['workerPid']=303
                elif mutation=='wrong-cause':self.records[1][1]['downSequence']=3
                elif mutation=='wrong-target':self.records[0][1]['targetApp']=1
                elif mutation=='wrong-coordinate':self.records[0][1]['x']=650
                elif mutation=='dropped-inputs':self.records[0][1]['before']['droppedInputs']=1
                elif mutation=='dropped-trace':self.outcome['droppedTrace']=1
                elif mutation=='trace-drift':self.records[0][1]['trace'][0]['detail']='forged'
                elif mutation=='unknown':self.records[0][1]['extra']=True
                elif mutation=='ready-without-input':self.records[2][1]['anchor']['state']['apps'][1]['inputReady']=False
                elif mutation=='live-wrong-package':self.records[0][1]['after']['history'][1]['packageId']='dev.ming.other'
                elif mutation=='completed-null-package':
                    row=self.records[0][1]['after']['history'][1];row['completed']=True;row['packageId']=None
                elif mutation=='final-wrong-package':self.outcome['instances'][0]['packageId']='dev.ming.other'
                else:
                    rec=record(2,202);rec['completed']=mutation=='before-closed-history'
                    self.records[0][1]['before']['history'].append(rec)
                with self.assertRaises(ValueError):self.check()
    def test_transport_offset_identity_log_loss_and_cross_clock_pseudoproofs_rejected(self):
        def raw(events,change):
            data=base64.b64decode(events[0]['data']);events[0]['data']=base64.b64encode(change(data)).decode()
        for mutation in ['transport-missing','transport-offset','transport-record','transport-identity','wayland-missing',
                         'wayland-extra','wayland-surface','wayland-pointer','wayland-button','native-json-injection','truncated','only-timestamps','early-raw-app2']:
            with self.subTest(mutation=mutation):
                self.setUp()
                def mutate(events):
                    if mutation=='transport-missing':events.pop(1)
                    elif mutation=='transport-offset':events[1]['logStartOffset']+=1
                    elif mutation=='transport-record':events[1]['record']['receipt']['sequence']=999
                    elif mutation=='transport-identity':events[1]['sessionId']='other'
                    elif mutation=='wayland-missing':raw(events,lambda b:b.replace(b'wl_pointer#36.button(1, 101, 272, 1)\n',b''))
                    elif mutation=='wayland-extra':raw(events,lambda b:b+b'wl_pointer#36.button(8, 108, 272, 1)\n')
                    elif mutation=='wayland-surface':raw(events,lambda b:b.replace(b'wl_surface#15',b'wl_surface#16'))
                    elif mutation=='wayland-pointer':raw(events,lambda b:b.replace(b'wl_pointer#36.button',b'wl_pointer#37.button'))
                    elif mutation=='wayland-button':raw(events,lambda b:b.replace(b'272, 1',b'273, 1'))
                    elif mutation=='native-json-injection':raw(events,lambda b:b.replace(b'MING_PERSISTENT_INSTALL ',b'other MING_PERSISTENT_INSTALL '))
                    elif mutation=='truncated':raw(events,lambda b:b[:-1])
                    elif mutation=='only-timestamps':
                        events[:]=[events[0],dict(type='host-guest-apparently-ordered',hostDownMonotonic=10,guestLaunchUptime=11,guestReadyUptime=12)]
                    else:
                        events.append(dict(type='desktop-state',state=state(0,[record(1,101),record(2,202)])))
                self.mutate_events=mutate
                with self.assertRaises(ValueError):self.check()
                del self.mutate_events


class InstalledReceiptTests(unittest.TestCase):
    def setUp(self):
        snapshot_spec=importlib.util.spec_from_file_location('snapshot_fixtures',HERE/'test_persistent_install_evidence.py')
        module=importlib.util.module_from_spec(snapshot_spec);snapshot_spec.loader.exec_module(module)
        self.context=module.context(1);self.snapshots={'post-desktop':module.receipt(1)}
        self.commit=dict(receipt=dict(content=module.content()),offset=10,endOffset=20)
        self.installed=dict(schemaVersion=1,registryRoot=audit.ROOT,catalogPath=audit.ROOT+'/catalog.json',stateRevision=5,content=module.content())
        self.before=anchor(4,[record(1,101)],2);self.after=anchor(6,[record(1,101)],4)
        self.records=[dict(offset=1,endOffset=9,record=dict(kind='native-input',receipt=dict(sequence=3,edge='down'))),
            dict(offset=30,endOffset=40,record=dict(kind='persistent-install-installed',receipt=self.installed)),
            dict(offset=50,endOffset=60,record=dict(kind='native-input',receipt=dict(sequence=4,edge='up',before=self.before,after=self.after,
                trace=[dict(kind='persistent-install-commit',detail=json.dumps(self.commit['receipt'])),dict(kind='installed',detail='dev.ming.install-demo')])))]
        self.dock=dict(dockDownSequence=5,dockOffset=70)
    def check(self,number=1):
        return audit.audit_installed_snapshot(self.records,dict(self.context,bootNumber=number),self.commit,self.snapshots,self.dock)
    def test_commit_installed_final_content_and_allow_up_revision_chain(self):
        self.assertEqual(self.check()['stateRevision'],5)
    def test_missing_duplicate_drift_reordered_and_boot2_rejected(self):
        for mutation in ['missing','duplicate','content','before-commit','after-up','wrong-revision','unknown','bool-schema','boot2','before-allow-down','allow-trace-missing']:
            with self.subTest(mutation=mutation):
                self.setUp();number=1
                if mutation=='missing':self.records.pop(1)
                elif mutation=='duplicate':self.records.insert(1,copy.deepcopy(self.records[1]))
                elif mutation=='content':self.installed['content']['registrySha256']='f'*64
                elif mutation=='before-commit':self.records[1]['offset']=5
                elif mutation=='after-up':self.records[1]['offset']=65
                elif mutation=='wrong-revision':self.installed['stateRevision']=7
                elif mutation=='unknown':self.installed['extra']=True
                elif mutation=='bool-schema':self.installed['schemaVersion']=True
                elif mutation=='before-allow-down':self.commit['offset']=0;self.commit['endOffset']=1
                elif mutation=='allow-trace-missing':self.records[-1]['record']['receipt']['trace']=[]
                else:number=2
                with self.assertRaises(ValueError):self.check(number)
        self.records=[];self.assertIsNone(self.check(2))

if __name__=='__main__':unittest.main()
