"""Boot infrastructure API v1. Pure guards plus a state-driven virtual input plan."""
import json
import os
import re
import stat
import time
from pathlib import Path

API_VERSION = 1
STATE_PATH = "/run/evidence/desktop-state.json"

def contained(root, name):
    if not isinstance(name, str) or not name or any(c in name for c in "\\:,=\0"):
        raise ValueError("invalid owned relative path")
    if any(part in ("", ".", "..") for part in name.split("/")) or name.startswith("/"):
        raise ValueError("owned path escapes root")
    root = Path(root).absolute()
    target = root / name
    current = root
    if root.resolve() != root:
        raise ValueError("symlink workspace forbidden")
    for part in name.split("/"):
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink owned path forbidden")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("owned path escapes root")
    return target

def regular_file(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("only single-link regular files permitted")
    return info

def integer(value, low=0, high=2**64-1):
    return type(value) is int and low <= value <= high

def validate_state(state, boot_id, session_id=None, revision=None):
    if not isinstance(state, dict) or state.get("schemaVersion") != 1:
        raise ValueError("state schemaVersion")
    if state.get("bootId") != boot_id or not isinstance(state.get("sessionId"), str) or not state["sessionId"]:
        raise ValueError("state boot/session identity")
    if session_id is not None and state["sessionId"] != session_id:
        raise ValueError("state session changed")
    if not integer(state.get("revision")) or (revision is not None and state["revision"] < revision):
        raise ValueError("state revision regressed")
    if state.get("phase") not in ("starting", "ready", "stopping", "stopped", "failed"):
        raise ValueError("state phase")
    root = state.get("root", {})
    if root is None:
        if state["phase"] == "ready": raise ValueError("ready state requires presented root")
    else:
        if not isinstance(root, dict) or not all(integer(root.get(k), 0, 4096) for k in ("width", "height")):
            raise ValueError("state root geometry")
        if root.get("presentedSequence") is not None and not integer(root["presentedSequence"]):
            raise ValueError("state root sequence")
    apps = state.get("apps")
    if not isinstance(apps, list) or len(apps) > 4:
        raise ValueError("state app count")
    seen = set()
    for app in apps:
        if not isinstance(app, dict) or not integer(app.get("app"), 1, 65535) or app["app"] in seen:
            raise ValueError("state app identity")
        seen.add(app["app"])
        if not integer(app.get("generation"), 1) or app.get("state") not in ("starting", "running", "ready", "closing", "minimized", "closed", "failed"):
            raise ValueError("state app generation/state")
        for key in ("sourceSequence", "presentedSourceSequence", "workerPid"):
            if app.get(key) is not None and not integer(app[key]):
                raise ValueError("state app sequence/pid")
        if (app.get("presentedSourceSequence") or 0) > (app.get("sourceSequence") or 0):
            raise ValueError("state source not presented")
        if "inputReady" in app and type(app["inputReady"]) is not bool:
            raise ValueError("state inputReady")
    if state.get("error") is not None and (not isinstance(state["error"], str) or len(state["error"]) > 2048):
        raise ValueError("state error bound")
    return state

def state_ready(state, required):
    if state.get("phase") != "ready" or (state.get("root", {}).get("presentedSequence") or 0) < 1:
        return False
    if state["root"]["width"] < 2 or state["root"]["height"] < 2:
        return False
    apps = {app["app"]: app for app in state["apps"]}
    return all(app in apps and apps[app]["state"] in ("running", "ready") and apps[app].get("inputReady") is True
               and not apps[app].get("minimized", False) and (apps[app].get("workerPid") or 0) > 0
               and (apps[app].get("presentedSourceSequence") or 0) > 0 for app in required)

KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789") | {"esc", "ret", "spc", "tab", "backspace", "delete", "left", "right", "up", "down", "home", "end", "shift", "shift_r", "ctrl", "ctrl_r", "alt", "alt_r", "meta_l", "meta_r"}

def input_events(events, state):
    if not isinstance(events, list) or not 1 <= len(events) <= 32:
        raise ValueError("input event count")
    result = []
    for event in events:
        kind = event.get("kind") if isinstance(event, dict) else None
        if kind == "pointer":
            if set(event) != {"kind", "x", "y"}:
                raise ValueError("pointer fields")
            width, height = state["root"]["width"], state["root"]["height"]
            if not integer(event["x"], 0, width-1) or not integer(event["y"], 0, height-1) or width < 2 or height < 2:
                raise ValueError("pointer outside presented root")
            result += [{"type": "abs", "data": {"axis": axis, "value": round(value * 32767 / (extent-1))}}
                       for axis, value, extent in (("x", event["x"], width), ("y", event["y"], height))]
        elif kind in ("key", "button"):
            field = "qcode" if kind == "key" else "button"
            allowed = KEYS if kind == "key" else {"left", "right", "middle"}
            if set(event) != {"kind", field, "down"} or event[field] not in allowed or type(event["down"]) is not bool:
                raise ValueError("invalid virtual key/button")
            data = {"down": event["down"]}
            data["key" if kind == "key" else "button"] = {"type": "qcode", "data": event[field]} if kind == "key" else event[field]
            result.append({"type": "key" if kind == "key" else "btn", "data": data})
        else:
            raise ValueError("unknown virtual input kind")
    return result

def classify_guest(records, timed_out):
    if timed_out:
        return "timeout-unaccepted"
    if any(x.get("type") in ("guest-error", "data-sync-error", "forced-process-stop") for x in records):
        return "guest-failed"
    terminals = [x for x in records if x.get("type") == "guest-result"]
    if not terminals:
        return "guest-exited-unverified"
    last = terminals[-1]
    if last.get("status") == "normal" and last.get("desktopExitCode") == 0 and last.get("dataSynced") is True and last.get("poweroffRequested") is True:
        return "normal-desktop-poweroff"
    return "guest-failed"

def validate_cold_pair(first, second):
    if any(x.get("status") != "normal-desktop-poweroff" for x in (first, second)):
        raise ValueError("cold boot requires two normal poweroffs")
    if not first.get("bootId") or not second.get("bootId") or first["bootId"] == second["bootId"]:
        raise ValueError("cold boot ID did not change")
    for key in ("bundleIdentity", "diskUuid"):
        if not first.get(key) or first[key] != second.get(key):
            raise ValueError("cold boot identity changed: " + key)
    if first.get("lastState") or second.get("lastState"):
        if not first.get("lastState") or not second.get("lastState") or first["lastState"]["sessionId"] == second["lastState"]["sessionId"]:
            raise ValueError("cold boot state session identity")
    if second.get("previousMarker") != first.get("marker"):
        raise ValueError("persisted previous marker mismatch")
    if first["marker"].get("bootId") != first["bootId"] or second.get("marker", {}).get("bootId") != second["bootId"] or second["marker"].get("counter") != first["marker"]["counter"] + 1:
        raise ValueError("cold boot marker/counter")

def append_record(path, record):
    with Path(path).open("a", encoding="utf-8") as output:
        output.write(json.dumps(dict(monotonic=time.monotonic(), **record), ensure_ascii=True) + "\n")
        output.flush()
        os.fsync(output.fileno())

class InputPlan:
    def __init__(self, spec, output, timeout, split_transactions=False, pointer_handshake=False):
        if spec.get("schemaVersion") != 1 or not isinstance(spec.get("steps"), list) or not 1 <= len(spec["steps"]) <= 64:
            raise ValueError("input script schema/steps")
        required = spec.get("requireApps")
        if not isinstance(required, list) or any(not integer(x, 1, 65535) for x in required) or len(set(required)) != len(required):
            raise ValueError("input requireApps")
        self.spec, self.output = spec, output
        self.index, self.base, self.waiting = 0, None, False
        self.held = {}
        self.split_transactions, self.routing_recorded = split_transactions, False
        if pointer_handshake and not split_transactions: raise ValueError("pointer receipt requires split transactions")
        self.pointer_handshake, self.pending_pointer, self.root_surface = pointer_handshake, None, None
        self.deadline = time.monotonic() + timeout
        for step in spec["steps"]:
            if set(step) - {"name", "events", "requireApps", "expect"} or not isinstance(step.get("name"), str):
                raise ValueError("input step fields/name")
            # Validate event shape before starting QEMU; real geometry is checked again at dispatch.
            input_events(step["events"], {"root": {"width": 4096, "height": 4096}})
            apps = step.get("requireApps", required)
            if not isinstance(apps, list) or any(not integer(x, 1, 65535) for x in apps):
                raise ValueError("step requireApps")
            expect = step.get("expect", {})
            if not isinstance(expect, dict) or set(expect) - {"phase", "rootAdvance", "sourceAdvance", "apps"}:
                raise ValueError("input expectation")
            if "phase" in expect and expect["phase"] not in ("ready", "stopping", "stopped", "failed"):
                raise ValueError("input expected phase")
            if "rootAdvance" in expect and type(expect["rootAdvance"]) is not bool:
                raise ValueError("input rootAdvance")
            if "sourceAdvance" in expect and (not isinstance(expect["sourceAdvance"], list) or any(not integer(x, 1, 65535) for x in expect["sourceAdvance"])):
                raise ValueError("input sourceAdvance")
            if "apps" in expect:
                conditions = expect["apps"]
                if not isinstance(conditions, list) or not 1 <= len(conditions) <= 4:
                    raise ValueError("input app expectations")
                seen = set()
                for condition in conditions:
                    if (not isinstance(condition, dict) or set(condition) != {"app", "generation", "state"}
                        or not integer(condition.get("app"), 1, 65535) or condition["app"] in seen
                        or not integer(condition.get("generation"), 1)
                        or condition.get("state") not in ("starting", "running", "ready", "closing", "minimized", "closed", "failed")):
                        raise ValueError("input app expectation fields")
                    seen.add(condition["app"])

    @property
    def complete(self):
        return self.index == len(self.spec["steps"])

    def check_deadline(self):
        if not self.complete and time.monotonic() >= self.deadline:
            raise TimeoutError("input handshake/expectation deadline")

    def advance(self, state, qmp, pointer_context=None):
        self.check_deadline()
        if self.complete:
            return
        step = self.spec["steps"][self.index]
        if self.pending_pointer is not None:
            pending = self.pending_pointer
            if time.monotonic() >= pending["deadline"]: raise TimeoutError("current-client pointer receipt deadline")
            context = pointer_context or {}; receipt = context.get("receipt") or {}
            point = pending["point"]
            matching = (receipt.get("entered") is True
                and receipt.get("bootId") == state["bootId"] and receipt.get("sessionId") == state["sessionId"]
                and receipt.get("desktopPid") == pending["desktopPid"] == context.get("desktopPid")
                and integer(receipt.get("logOffset")) and receipt["logOffset"] > pending["afterLogOffset"]
                and integer(receipt.get("pointer"), 1) and integer(receipt.get("surface"), 1)
                and (self.root_surface is None or receipt["surface"] == self.root_surface)
                and all(type(receipt.get(axis)) in (int, float) and abs(receipt[axis]-point[axis]) <= 1 for axis in ("x", "y")))
            if not matching or not state_ready(state, step.get("requireApps", self.spec["requireApps"])): return
            self.root_surface = receipt["surface"]
            append_record(self.output, {"type": "input-pointer-ready", "step": step["name"], "receipt": receipt, "afterLogOffset": pending["afterLogOffset"], "state": state})
            self._send_transactions(qmp, pending["transactions"][1:])
            self._dispatched(state, step, pending["events"], pending["transactions"])
            self.pending_pointer = None
            return
        if self.waiting:
            expected = step.get("expect", {})
            if (state["bootId"] != self.base["bootId"] or state["sessionId"] != self.base["sessionId"]
                or state["revision"] <= self.base["revision"]):
                return
            if "phase" in expected and state["phase"] != expected["phase"]:
                return
            if expected.get("rootAdvance") and (state["root"].get("presentedSequence") or 0) <= (self.base["root"].get("presentedSequence") or 0):
                return
            now = {a["app"]: a for a in state["apps"]}; old = {a["app"]: a for a in self.base["apps"]}
            if any(app not in now or (now[app].get("presentedSourceSequence") or 0) <= (old.get(app, {}).get("presentedSourceSequence") or 0) for app in expected.get("sourceAdvance", [])):
                return
            if any(condition["app"] not in now
                   or now[condition["app"]]["generation"] != condition["generation"]
                   or now[condition["app"]]["state"] != condition["state"]
                   for condition in expected.get("apps", [])):
                return
            append_record(self.output, {"type": "input-expectation", "step": step["name"], "state": state})
            self.index += 1; self.waiting = False
            return
        if not state_ready(state, step.get("requireApps", self.spec["requireApps"])):
            return
        events = input_events(step["events"], state)
        if self.split_transactions and not self.routing_recorded:
            routing = qmp.execute("query-mice")
            append_record(self.output, {"type": "input-routing", "mice": routing, "state": state})
            self.routing_recorded = True
        transactions = [events]
        if self.split_transactions:
            transactions, absolute = [], []
            for event in events:
                if event["type"] == "abs":
                    absolute.append(event)
                else:
                    if absolute: transactions.append(absolute); absolute = []
                    transactions.append([event])
            if absolute: transactions.append(absolute)
        if self.pointer_handshake and any(event["type"] == "abs" for event in events):
            points = [event for event in step["events"] if event["kind"] == "pointer"]
            if len(points) != 1 or any(event["type"] != "abs" for event in transactions[0]):
                raise ValueError("pointer diagnostic requires exactly one leading ABS point")
            context = pointer_context or {}
            if not integer(context.get("desktopPid"), 1) or not integer(context.get("logOffset")):
                raise ValueError("current desktop PID/log offset required before ABS")
            self.pending_pointer = {"point": points[0], "events": events, "transactions": transactions,
                                    "desktopPid": context["desktopPid"], "afterLogOffset": context["logOffset"], "deadline": time.monotonic()+5}
            self._send_transactions(qmp, transactions[:1])
            append_record(self.output, {"type": "input-pointer-dispatched", "step": step["name"], "point": points[0], "desktopPid": context["desktopPid"], "afterLogOffset": context["logOffset"], "waitSeconds": 5, "state": state})
            return
        self._send_transactions(qmp, transactions)
        self._dispatched(state, step, events, transactions)

    def _send_transactions(self, qmp, transactions):
        for transaction in transactions:
            qmp.execute("input-send-event", {"events": transaction})
            for event in transaction:
                if event["type"] in ("key", "btn"):
                    key = json.dumps({k: v for k, v in event["data"].items() if k != "down"}, sort_keys=True)
                    if event["data"]["down"]: self.held[key] = event
                    else: self.held.pop(key, None)

    def _dispatched(self, state, step, events, transactions):
        append_record(self.output, {"type": "input-dispatched", "step": step["name"], "events": events, "transactions": transactions, "diagnosticSplit": self.split_transactions, "pointerHandshake": self.pointer_handshake, "state": state})
        self.base, self.waiting = state, True

    def release(self, qmp):
        if not self.held:
            return
        if not qmp.execute("query-status")["running"]:
            append_record(self.output, {"type": "input-release-skipped", "reason": "VM not running", "held": list(self.held.values())})
            return
        events = [dict(type=x["type"], data=dict(x["data"], down=False)) for x in self.held.values()]
        qmp.execute("input-send-event", {"events": events})
        append_record(self.output, {"type": "input-released", "events": events})
        self.held.clear()
