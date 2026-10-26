# Boot infrastructure API v1 — 2026-10-25

Scope: scripts/boot, artifacts/boot-* and task-owned Docker volume; no D: writes, no Rust/SDK edits. v4/raw evidence and old review remain immutable. Final persistent host/hash/session review is still required.

## Runtime and owned workspace

Tools image: mingos-boot-infra:20261025-v1. Runtime: uid/gid65534, cap-drop ALL, no-new-privileges, readonly root, network none. Only e2fsprogs/libext2fs2t64/libss2=1.47.2-3+b12 were installed in the derived image; deb hashes/logs retained.

Volume mingos-boot-infra-20261025-work is first mounted at /var/tmp (image mode1777 copy-up). UID65534 creates /var/tmp/mingos-boot-infra-20261025 mode0700. Later mount the same volume at /work and use /work/mingos-boot-infra-20261025. No old directory is chowned. The task marker, uid/mode, containment, absence of symlinks/hardlinks and regular-file type are checked.

~~~text
data_disk.py init-workspace --work-root ROOT
data_disk.py create --work-root ROOT --name developer-128 --size-mib 128
data_disk.py check --work-root ROOT --manifest disks/developer-128.json
~~~

Sizes exactly128/256MiB. Exclusive regular files + mkfs.ext4 root_owner=0:0; no host mounts/loops/block devices. Manifest records ext4 UUID, 20-char serial, size and initial SHA. Initial SHA is provenance, not immutable after guest writes. Runs take exclusive flock. No overwrite/autoformat/automatic fsck repair.

## Bundle/guest

Future bundle has infraApiVersion=1. Include virtio_blk/ext4 module closure and cgroup2 CPU/memory/pids support. Guest PID1 prepares uid0/mode0700 /sys/fs/cgroup/mingos and /run/mingos-workers; enables/readbacks controllers. It never joins desktop/PID1 to the workers root or silently weakens strict.

Optional QEMU disk is verified before launch. Expected UUID/serial are fixed mingos.data_uuid/mingos.data_serial kernel parameters. Guest resolves only matching virtio by-id, checks ext4 UUID, mounts nodev,nosuid at /data/mingos. Atomic fsync/readback marker .mingos-boot/current.json contains bootId/counter; previous/current exported. Sync/unmount before poweroff. Guest never formats a device.

~~~text
bundle.py --payload DIR --review JSON --output NEW_DIR [--isolation-probes FROZEN_DIR]
~~~

Strict diagnostic probes require exact strictProbeSha256 in a NEW review and hard-pinned frozen hashes. vm runs before desktop; vm-resources only after desktop exit and absence of active Kotlin workers. Probe output/cleanup markers remain diagnostic evidence, not final OS/Kotlin strict claims.

## State-file: matches main

Exactly /run/evidence/desktop-state.json, supplied only in explicitly approved host args. Host atomically replaces in same directory, <=10Hz routine, forced start/stop, no app text.

- schemaVersion:1; bootId:string (actual guest boot_id); sessionId:string; revision:u64.
- phase:starting|ready|stopping|stopped|failed.
- root:{width,height,presentedSequence}; sequence nullable/0 before presentation.
- apps:[{app,generation,window,workerPid,state,sourceSequence,presentedSourceSequence,firstReadyMs,inputReady,minimized,outer}]. Extra main fields retained; window/outer optional to runner validation.
- error:null|string <=2048 chars.

Guest relays snapshots as virtio desktop-state. Runner requires current bootId, stable sessionId, non-regressing revision; same revision with different data fails. Target app ready AND inputReady=true, not minimized, workerPid/presentedSourceSequence>0; root actually presented. No fixed-delay readiness. Terminal state can satisfy stopped expectation.

## Virtual input

~~~text
vm.py --bundle DIR --review JSON --output NEW_DIR --timeout 900 [--work-root ROOT --data-manifest RELATIVE_JSON] [--input-script JSON]
~~~

Input requires exact reviewed --state-file path; otherwise fail before kernel. JSON:

~~~json
{"schemaVersion":1,"requireApps":[1,2],"steps":[{"name":"focus-compose","events":[{"kind":"pointer","x":600,"y":765},{"kind":"button","button":"left","down":true},{"kind":"button","button":"left","down":false}],"expect":{"rootAdvance":true}},{"name":"exit","events":[{"kind":"key","qcode":"esc","down":true},{"kind":"key","qcode":"esc","down":false}],"expect":{"phase":"stopped"}}]}
~~~

Max64 steps/32 events per step. Presented-root pixels map to QMP0..32767. Only pointer/button/key; no shell or physical input. Per-step requireApps overrides global list; empty list means trusted shell/root-only action. Expectations phase/rootAdvance/sourceAdvance:[app IDs]; revision must advance. inputs.jsonl records full snapshots/events. Held keys/buttons released on error if VM running; inability recorded. Timeout is failure.

## Transcript/result/cold boot

qmp.jsonl persists each sent request and received reply/event with monotonic time and fsync; qmp.json final transcript. Captures are actual PPM screendump. Paused probes query block/schema and attempt harmless Shift release: QEMU rejects VM not running. This verifies protocol/error capture, not guest consumption.

Terminal guest-result has status/desktopExitCode/dataSynced/poweroffRequested/bootId/diskUuid/previousMarker/marker. Normal requires code0, no platform/probe/backend error, absent Kotlin workers and synced/unmounted data. Runner additionally requires QEMU code0 and serial Power down. normal-desktop-poweroff is diagnostic-only; finalSystemAccepted=false. Timeout=timeout-unaccepted; missing legacy terminal=guest-exited-unverified. Exceptions preserve disk/evidence, never retry/format/count pass.

~~~text
cold-boot.py --bundle DIR --review JSON --work-root ROOT --data-manifest RELATIVE_JSON --output NEW_DIR --timeout 900 [--input-script JSON]
~~~

Exactly two fresh sequential QEMU processes. Same bundle hash+disk UUID, distinct actual bootIds, normal shutdowns, second previousMarker==first marker and counter+1. If state exists, session IDs must change too. First failure stops sequence. Recovery is an explicit new invocation on preserved disk. Final host qualification waits for main review.

## Current handoff availability

The derived image was built; uid65534 regular 128MiB ext4/fsck/lease and two paused QEMU attachments were verified; Linux16 tests passed. Later Windows17 guard tests passed. These are historical results, not current image/volume availability.

During strict-v1 post-build inspection, all local images/cache disappeared. Official fixed Debian base pull/resolution succeeded, then recovery build failed with RPC EOF. The subsequent daemon snapshot reported images/containers/volumes/cache all zero and no owned workspace volume. This task issued no deletion/prune/reset/restart. Deletion versus data-root switch is unknown.

Strict-v1 builder returned0 and its manifest is preserved; its bundle bytes and both new data images were in the now-unavailable volume. No new strict kernel, guest cgroup/data marker, live QMP input or real two-boot validation has executed. Old v4 exported kernel/initramfs/raw evidence remain on C: and hashes were rechecked.

When Docker state is stable, Dockerfile.recovery/recovery-packages.lock rebuild runtime-only tools from fixed Debian digest. Recreate a NEW owned workspace/disk generation; never present a recreated disk as the earlier disk. Final persistent host review remains pending.
