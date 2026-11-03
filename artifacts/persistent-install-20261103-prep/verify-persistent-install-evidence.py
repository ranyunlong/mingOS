#!/usr/bin/env python3
"""Fail-closed persistent-install raw verifier. Synthetic controls are not boot evidence."""
import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path, PurePosixPath

HERE=Path(__file__).resolve().parent
BASE_SHA256='fc005850bac0f4985e1ab0708f7ff3981cc389658170ee2233f68a4bdfe28bb2'
assert hashlib.sha256((HERE/'raw-audit-base.py').read_bytes()).hexdigest()==BASE_SHA256
_spec=importlib.util.spec_from_file_location('persistent_frozen_raw_base',HERE/'raw-audit-base.py')
base=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(base)
require=base.require
ROOT='/data/mingos/install-root'
INPUT_SHA256={1:'fdadb4fb252a1edaf0befc35fcc912eeb0837c320d3d7f8ce293eab5ca986f54',2:'82fd7911b7b0851bf94e77550de52cffc69d457496b75e835998e4b0b1d76169'}
ROOT_FILES={'catalogSha256':'catalog.json','registrySha256':'installed-packages-v1','trustSha256':'trusted-publishers-v1','bootstrapSha256':'desktop-install-state-v1.json'}
ACTION_KEYS={'initializedRoots','packagesInstalled','trustStoresProvisioned','catalogsCreated','sourcesCreated'}
APP_KEYS={'app','packageId','version','fingerprint','manifestPath','manifestSha256','wasmPath','wasmSha256'}
ENVELOPE_KEYS={'schemaVersion','bootId','sessionId','bootNumber','role','marker','previousMarker','diskUuid','registryRoot','catalogPath','phase','status','actions'}
HEX=re.compile(r'[0-9a-f]{64}\Z')
PACKAGE_FILE=re.compile(r'packages/([0-9a-f]+)/([0-9]+\.[0-9]+\.[0-9]+)-([0-9a-f]{64})/(app\.json|main\.wasm)\Z')

def exact(value,keys,label):
    require(type(value) is dict and set(value)==set(keys),'exact schema required: '+label)

def digest(value):
    return isinstance(value,str) and HEX.fullmatch(value) is not None

def raw_rows(path,clock):
    """Return parsed raw rows with byte offsets; offsets never mix clock domains."""
    parsed=base.records(path,clock);data=Path(path).read_bytes();offset=0;rows=[]
    for event,line in zip(parsed,data.splitlines(keepends=True)):
        rows.append({'offset':offset,'endOffset':offset+len(line),'event':event});offset+=len(line)
    require(offset==len(data),'raw stream byte coverage differs')
    return rows

def relative(value):
    require(isinstance(value,str) and 0<len(value)<=768 and '\\' not in value and ':' not in value and '\0' not in value,'bounded controlled relative path required')
    path=PurePosixPath(value)
    require(not path.is_absolute() and str(path)==value and all(x not in ('','.','..') for x in path.parts),'unsafe controlled relative path')

def validate_content(content,include_new):
    exact(content,set(ROOT_FILES)|{'apps','files'},'content')
    require(all(digest(content[field]) for field in ROOT_FILES),'content root digest')
    apps,files=content['apps'],content['files']
    require(type(apps) is list and type(files) is list and 1<=len(apps)<=4 and 1<=len(files)<=256,'bounded content apps/files')
    require(all(type(item) is dict and isinstance(item.get('packageId'),str) for item in apps),'content app objects')
    require([a['packageId'] for a in apps]==sorted({a['packageId'] for a in apps}),'apps must be unique package-ID sorted')
    expected={'dev.ming.text-entry-demo':(1,'0.1.0')}
    if include_new:expected['dev.ming.install-demo']=(2,'1.0.0')
    require(set(expected)=={a['packageId'] for a in apps},'qualification active package set')
    enum={};package_files={}
    for item in files:
        exact(item,{'path','sha256'},'content file');relative(item['path'])
        require(digest(item['sha256']) and item['path'] not in enum,'content file digest/uniqueness')
        match=PACKAGE_FILE.fullmatch(item['path'])
        require(item['path'] in ROOT_FILES.values() or match,'unknown/pending/journal/lock content file')
        if match:
            require(len(match[1])%2==0 and len(match[1])<=512 and len(match[2])+1+len(match[3])<=160,
                    'unbounded/noncanonical package content path')
            package_files.setdefault(item['path'].rsplit('/',1)[0],set()).add(match[4])
        enum[item['path']]=item['sha256']
    require(all(names=={'app.json','main.wasm'} for names in package_files.values()),'incomplete historical package pair')
    require([item['path'] for item in files]==sorted(enum),'file enumeration must be sorted')
    for field,name in ROOT_FILES.items():require(enum.get(name)==content[field],'root file missing or digest differs: '+name)
    for app in apps:
        exact(app,APP_KEYS,'content app')
        require(type(app['app']) is int and (app['app'],app['version'])==expected[app['packageId']],'stable UI/package/version differs')
        require(all(digest(app[key]) for key in ['fingerprint','manifestSha256','wasmSha256']),'app digest/fingerprint')
        directory=f"packages/{app['packageId'].encode().hex()}/{app['version']}-{app['fingerprint']}"
        for pathkey,hashkey,name in [('manifestPath','manifestSha256','app.json'),('wasmPath','wasmSha256','main.wasm')]:
            require(app[pathkey]==directory+'/'+name and enum.get(app[pathkey])==app[hashkey],'active package controlled path/file digest differs')
    return content

