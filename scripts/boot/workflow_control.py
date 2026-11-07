"""Private, bounded developer input. Only the VM event loop may own QMP.

Commands are real virtual keyboard/mouse input through the existing InputPlan.
This diagnostic interface grants no storage permission and exposes no raw QMP.
"""
import hmac
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import struct
import time

from bootlib import strict_json
from infra import InputPlan, KEYS, append_record, input_events, state_input_ready

MAX_REQUEST=8192
MAX_CLIENTS=4
COMMAND_SECONDS=30

def layout_digest(state):
    """Bind the presented layout/target identities while allowing animation frames.

    Native state has no separate focus/z-order field. The controller's inputEpoch
    additionally invalidates every earlier observation when another input starts.
    This assumes the reviewed sole QMP owner and no competing physical input.
    """
    if type(state) is not dict or type(state.get('root')) is not dict or type(state.get('apps')) is not list:
        raise ValueError('presented layout state required')
    root=state['root']
    value={'bootId':state['bootId'],'sessionId':state['sessionId'],'phase':state['phase'],
        'root':{key:root.get(key) for key in ('width','height','storagePrompt')},
        'apps':[{key:app.get(key) for key in ('app','generation','window','workerPid','state',
            'inputReady','minimized','outer','appData')} for app in sorted(state['apps'],key=lambda item:item['app'])],
        'error':state.get('error')}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def key_events(qcode,modifiers=()):
    return ([{'kind':'key','qcode':m,'down':True} for m in modifiers]
        +[{'kind':'key','qcode':qcode,'down':True},{'kind':'key','qcode':qcode,'down':False}]
        +[{'kind':'key','qcode':m,'down':False} for m in reversed(modifiers)])

def command_steps(command):
    if type(command) is not dict: raise ValueError('command object required')
    op=command.get('op')
    if op in ('status','capture'):
        if set(command)!={'op'}: raise ValueError('status/capture has no path or arbitrary argument')
        return []
    if op in ('click','power'):
        if set(command)!={'op','x','y'} or any(type(command[k]) is not int or not 0<=command[k]<4096 for k in ('x','y')):
            raise ValueError('bounded integer pointer coordinates required')
        events=[{'kind':'pointer','x':command['x'],'y':command['y']},
                {'kind':'button','button':'left','down':True},{'kind':'button','button':'left','down':False}]
        return [{'name':op,'events':events,'expect':{'phase':'stopped'} if op=='power' else {'rootAdvance':True}}]
    if op=='key':
        modifiers=command.get('modifiers',[])
        if (set(command)-{'op','qcode','modifiers'} or not {'op','qcode'}<=set(command)
            or command['qcode'] not in KEYS or type(modifiers) is not list or len(modifiers)>2
            or any(m not in ('ctrl','shift','alt') for m in modifiers) or len(set(modifiers))!=len(modifiers)):
            raise ValueError('supported qcode and unique ctrl/shift/alt modifiers required')
        return [{'name':'key','events':key_events(command['qcode'],modifiers),'expect':{'rootAdvance':True}}]
    if op=='type':
        value=command.get('text')
        if set(command)!={'op','text'} or type(value) is not str or not 1<=len(value)<=512 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \n' for c in value):
            raise ValueError('type supports 1..512 Latin letters, digits, spaces and newlines; use key for navigation')
        steps=[]
        for start in range(0,len(value),8):
            events=[]
            for c in value[start:start+8]:
                events+=key_events('spc' if c==' ' else 'ret' if c=='\n' else c.lower(),['shift'] if c.isupper() else [])
            steps.append({'name':'type-%03d'%start,'events':events,'expect':{'sourceAdvance':[1],'rootAdvance':True}})
        return steps
    raise ValueError('unknown workflow command')

