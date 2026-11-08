# P01 S1 strict handoff import

`scripts/boot/import_handoff.py` provides Linux integrity import, exclusive current
source restore and read-only reopen. It uses the public `current_source`,
`handoff_manifest`, `workflow_pack` and `strict_ustar` modules in `scripts/boot`.
It does not load images, compile code, boot a guest or create Docker resources.

## Trust inputs and output

Supply three independently recorded, lowercase SHA256 values: the outer archive,
the handoff manifest and the current-source descriptor. Do not derive the trusted
pins from the same incoming archive. The existing sealed development handoff has:

| Input | SHA256 |
| --- | --- |
| `handoff.tar` | `cb8ef776f35584c915ad4b72d322e1973a06ca9b856c8790625e6b23a09dc744` |
| handoff manifest | `7a4ad1336705de97fd51eb1602fed4d852d54935f3ee3f6764ad96d2ce2d1a68` |
| source descriptor | `26b90b37e3abaad293d0fafd3aae626909ed549607e42aee19557077f53e3ef0` |

All paths must be absolute and have no symlink parents. The output directory must
be absent; its parent must already exist. A new volume's mount root already exists,
so choose a new child such as `/output/generation-1`. Never reuse a successful or
failed output directory. Import retains every partial output on failure and writes
the success receipt only after all checks pass.

The successful root contains exactly:

```
generation-1/
  delivery/
  source-repo/
  import-restore-result.json
```

`restore(archive_path, archive_pin, handoff_pin, destination)` restores only the
delivery. `import_restore(archive_path, archive_pin, handoff_pin,
source_descriptor_pin, output_directory)` also restores source and creates the
canonical receipt. `reopen(archive_path, archive_pin, handoff_pin,
source_descriptor_pin, imported_directory)` recomputes and compares that receipt,
the entire delivery and the complete source byte/path/mode inventory.

## Linux CLI

With the public module closure available at `/public`, run:

```sh
python3 -B /public/import_handoff.py import-restore \
  --archive /input/handoff.tar \
  --archive-sha256 cb8ef776f35584c915ad4b72d322e1973a06ca9b856c8790625e6b23a09dc744 \
  --handoff-sha256 7a4ad1336705de97fd51eb1602fed4d852d54935f3ee3f6764ad96d2ce2d1a68 \
  --source-descriptor-sha256 26b90b37e3abaad293d0fafd3aae626909ed549607e42aee19557077f53e3ef0 \
  --output-directory /output/generation-1
```

Run reopen in a different container with the archive, output volume and verifier
directory mounted read-only. Each of `/input/handoff.tar`,
`/output/generation-1` and `/public` must report `ST_RDONLY` through `statvfs`:

```sh
python3 -B /public/import_handoff.py reopen \
  --archive /input/handoff.tar \
  --archive-sha256 cb8ef776f35584c915ad4b72d322e1973a06ca9b856c8790625e6b23a09dc744 \
  --handoff-sha256 7a4ad1336705de97fd51eb1602fed4d852d54935f3ee3f6764ad96d2ce2d1a68 \
  --source-descriptor-sha256 26b90b37e3abaad293d0fafd3aae626909ed549607e42aee19557077f53e3ef0 \
  --imported-directory /output/generation-1
```

Both commands emit canonical JSON to stdout and exit nonzero on error. Capture
stdout/stderr outside the imported root. Reopen writes no receipt or other file.
Use `-B` so Python also avoids bytecode writes in the verifier mount.

The fixed Tools image for these controls is
`sha256:a085efa9e8b4958b1943ee87857313f3d2666f696f730f242daef643347b0091`.
Docker wrappers should use unique retained container/volume names, `--network none`,
`--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, a writable
`/tmp` tmpfs and read-only public/source input binds. A fresh volume is owned by
root; the caller must arrange output ownership or explicitly use `--user 0:0`
with the same dropped capabilities. Preserve failed containers, volumes and logs;
start a new generation for each retry. S1 itself has no Docker dependency.

## Admission and scope

The outer tar must contain precisely `delivery` and every file/directory admitted
by its pinned handoff manifest. Links, special files, sparse entries, duplicate,
missing and additional paths, unsafe paths and wrong types/sizes/modes/hashes are
rejected. PAX `path`, `mtime`, `atime` and `ctime` are supported; other PAX metadata
is rejected. Limits are 4096 logical members, 16 GiB per file, 8 MiB for the handoff
manifest and 32 GiB total payload. A single no-follow regular archive handle is
held, with pre/post identity and digest checks. No `extractall` is used.

Windows runs API, pin and CLI admission controls. Actual restore and mount
semantics require Linux. Tests include statvfs adapters for negative admission
controls; adapters alone do not prove a read-only mount. The retained small
fixture import/reopen evidence is separate from the actual 3.54 GB handoff test.

## Actual sealed archive acceptance — 2026-11-08

The reviewed implementation is commit
`e30139267f710afd7a13e66b58542fd8a4a6be26`. Independent implementation review
approved spec compliance and task quality. The real 3,544,954,880-byte archive
then passed public import and a separate container's read-only reopen. Evidence
is retained at `artifacts/p01-import-actual-20261108-b/`, including each command,
stdout/stderr, before/after inspections, frozen six-module public closure and
`result.json`. The controller independently rechecked the captured results,
resource configurations, closure hashes and unchanged volume identity. A separate
supplemental review of the actual large integration approved the S1 stage gate;
it did not rerun the tests or claim another live resource inspection.

| Observation | Actual value |
| --- | --- |
| Restored source | 1561 files, 406 directories; complete byte/path/mode verification |
| Public code-pins SHA256 | `41c883e12e04c8a03cb495c5b105b0aaaaee87745bc7713b83492e75d65385a6` |
| Output volume | `p01-import-actual-20261108-b-output`, child `import/` |
| Import container | `4ce5ca49520626ae285a8e405882ad36638f68d1af1fc544c38b4c25aaf03262` |
| Reopen container | `a1e81a9c09c6aa877af52b3b2e87b8b67cbd32a6ee37ba496d9af5b47c4908df` |
| Worker restrictions | UID/GID65534, network none, read-only root, drop ALL, no-new-privileges, 2 CPU, 4 GiB, pids256 |
| Reopen inputs | Archive, verifier and complete imported volume mounted read-only |
| Worker outcomes | Both exact containers exited0, not OOM; volume identity unchanged |

Linux controls retained under
`artifacts/p01-import-controls-20261108-default-worker/` report 19 importer tests
and 88 required regression tests passed, with zero skips. Windows importer
controls report 3 passed and 16 Linux-only skips; they are not Linux restore
evidence. The independent review also inspected the genuine small-fixture
import/reopen evidence before this large integration.

The first large integration generation, `p01-import-actual-20261108-a`, failed
before its initializer started because the controller compared `CHOWN` with
Docker's equivalent `CAP_CHOWN` spelling. Its directory, container and volume
remain retained. The harness now normalizes only that prefix when comparing
the exact capability set; generation b used entirely new resources. No public
importer code or historical driver was changed to bypass that failure.

This acceptance used the existing Desktop daemon and proves S1 import semantics.
It is not a fresh-daemon or cold-build result. S1 does not close P01: S2–S6 still
require a fresh daemon, compilation, assembly and runtime persistence acceptance.
All six `current_source.FLAGS` remain false, including `formalQualification` and
`finalSystemAccepted`.