def validate_snapshot(receipt,context,phase):
    exact(receipt,{'envelope','content'},'snapshot');env=receipt['envelope'];exact(env,ENVELOPE_KEYS,'snapshot envelope')
    number=context['bootNumber'];require(type(number) is int and number in (1,2),'fixed boot number')
    require(env['schemaVersion']==1 and type(env['schemaVersion']) is int and env['bootNumber']==number and type(env['bootNumber']) is int,'snapshot schema/boot number')
    for field in ['bootId','diskUuid','marker','previousMarker']:require(env[field]==context[field],'snapshot envelope differs from raw '+field)
    require(isinstance(env['bootId'],str) and env['bootId'] and isinstance(env['diskUuid'],str) and env['diskUuid'],'snapshot identities')
    exact(env['marker'],{'bootId','counter'},'marker')
    require(env['marker']=={'bootId':env['bootId'],'counter':number} and type(env['marker']['counter']) is int,'marker identity/counter')
    require(env['previousMarker'] is None if number==1 else type(env['previousMarker']) is dict,'previous marker shape')
    if number==2:exact(env['previousMarker'],{'bootId','counter'},'previous marker');require(env['previousMarker']['counter']==1 and type(env['previousMarker']['counter']) is int,'previous marker counter')
    require(phase in ['pre-desktop','post-desktop'] and env['phase']==phase,'snapshot phase')
    require(env['sessionId']==(None if phase=='pre-desktop' else context['sessionId']),'snapshot session binding')
    require(env['registryRoot']==ROOT and env['catalogPath']==ROOT+'/catalog.json','fixed persistent paths')
    require(env['role']==('initialize-first' if number==1 else 'open-existing'),'fixed persistent role')
    first=number==1 and phase=='pre-desktop'
    require(env['status']==('initialized-first' if first else 'opened-existing'),'snapshot status')
    exact(env['actions'],ACTION_KEYS,'snapshot actions')
    require(all(type(v) is int and v==(1 if first else 0) for v in env['actions'].values()),'initialization writes forbidden or incomplete')
    return validate_content(receipt['content'],not first)

def compare_reboot_snapshots(final1,pre2,context1,context2):
    require(context1['bootNumber']==1 and context2['bootNumber']==2 and context1['bootId']!=context2['bootId'] and context1['sessionId']!=context2['sessionId'],'fresh boot/session identities required')
    require(context1['diskUuid']==context2['diskUuid'] and context2['previousMarker']==context1['marker'],'same disk and exact previous marker chain required')
    one=validate_snapshot(final1,context1,'post-desktop');two=validate_snapshot(pre2,context2,'pre-desktop')
    require(one==two,'stable persistent content drift across reboot')
    return one

def bind_signed_content(content,signed,include_new):
    validate_content(content,include_new)
    initial=next(app for app in content['apps'] if app['app']==1)
    require(initial==signed['initialContent']['apps'][0]
            and all(content[field]==signed['initialContent'][field] for field in ['trustSha256','bootstrapSha256']),
            'initial signed package/trust/bootstrap drifted during install or reboot')
    if include_new:
        source=next(app for app in content['apps'] if app['app']==2)
        require(source['fingerprint']==signed['source']['storageFingerprint']
                and source['manifestSha256']==signed['source']['manifestSha256']
                and source['wasmSha256']==signed['source']['wasmSha256'],'snapshot source differs from verified signature/bytes')

def validate_input(spec,number):
    path=HERE/('input-boot%d.json'%number);require(base.sha(path)==INPUT_SHA256[number],'checker reference input hash differs')
    expected=base.read_json(path)
    require(json.dumps(spec,sort_keys=True)==json.dumps(expected,sort_keys=True),'input differs from exact role-specific Dock plan')
    require('inputScriptSha256' not in spec,'legacy single input forbidden')
    dock=spec['steps'][2 if number==1 else 0]
    require(dock['events']==[{'kind':'pointer','x':680,'y':770},{'kind':'button','button':'left','down':True},{'kind':'button','button':'left','down':False}],'actual native Dock button pair required')
    return spec

def expected_helper_argv(review,number,phase):
    fixture=review['persistentInstallFixture']
    first=number==1 and phase=='pre-desktop'
    argv=[fixture['helper'],'initialize-first' if first else 'open-existing',ROOT]
    if first:argv += [fixture['initialWasm'],fixture['sourceWasm'],fixture['sourceManifest'],'/opt/mingos/catalog.json',review['catalogSha256']]
    return argv+[fixture['receipt']]

