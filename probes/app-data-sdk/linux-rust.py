import json, os, pathlib, subprocess, sys, hashlib, time, re
stage=sys.argv[1]
assert re.fullmatch(r'[a-z0-9-]+',stage)
out=pathlib.Path('/evidence')/('rust-'+stage+'-linux')
out.mkdir(parents=True,exist_ok=False)
root=pathlib.Path('/workspace')
inputs=['crates/ming-app-host/src/app_data.rs','crates/ming-app-host/tests/app_data.rs','probes/app-data-sdk/linux-rust.py']
sha=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
hashes={p:sha(root/p) for p in inputs}
for p in inputs:
 dest=out/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((root/p).read_bytes())
lib='/ming-cache/target/x86_64-unknown-linux-gnu/debug/deps/liblibc-820de6792479440a.rlib'
binary=out/'app-data-tests'
commands=[['rustc','--crate-type=lib','--edition=2024','-Dwarnings',str(root/inputs[0]),'--extern','libc='+lib,'-L','dependency='+str(pathlib.Path(lib).parent),'-o',str(out/'libapp_data.rlib')],['rustc','--test','--edition=2024','-Dwarnings',str(root/inputs[1]),'--extern','libc='+lib,'-L','dependency='+str(pathlib.Path(lib).parent),'-o',str(binary)],[str(binary),'--test-threads=1','--nocapture']]
report={'stage':stage,'platform':'linux','inputs':hashes,'libcSha256':sha(pathlib.Path(lib)),'commands':[],'rustc':subprocess.check_output(['rustc','--version','--verbose'],text=True)}
try:
 for i,cmd in enumerate(commands):
  start=time.monotonic();p=subprocess.run(cmd,env={**os.environ,'CARGO_INCREMENTAL':'0'},capture_output=True,text=True,timeout=45)
  (out/('command-'+str(i)+'.stdout.log')).write_text(p.stdout);(out/('command-'+str(i)+'.stderr.log')).write_text(p.stderr)
  report['commands'].append({'argv':cmd,'exitCode':p.returncode,'elapsedSeconds':time.monotonic()-start});print(p.stdout,p.stderr,flush=True)
  if i<2: assert p.returncode==0, 'Compilation failed'
  else: assert (p.returncode!=0 if stage=='red' else p.returncode==0), 'Unexpected test outcome'
 assert hashes=={p:sha(root/p) for p in inputs},'Source changed during test'
 report['status']='expected-red' if stage=='red' else 'passed'
except BaseException as e:
 report['status']='failed';report['error']=repr(e);raise
finally:
 (out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
