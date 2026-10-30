"""Guest-only cgroup/data/probe preparation. Never invoke on a host/container kernel."""
import json
import os
import re
import stat
import subprocess
from pathlib import Path

class Platform:
    def __init__(self, emit, logs):
        self.emit, self.logs = emit, logs
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if os.getpid() != 1 or os.geteuid() != 0 or "rdinit=/init" not in Path("/proc/cmdline").read_text():
            raise RuntimeError("guest platform requires authorized VM PID1")
        self.mounted = False
        self.disk_uuid = None
        self.marker = self.previous = None
        self.probes = None

    def call(self, argv, timeout=30, name=None):
        self.emit("platform-command-start", argv=argv)
        if name:
            with (self.logs / (name + ".log")).open("wb") as output:
                result = subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
            text = (self.logs / (name + ".log")).read_text(errors="replace")
        else:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            text = result.stdout + result.stderr
        self.emit("platform-command", argv=argv, code=result.returncode, output=text[-8192:])
        if result.returncode: raise RuntimeError("guest platform command failed: " + repr(argv))
        return text

    def owned_directory(self, path):
        path = Path(path)
        if not path.exists(): path.mkdir(mode=0o700)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise RuntimeError("unsafe guest root directory: " + str(path))
        return path

    def prepare(self):
        root = Path("/sys/fs/cgroup")
        root.mkdir(exist_ok=True)
        if not any(" /sys/fs/cgroup " in line and " - cgroup2 " in line for line in Path("/proc/self/mountinfo").read_text().splitlines()):
            self.call(["mount", "-t", "cgroup2", "-o", "nosuid,nodev,noexec", "cgroup2", str(root)])
        wanted = {"cpu", "memory", "pids"}
        if not wanted <= set((root / "cgroup.controllers").read_text().split()):
            raise RuntimeError("cgroup2 controllers unavailable")
        (root / "cgroup.subtree_control").write_text("+cpu +memory +pids\n")
        mingos = self.owned_directory(root / "mingos")
        if (mingos / "cgroup.procs").read_text().strip(): raise RuntimeError("mingos root unexpectedly populated")
        (mingos / "cgroup.subtree_control").write_text("+cpu +memory +pids\n")
        private = self.owned_directory("/run/mingos-workers")
        self.emit("cgroup-prepared", root=str(mingos), privateRoot=str(private),
                  controllers=(mingos / "cgroup.controllers").read_text().split(),
                  subtreeControl=(mingos / "cgroup.subtree_control").read_text().split(),
                  cgroupProcs=(mingos / "cgroup.procs").read_text().split())
        params = dict(part.split("=", 1) for part in Path("/proc/cmdline").read_text().split() if "=" in part)
        identifier, serial = params.get("mingos.data_uuid"), params.get("mingos.data_serial")
        if bool(identifier) != bool(serial): raise RuntimeError("incomplete data disk parameters")
        if identifier:
            self.mount_data(identifier, serial)
        path = Path("/etc/mingos/probe.json")
        if path.exists(): self.probes = json.loads(path.read_text())
        self.seed_public_storage()

    def seed_public_storage(self):
        config_path = Path("/etc/mingos/public-storage-seed.json")
        if not config_path.exists():
            return
        if not self.mounted:
            raise RuntimeError("public storage seed requires mounted data disk")
        config = json.loads(config_path.read_text())
        if config != {"packageIds": ["dev.ming.public-kernel-probe"],
                      "profile": "data-text-public-probe", "schemaVersion": 1}:
            raise RuntimeError("unexpected public storage seed")
        root = Path("/data/mingos")
        policy = self.owned_directory(root / "system-public-storage-policy-v1")
        public = self.owned_directory(root / "public-files-v1")
        expected = b"ming-public-storage-policy-v1\ndev.ming.public-kernel-probe\n"
        target = policy / "slot-1"
        if target.exists() or target.is_symlink():
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
                raise RuntimeError("unsafe public storage policy")
            if target.read_bytes() != expected:
                raise RuntimeError("public storage policy mismatch")
        else:
            temp = policy / (".seed-" + self.boot_id)
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(expected); output.flush(); os.fsync(output.fileno())
            os.replace(temp, target)
            fd = os.open(policy, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        self.emit("public-storage-preauthorized", packageIds=config["packageIds"], policy=str(target), publicRoot=str(public))

    def mount_data(self, identifier, serial):
        import uuid
        if str(uuid.UUID(identifier)) != identifier or not re.fullmatch(r"MINGDATA[0-9a-f]{12}", serial):
            raise RuntimeError("invalid expected data identity")
        device = Path("/dev/disk/by-id") / ("virtio-" + serial)
        target = device.resolve(strict=True)
        if not re.fullmatch(r"/dev/vd[a-z]+", str(target)) or not stat.S_ISBLK(target.stat().st_mode):
            raise RuntimeError("data device is not owned virtio block disk")
        actual = self.call(["/usr/sbin/blkid", "-p", "-s", "UUID", "-o", "value", str(target)]).strip()
        kind = self.call(["/usr/sbin/blkid", "-p", "-s", "TYPE", "-o", "value", str(target)]).strip()
        if actual != identifier or kind != "ext4": raise RuntimeError("data disk UUID/type mismatch")
        mount = Path("/data/mingos"); mount.mkdir(parents=True)
        self.call(["mount", "-t", "ext4", "-o", "nodev,nosuid", str(target), str(mount)])
        self.mounted = True; self.disk_uuid = identifier
        if mount.stat().st_uid != 0: raise RuntimeError("data root must be uid0 (mkfs root_owner=0:0)")
        mount.chmod(0o700)
        root_info = mount.stat()
        if root_info.st_uid != 0 or stat.S_IMODE(root_info.st_mode) != 0o700:
            raise RuntimeError("data root owner/mode readback failed")
        directory = self.owned_directory(mount / ".mingos-boot")
        current = directory / "current.json"
        if current.exists():
            info = current.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0:
                raise RuntimeError("unsafe persisted marker")
            self.previous = json.loads(current.read_text())
            if type(self.previous.get("counter")) is not int or self.previous["counter"] < 1 or not isinstance(self.previous.get("bootId"), str):
                raise RuntimeError("invalid previous marker")
        self.marker = {"bootId": self.boot_id, "counter": (self.previous["counter"] if self.previous else 0) + 1}
        temp = directory / (self.boot_id + ".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(self.marker, output); output.write("\n"); output.flush(); os.fsync(output.fileno())
        os.replace(temp, current)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        if json.loads(current.read_text()) != self.marker: raise RuntimeError("marker readback mismatch")
        self.emit("data-marker", uuid=identifier, serial=serial, device=str(target), mount=str(mount),
                  previousMarker=self.previous, marker=self.marker, readbackVerified=True,
                  rootUid=root_info.st_uid, rootGid=root_info.st_gid, rootMode=oct(stat.S_IMODE(root_info.st_mode)))

    def strict_before(self):
        if not self.probes: return
        text = self.call(["/bin/sh", "/usr/local/libexec/mingos-isolation-prepare.sh", self.boot_id], name="isolation-prepare")
        if "PREPARED_OWNED_ROOTS boot_id=" + self.boot_id not in text: raise RuntimeError("prepare marker missing")
        text = self.call(["/usr/local/bin/mingos-isolation-probe", "vm"], timeout=180, name="isolation-vm")
        if "STRICT_CLEANUP_OK" not in text: raise RuntimeError("strict smoke cleanup marker missing")
        self.emit("strict-smoke-complete", cleanupVerified=True)

    def strict_after(self):
        if not self.probes: return
        # Called only after desktop exit and worker PID absence has been verified.
        text = self.call(["/usr/local/bin/mingos-isolation-probe", "vm-resources"], timeout=400, name="isolation-resources")
        for case in range(3):
            if "RESOURCE_VERIFIED_AND_REAPED case=" + str(case) not in text:
                raise RuntimeError("strict resource marker missing: " + str(case))
        self.emit("strict-resources-complete", cases=[0, 1, 2])

    def finish_data(self):
        os.sync()
        if self.mounted:
            self.call(["umount", "/data/mingos"])
            self.mounted = False
        self.emit("data-synced", uuid=self.disk_uuid, marker=self.marker, unmounted=True)