def audit_snapshot_stream(rows,report,review,outcome):
    """Bind helper file receipts to raw guest stream positions and exact commands."""
    events=[row['event'] for row in rows]
    def one(kind,predicate=lambda e:True):
        matches=[i for i,e in enumerate(events) if e.get('type')==kind and predicate(e)]
        require(len(matches)==1,'expected exactly one raw '+kind);return matches[0]
    marker=one('data-marker');desktop=one('process-start',lambda e:e.get('name')=='desktop');workers=one('desktop-workers-stopped');synced=one('data-synced');terminal=one('guest-result')
    states=[e['state'] for e in events if e.get('type')=='desktop-state']
    require(states,'raw desktop states required')
    number=report.get('bootNumber');context={'bootNumber':number,'bootId':events[terminal].get('bootId'),'sessionId':states[0]['sessionId'],'diskUuid':events[terminal].get('diskUuid'),'marker':events[terminal].get('marker'),'previousMarker':events[terminal].get('previousMarker')}
    require(type(number) is int and number in (1,2),'result fixed bootNumber required')
    snapshots=[(i,e) for i,e in enumerate(events) if e.get('type')=='persistent-install-state']
    require(len(snapshots)==2,'exactly pre/post raw persistent snapshots required')
    result={}
    for (index,event),phase in zip(snapshots,['pre-desktop','post-desktop']):
        require(event.get('code')==0 and type(event.get('code')) is int and isinstance(event.get('stdout'),str) and isinstance(event.get('stderr'),str),'raw helper completion required')
        require(event.get('argv')==expected_helper_argv(review,number,phase),'raw persistent helper mode/argv differs')
        receipt=event.get('receipt');validate_snapshot(receipt,context,phase)
        if number==1 and phase=='pre-desktop':
            require(digest(review.get('catalogSha256')) and receipt['content']['catalogSha256']==review['catalogSha256'],'initialized catalog differs from reviewed template bytes')
        stdout=base.strict_json(event['stdout']);exact(stdout,{'schemaVersion','status','content','actions'},'helper stdout receipt')
        require(type(stdout['schemaVersion']) is int and stdout['schemaVersion']==1 and stdout['content']==receipt['content'] and stdout['status']==receipt['envelope']['status'] and stdout['actions']==receipt['envelope']['actions'],'raw helper stdout differs from file/envelope receipt')
        result[phase]=receipt
        if phase=='pre-desktop':require(marker<index<desktop,'persistent pre snapshot must follow verified marker and precede desktop')
        else:require(workers<index<synced<terminal,'persistent final snapshot must follow worker reap and precede sync')
    argv=['/opt/mingos/ming-app-host','desktop',ROOT+'/catalog.json','--renderer','cpu']+review['extraArgs']+['--persistent-install-state',ROOT]
    if number==1:argv+=['--install-source','/opt/mingos/install-source/app.json']
    require(events[desktop].get('argv')==argv,'desktop exact persistent root/catalog/source argv differs')
    require(not any(e.get('type') in ['install-fixture','update-fixture'] for e in events),'legacy fixture is forbidden in persistent boot')
    require(outcome.get('droppedTrace')==0,'complete native trace required')
    if number==2:
        require(not any(t.get('kind') in ['installed','updated','reinstalled','seeded'] for t in outcome.get('trace',[])),'second boot contains installation/update/seed trace')
        require(result['pre-desktop']['content']==result['post-desktop']['content'],'second boot mutated persistent content')
    return {'context':context,**result}

def persistent_receipts(events,context,desktop_pid):
    """Read complete producer lines from their original diagnostic byte stream."""
    data=base.reconstruct_log(events,'desktop-wayland.stderr.log')
    require(data.endswith(b'\n'),'persistent diagnostic stream truncated')
    prefix=b'MING_PERSISTENT_INSTALL ';result=[];offset=0
    for raw_line in data.split(b'\n')[:-1]:
        line=raw_line+b'\n'
        end=offset+len(line)
        if line.startswith(prefix):
            require(len(line)-1<=262144,'persistent producer line exceeds bound')
            record=base.strict_json(line[len(prefix):-1])
            exact(record,{'kind','bootId','sessionId','desktopPid','receipt'},'persistent producer wrapper')
            require(isinstance(record['kind'],str) and record['kind'],'persistent producer kind')
            require(record['bootId']==context['bootId'] and record['sessionId']==context['sessionId']
                    and type(record['desktopPid']) is int and record['desktopPid']==desktop_pid,
                    'persistent producer boot/session/PID binding')
            result.append({'offset':offset,'endOffset':end,'record':record})
        offset=end
    return result

def audit_transaction_receipts(events,context,desktop_pid,outcome,snapshots,signed):
    records=persistent_receipts(events,context,desktop_pid)
    transactions=[row for row in records if row['record']['kind'] in
                  ('persistent-install-commit','persistent-install-failed')]
    require(not any(row['record']['kind']=='persistent-install-failed' for row in transactions),
            'persistent installation failure receipt present')
    trace=outcome.get('trace')
    require(type(trace) is list and all(type(item) is dict for item in trace),'complete transaction trace required')
    commits=[(i,item) for i,item in enumerate(trace) if item.get('kind')=='persistent-install-commit']
    require(not any(item.get('kind')=='persistent-install-failed' for item in trace),'persistent failure trace present')
    installed=[(i,item) for i,item in enumerate(trace) if item.get('kind')=='installed']
    if context['bootNumber']==2:
        require(not transactions and not commits and not installed,'second boot contains an install transaction')
        return {'receipt':None,'offset':None,'endOffset':None}
    require(context['bootNumber']==1 and len(transactions)==1 and len(commits)==1 and len(installed)==1,
            'exactly one first-boot committed install and installed trace required')
    row=transactions[0];receipt=row['record']['receipt']
    exact(receipt,{'schemaVersion','status','packageId','version','fingerprint','registryRoot','catalogPath',
                   'content','persistentBlockConfirmed'},'persistent commit receipt')
    require(type(receipt['schemaVersion']) is int and receipt['schemaVersion']==1
            and receipt['status']=='committed' and receipt['persistentBlockConfirmed'] is False,
            'durable unblocked commit required')
    require(receipt['packageId']=='dev.ming.install-demo' and receipt['version']=='1.0.0'
            and receipt['registryRoot']==ROOT and receipt['catalogPath']==ROOT+'/catalog.json',
            'commit qualification identity/paths differ')
    validate_content(receipt['content'],True)
    source=signed['source'];app=next(item for item in receipt['content']['apps'] if item['app']==2)
    require(receipt['fingerprint']==app['fingerprint']==source['storageFingerprint']
            and app['manifestSha256']==source['manifestSha256'] and app['wasmSha256']==source['wasmSha256'],
            'commit source signature/fingerprint/content differs')
    require(receipt['content']==snapshots['post-desktop']['content'],'commit differs from final persistent snapshot')
    require(base.strict_json(commits[0][1].get('detail'))==receipt,'commit raw receipt differs from native trace')
    require(commits[0][0]<installed[0][0] and installed[0][1].get('detail')==receipt['packageId'],
            'installed trace must follow commit for signed source')
    return {'receipt':receipt,'offset':row['offset'],'endOffset':row['endOffset']}


