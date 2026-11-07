# Independent persistent uninstall raw checker

This module implements the Task 6/7 evidence contract in the November 4 uninstall
specification. It never starts Docker, a guest, a worker, or a fixture helper.
Only the signature verifier invokes local OpenSSL, including a tampered-message
negative control. Unit fixtures are synthetic and do not establish qualification.

`checker.pair(root)` consumes a sealed directory containing `freeze.json`,
`file-sha256.json`, this directory's `checker.py`, `raw_boot_audit.py`, both fixed
inputs, `replay/scripts/boot/` dependencies, `inputs/session-review.json`,
`inputs/payload/`, `bundle/`, `fixture/`, and `pair/boot-{1,2}/`. The raw boot files
are result.json, guest.jsonl, inputs.jsonl, qmp.jsonl, qmp.json and kernel-serial.log.
The fixture is an initialization-only staging tree, with command.json,
fixture-receipt.json, stdout.log, data/install-root, and install-source. Its actual
stored signed packages and source are independently checked against packaged
payload bytes. The immutable export manifest enumerates every regular file;
freeze binds checker, raw auditor, replay modules, and both input hashes.

`verify_boot` is also available for focused controls. Failure raises ValueError;
the CLI `--pair ROOT --output NEW_EXTERNAL_JSON` records failure and exits 1.
The repository CLI verifies a sealed export and runs its own checker with -I -B;
replay imports occur only after the complete manifest and freeze have verified.

Runtime integration also accepts
`verify_boot(run, number, review, spec, signed, bundleIdentity, repo)`;
the compact three-argument form takes a payload mapping with review, signed,
bundleIdentity and repo. Both return before/after/sessionId/bootId/report and
raw receipt offsets. `verify_fixture_directory(fixture, inputs, review)` returns
independently verified bootstrap/source package identities and initialContent.
The runtime staging fixture is `build/fixture`; portable export places the same
bytes at `fixture`. command.json always retains original POSIX command roots
`/prep/build/fixture` and `/prep/inputs`, even after export relocation.
The parent implements its own runtime-freeze/cold-chain `driver.py`; this
checker does not substitute an old historical freeze for that runtime contract.

The seven boot1 points are frozen from desktop_native's
persistent_uninstall_two_app_input_geometry assertion at 1280x800:
INSTALL(1148,12), ALLOW(880,478), Dock(676,766), Close(96,104), U(656,748),
UNINSTALL(880,478), POWER(1238,12). Boot2 has only POWER. Every step has leading
ABS, the existing five-second pointer-ready handshake, then matching left down/up.
Close requires app1/app2 Ready; U requires normal target result/reap and nonempty
privateBefore; POWER requires committed removal. No budget is increased.

Checks retain the complete shared raw QMP/pointer/state/log/powerdown gates;
strict uninstall relay causality; exact five helper action counts in each of
four snapshots; exact helper/desktop argv and no boot2 install source/reseed;
complete content and removed identity/history; exact note.txt=alpha snapshot
before/after commit and across post/pre/post; all final worker histories, native
Ready/Close/Result/reap/normal error-free terminal records; and no app2 in any
boot2 state, history, trace or sampled worker process. Logs use byte offsets and
host monotonic and guest uptime are never compared across domains.

Final success means persistentUninstallNormalRebootVerified=true and
privateDataPreserved=true within this instrumented Linux normal reboot scope.
formalQualification=false and finalSystemAccepted=false. Physical input/display,
power failure, malicious same-UID host mutation and crash recovery are excluded.
