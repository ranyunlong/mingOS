#!/usr/bin/env python3
"""Task-owned regular ext4 images. No host mount, block device, or overwrite support."""
import argparse
import contextlib
import json
import os
import stat
import subprocess
import uuid
from pathlib import Path
from bootlib import sha256
from infra import contained, regular_file

TASK = "boot-infra-20261025"
MARKER = ".mingos-boot-workspace.json"

def workspace(root, create=False):
    root = Path(root).absolute()
    if os.geteuid() != 65534 or root.resolve() != root or root.parent not in (Path("/work"), Path("/var/tmp")) or root.name != "mingos-boot-infra-20261025":
        raise ValueError("workspace must be task subdirectory of /work or /var/tmp, owned by uid65534")
    if create:
        root.mkdir(mode=0o700)
        with (root / MARKER).open("x") as output:
            json.dump({"schemaVersion": 1, "task": TASK, "uid": 65534}, output)
        (root / MARKER).chmod(0o600)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 65534 or info.st_mode & 0o077:
        raise ValueError("workspace owner/mode must be 65534/0700")
    regular_file(root / MARKER)
    if json.loads((root / MARKER).read_text()) != {"schemaVersion": 1, "task": TASK, "uid": 65534}:
        raise ValueError("workspace marker mismatch")
    return root

def command(argv, log):
    result = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    Path(str(log) + ".stdout.log").write_bytes(result.stdout)
    Path(str(log) + ".stderr.log").write_bytes(result.stderr)
    Path(str(log) + ".command.json").write_text(json.dumps({"argv": argv, "exitCode": result.returncode}, indent=2) + "\n")
    if result.returncode:
        raise RuntimeError("disk command failed: " + repr(argv) + ": " + result.stderr.decode(errors="replace"))
    return result.stdout.decode()

def fs_identity(path):
    regular_file(path)
    result = subprocess.run(["/usr/sbin/blkid", "-p", "-o", "export", str(path)], capture_output=True, text=True, timeout=15, check=True)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

def load_disk(root, name):
    root = workspace(root)
    manifest_path = contained(root, name)
    regular_file(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schemaVersion") != 1 or manifest.get("task") != TASK or manifest.get("format") != "raw" or manifest.get("filesystem") != "ext4" or manifest.get("sizeMiB") not in (128, 256):
        raise ValueError("disk manifest schema/type/size")
    identifier = str(uuid.UUID(manifest["uuid"]))
    if identifier != manifest["uuid"] or manifest.get("serial") != "MINGDATA" + identifier.replace("-", "")[:12]:
        raise ValueError("disk UUID/serial mismatch")
    image = contained(root, manifest["image"])
    info = regular_file(image)
    if info.st_uid != 65534 or info.st_mode & 0o077 or info.st_size != manifest["sizeMiB"] * 1024**2:
        raise ValueError("disk image owner/mode/size")
    identity = fs_identity(image)
    if identity.get("TYPE") != "ext4" or identity.get("UUID") != identifier or identity.get("LABEL") != "MINGOS-DATA":
        raise ValueError("on-disk filesystem identity mismatch")
    return dict(manifest, imagePath=str(image), manifestPath=str(manifest_path))

@contextlib.contextmanager
def lease(root, manifest):
    import fcntl
    image = contained(workspace(root), manifest["image"])
    lock = image.with_suffix(image.suffix + ".lock")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    fd = os.open(lock, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 65534:
            raise ValueError("disk lease must be owned regular file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)

def create_disk(root, name, size):
    root = workspace(root)
    if size not in (128, 256) or not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
        raise ValueError("disk name/size")
    directory = root / "disks"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = contained(root, "disks/" + name + ".raw")
    manifest_path = contained(root, "disks/" + name + ".json")
    if manifest_path.exists():
        raise FileExistsError(manifest_path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.ftruncate(fd, size * 1024**2)
        os.fsync(fd)
    finally:
        os.close(fd)
    regular_file(path)
    identifier = str(uuid.uuid4())
    command(["/usr/sbin/mkfs.ext4", "-q", "-F", "-L", "MINGOS-DATA", "-U", identifier,
             "-E", "lazy_itable_init=0,lazy_journal_init=0,root_owner=0:0", str(path)], path.with_suffix(".format"))
    command(["/usr/sbin/e2fsck", "-f", "-n", str(path)], path.with_suffix(".fsck"))
    manifest = {"schemaVersion": 1, "task": TASK, "format": "raw", "filesystem": "ext4",
                "image": str(path.relative_to(root)), "sizeMiB": size, "uuid": identifier,
                "serial": "MINGDATA" + identifier.replace("-", "")[:12], "creationSha256": sha256(path)}
    with manifest_path.open("x") as output:
        json.dump(manifest, output, indent=2); output.write("\n"); output.flush(); os.fsync(output.fileno())
    manifest_path.chmod(0o600)
    return load_disk(root, str(manifest_path.relative_to(root)))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("init-workspace", "create", "check"))
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--name", default="developer-128")
    parser.add_argument("--size-mib", type=int, default=128)
    parser.add_argument("--manifest")
    args = parser.parse_args()
    if args.operation == "init-workspace":
        print(json.dumps({"workspace": str(workspace(args.work_root, create=True)), "uid": os.geteuid()}))
    elif args.operation == "create":
        print(json.dumps(create_disk(args.work_root, args.name, args.size_mib), indent=2))
    else:
        if not args.manifest:
            parser.error("check requires --manifest relative to owned work-root")
        print(json.dumps(load_disk(args.work_root, args.manifest), indent=2))

if __name__ == "__main__":
    main()