APP_RECORD_REQUIRED={'app','generation','window','title','workerPid','status','completed','firstFrameMs','firstReadyMs','framesSubmitted','lastSourceSequence','presentedSourceSequence','presentedViewport','packageId','requests','inputCounts','events','gpuStats','error'}
APP_RECORD_OPTIONAL={'moduleSource','fuelConsumed','frameClock','appData','isolation'}
TRACE_KEYS={'atMs','app','window','generation','kind','detail'}

def audit_native_receipts(events,context,desktop_pid,outcome,progress,spec):
    """Bind all real left edges to Wayland and the already audited QMP step order."""
    sys.path.insert(0,str(HERE.parents[1]/'scripts/boot'))
    from infra import validate_state
    from wayland_receipt import PointerReceipt
    records=persistent_receipts(events,context,desktop_pid)
    allowed={'persistent-install-commit','persistent-install-failed','persistent-install-installed','native-input','native-launch'}
    require(len(records)<=128 and all(row['record']['kind'] in allowed for row in records),'unknown/unbounded producer records')
    emitted=[event for event in events if event.get('type')=='desktop-persistent-receipt']
    require(len(emitted)==len(records),'native transport coverage differs')
    for row,event in zip(records,emitted):
        require(event.get('bootId')==context['bootId'] and event.get('sessionId')==context['sessionId']
                and event.get('desktopPid')==desktop_pid and type(event.get('desktopPid')) is int
                and event.get('logStartOffset')==row['offset'] and event.get('logEndOffset')==row['endOffset']
                and event.get('record')==row['record'],'native transport bytes/identity differ')
    trace=outcome.get('trace');instances=outcome.get('instances')
    require(type(trace) is list and type(instances) is list and outcome.get('droppedTrace')==0
            and outcome.get('droppedInputs')==0,'complete native trace/inputs required')
    for item in trace:
        exact(item,TRACE_KEYS,'native trace')
        require(all(type(item[key]) is int and item[key]>=0 for key in ['atMs','app','window','generation'])
                and isinstance(item['kind'],str) and isinstance(item['detail'],str),'native trace values')
    anchors=[]
    def identity(record):return (record['app'],record['generation'],record['window'])
    def anchor(value):
        exact(value,{'state','history','traceCount','droppedTrace','droppedInputs'},'native anchor')
        require(type(value['traceCount']) is int and 0<=value['traceCount']<=len(trace)
                and type(value['droppedTrace']) is int and value['droppedTrace']==0
                and type(value['droppedInputs']) is int and value['droppedInputs']==0,'native anchor trace/drop count')
        state=value['state'];exact(state,{'schemaVersion','bootId','sessionId','revision','phase','root','apps','error'},'native state')
        validate_state(state,context['bootId'],context['sessionId'])
        require(type(state['schemaVersion']) is int and state['schemaVersion']==1 and state['error'] is None
                and state['phase']!='failed','native state failed/schema')
        history=value['history'];require(type(history) is list and 1<=len(history)<=4,'complete bounded native history')
        ids=[]
        for item in history:
            require(type(item) is dict and APP_RECORD_REQUIRED<=set(item)<=APP_RECORD_REQUIRED|APP_RECORD_OPTIONAL,'exact native AppRecord schema')
            require(all(type(item[key]) is int and item[key]>0 for key in ['app','generation','window'])
                    and type(item['workerPid']) is int and item['workerPid']>=0 and item['error'] is None
                    and type(item['completed']) is bool,'native history identity/error')
            expected_package={1:'dev.ming.text-entry-demo',2:'dev.ming.install-demo'}.get(item['app'])
            require(item['app'] in (1,2) and item['generation']==1 and
                    (item['packageId']==expected_package or (item['packageId'] is None and item['completed'] is False)),
                    'native qualification package/generation differs')
            ids.append(identity(item))
        require(len(ids)==len(set(ids)),'duplicate native history identity')
        for app in state['apps']:
            matching=[item for item in history if identity(item)==(app['app'],app['generation'],app['window'])]
            require(len(matching)==1 and matching[0]['workerPid']==app['workerPid'],'native state differs from complete history')
        anchors.append(value);return value
    native=[row for row in records if row['record']['kind']=='native-input']
    require(len(native)==len(spec['steps'])*2 and len(native)<=64,'complete all-left native sequence required')
    data=base.reconstruct_log(events,'desktop-wayland.stderr.log');parser=PointerReceipt();buttons=[];offset=0
    pattern=re.compile(rb'^(?:\[[^\]\r\n]+\]\s+)?wl_pointer[#@](\d+)\.button\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)\s*$')
    for raw_line in data.split(b'\n')[:-1]:
        line=raw_line+b'\n'
        parser.feed(line);end=offset+len(line)
        if not line.startswith(b'MING_PERSISTENT_INSTALL '):
            match=pattern.fullmatch(line[:-1])
            if match:
                require(int(match[4])==272 and int(match[5]) in (0,1),'unreviewed Wayland button')
                require(parser.entered and parser.pointer==int(match[1]),'Wayland button lacks active pointer/surface')
                buttons.append(dict(offset=offset,endOffset=end,pointer=int(match[1]),surface=parser.surface,
                                    x=parser.x,y=parser.y,down=int(match[5])==1,serial=int(match[2])))
        offset=end
    require(len(buttons)==len(native) and len({b['serial'] for b in buttons})==len(buttons),'Wayland/native all-left coverage differs')
    previous_revision=0;dock=None
    for index,(row,button) in enumerate(zip(native,buttons)):
        receipt=row['record']['receipt'];exact(receipt,{'schemaVersion','sequence','pressSequence','edge','x','y','action','targetApp','before','after','trace'},'native input')
        sequence=index+1;down=sequence%2==1;step=index//2;point=spec['steps'][step]['events'][0]
        require(type(receipt['schemaVersion']) is int and receipt['schemaVersion']==1 and type(receipt['sequence']) is int
                and receipt['sequence']==sequence and type(receipt['pressSequence']) is int
                and receipt['pressSequence']==(sequence if down else sequence-1)
                and receipt['edge']==('down' if down else 'up'),'native left sequence/press pairing')
        ready=progress[2+step*4]['receipt']
        require(button['down']==down and button['endOffset']<=row['offset']
                and button['offset']>=ready['logOffset'] and button['pointer']==ready['pointer']
                and button['surface']==ready['surface'],'native edge lacks current Wayland ready/button order')
        if context['bootNumber']==1 and sequence==4:
            require(all(button['endOffset']<=item['offset'] for item in records
                        if item['record']['kind']=='persistent-install-commit'),
                    'persistent commit precedes actual Wayland ALLOW up button')
        require(all(type(receipt[axis]) is int and abs(receipt[axis]-point[axis])<=1
                    and button[axis] is not None and abs(button[axis]-receipt[axis])<=1 for axis in ['x','y']),
                'native edge actual coordinates differ')
        before=anchor(receipt['before']);after=anchor(receipt['after'])
        require(previous_revision<=before['state']['revision']<after['state']['revision'],'native edge state revision order')
        previous_revision=after['state']['revision']
        require(before['traceCount']<=after['traceCount'] and receipt['trace']==trace[before['traceCount']:after['traceCount']],
                'native handler trace differs from full outcome')
        baseline=progress[3+step*4]['state'];confirmed=progress[4+step*4]['state']
        require(baseline['revision']<=before['state']['revision'],'native handler precedes audited input baseline')
        if after['state']['revision']>confirmed['revision']:
            # ALLOW publishes an installed state inside its up handler. The
            # controller can observe that revision before end_native_input
            # publishes the final after anchor; the next Dock handshake must
            # still observe the completed handler in both state and raw bytes.
            installed=[item for item in records if item['record']['kind']=='persistent-install-installed']
            require(context['bootNumber']==1 and sequence==4 and not down
                    and spec['steps'][step]['name']=='allow-install'
                    and step+1<len(spec['steps']) and spec['steps'][step+1]['name']=='dock-launch-app2'
                    and len(installed)==1,'native handler not enclosed by audited input states')
            installation=installed[0];revision=installation['record']['receipt'].get('stateRevision')
            require(type(revision) is int and before['state']['revision']<revision<=confirmed['revision']
                    and installation['endOffset']<=row['offset']
                    and all(progress[position+(step+1)*4]['state']['revision']>=after['state']['revision']
                            for position in (1,3))
                    and row['endOffset']<=progress[2+(step+1)*4]['receipt']['logOffset'],
                    'ALLOW intermediate confirmation lacks installed/completed next-step anchors')
        is_dock=spec['steps'][step]['name']=='dock-launch-app2' and down
        require(receipt['action']==('launch' if is_dock else 'other')
                and receipt['targetApp']==(2 if is_dock else None),'actual native Dock launch action required')
        if is_dock:
            require(dock is None,'duplicate Dock down');dock=row
            require(not any(item['app']==2 for item in before['history'])
                    and not any(item['app']==2 for item in before['state']['apps'])
                    and not any(item['app']==2 for item in trace[:before['traceCount']]),'app2 existed before native Dock launch')
            new=[item for item in after['history'] if item['app']==2]
            launched=[item for item in receipt['trace'] if item['app']==2 and item['kind']=='launch']
            require(len(new)==1 and new[0]['generation']==1 and len(launched)==1
                    and (launched[0]['app'],launched[0]['generation'],launched[0]['window'])==identity(new[0]),
                    'Dock down did not create first app2 generation')
    require(dock is not None,'native Dock down missing')
    down=dock['record']['receipt'];launches=[row for row in records if row['record']['kind']=='native-launch']
    for event in events:
        if event.get('type')=='desktop-state' and event['state']['revision']<=down['before']['state']['revision']:
            require(not any(app['app']==2 for app in event['state']['apps']),'raw app2 state precedes actual Dock down')
    require(len(launches)==2 and [row['record']['receipt'].get('phase') for row in launches]==['worker-started','ready'],
            'unique worker-started then accepted ready receipts required')
    worker_pid=None;window=None
    for row in launches:
        receipt=row['record']['receipt'];exact(receipt,{'schemaVersion','downSequence','phase','app','generation','window','workerPid','anchor'},'native launch')
        require(type(receipt['schemaVersion']) is int and receipt['schemaVersion']==1 and type(receipt['downSequence']) is int
                and receipt['downSequence']==down['sequence'] and type(receipt['app']) is int and receipt['app']==2
                and type(receipt['generation']) is int and receipt['generation']==1
                and type(receipt['window']) is int and receipt['window']>0
                and type(receipt['workerPid']) is int and receipt['workerPid']>0,'native first-launch cause/identity')
        value=anchor(receipt['anchor']);require(value['state']['revision']>down['before']['state']['revision'],'native launch precedes Dock down anchor')
        records2=[item for item in value['history'] if item['app']==2]
        require(len(records2)==1 and identity(records2[0])==(2,1,receipt['window'])
                and records2[0]['workerPid']==receipt['workerPid'],'native worker anchor identity differs')
        if worker_pid is None:worker_pid=receipt['workerPid'];window=receipt['window']
        require(worker_pid==receipt['workerPid'] and window==receipt['window'],'native ready worker changed')
        if receipt['phase']=='ready':
            apps=[app for app in value['state']['apps'] if app['app']==2]
            require(len(apps)==1 and apps[0]['inputReady'] is True and apps[0]['workerPid']==worker_pid
                    and apps[0]['generation']==1 and apps[0]['presentedSourceSequence']>0
                    and records2[0]['firstReadyMs'] is not None
                    and any(item['kind']=='ready' and item['app']==2 and item['generation']==1
                            and item['window']==window for item in trace[:value['traceCount']]),'accepted native first ready missing')
    require(launches[0]['offset']<launches[1]['offset'],'native worker/ready byte order')
    require(launches[0]['offset']>=dock['endOffset'],'worker receipt precedes completed native Dock down')
    require(launches[1]['record']['receipt']['anchor']['state']['revision']<=progress[4+(down['sequence']-1)//2*4]['state']['revision'],
            'native ready not reached within Dock expectation')
    by_revision={}
    for value in anchors:
        rev=value['state']['revision']
        require(rev not in by_revision or by_revision[rev]==value,'same native revision changed')
        by_revision[rev]=value
    previous=set();last_count=0;known_pids={}
    for revision,value in sorted(by_revision.items()):
        identities={identity(item) for item in value['history']}
        require(previous<=identities and last_count<=value['traceCount'],'native history/trace regressed')
        previous=identities;last_count=value['traceCount']
        for item in value['history']:
            key=identity(item);pid=item['workerPid']
            require(key not in known_pids or pid==known_pids[key],'native worker PID regressed/changed')
            if pid>0:known_pids[key]=pid
        for event in events:
            if event.get('type')=='desktop-state' and event['state']['revision']==revision:
                require(event['state']==value['state'],'native anchor differs from polled raw state')
    require(previous=={identity(item) for item in instances},'native complete history differs from final instances')
    require(all(item.get('packageId')=={1:'dev.ming.text-entry-demo',2:'dev.ming.install-demo'}.get(item['app'])
                for item in instances),'final worker package identity differs from reviewed catalog')
    final2=[item for item in instances if item['app']==2]
    require(len(final2)==1 and final2[0]['generation']==1 and final2[0]['workerPid']==worker_pid
            and final2[0]['window']==window and final2[0].get('packageId')=='dev.ming.install-demo',
            'final app2 identity differs from actual Dock worker')
    require(all(item['workerPid']!=worker_pid for item in instances if item['app']!=2),'Dock launch reused existing worker PID')
    return {'dockDownSequence':down['sequence'],'workerPid':worker_pid,'window':window,
            'nativeLeftEdges':len(native),'waylandButtons':len(buttons),'dockOffset':dock['offset'],
            'workerStartedOffset':launches[0]['offset'],'readyOffset':launches[1]['offset']}

def audit_installed_snapshot(records,context,transaction,snapshots,dock):
    rows=[row for row in records if row['record']['kind']=='persistent-install-installed']
    if context['bootNumber']==2:
        require(not rows,'second boot contains installed-complete receipt');return None
    require(len(rows)==1,'unique installed-complete receipt required')
    row=rows[0];receipt=row['record']['receipt']
    exact(receipt,{'schemaVersion','registryRoot','catalogPath','stateRevision','content'},'installed-complete')
    require(type(receipt['schemaVersion']) is int and receipt['schemaVersion']==1
            and type(receipt['stateRevision']) is int and receipt['stateRevision']>0
            and receipt['registryRoot']==ROOT and receipt['catalogPath']==ROOT+'/catalog.json','installed-complete identity/schema')
    validate_content(receipt['content'],True)
    require(receipt['content']==transaction['receipt']['content']==snapshots['post-desktop']['content'],
            'commit/installed/final stable content differs')
    allow=[r for r in records if r['record']['kind']=='native-input' and r['record']['receipt'].get('sequence')==4]
    require(len(allow)==1,'ALLOW up native anchor required');up=allow[0]['record']['receipt']
    down=[r for r in records if r['record']['kind']=='native-input' and r['record']['receipt'].get('sequence')==3]
    require(len(down)==1 and down[0]['record']['receipt'].get('edge')=='down'
            and down[0]['endOffset']<=transaction['offset'],'commit must follow actual ALLOW down')
    commit_trace=[item for item in up.get('trace',[]) if item.get('kind')=='persistent-install-commit']
    installed_trace=[item for item in up.get('trace',[]) if item.get('kind')=='installed']
    require(len(commit_trace)==1 and base.strict_json(commit_trace[0].get('detail'))==transaction['receipt']
            and len(installed_trace)==1 and installed_trace[0].get('detail')=='dev.ming.install-demo',
            'commit and installation must occur inside actual ALLOW up handler trace')
    require(up['edge']=='up' and up['before']['state']['revision']<receipt['stateRevision']<=up['after']['state']['revision']
            and transaction['endOffset']<=row['offset']<row['endOffset']<=allow[0]['offset']<dock['dockOffset']
            and dock['dockDownSequence']==5,'commit/installed/ALLOW-up/Dock causal order differs')
    return {'stateRevision':receipt['stateRevision'],'offset':row['offset'],'endOffset':row['endOffset'],
            'contentSha256':hashlib.sha256(json.dumps(receipt['content'],sort_keys=True,separators=(',',':')).encode()).hexdigest()}


def verify_fixture_directory(directory,inputs,review):
    """Reverify actual helper files; never trust a success-only fixture summary."""
    import base64
    import stat
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    directory=Path(directory).absolute();payload=inputs/'payload';root=directory/'install-root'
    require(not directory.is_symlink() and not root.is_symlink(),'unsafe fixture directory')
    command=base.read_json(directory/'command.json');helper=payload/'prepare-persistent-install-fixture'
    expected=[str(helper),'initialize-first',str(root),str(payload/'packages/initial/main.wasm'),str(payload/'packages/new/main.wasm'),
        str(directory/'install-source/app.json'),str(payload/'catalog.json'),review['catalogSha256'],str(directory/'fixture-receipt.json')]
    helper_hash=base.sha(helper)
    require(command.get('stage')=='fixture' and type(command.get('exitCode')) is int and command['exitCode']==0
            and command.get('argv')==expected and command.get('executableSha256')==helper_hash
            and helper_hash==review['persistentInstallFixtureSha256']['helper'],'actual reviewed helper command differs/failed')
    receipt=base.read_json(directory/'fixture-receipt.json')
    exact(receipt,{'schemaVersion','status','content','actions'},'actual helper fixture receipt')
    exact(receipt['actions'],ACTION_KEYS,'actual helper fixture actions')
    require(type(receipt['schemaVersion']) is int and receipt['schemaVersion']==1 and receipt['status']=='initialized-first'
            and all(type(v) is int and v==1 for v in receipt['actions'].values()),'actual initialize-first fixture receipt required')
    content=validate_content(receipt['content'],False)
    require(base.strict_json((directory/'stdout.log').read_bytes())==receipt,'actual helper stdout/receipt differs')
    require(content['catalogSha256']==base.sha(payload/'catalog.json')==review['catalogSha256'],'actual initialized catalog differs from reviewed bytes')
    actual={};locks={'.ming-installed-packages.lock','.ming-publisher-keyring.lock'}
    for path in sorted(root.rglob('*')):
        metadata=path.lstat();require(not stat.S_ISLNK(metadata.st_mode),'fixture symlink')
        if stat.S_ISDIR(metadata.st_mode):continue
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink==1,'unsafe fixture content metadata')
        name=str(path.relative_to(root))
        if name not in locks:actual[name]=base.sha(path)
    require(actual=={item['path']:item['sha256'] for item in content['files']},'actual fixture file enumeration/digest differs')
    keyring=root/'trusted-publishers-v1';lines=keyring.read_text().splitlines()
    require(len(lines)==2 and lines[0]=='ming-trusted-publishers-v1','actual keyring shape')
    row=lines[1].split('\t');require(len(row)==3 and row[:2]==['mingos-dev-2026','active'],'actual active publisher identity')
    public=base64.b64decode(row[2],validate=True)
    expected_public=Ed25519PrivateKey.from_private_bytes(bytes([7])*32).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    require(public==expected_public,'actual development fixture publisher key differs')
    key=Ed25519PublicKey.from_public_bytes(public);app=content['apps'][0]
    for path in [root/app['manifestPath'],directory/'install-source/app.json']:
        manifest=base.read_json(path)
        required={'schemaVersion','id','version','sdkVersion','entry','privateStorage','publicStorage','publisherKeyId','publisherSignature'}
        require(type(manifest) is dict and required<=set(manifest)<=required|{'dataMigration'},'exact signed manifest schema')
        require(type(manifest['schemaVersion']) is int and manifest['schemaVersion']==1
                and type(manifest['sdkVersion']) is int and manifest['sdkVersion']==6
                and manifest['privateStorage'] is False and manifest['publicStorage'] is False
                and manifest.get('dataMigration') is None,'signed manifest exact numeric/storage schema')
    initial=base.package(root/app['manifestPath'],'dev.ming.text-entry-demo','0.1.0',payload/'packages/initial/main.wasm',key)
    source=base.package(directory/'install-source/app.json','dev.ming.install-demo','1.0.0',payload/'packages/new/main.wasm',key)
    require(initial['storageFingerprint']==app['fingerprint'] and initial['manifestSha256']==app['manifestSha256']
            and initial['wasmSha256']==app['wasmSha256'],'actual initial signature/fingerprint differs from helper content')
    for label,package in [('initial',initial),('source',source)]:
        require(package['wasmSha256']==review['persistentInstallFixtureSha256'][label+'Wasm'],'fixture Wasm differs from reviewed payload')
    return {'status':'actual-persistent-fixture-signatures-and-fingerprints-verified','fixtureDirectory':str(directory),
        'initial':initial,'source':source,'initialContent':content,'helperSha256':helper_hash,
        'commandSha256':base.sha(directory/'command.json'),'receiptSha256':base.sha(directory/'fixture-receipt.json'),
        'stdoutSha256':base.sha(directory/'stdout.log'),'stderrSha256':base.sha(directory/'stderr.log'),
        'publisherKeyringSha256':base.sha(keyring)}

