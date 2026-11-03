#!/usr/bin/env python3
"""One explicitly authorized persistent-install Linux stage; drafts fail before writes."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

def sha(path):
    with Path(path).open('rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['fixture','bundle','cold-pair'])
    for name in ['repo','inputs','review','output']:p.add_argument('--'+name,type=Path,required=True)
    for name in ['bundle','work-root']:p.add_argument('--'+name,type=Path)
    p.add_argument('--data-manifest');a=p.parse_args()
    if sys.platform!='linux':raise ValueError('Linux stage only')
    sys.path.insert(0,str(a.repo/'scripts/boot'))
    from bootlib import checked_session,validate_reviewed_input
    from bundle import verify_payload
    review=json.loads(a.review.read_text());checked_session(review,a.inputs/'payload');verify_payload(a.inputs/'payload',review)
    for number in [1,2]:validate_reviewed_input(review,a.inputs/('input-boot%d.json'%number),number)
    if review.get('profile')!='persistent-install':raise ValueError('persistent profile required')
    if a.output.exists() or a.output.resolve().is_relative_to(a.inputs.resolve()):raise ValueError('new output outside immutable inputs required')
    a.output.mkdir(parents=True)
    if a.stage=='fixture':
        executable=a.inputs/'payload/prepare-persistent-install-fixture'
        command=[str(executable),'initialize-first',str(a.output/'install-root'),str(a.inputs/'payload/packages/initial/main.wasm'),str(a.inputs/'payload/packages/new/main.wasm'),str(a.output/'install-source/app.json'),str(a.inputs/'payload/catalog.json'),review['catalogSha256'],str(a.output/'fixture-receipt.json')];timeout=60
    elif a.stage=='bundle':
        executable=a.repo/'scripts/boot/bundle.py';command=[sys.executable,'-B',str(executable),'--payload',str(a.inputs/'payload'),'--review',str(a.review),'--output',str(a.output/'bundle'),'--isolation-profile','source-v2','--isolation-probes',str(a.repo/'probes/linux-isolation-source-v2/build-current'),'--input-diagnostics','--wayland-client-diagnostics','--pointer-handshake-diagnostics'];timeout=300
    else:
        if not a.bundle or not a.work_root or not a.data_manifest:raise ValueError('pair requires bundle/work-root/data-manifest')
        executable=a.repo/'scripts/boot/cold-boot.py';command=[sys.executable,'-B',str(executable),'--bundle',str(a.bundle),'--review',str(a.review),'--work-root',str(a.work_root),'--data-manifest',a.data_manifest,'--output',str(a.output/'cold-pair'),'--boot1-input-script',str(a.inputs/'input-boot1.json'),'--boot2-input-script',str(a.inputs/'input-boot2.json'),'--timeout','900'];timeout=2000
    record={'stage':a.stage,'argv':command,'executableSha256':sha(executable),'inputSha256':{str(f.relative_to(a.inputs)):sha(f) for f in sorted(a.inputs.rglob('*')) if f.is_file() and not f.is_symlink()},'startedAt':datetime.now(timezone.utc).isoformat(),'timeoutSeconds':timeout,'exitCode':None}
    path=a.output/'command.json';path.write_text(json.dumps(record,indent=2)+'\n')
    try:
        with (a.output/'stdout.log').open('xb') as out,(a.output/'stderr.log').open('xb') as err:record['exitCode']=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err,timeout=timeout,check=False,cwd='/tmp').returncode
    except Exception as error:record['error']=repr(error)
    record['completedAt']=datetime.now(timezone.utc).isoformat()
    for name in ['stdout.log','stderr.log']:
        if (a.output/name).exists():record[name+'Sha256']=sha(a.output/name)
    path.write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));return 0 if record['exitCode']==0 else 1
if __name__=='__main__':raise SystemExit(main())
