import copy,tempfile,unittest
from pathlib import Path
from infra import InputPlan
class Transport:
    def __init__(self):self.calls=[]
    def execute(self,command,args=None):self.calls.append((command,args));return {}
class LifecycleInputTests(unittest.TestCase):
    def state(self):
        return {'schemaVersion':1,'bootId':'boot','sessionId':'session','revision':1,'phase':'ready','root':{'width':1280,'height':800,'presentedSequence':1},'apps':[{'app':1,'generation':1,'workerPid':20,'state':'running','sourceSequence':5,'presentedSourceSequence':5,'inputReady':True},{'app':2,'generation':1,'workerPid':21,'state':'running','sourceSequence':5,'presentedSourceSequence':5,'inputReady':True}],'error':None}
    def spec(self,apps):return {'schemaVersion':1,'requireApps':[1],'steps':[{'name':'lifecycle','events':[{'kind':'key','qcode':'a','down':False}],'expect':{'apps':apps}}]}
    def test_app_expectation_schema_rejects_ambiguous_unbounded_or_unknown_values(self):
        item={'app':2,'generation':2,'state':'running'}
        bad=[None,{},[],[item]*2,[dict(item,app=True)],[dict(item,app=0)],[dict(item,app=65536)],[dict(item,generation=True)],[dict(item,generation=0)],[dict(item,generation=2**64)],[dict(item,state='unknown')],[{'app':2,'state':'running'}],[dict(item,inputReady=True)],[dict(item,unknown=1)],[dict(item,app=i) for i in range(1,6)]]
        with tempfile.TemporaryDirectory() as tmp:
            for value in bad:
                with self.subTest(value=value),self.assertRaises(ValueError):InputPlan(self.spec(value),Path(tmp)/'input',10)
    def test_close_waits_for_exact_generation_state_and_unchanged_sibling(self):
        expect=[{'app':1,'generation':1,'state':'running'},{'app':2,'generation':1,'state':'closed'}]
        with tempfile.TemporaryDirectory() as tmp:
            plan=InputPlan(self.spec(expect),Path(tmp)/'input',10);q=Transport();base=self.state();plan.advance(base,q)
            candidates=[]
            for status,generation in [('closing',1),('running',1),('closed',2)]:
                s=copy.deepcopy(base);s['revision']=2;s['apps'][1].update(state=status,generation=generation);candidates.append(s)
            s=copy.deepcopy(base);s['revision']=2;s['apps']=s['apps'][:1];candidates.append(s)
            s=copy.deepcopy(base);s['revision']=2;s['apps'][1]['state']='closed';s['apps'][0]['generation']=2;candidates.append(s)
            for s in candidates:plan.advance(s,q);self.assertFalse(plan.complete)
            s=copy.deepcopy(base);s['revision']=3;s['apps'][1]['state']='closed';plan.advance(s,q);self.assertTrue(plan.complete);self.assertEqual(len(q.calls),1)
    def test_relaunch_never_accepts_old_generation_even_with_newer_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan=InputPlan(self.spec([{'app':2,'generation':2,'state':'running'}]),Path(tmp)/'input',10);q=Transport();base=self.state();plan.advance(base,q)
            old=copy.deepcopy(base);old['revision']=2;old['apps'][1].update(sourceSequence=99,presentedSourceSequence=99);plan.advance(old,q);self.assertFalse(plan.complete)
            new=copy.deepcopy(old);new['revision']=3;new['apps'][1].update(generation=2,state='starting');plan.advance(new,q);self.assertFalse(plan.complete)
            new=copy.deepcopy(new);new['revision']=4;new['apps'][1]['state']='running';plan.advance(new,q);self.assertTrue(plan.complete);self.assertEqual(len(q.calls),1)
    def test_lifecycle_expectation_requires_new_revision_and_same_boot_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan=InputPlan(self.spec([{'app':2,'generation':1,'state':'closed'}]),Path(tmp)/'input',10);q=Transport();base=self.state();plan.advance(base,q)
            same=copy.deepcopy(base);same['apps'][1]['state']='closed';plan.advance(same,q);self.assertFalse(plan.complete)
            for key in ('bootId','sessionId'):
                wrong=copy.deepcopy(same);wrong['revision']=2;wrong[key]='other';plan.advance(wrong,q);self.assertFalse(plan.complete)
            good=copy.deepcopy(same);good['revision']=2;plan.advance(good,q);self.assertTrue(plan.complete)
    def test_next_input_still_waits_for_new_generation_presented_and_input_ready(self):
        spec=self.spec([{'app':2,'generation':2,'state':'running'}]);spec['steps'].append({'name':'input','requireApps':[1,2],'events':[{'kind':'key','qcode':'b','down':False}],'expect':{'phase':'stopped'}})
        with tempfile.TemporaryDirectory() as tmp:
            plan=InputPlan(spec,Path(tmp)/'input',10);q=Transport();base=self.state();base['apps'][1].update(state='closed',inputReady=False);plan.advance(base,q)
            new=copy.deepcopy(base);new['revision']=2;new['phase']='starting';new['apps'][1].update(generation=2,state='running',sourceSequence=2,presentedSourceSequence=0,inputReady=False);plan.advance(new,q);self.assertEqual(plan.index,1)
            for phase,ready,presented in [('starting',True,2),('ready',False,2),('ready',True,0)]:
                s=copy.deepcopy(new);s['revision']=3;s['phase']=phase;s['apps'][1].update(inputReady=ready,presentedSourceSequence=presented);plan.advance(s,q);self.assertEqual(len(q.calls),1)
            s=copy.deepcopy(new);s['revision']=4;s['phase']='ready';s['apps'][1].update(inputReady=True,presentedSourceSequence=2);plan.advance(s,q);self.assertEqual(len(q.calls),2)
    def test_legacy_expectation_without_apps_keeps_wire_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec=self.spec([]);spec['steps'][0]['expect']={'rootAdvance':True};plan=InputPlan(spec,Path(tmp)/'input',10);q=Transport();base=self.state();plan.advance(base,q);new=copy.deepcopy(base);new['revision']=2;new['root']['presentedSequence']=2;plan.advance(new,q);self.assertTrue(plan.complete);self.assertEqual(q.calls,[('input-send-event',{'events':[{'type':'key','data':{'down':False,'key':{'type':'qcode','data':'a'}}}]})])
if __name__=='__main__':unittest.main()