def fixture(args):
    sys.path.insert(0,str(args.repo/'scripts/boot'))
    from bootlib import checked_session,validate_reviewed_input
    from bundle import verify_payload
    review=base.read_json(args.review);checked_session(review,args.inputs/'payload')
    require(review.get('profile')=='persistent-install','persistent profile required')
    verify_payload(args.inputs/'payload',review)
    for number in (1,2):validate_reviewed_input(review,args.inputs/('input-boot%d.json'%number),number)
    return verify_fixture_directory(args.fixture,args.inputs,review)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',nargs='?',choices=['fixture','pair'],default='pair')
    for name in ['repo','inputs','review','output']:parser.add_argument('--'+name,type=Path,required=True)
    for name in ['bundle','pair','fixture-verification','fixture']:parser.add_argument('--'+name,type=Path)
    args=parser.parse_args();require(not args.output.exists(),'new output required')
    try:
        required=['fixture'] if args.operation=='fixture' else ['bundle','pair','fixture_verification']
        require(all(getattr(args,key) is not None for key in required),'operation paths missing')
        result=fixture(args) if args.operation=='fixture' else pair(args);code=0
    except Exception as error:result={'status':'failed','error':repr(error),'persistentInstallNormalRebootVerified':False,'dockLaunchVerified':False,'finalSystemAccepted':False};code=1
    with args.output.open('x') as output:json.dump(result,output,indent=2);output.write('\n')
    print(json.dumps(result,indent=2));return code

