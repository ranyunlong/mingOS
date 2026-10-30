"""Read-only, bounded Linux worker CPU receipts; guest monotonic is the only clock."""
import os
import re
import time
from pathlib import Path

MAX_READ_BYTES = 4096
CPU_FIELDS = {"usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec"}


def _read_text(path):
    with path.open("rb", buffering=0) as source:
        raw = source.read(MAX_READ_BYTES + 1)
    if len(raw) > MAX_READ_BYTES:
        raise ValueError("kernel sample exceeds read limit")
    return raw.decode("ascii")


def _counter(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 20:
        raise ValueError("invalid kernel counter")
    number = int(value)
    if number > 2**64 - 1:
        raise ValueError("kernel counter overflow")
    return number


def _process(proc, clock_ticks):
    text = _read_text(proc / "stat")
    pid, separator, tail = text.partition(" (")
    _, closing, fields = tail.rpartition(") ")
    values = fields.split()
    if not separator or not closing or len(values) < 20:
        raise ValueError("invalid process stat")
    return {"pid": _counter(pid), "startTicks": _counter(values[19]),
            "userTicks": _counter(values[11]), "systemTicks": _counter(values[12]),
            "clockTicksPerSecond": clock_ticks}


def _unified_cgroup(proc):
    groups = [line[3:] for line in _read_text(proc / "cgroup").splitlines() if line.startswith("0::")]
    if len(groups) != 1:
        raise ValueError("missing or ambiguous unified cgroup")
    return groups[0]


def _owned_group(path):
    match = re.fullmatch(r"/mingos/app-([1-9][0-9]{0,4})-g-([1-9][0-9]{0,19})", path)
    return bool(match and int(match[1]) <= 65535 and int(match[2]) <= 2**64 - 1)


def _cpu(group, path):
    stats = {}
    seen = set()
    for line in _read_text(group / "cpu.stat").splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] in seen:
            raise ValueError("invalid or repeated CPU statistic")
        key, value = fields
        seen.add(key)
        number = _counter(value)
        if key in CPU_FIELDS:
            stats[key] = number
    if not {"usage_usec", "nr_periods", "nr_throttled", "throttled_usec"} <= stats.keys():
        raise ValueError("missing CPU statistics")
    fields = _read_text(group / "cpu.max").split()
    if len(fields) != 2:
        raise ValueError("invalid CPU quota")
    quota = None if fields[0] == "max" else _counter(fields[0])
    period = _counter(fields[1])
    if period == 0 or quota == 0:
        raise ValueError("zero CPU quota or period")
    return {"path": path, "cpuStat": stats, "cpuMax": {"quotaUsec": quota, "periodUsec": period}}


def sample_worker(proc, cgroup_root=Path("/sys/fs/cgroup/mingos"), *, clock_ticks=None):
    """Sample one PID without inferring exit, success, or CPU utilization from absence.

    Counters are cumulative, not an atomic snapshot. A receipt spans the two
    monotonic timestamps; deltas require matching PID/startTicks/cgroup identity.
    Only the parent's owned MingOS worker leaf is read. No quota is changed.
    """
    receipt = {"sampleStartMonotonicNs": time.monotonic_ns()}
    try:
        ticks = os.sysconf("SC_CLK_TCK") if clock_ticks is None else clock_ticks
        if type(ticks) is not int or ticks <= 0:
            raise ValueError("invalid process clock tick rate")
        first = _process(proc, ticks)
        if first["pid"] != int(proc.name):
            receipt["status"] = "identity-changed"
            return receipt
        path = _unified_cgroup(proc)
        resources = _cpu(cgroup_root / path.rsplit("/", 1)[1], path) if _owned_group(path) else None
        last = _process(proc, ticks)
        if (first["pid"], first["startTicks"]) != (last["pid"], last["startTicks"]) or _unified_cgroup(proc) != path:
            receipt["status"] = "identity-changed"
            return receipt
        receipt.update(status="sampled" if resources is not None else "outside-owned-cgroup", process=first)
        if resources is not None:
            receipt["cgroup"] = resources
    except OSError as error:
        receipt.update(status="unavailable", reason=type(error).__name__)
    except ValueError:
        receipt["status"] = "invalid-data"
    finally:
        receipt["sampleEndMonotonicNs"] = time.monotonic_ns()
    return receipt


def process_table(proc_root=Path("/proc"), cgroup_root=Path("/sys/fs/cgroup/mingos"), *, resources=False):
    """Keep every visible host process, including workers whose sample failed."""
    records = []
    for proc in proc_root.iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if "ming-app-host" not in argv:
                continue
            record = dict(pid=int(proc.name), argv=argv, status=(proc / "status").read_text())
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if resources and "__worker" in argv:
            record["resources"] = sample_worker(proc, cgroup_root)
        records.append(record)
    return records
