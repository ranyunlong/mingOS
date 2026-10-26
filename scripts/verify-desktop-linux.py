#!/usr/bin/env python3
"""Targeted CPU/Wayland desktop verification inside the existing isolated image."""
import argparse, hashlib, json, os, platform, signal, subprocess, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,data): Path(path).write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
def require(value,message):
    if not value: raise AssertionError(message)
def command(args,name,out,env,timeout=600):
    started=time.monotonic()
    result=subprocess.run(args,cwd="/tmp",env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=False)
    (out/(name+".stdout.log")).write_bytes(result.stdout);(out/(name+".stderr.log")).write_bytes(result.stderr)
    save(out/(name+".command.json"),{"args":args,"exitCode":result.returncode,"elapsedSeconds":time.monotonic()-started})
    require(result.returncode==0,name+" failed: "+result.stderr.decode(errors="replace")[-5000:]);return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",required=True);args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True);require(not(out/"result.json").exists(),"Fresh evidence directory required")
    env=dict(os.environ);env["RUSTUP_AUTO_INSTALL"]="0"
    report={"schemaVersion":1,"status":"running","renderer":"cpu","physicalInput":False,"physicalGpu":False,"system":platform.uname()._asdict(),"runs":{},"packages":[],"inputSha256":{}}
    weston=None
    try:
        command(["rustc","+1.99.1","--version","--verbose"],"compiler",out,env)
        command(["cargo","+1.99.1","build","--locked","--offline","--release","--all-features","--manifest-path",str(ROOT/"Cargo.toml")],"build",out,env)
        frozen=out/"frozen";frozen.mkdir();binary=Path(env["CARGO_TARGET_DIR"])/"release/ming-app-host";host=frozen/"ming-app-host";host.write_bytes(binary.read_bytes());host.chmod(0o755)
        catalog=json.loads((ROOT/"samples/desktop/catalog.json").read_text());inputs={str(host):sha(host),str(ROOT/"scripts/verify-desktop-linux.py"):sha(ROOT/"scripts/verify-desktop-linux.py")}
        for app in catalog["apps"]:
            source=(ROOT/"samples/desktop"/app["manifest"]).resolve();pkg=source.parent;manifest=json.loads(source.read_text());report["packages"].append({"app":app["id"],"packageId":manifest["id"]});info=json.loads((pkg/"build-info.json").read_text());receipt=json.loads((pkg/"build-receipt.json").read_text())
            require(receipt["status"]=="passed","Unverified Kotlin build");require(sha(source)==receipt["manifestSha256"],"Manifest changed");require(sha(pkg/"main.wasm")==receipt["packagedSha256"],"Packaged Kotlin changed")
            for rel,digest in receipt["inputSha256"].items():
                path=ROOT/rel;require(sha(path)==digest,"Source changed: "+rel);inputs[str(path)]=digest
            raw=list((ROOT/"samples"/pkg.name/"build/compileSync").rglob("*.wasm"));require(len(raw)==1,"Expected one raw Kotlin module");require(sha(raw[0])==receipt["rawSha256"],"Raw Kotlin changed")
            target=frozen/"packages"/str(app["id"]);target.mkdir(parents=True)
            for name in ["app.json","main.wasm","build-info.json","build-receipt.json"]:(target/name).write_bytes((pkg/name).read_bytes());inputs[str(target/name)]=sha(target/name)
            (target/"compiler.wasm").write_bytes(raw[0].read_bytes());inputs[str(target/"compiler.wasm")]=sha(target/"compiler.wasm");app["manifest"]="packages/"+str(app["id"])+"/app.json"
        save(frozen/"catalog.json",catalog);(frozen/"events.json").write_bytes((ROOT/"samples/desktop/verification-events.json").read_bytes());inputs[str(frozen/"catalog.json")]=sha(frozen/"catalog.json");inputs[str(frozen/"events.json")]=sha(frozen/"events.json")
        runtime=out/"wayland-runtime";runtime.mkdir();runtime.chmod(0o700);wayland=dict(env);wayland.pop("DISPLAY",None);wayland.pop("WAYLAND_SOCKET",None);wayland.update(XDG_RUNTIME_DIR=str(runtime),WAYLAND_DISPLAY="ming-desktop")
        weston_log=(out/"weston.stderr.log").open("wb")
        weston=subprocess.Popen(["weston","--backend=headless-backend.so","--use-pixman","--width=1280","--height=800","--socket=ming-desktop","--no-config","--idle-time=0","--log="+str(out/"weston.log")],env=wayland,stdin=subprocess.DEVNULL,stdout=weston_log,stderr=weston_log,start_new_session=True)
        deadline=time.monotonic()+15
        while not(runtime/"ming-desktop").exists():
            require(weston.poll() is None,"Weston terminated");require(time.monotonic()<deadline,"Weston socket timeout");time.sleep(.05)
        for backend in ["offscreen","native"]:
            run=out/backend;run.mkdir();snapshot=run/"desktop.png"
            argv=[str(host),"desktop",str(frozen/"catalog.json"),"--renderer","cpu","--backend",backend,"--width","1280","--height","800","--duration-ms","6500","--events",str(frozen/"events.json"),"--snapshot",str(snapshot)]
            result=command(argv,backend,out,wayland,timeout=360);actual=json.loads(result.stdout)
            require(actual["runtime"]=="ming-desktop","Wrong runtime");require(actual["activeWorkersAfterShutdown"]==0,"Workers retained");require(actual["actionsExecuted"]==15,"Missing actions");require(actual["droppedInputs"]==0,"Input dropped");require(actual["maxConcurrentWorkers"]>=2,"No worker concurrency");require(actual["acceptedRoot"]["sequence"]>0,"No root accepted")
            instances=actual["instances"];require(len(instances)==4,"Missing generations");pids=[a["workerPid"] for a in instances];require(len(set(pids))==4 and all(pids),"Invalid actual PIDs")
            require(all(a["completed"] and a["firstReadyMs"] is not None and a["lastSourceSequence"]>0 for a in instances),"Unqualified app instance")
            require(any(a["app"]==2 and a["generation"]==1 and a["status"]=="failed" and "APP_CANCELLED" in a["error"] for a in instances),"Missing forced worker failure")
            require(any(a["app"]==1 and a["generation"]==1 and a["events"] and any(e["code"]==40 for e in a["events"]) for a in instances),"Normal Kotlin app did not report completion")
            for app in [1,2]:
                last=[a for a in instances if a["app"]==app][-1];require(last["generation"]==2 and last["status"]=="closed" and last["framesSubmitted"]>0 and last["presentedSourceSequence"]>0,"Restart not qualified");require(last["packageId"]==next(p["packageId"] for p in report["packages"] if p["app"]==app),"Wrong actual Kotlin package")
            remaining=[pid for pid in pids if (Path("/proc")/str(pid)).exists()];require(not remaining,"Worker processes remain: "+str(remaining))
            if backend=="native":require(actual["windowSystem"]=="wayland" and actual["framesPresented"]>0 and actual["presentedFrame"]==actual["acceptedRoot"],"Wayland presentation missing")
            else:require(actual["windowSystem"]=="offscreen" and actual["framesPresented"]==0 and actual["offscreenFrames"]>0,"Invalid offscreen scope")
            report["runs"][backend]={"status":"passed","outcome":actual,"pngSha256":sha(snapshot),"workerPidsAbsentAfterExit":pids};save(run/"result.json",report["runs"][backend])
        require(report["runs"]["offscreen"]["pngSha256"]==report["runs"]["native"]["pngSha256"],"Offscreen/Wayland terminal images differ")
        for path,digest in inputs.items():require(sha(path)==digest,"Frozen input changed: "+path)
        report["inputSha256"]=inputs;report["terminalImageEquality"]=True;report["status"]="passed"
    except Exception as error:
        report["status"]="failed";report["error"]=repr(error);raise
    finally:
        if weston is not None:
            try:os.killpg(weston.pid,signal.SIGTERM);weston.wait(timeout=5)
            except ProcessLookupError:pass
            except subprocess.TimeoutExpired:os.killpg(weston.pid,signal.SIGKILL);weston.wait(timeout=5)
        save(out/"result.json",report)
if __name__=="__main__":main()