def pair(args):
    sys.path.insert(0,str(args.repo/'scripts/boot'))
    from bootlib import validate_bundle,validate_reviewed_input,checked_session
    from bundle import verify_payload
    from infra import validate_cold_pair,state_input_ready
    review=base.read_json(args.review);checked_session(review,args.inputs/'payload')
    require(review.get('profile')=='persistent-install','persistent profile required')
    verify_payload(args.inputs/'payload',review)
    specs=[]
    for number in (1,2):
        frozen,input_digest=validate_reviewed_input(review,args.inputs/('input-boot%d.json'%number),number)
        require(input_digest==INPUT_SHA256[number],'reviewed input differs from frozen role input bytes')
        specs.append(validate_input(base.strict_json(frozen),number))
    bundle=validate_bundle(args.bundle,base.sha(args.review))
    require(bundle.get('sessionReview')==review and bundle.get('payloadProfile')=='persistent-install'
            and bundle.get('instrumentedQualification') is True and bundle.get('inputDiagnostics') is True
            and bundle.get('diagnosticOnly') is False and bundle.get('diagnosticProfile')=='input-pointer-ready-v1'
            and bundle.get('autostart')==[1],'actual persistent pointer-ready bundle required')
    for name,digest in bundle['payloadSha256'].items():
        require(base.sha(args.inputs/'payload'/name)==digest,'reviewed bundle payload changed: '+name)
    signed=base.read_json(args.fixture_verification)
    require(signed.get('status')=='actual-persistent-fixture-signatures-and-fingerprints-verified',
            'actual persistent fixture verification missing')
    from types import SimpleNamespace
    reproduced=fixture(SimpleNamespace(repo=args.repo,inputs=args.inputs,review=args.review,fixture=Path(signed['fixtureDirectory'])))
    require(signed==reproduced,'fixture signature summary differs from independently reverified bytes')
    reports=[];snapshots=[];audited=[]
    for number,spec in enumerate(specs,1):
        boot=args.pair/('boot-'+str(number));report=base.read_json(boot/'result.json')
        require(type(report.get('bootNumber')) is int and report['bootNumber']==number
                and report.get('bundleIdentity')==base.sha(args.bundle/'bundle.json'),'raw boot number/bundle differs')
        raw=base.audit_raw_boot(boot,spec,report,args.repo);events=raw['events'];outcome=raw['outcome'];progress=raw['progress']
        rows=raw_rows(boot/'guest.jsonl','uptime');require([r['event'] for r in rows]==events,'raw guest reread changed')
        snapshot=audit_snapshot_stream(rows,report,review,outcome);context=snapshot['context']
        for phase in ['pre-desktop','post-desktop']:
            bind_signed_content(snapshot[phase]['content'],signed,not (number==1 and phase=='pre-desktop'))
        desktop=[event for event in events if event.get('type')=='process-start' and event.get('name')=='desktop']
        require(len(desktop)==1 and type(desktop[0].get('pid')) is int and desktop[0]['pid']>0,'unique real desktop PID required')
        transaction=audit_transaction_receipts(events,context,desktop[0]['pid'],outcome,snapshot,signed)
        native=audit_native_receipts(events,context,desktop[0]['pid'],outcome,progress,spec)
        installed=audit_installed_snapshot(persistent_receipts(events,context,desktop[0]['pid']),context,transaction,snapshot,native)
        if number==1:
            require(snapshot['pre-desktop']['content']==signed['initialContent'],'first-boot initialized content differs from independently verified fixture')
            prompt=progress[4]['state']['root'];baseline=progress[3]['state']['root']
            require(prompt.get('storagePrompt')==spec['steps'][0]['expect']['storagePrompt']
                    and type(prompt.get('storagePromptPresentedSequence')) is int
                    and prompt['storagePromptPresentedSequence']==prompt['presentedSequence']>baseline['presentedSequence'],
                    'actual post-dispatch install prompt presentation required')
            require(progress[8]['state']['root'].get('storagePrompt') is None,'install prompt did not finish')
        require(state_input_ready(progress[-2]['state'],[1,2]),'shutdown before both actual workers ready')
        require(all(item.get('completed') is True and item.get('error') is None for item in outcome['instances']),
                'both actual workers must complete without errors')
        reports.append(report);snapshots.append(snapshot)
        audited.append({'boot':number,'bootId':context['bootId'],'sessionId':context['sessionId'],'desktopPid':desktop[0]['pid'],
            'confirmedSteps':[step['name'] for step in spec['steps']],'pointerHandshakes':raw['pointerHandshakes'],
            'qmpInputRequestsAcknowledged':raw['qmpInputRequestsAcknowledged'],'nativeDock':native,'installedComplete':installed,
            'commitReceiptOffset':transaction['offset'],'diagnosticLogSha256':raw['diagnosticLogSha256'],
            **{name+'Sha256':base.sha(boot/name) for name in ['qmp.jsonl','result.json','inputs.jsonl','guest.jsonl']}})
    validate_cold_pair(*reports)
    require([r['marker']['counter'] for r in reports]==[1,2],'fresh marker chain 1 -> 2 required')
    stable=compare_reboot_snapshots(snapshots[0]['post-desktop'],snapshots[1]['pre-desktop'],snapshots[0]['context'],snapshots[1]['context'])
    require(base.read_json(args.pair/'result.json').get('status')=='same-bundle-disk-cold-boot-verified','cold-pair runner success missing')
    return {'status':'actual-persistent-install-cold-pair-evidence-verified','boots':audited,
        'bundleIdentity':base.sha(args.bundle/'bundle.json'),'diskUuid':reports[0]['diskUuid'],'markers':[1,2],
        'fixtureVerificationSha256':base.sha(args.fixture_verification),'checkerSha256':base.sha(Path(__file__)),
        'frozenRawBaseSha256':BASE_SHA256,'stableContent':stable,'persistentInstallNormalRebootVerified':True,
        'dockLaunchVerified':True,'finalSystemAccepted':False,
        'qualification':'Complete raw QMP/Wayland/native producer receipts and persistent snapshots only; physical display/input remains unverified.'}

if __name__=='__main__':raise SystemExit(main())