class RequestGuard:
    def __init__(self,token,instance):
        self.token,self.instance,self.sequence=token,instance,0
        self.input_epoch=0

    def accept(self,request,state):
        if (type(request) is not dict or set(request)!={'schemaVersion','token','instance','sequence','sessionId','revision','inputEpoch','layoutSha256','command'}
            or type(request['schemaVersion']) is not int or request['schemaVersion']!=1
            or type(request['token']) is not str or not hmac.compare_digest(request['token'],self.token)
            or request['instance']!=self.instance or type(request['sequence']) is not int
            or not self.sequence<request['sequence']<2**63):
            raise ValueError('workflow request authentication/identity/sequence differs')
        command=request['command']; command_steps(command)
        if command['op']!='status' and (not state or request['sessionId']!=state['sessionId']
            or type(request['revision']) is not int or not 0<=request['revision']<=state['revision']
            or type(request['inputEpoch']) is not int or request['inputEpoch']!=self.input_epoch
            or request['layoutSha256']!=layout_digest(state)):
            raise ValueError('workflow current session/layout/input epoch differs; refresh status')
        self.sequence=request['sequence']
        return command

class Controller:
    def __init__(self,directory,runtime,output):
        self.directory=Path(directory); runtime=Path(runtime).resolve(strict=True)
        if os.name!='posix' or not hasattr(socket,'AF_UNIX'): raise ValueError('Linux private Unix control required')
        if self.directory.is_symlink() or not self.directory.is_absolute() or not self.directory.parent.resolve(strict=True).is_relative_to(runtime):
            raise ValueError('new control directory must be inside owned runtime')
        self.directory.mkdir(mode=0o700,exist_ok=False)
        self.directory.chmod(0o700)
        self.instance=secrets.token_hex(16); token=secrets.token_hex(32)
        self.guard=RequestGuard(token,self.instance)
        self.path=self.directory/'control.sock'
        if len(os.fsencode(self.path))>107: raise ValueError('Unix control socket path exceeds Linux bound')
        self.output=Path(output); self.log=self.output/'workflow-control.jsonl'
        self.server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.server.bind(str(self.path)); self.path.chmod(0o600)
        self.server.listen(MAX_CLIENTS); self.server.setblocking(False)
        self.clients={}; self.active=None; self.capture_index=0
        credential=self.directory/'control.json'
        fd=os.open(credential,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as out:
            json.dump({'schemaVersion':1,'instance':self.instance,'token':token,'socket':str(self.path)},out)
            out.write('\n'); out.flush(); os.fsync(out.fileno())
        append_record(self.log,{'type':'control-start','instance':self.instance,'transport':'private-unix','qualification':False})

    def reply(self,client,record):
        raw=json.dumps(record,separators=(',',':')).encode()+b'\n'
        # Replies are bounded and best effort; input completion is independently logged.
        try: client.send(raw[:262144])
        except (BlockingIOError,BrokenPipeError,ConnectionResetError): pass
        client.close(); self.clients.pop(client,None)

    def capture(self,qmp):
        name='workflow-capture-%04d.ppm'%self.capture_index; self.capture_index+=1
        qmp.execute('screendump',{'filename':str(self.output/name)})
        return name

    def poll(self,stream,qmp):
        if self.active:
            active=self.active
            active['plan'].check_deadline()
            if (active['plan'].pending_pointer is not None or not active['plan'].waiting) and layout_digest(stream.state)!=active['layoutSha256']:
                raise ValueError('presented target layout changed before workflow input dispatch')
            if stream.state: stream.advance_plan(stream.state,qmp)
            if active['plan'].complete:
                active['plan'].release(qmp)
                self._completed(stream,None if active['command']['op']=='power' else self.capture(qmp))
        try:
            client,_=self.server.accept(); client.setblocking(False)
            if len(self.clients)>=MAX_CLIENTS: client.close()
            elif hasattr(socket,'SO_PEERCRED') and struct.unpack('3i',client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=os.getuid():
                client.close()
            else: self.clients[client]={'buffer':b'','deadline':time.monotonic()+1}
        except BlockingIOError: pass
        for client,entry in list(self.clients.items()):
            if self.active and self.active['client'] is client: continue
            try:
                try: block=client.recv(MAX_REQUEST+1-len(entry['buffer']))
                except BlockingIOError: block=None
                if block==b'': self.reply(client,{'status':'closed'}); continue
                if block is not None: entry['buffer']+=block
                raw=entry['buffer']
                if len(raw)>MAX_REQUEST: raise ValueError('request exceeds 8192 bytes')
                if b'\n' not in raw:
                    if time.monotonic()>=entry['deadline']: raise ValueError('request read deadline')
                    continue
                line,tail=raw.split(b'\n',1)
                if tail: raise ValueError('one request per connection required')
                request=strict_json(line)
                command=self.guard.accept(request,stream.state)
                if command['op']=='status':
                    self.reply(client,{'status':'ready' if stream.state and state_input_ready(stream.state,[1]) else 'starting',
                        'instance':self.instance,'active':bool(self.active),'state':stream.state,
                        'layoutSha256':layout_digest(stream.state) if stream.state and stream.state.get('root') else None,
                        'inputEpoch':self.guard.input_epoch})
                    continue
                if command['op']=='capture':
                    self.reply(client,{'status':'captured','instance':self.instance,'state':stream.state,'capture':self.capture(qmp)})
                    continue
                if self.active or stream.plan is not None: raise ValueError('another input command is active')
                if not state_input_ready(stream.state,[1]): raise ValueError('current presented desktop/app is not input-ready')
                steps=command_steps(command)
                for step in steps: input_events(step['events'],stream.state)
                sequence=request['sequence']
                plan=InputPlan({'schemaVersion':1,'requireApps':[1],'steps':steps},
                    self.output/('workflow-input-%d.jsonl'%sequence),COMMAND_SECONDS,
                    split_transactions=True,pointer_handshake=True)
                self.active={'client':client,'command':command,'sequence':sequence,'plan':plan,
                    'layoutSha256':layout_digest(stream.state)}
                self.guard.input_epoch+=1
                stream.plan=plan; stream.input_capture_prefix='workflow-input-%d'%sequence
                append_record(self.log,{'type':'control-accepted','instance':self.instance,'sequence':sequence,'command':command,'state':stream.state})
            except (ValueError,OSError,KeyError,TypeError) as error:
                self.reply(client,{'status':'rejected','error':str(error),'instance':self.instance})
        if self.active and stream.plan is None: raise ValueError('active workflow input owner changed')

    def _completed(self,stream,capture):
        active=self.active
        record={'type':'control-completed','sequence':active['sequence'],'instance':self.instance,
            'command':active['command'],'state':stream.state,'capture':capture}
        append_record(self.log,record); self.reply(active['client'],record)
        self.active=None; stream.plan=None

    def finish(self,stream):
        """Called after final guest drain; QEMU may already be gone."""
        if not self.active: return
        if not self.active['plan'].complete or self.active['plan'].held:
            raise ValueError('workflow command unfinished at guest exit')
        self._completed(stream,None)

    def close(self,qmp):
        if self.active:
            self.active['plan'].release(qmp)
            append_record(self.log,{'type':'control-incomplete','instance':self.instance,'sequence':self.active['sequence']})
        for client in list(self.clients): client.close()
        self.clients.clear(); self.server.close()
        # Keep credentials, transcripts and socket path as evidence; a new launch needs a new directory.

def read_credentials(directory):
    directory=Path(directory)
    if directory.is_symlink() or not directory.is_dir(): raise ValueError('regular private control directory required')
    for path,mode in ((directory,0o700),(directory/'control.json',0o600)):
        info=path.lstat()
        if stat.S_IMODE(info.st_mode)!=mode or info.st_uid!=os.getuid() or path.is_symlink():
            raise ValueError('control credential ownership/mode differs')
    path=directory/'control.json'
    if path.stat().st_size>1024 or path.stat().st_nlink!=1: raise ValueError('credential file bound differs')
    value=strict_json(path.read_bytes())
    if set(value)!={'schemaVersion','instance','token','socket'} or value['schemaVersion']!=1 or value['socket']!=str(directory/'control.sock'):
        raise ValueError('credential schema/socket differs')
    return value

def request(directory,command,view=None):
    credential=read_credentials(directory)
    state=(view or {}).get('state')
    value={'schemaVersion':1,'token':credential['token'],'instance':credential['instance'],
        'sequence':time.monotonic_ns(),'sessionId':state['sessionId'] if state else None,
        'revision':state['revision'] if state else 0,'command':command,
        'layoutSha256':(view or {}).get('layoutSha256'),'inputEpoch':(view or {}).get('inputEpoch',0)}
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(COMMAND_SECONDS+5); client.connect(credential['socket'])
        client.sendall(json.dumps(value).encode()+b'\n'); data=b''
        while b'\n' not in data:
            block=client.recv(65536)
            if not block: raise RuntimeError('control closed before response')
            data+=block
            if len(data)>262144: raise ValueError('control response bound')
        return strict_json(data)
