# P01 S3 source, cache and Kotlin build design

Date: 2026-11-08. Published design; Task 1 local gate/freeze implemented and reviewed, actual S3 acceptance remains pending. Binding parent: `docs/superpowers/specs/2026-11-08-p01-clean-bootstrap-design.md`. Implementation plan: [P01 S3 source build plan](../plans/2026-11-08-p01-source-build.md). The independently reviewed controller reconciliation supersedes only the stale S2 qualification/status portions of the earlier drafts. CodeGraph CLI is unavailable; no indexing or production changes accompany publication.

## Scope and execution gate

S3 restores the original handoff and descriptor-identical source on accepted S2 ext4, restores cache consumers, genuinely compiles six Kotlin packages, independently replays their evidence read-only, verifies executable/toolchain bytes, and exports a sealed evidence closure. It does not compile Rust host/probe, assemble bundle/pack, start Notes or satisfy S4/S5/S6. `formalQualification=false`, `finalSystemAccepted=false`, `coldBuildExecuted=false`, `guestStarted=false` remain explicit.

S2 generation `mingos-p01-s2-20261108-f4244bac`, distro `MingOS-P01-20261108-f4244bac`, is independently accepted only at its recorded witness intervals. Reviewed source provenance is `5ac0edd5feb525951e44faffbc935e4b4eee586f`; commit identity is not byte authority. The controller reconciliation and its separate independent representation review passed on 2026-11-08. Their concrete packet-v2 representation is adopted here. Earlier proposal `s3-acceptance-reconciliation-proposal.md` remains unadopted; its extra approval envelopes, thirteen strict observed schemas and full duplicate semantic evaluator are not S3 prerequisites.

The externally supplied acceptance SHA in the trusted S3 spec/frozen harness is the local project trust anchor, like an external handoff digest. Rehash every referenced raw object; check the complete typed source/verifier/environment graph below. A candidate-selected pin, ready JSON, success exit, collector status or accepted string cannot mint authority. Collector records intentionally retain `s2Accepted=false` and `independentReviewRequired=true`; the separately authored independent actual verdict supplies stage acceptance. No publisher authentication is claimed.

Task 1 is strictly local spec/qualification validation and harness freeze: no WSL/Docker process, live admission, transfer, resource allocation or build is allowed there. Later downstream operations must additionally pass original accepted S2 `_read_ready` and fresh live namespace admission before any runtime allocation/transfer. Historical acceptance cannot authorize a stale daemon. This document publishes requirements, not S3 implementation or actual acceptance. The new bulk adapter remains separately reviewed/frozen before implementation/use.

Retain unaccepted historical `554bde65`, failed-launch `8039baf3`, terminal-failed `dbdcae92` and all other failed/sealed assets. `dbdcae92` had correct launch namespaces and successful inner loads but failed outer preservation on old container `0421...`; it is not reclassified by f4244bac success. Historical `d71b79d`/`28a67af` are not current reviewed provenance. Product rejection checks missing/incompatible proof chains for any generation, not a token blacklist.

### Fixed accepted packet and pins

Base `A = C:/Users/54951/projects/mingOS/artifacts/p01-s2-formal-20261108-f4244bac`; `Q = A/qualification-evidence/packet-v2`; `R = C:/Users/54951/projects/mingOS/.superpowers/sdd/2026-11-08-p01-wsl-environment`. These aliases are documentation notation; actual references retain their original canonical absolute Windows paths. Preserve originals and do not normalize/reserialize them before hashing.

| Role | Path | Bytes | SHA256 |
| --- | --- | ---: | --- |
| Externally pinned acceptance | Q/s2-acceptance.json | 699 | `44a5c2a4056288c75400aae012b2970c9994f144cdab8bf6e2e210c7d1e75fda` |
| Reviewed source closure | Q/source-closure.json | 5813 | `6912751518fa06e67f1779063028878a691236389bd4f02063aae67d3c55b241` |
| Independent actual verifier record | R/task-5-f4244bac-independent-actual-review-v2.json | 7062 | `3af53d027f2fbd8f3ec0c858064adad01df9e8bb946137d2b5d5f4f8d1b7497b` |
| Accepted environment receipt | A/environment/environment-result.json | 14222031 | `60a8b23dd0118a6e37f5b6b924f32ee9ef21c2b12b31b0eea3fd36e1d92d0ea6` |
| Independent actual Markdown report | R/task-5-f4244bac-independent-actual-review.md | 14118 | `440278c9d358767118adf28119d0e6dc2b1dabdb6493b92a246ab26a89bf6d71` |
| Original reviewed code-pins | A/code-pins.json | 715 | `99627b9212370befe009e45e69811f62a6607707232268592c22caee629448dc` |
| Raw evidence inventory | A/qualification-evidence/manifest.json | 62529 | `77be28e82cf1380fd7c1c2f3cd78d42a1520a148724d9bccac60b30a090072bf` |

The original independent JSON had a stale Markdown ref and remains retained rejected packet-input evidence. Only the v2 corrected independent record is accepted; controller verified its only parsed change was that ref. Observer/witness code identities remain distinct from the independent verifier JSON/Markdown: observer SHA256 `53ee6b3c0848d0c42b961fbcf668948e8546377123f22feeedf7e20174ec7d95` (31899 bytes), witness SHA256 `ca827d6d84cf3e34fd1c5cc8b74bff5af2923b633fc2724606be276d263edb1f` (7258 bytes).

### Frozen typed qualification contract for Task 1

This is the adopted packet-v2 schema, not a new evaluator or approval hierarchy. The reviewed S3 gate must freeze these keys, types, crosslinks and literals as code/constants with independent mutation tests; never infer an expected allowlist from a candidate at runtime. `Object{...}` means exactly the listed required keys, with no extras. Reject duplicate JSON keys at every depth, null except where original format explicitly allows it, unknown/missing keys, booleans in integer/count/size/mode fields, strings coerced to numbers, malformed lowercase SHA256/40hex commit and noncanonical/unsafe absolute paths. Rehash raw bytes using bounded reads and safe no-reparse/no-follow paths. `Int` is exact integer (not bool); `Size` is Int>=0; `Text` is nonempty string; `SHA` is lowercase64hex; `Path` is safe absolute host path. Objects remain original host-path refs, not the unadopted proposal's relative-path envelopes.

```text
Pin = Object{path:Path, sha256:SHA}
Ref = Object{path:Path, bytes:Size, sha256:SHA}
FilePin = Object{bytes:Size, sha256:SHA}
HelperNames = {wsl_environment.py, wsl_environment_guest.py, docker_transport.py,
               development_image_migration.py, qualification_image_archive.py}
CodePins = Object with exactly HelperNames keys, each value FilePin
S2Acceptance = Object{
  schemaVersion:Int=1, stage:Text=P01-S2, status:Text=accepted,
  environmentReceiptSha256:SHA, reviewedSourceClosure:Pin, actualVerifierRecord:Pin}
ClosureFile = Object{bytes:Size, sha256:SHA, public:Ref, frozen:Ref, hostMode:Int>=0}
SourceClosure = Object{
  schemaVersion:Int=1, kind:Text=p01-s2-reviewed-source-closure,
  reviewedCommit:40hex, generation:Text, distro:Text,
  environmentReceipt:Ref, codePins:Ref, request:Ref, repositoryBeforeLaunch:Ref,
  frozenManifest:Ref, sourceReview:Ref, frozenControls:Ref,
  files:Object with exactly HelperNames keys, each value ClosureFile,
  hostModeSemantics:Text}
VerdictRefs = Object{
  environmentReceipt:Ref, codePins:Ref, request:Ref, observerResult:Ref,
  observerController:Ref, standaloneWitness:Ref, standaloneBefore:Ref,
  standaloneAfter:Ref, publicObserveReceipt:Ref, publicExecReceipt:Ref,
  evidenceManifest:Ref, frozenControls:Ref, sourceReview:Ref, markdownReport:Ref}
Gate = Object{name:Text from frozen21-name set below, result:Text as below}
ActualVerifierRecord = Object{
  schemaVersion:Int=1, stage:Text=P01-S2, status:Text=accepted,
  generation:Text, distro:Text, reviewedCommit:40hex, refs:VerdictRefs,
  checkedGates:Array[Gate] length 21 with unique names and exact set,
  dnsEquivalenceRecordsExpected:Int=0, dnsEquivalenceRecordsObserved:Int=0,
  unresolvedItems:Array[Text] empty,
  scopeLimitations:Array[Text], formalQualification:Bool=false,
  finalSystemAccepted:Bool=false, coldBuildExecuted:Bool=false,
  p01Completed:Bool=false, resourcesPreserved:Bool=true}
FrozenManifest = Object{root:Path, files:Object with exactly HelperNames keys,
  each value Object{sha256:SHA, bytes:Size, mode:Int>=0}}
RepositoryBeforeLaunch = Object{reviewedCommit:40hex, head:40hex,
  gitStatusShort:String, trackedWorktreeAndIndexStatus:String, observedAt:Number}
ObserverResult = Object{status:Text=independent-public-followups-and-readonly-evidence-collected,
  s2Accepted:Bool=false, p01Completed:Bool=false, formal:Bool=false,
  formalQualification:Bool=false, finalSystemAccepted:Bool=false,
  coldBuildExecuted:Bool=false, resourcesPreserved:Bool=true,
  independentReviewRequired:Bool=true}
EvidenceManifest = Object{schemaVersion:Int=1,
  kind:Text=p01-s2-retained-evidence-inventory,
  status:Text=sealed-awaiting-independent-review, generation:Text, distro:Text,
  pathBase:Path, directories:Array[canonical relative POSIX directory String],
  exclusions:Array[Object{path:canonical relative POSIX path String, reason:Text}],
  files:Object[canonical relative POSIX file String -> FilePin], modeSemantics:Text,
  formalQualification:Bool=false, finalSystemAccepted:Bool=false,
  coldBuildExecuted:Bool=false, p01Completed:Bool=false}
```

`String` permits empty text where original fields use it; `Number` is finite JSON numeric value excluding bool. Repository head/reviewedCommit match closure/verdict/request reviewedHead, with both status strings empty in this accepted staged record. Source closure `files[name]` bytes/hash/public/frozen match actual file bytes, CodePins and FrozenManifest entry; hostMode equals recorded manifest mode438 (0666). It is a Windows host-stat observation, not Linux POSIX authority. hostModeSemantics/modeSemantics are provenance strings, not policy switches. Preserve Linux byte/transfer observations in their original scope; do not invent a helper-mode stat or claim NTFS POSIX identity.

The original request is rehashed as its Ref and parsed with original fixed keys `generation,distro,reviewedHead,archivePreparationResult,archivePreparationPin,inputAdmission,argv,codePins,createdAt,formalQualification,finalSystemAccepted,coldBuildExecuted`: identity/path fields are Text/safe Path, archivePreparationPin is FilePin, argv is Array[Text], codePins is CodePins, createdAt is finite Number and three flags are exact false. These are actual raw fields, not new prospective schema requirements. Original large ready/public/standalone/frozenControls payloads are retained and hash-bound in the accepted graph; use their existing reviewed reader contracts for required identity/control crosslinks, without adding a new full semantic reevaluator. In frozenControls the five source CodePins must match closure/request; its broader control/test inventory is not reduced to five entries. Pin existing bytes rather than inventing strict observed schemas for every raw runtime object.

Controller/witness pin files are `FilePin`, not full Refs. Existing frozenControls result has exact keys `status,container,interpreter,testsRun,passed,skips,failures,errors,codePins,actualWSLCreated,formalQualification,finalSystemAccepted,resourcesPreserved`: status/container are Text; interpreter is Object{python:Text,tomllib:GuestPath}; testsRun/passed/failures/errors are Size; skips is Array[Array[Text] length2]; codePins is canonical helper/test filename to FilePin map (broader than the five production helpers); actualWSLCreated/formalQualification/finalSystemAccepted are false and resourcesPreserved true. `GuestPath` here is a canonical absolute POSIX path observed inside the Linux control container, validated structurally without opening it on Windows; it is not a host `Ref.path` or `Pin.path`. Retain its original accepted hash and scope; do not relabel controls as actual WSL evidence.
Validate all repeated generation/distro/reviewedCommit and environment/codePins/request/sourceReview/frozenControls refs across SourceClosure, ActualVerifierRecord and original request/receipt. Acceptance.environmentReceiptSha256 equals the rehashed receipt and both full refs. Top-level two-field Pins bind exact closure/verdict bytes; nested full Refs additionally bind byte counts. Explicit observer/witness literals above match rehashed code and the original observer/witness pin records. They are collection identities, never verdict aliases. Rehash Markdown/report/source-review/control refs even when outside A; they are explicitly pinned independent objects, not claimed inventory members.

Verify all 365 immutable files and exact directory membership from the pinned manifest, with canonical names, no duplicate aliases, links or special files. Exclusions are exactly `environment/distro` (live VHD bytes; identity/size observations remain evidence) and `qualification-evidence` (self-output avoids cycles), with original descriptive reasons retained; no candidate-added exclusion can shrink the closure. The inventory's collection-era sealed-awaiting-independent-review status remains unchanged after independent acceptance. Packet outputs/verdict refs outside inventory are independently pinned. No new candidate-controlled layers create authority.

The frozen 21 gate-name set is:

1. `reviewed-committed-frozen-closure`
2. `source-and-affected-controls`
3. `s1-provenance-and-strict-input-pins`
4. `explicit-endpoint-and-complete-command-records`
5. `new-distro-vhd-daemon-store`
6. `rootfs-bundle-tools-and-binaries`
7. `historical-fsynced-first-empty-and-order`
8. `three-fixed-index-config-ordered-rootfs-identities`
9. `actual-daemon-environment-and-helper-trust`
10. `actual-containerd-namespace-pair-and-cli`
11. `private-mount-propagation-visible-tmpfs-and-sockets`
12. `private-cgroups-and-unchanged-shared-controls`
13. `managed-containerd-identity-and-paths`
14. `configured-and-effective-retained-smoke`
15. `complete-old-resource-preservation`
16. `dns-equivalence-set-and-schema`
17. `independent-standalone-before-after-witness`
18. `public-observe-and-explicit-linux-exec`
19. `store-completeness-and-retained-state`
20. `sealed-evidence-exact-membership-and-pins`
21. `final-receipt-and-scope-flags`

Each result is exactly `passed`, except gate16 may be `passed-empty-no-equivalence-applied` only when both typed integer counts and original receipt `oldResources.equivalences` array length are0. This accepted staged proof uses that empty result and no DNS equivalence. Any unknown/missing/duplicate gate, rejected/inconclusive result, nonempty unresolvedItems, relaxed flags or mismatched ref fails. These are verified assertions in the externally pinned independent record, not a request for S3 to re-audit all runtime gates.

S6 still requires an independently frozen/reviewed automatic semantic qualifier for each new install that creates its own equivalent proof chain. It cannot copy f4244bac's report/packet or use candidate ready/collector status as acceptance. No per-install human approval is mandated; the automatic qualifier is an explicit later S6 implementation gate. Current S3 strict packet consumption does not claim to implement it.
## Options and decision

1. Recommended: Windows public coordinator + admitted binary transfer + guest management coordinator + separately frozen harness + explicit Docker executable wrapper. Reuses current `developer_cache.restore` and `kotlin_delivery.run` contracts without fake-control labels. Adds small transport/export boundaries and preserves original source.
2. Introduce a real typed transport injection into every cache/Kotlin caller. Better long-term extensibility, but larger changes to stable confinement/capture paths and genuine/fake evidence classification. Defer unless wrapper limitations actually prevent execution.
3. Run imported source with patched scripts or compile on the management root. Rejected: patching invalidates the descriptor and management admission does not supply worker confinement. Windows-extracted source/mode reconstruction or text-piped archive transfer also fails the intended contract.

## Source and execution identities

Keep three identities separate in every receipt: original S1 source descriptor; independently frozen S3 orchestration manifest; fixed image/toolchain identities from S2 and actual worker observations. The original handoff archive is exactly 3544954880 bytes, SHA256 `cb8ef776f35584c915ad4b72d322e1973a06ca9b856c8790625e6b23a09dc744`; external handoff pin `7a4ad1336705de97fd51eb1602fed4d852d54935f3ee3f6764ad96d2ce2d1a68`; external source descriptor pin `26b90b37e3abaad293d0fafd3aae626909ed549607e42aee19557077f53e3ef0`.

The harness is frozen from the reviewed implementation checkout into a new directory before transfer. Its exact file/directory/mode/hash manifest includes all local Python imports, JS helpers, wrapper, worker and seal verifier, and the S1 import module/dependencies. Freeze closure is determined from explicit reviewed module dependencies, not by an unbounded whole checkout copy. Verify it before each phase and mount worker helper snapshots RO. Record source commit and dirty-file manifest as provenance; a commit identifier alone does not freeze bytes. Never write a new helper into `imported/source-repo` or silently substitute the restored runner. Original source remains RO to every consumer. Kotlin's existing filtered private source snapshot is separately identified, and mapped against the full S1 source inventory; its generated directory modes are not represented as descriptor-identical full source.

### Frozen harness input manifest contract

Task1 consumes `spec.harness.path/manifest.json` under the externally pinned `spec.harness.manifestSha256`. This manifest is metadata excluded from the `files` map; exact input membership is the declared directories/files plus that single manifest. No whole-checkout copy is permitted. Its exact typed schema is:

```text
HarnessInput = Object{
  schemaVersion:Int=1, kind:Text=p01-s3-harness-input,
  checkout:Object{path:Path, reviewedCommit:40hex,
    dirtyFiles:Object[canonical relative file path -> FilePin or null deletion]},
  directories:Object[canonical nonroot relative directory path ->
    Object{hostMode:Int>=0, transferMode:Int=365}],
  files:Object[canonical relative file path ->
    Object{bytes:Size,sha256:SHA,hostMode:Int>=0,transferMode:Int in {292,365}}]}
```

292/365 decimal mean intended transfer0444/0555; directory/root transfer0555 is fixed (root is implicit, not a directory-map key). Record root hostMode as an observed result separately. Every hostMode must match actual host-stat; observed frozen-copy host modes are separate from intended transfer modes and never imply Windows POSIX authority. Canonical relative paths exclude empty/root/dot/dotdot/absolute/backslash/alias components and reserved metadata `manifest.json` as a declared payload; exact file/dir sets and complete parent-directory closure are required. Reject links/reparse points/hardlinks and overlaps; no extra entry, undeclared parent or omitted empty directory is permitted.

checkout is pinned provenance: validate each non-null dirtyFiles FilePin against safe current bytes beneath checkout.path and each null deletion as safe absence, without a Git subprocess in Task1. The reviewedCommit is provenance, not a replacement for manifest byte authority. Freeze requalifies spec and accepted S2 packet, exclusively creates a local snapshot, copies only declared files in bounded reads, fsyncs, then rechecks source and copied pins plus exact closure. Preserve any failed partial snapshot. Record actual copied/root host-stat observations separately, preserving fixed intended transfer modes for the later admitted file-only harness transport. This schema is a concrete implementation contract, not a new approval gate.
## Implementation interfaces and files

These APIs are the implementation contract, not existing implemented features. Task 1 creates local qualify_source_build/freeze_harness only; run/reopen orchestration is completed in Task 6. Existing APIs below are reused unchanged. source_build.py owns strict spec, typed packet consumption, local harness freeze and later public orchestration; test_source_build.py owns their focused controls. Transfer, guest, seal, toolchain and replay files below remain separately staged; no stable S2 helper is edited.

`scripts/boot/source_build.py`:

```python
def qualify_source_build(spec: Path, spec_sha256: str) -> dict: ...
def freeze_harness(spec: dict, qualification: dict, *, output_directory: Path) -> dict: ...
def run_source_build(spec: Path, spec_sha256: str, *, output_directory: Path) -> dict: ...
def reopen_source_build(spec: Path, spec_sha256: str, *, export_directory: Path,
                        export_seal_sha256: str, output_directory: Path) -> dict: ...
```

CLI: `source_build.py run --spec ABS --spec-sha256 HEX --output-directory ABS`; `reopen` adds `--export-directory ABS --export-seal-sha256 HEX`. Output directory must not exist. `spec` schemaVersion=1 has exact keys: `schemaVersion`, `s2Acceptance` (`path`, `sha256`), `environment` (`path`, `sha256`), `handoff` (`path`, `sha256`, `bytes`, `manifestSha256`, `sourceDescriptorSha256`), `cacheDescriptorSha256`, `harness` (`path`, `manifestSha256`), `generation`, `limits` (`transferTimeoutSeconds`, `phaseTimeoutSeconds`, `hostReserveBytes`, `linuxReserveBytes`, `exportMaxBytes`). Reject unknown fields, noncanonical paths, overlapping input/output, links/reparse ancestors, malformed digests and nonpositive limits. Profiles are fixed to developer-workflow then uninstall, never user-overridable. Cache descriptor pin is extracted from the independently verified handoff inventory and compared to the spec's external pin; historical `5ee32079347e5bc107bc3196ed22ac8bd33d224f55c465e316a225fa5790be9d` is a cross-check, not authority.


The spec itself is `Object{schemaVersion:Int=1,s2Acceptance:Pin,environment:Pin,handoff:Object{path:Path,sha256:SHA,bytes:Size,manifestSha256:SHA,sourceDescriptorSha256:SHA},cacheDescriptorSha256:SHA,harness:Object{path:Path,manifestSha256:SHA},generation:Text,limits:Object{transferTimeoutSeconds:Int>0,phaseTimeoutSeconds:Int>0,hostReserveBytes:Int>0,linuxReserveBytes:Int>0,exportMaxBytes:Int>0}}`. No boolean/coercible numeric limits. Task 1 enforces its exact type/path/pin graph locally; actual cache extraction/consumer semantics remain Task 4, with no WSL invocation in Task 1. The later run/reopen CLI is completed in Task 6.

`scripts/boot/wsl_environment_transfer.py`:

```python
def transfer_environment(environment_receipt: Path, source: Path, destination: str,
                         sha256: str, size: int, mode: int, *,
                         spec: Path, spec_sha256: str,
                         output_directory: Path, timeout: int) -> dict: ...
def transfer_harness(environment_receipt: Path, frozen_harness: Path, *,
                     spec: Path, spec_sha256: str,
                     output_directory: Path, timeout: int) -> dict: ...
def export_environment(environment_receipt: Path, guest_directory: str,
                       seal_sha256: str, *, spec: Path, spec_sha256: str,
                       output_directory: Path,
                       max_bytes: int, timeout: int) -> dict: ...
```

Each public transfer/harness/export call first invokes Task 1 `qualify_source_build(spec,spec_sha256)` on the externally pinned raw spec and proof graph. The receipt argument must identify exactly qualified `spec.environment.path` and its SHA; no ready-only receipt, caller qualification dict, hidden global or environment variable grants authority. Then use accepted `_read_ready` and fresh bounded live admission before allocating any S3 payload generation/input/spool or payload worker. The admission-only observer exception below permits only the management evidence and fixed bounded observer mechanics needed to establish that admission. Derive S2/S3 generations, fixed paths, manifest and reserve limits from that qualified context. Reject mismatched source role/destination/pin/mode or nonpositive/excessive timeout before WSL calls. A private context may be reused only within one public call with source-identity/pin rechecks; it is not a caller-constructible authorization envelope.

Task 2 public `transfer_environment` handles only the original `spec.handoff` regular file, at fixed `/var/lib/<S2-generation>/s3/<spec.generation>/inputs/handoff/handoff.tar`, mode0444. Harness members use private file-only helpers under the aggregate `transfer_harness` call, never an arbitrary public uploader. `frozen_harness` names the Task 1 freeze output directory containing `freeze-result.json` and `harness/`; verify its successful existing result and requalified context, then independently verify exact snapshot membership, the original externally pinned manifest metadata bytes, payload hashes and intended0444/0555 modes. Copied NTFS host modes remain observations. There is no new acceptance schema or extra human approval. Metadata `manifest.json` is transferred0444 as an explicitly counted member alongside the payload map, not added to that map. Aggregate harness creation, per-file transfer, bottom-up directory/root0555 finalization and independent completed guest-closure verification all belong to `transfer_harness`; no import/execute before completion.

The bootstrap adapter's external byte authority is the original host harness manifest already pinned by `spec.harness.manifestSha256`, specifically fixed role `scripts/boot/wsl_environment_transfer.py`; no separate prospective pin artifact or extra spec field is required. Before WSL, safe-open the installed trusted module and require its actual bytes/size/hash to match that exact manifest member. Freeze its bytes, embedded loader literal, exact argv and spec/manifest crosslinks in exclusive local operation evidence. The independently verified loader installs only those pinned adapter bytes into the fresh admission evidence scope, checks installed size/hash/identity, verifies accepted S2 helpers separately, and then imports. This does not depend on the incomplete guest harness and cannot select arbitrary candidate code/role/pins. Missing role, mismatched host module or guest substitution fails before payload execution. A separately deployed launcher would need its own fixed declared manifest role; this two-file implementation uses the loader embedded in the pinned module.

Task 2 capacity preflight covers the complete input set: handoff bytes plus every harness payload and manifest metadata byte, twice on ext4 for retained spool and authoritative destination, bounded logs/metadata, and pinned host/Linux reserves. Observe actual host free bytes and admitted destination bavail before input allocation, then recheck remaining growth before each spool. Count retained bytes once and remaining growth once, with no budget reset or cleanup. Sibling handoff/harness operations may share only a generation root whose ownership, exact qualified context and successful prior operation records are verified; each role/output is exclusive, any failed role latches the generation and forbids continuation/retry. Task 6 separately owns whole-S3 restore/cache/two private compiler copies/export capacity and must check it before the first transfer; an input-stage receipt does not establish future build capacity. Live free-space observations are not reservations, so concurrent exhaustion fails with retained partial state.

Task 2 completion is fail-closed across the host/guest boundary: guest role/completion markers remain provisional and cannot alone authorize a sibling. A sibling must requalify the original external authority, safe-open/recheck exact durable predecessor host result/observed acknowledgement/guest closure references and perform fresh admission. In addition, the same actual live host process must have observed every required supervised persistence/finalization operation succeed; its private successful-completion witness is only an extra rejection predicate, never cached qualification or replacement authority. Bind it to exact qualified context, original role/nonce and immutable evidence pins; publish only after all fallible external actions and source final checks, revoke on every BaseException and serialize same-generation calls through the guarded epilogue. Forked/inherited/restarted/reloaded state, absent witness, host process loss, lost final worker response, failed host log/result fsync or ambiguous guest acknowledgement rejects continuation even when durable success-looking files exist or a negative latch cannot be written. Successful handoff/harness transfers therefore share one live orchestrating host process; cross-process transfer continuation is not supported. Task 6 must preserve that orchestration; read-only completed-output reopen does not resume a transfer. No new public API, external pin, approval/schema or reinterpretation of public failure as success is introduced.

Transfer/export require a **new separately reviewed and frozen S3 bulk admission adapter**, not a claimed existing S2 streaming feature. Keep accepted S2 helper bytes unchanged. Internal proposed interface: `stream_admitted(handle, argv, *, input_file, output_policy, guest_deadline, operation_directory) -> control_record`. New bootstrap and adapter bytes/argv/pins are independently frozen. Large streams never traverse `_entered`/`admit_environment`/`exec_environment` returned stdout.

Actual source correction: `_entered` calls `_stdin`, `tell()` and `seek()`; raw WSL stdin is a nonseekable pipe. Current source already hashes input in a bounded 1MiB loop, so the review's full-input-read wording is stale for this source. However, `_entered` still calls stdout/stderr `read_bytes()` after completion, so spool alone cannot solve export or bound that output path. New bulk adapter uses bounded pumps for both input receiver control output and export, never that capture path.

Windows safe-opens original input once, checks fd/path identity and external size/hash in <=1MiB chunks, rewinds, passes its descriptor through absolute binary WSL/Python invocation (`shell=False`), then rechecks original identity/hash. Guest bootstrap treats stdin as a nonseekable pipe. After closure/qualification and live-handle checks, create exclusive generation-scoped management staging at `/var/lib/<S2-generation>/s3/<S3-generation>/staging/<operation>/input.partial` with nofollow/openat. Copy <=1MiB chunks up to expected size plus one sentinel; reject short/extra/hash mismatch, fsync, recheck inode/size/hash and reopen `rb`. Retain both successful and partial staging. This management spool is not the authoritative admitted destination. Preflight reserves retained spool + destination for every input/harness file (2x transferred bytes), retained exports, restores/private copies and host/Linux reserves against actual bavail. No bulk full-file `read()` or `read_bytes()` is allowed.

The new adapter preserves actual admission semantics without invoking `_entered` bulk I/O: use accepted frozen `paths`, `validate_handle`, `validate_containerd_namespaces`, `daemon_environment`; implement separately reviewed bounded observation equivalent to `observe_handle`. Capture docker-info control with a 64KiB ceiling and fixed accepted endpoint/config, validate pid/starttime/exe/mnt/net/cgroup/full socket device-inode-uid-gid/daemonId/containerd namespaces against the qualified handle. Move adapter PID into generation management cgroup, repeat observation/validation, then spawn exact `/usr/bin/nsenter --target PID --mount --net --cgroup /usr/bin/env -i <pinned environment> <absolute argv>`. Recheck handle after completion. Do not invoke existing observation's unbounded `_docker` capture for this adapter; equivalence of bounded observation and accepted validators is an explicit independent review obligation. Record all before/after identity observations and command evidence.

For receive, child stdin is verified seekable spool; exact frozen inline argv imports accepted `receive_file` and calls it on stdin only inside admitted namespaces. For export, stdin is DEVNULL. All authoritative input destinations are below `.../inputs`, including distinct `inputs/handoff` and `inputs/harness`. Bootstrap does not import the incomplete harness. Freeze exact directory path/mode and file path/size/hash/mode manifest; admitted directory creator uses canonical nofollow/openat exclusive children initially 0700. Transfer each regular harness file separately through file-only transfer (0444 data/0555 executable); finalize declared directory modes bottom-up to 0555. Reopen and verify exact aggregate directory/file closure and manifest pin inside admission before any import or harness execution. Never pass a host directory to `transfer_environment`.

Child stdout/stderr are separate binary pipes, drained concurrently in <=64KiB pipe chunks with at most2 queued chunks per direction and locally derived monotonic deadlines. Receiver stdout is control JSON capped at 64KiB; stderr cap is 1MiB. Export stdout is deterministic regular-file/directory archive bytes only, bounded by `max_bytes`; stderr is diagnostics only capped at 1MiB. Windows independently pumps WSL stdout to exclusive `xb` archive, counts/hashes bytes up to max_bytes plus a sentinel and enforces its own deadline; no capture_output/communicate memory archive. Sender traverses exact pinned complete seal with nofollow/stat checks, streams validated bytes/modes and rechecks identities. Final sender nonce/seal pin/byte count/digest/child identity/success is written to an exclusive fsynced guest control file capped at 64KiB. Retrieve through separate bounded control invocation after stream EOF/zero exit and compare nonce, pinned record, host digest/count and seal. Missing completion is failure even with exit zero. No control JSON contaminates archive stdout.

On timeout, over-limit, nonzero exit or partial read/write failure: close pipes; terminate only exact owned process group after PID/starttime/nonce checks, bound waits, record possible survivors and failed phase/partial counts/digests/logs. Preserve spool, destination, archive, extraction roots and every owned resource; never retry in place or accept/seal partial output. Strict host extraction rejects links/specials/duplicates/escape paths and compares all members/modes/bytes to seal. Windows filesystem modes are not authority. Memory uses <=1MiB hashing chunks and capped pipe queues/control records under the8MiB transport-owned-buffer budget below, independent of total archive size; concurrent stderr drain is mandatory to avoid deadlock.
### Task 2 admission-only observer ordering

After local qualification, exact receipt binding and accepted `_read_ready` only, the independently pinned bootstrap may create one exclusive admission evidence operation at `/opt/<qualified-S2-generation>/s3-admission-observations/<spec.generation>/<operationNonce>/`, outside the S3 payload root. The independently pinned bootstrap may install only its independently pinned bootstrap/adapter code in this admission-only scope, verify its exact bytes and accepted helpers/canonical S2 paths, then import/execute it; it must not depend on the untransferred harness, change S2 helpers or execute payload. This installation is fixed bootstrap mechanics, never a candidate-selected code upload. Traverse with nofollow directory descriptors and root type/ownership checks; shared prefixes may be created or verified/reopened, but the terminal nonce directory is always new0700. This exception permits only frozen bootstrap/registration/bounded persistence mechanics and the fixed identity/socket/namespace reads plus literal docker-info observation. Record spec/environment/acceptance/adapter pins, generation, nonce, fixed action, started state and independently registered child identities. No candidate command, receiver, export, compiler, image operation, payload filesystem mutation or Docker resource creation is allowed before admission.

Collect/compare the qualified handle, move the adapter's exact verified PID to the intended management cgroup and verify membership, then collect/compare the complete handle again. An observer helper needing management membership may move only its own registered PID after static accepted-path/process checks; this does not replace either parent-handle comparison. Record admission-succeeded with both exact observations before allocating payload generation/input/staging or launching payload work. Each public sibling operation receives a new nonce and fresh admission; old admission records cannot authorize it. The later post-child comparison remains mandatory. The same narrow observation allowance covers post-check and exact registered bounded completion retrieval, never arbitrary guest file reads.

Admission consumes the same guest-local operation budget established before guest work, with the reviewed dedicated SID/PGID, pre-exec handshake, ownership revalidation, channel caps, bounded persistence and TERM2seconds/KILL-reap3seconds allowance. Failed nonce directories, observations, command records, partial logs and possible-survivor evidence remain retained. A qualified operation's observation failure latches its intended S3 generation even when no payload root exists; later continuation/retry is forbidden. A new attempt needs a new externally pinned S3 generation/spec and nonce. Local prequalification rejection makes zero guest calls and cannot latch an untrusted guest generation. This is a narrow observation ordering exception, not a new acceptance schema or human approval.

### Task 2 bounded stream admission contract

This subsection adopts the three narrow corrections in `s3-stream-admission-design-review.md`; it leaves Task 1 and accepted S2 helpers unchanged. Internal interface is `stream_admitted(handle, argv, *, input_file, output_policy, guest_deadline, operation_directory) -> control_record`. `output_policy` is a closed trusted dispatch enum: RECEIVER_CONTROL, EXPORT_ARCHIVE, COMPLETION_CONTROL. Public transfer/export/completion action selects the literal in reviewed code, never arbitrary argv/candidate JSON. Child stdout in RECEIVER_CONTROL is captured to a <=64KiB control record; EXPORT_ARCHIVE forwards child bytes only to inherited binary fd1; COMPLETION_CONTROL emits only a validated <=64KiB operation record through the same bounded transport. Diagnostics use stderr. No archive-mode error/receipt JSON is ever written to fd1. Completion retrieval uses this new bounded path end-to-end, not a capped reader above `_entered`/`exec_environment` unbounded capture. Export/seal semantics remain Task 6; Task 2 implements the generic bounded policy mechanics and receive/control only, without importing the future seal module.

Bounded observer replaces both `observe_handle` I/O and `process_identity` executable read; it reuses accepted identity semantics/validators, not the latter's `read_bytes()`. Read `/proc/PID/stat` starttime and namespace links; open the actual `/proc/PID/exe` descriptor, hash in <=1MiB chunks, compare descriptor identity and process starttime/executable identity/namespaces again before returning. Reject process/executable replacement or any drift, retaining exact accepted `{pid,starttime,exeSha256,namespaces}` types and mnt/net/cgroup namespace keys. Socket lstat/nofollow must show S_ISSOCK, uid=gid=0 and exact `{device,inode,uid,gid}` equality. The bounded docker-info child enters the same target mnt/net/cgroup namespaces with the exact accepted `/usr/local/bin/docker`, endpoint, config and clean daemon environment. Require exit0, complete <=64KiB JSON and <=1MiB stderr, actual nonempty info.ID and exact Containers/Plugins pair through accepted validators; malformed/missing/truncated/oversized data or timeout fails. Verify actual adapter management-cgroup membership after moving PID and before spawning the admitted child, with handle comparison before transition, after transition and after child completion. Host qualification and `_read_ready` precede this runtime path.

Host sends a validated positive integer duration, never a Windows monotonic timestamp. Bootstrap starts an absolute guest `time.monotonic()` deadline before initial staging and passes that guest-created deadline internally; host maintains its own local monotonic deadline. The total guest deadline covers initial spool, observations, child I/O, blocked writes, EOF, reap, post-observation and completion retrieval (retrieval receives the remaining duration, cannot reset the operation budget). Separately bounded cancellation allowance is at most5seconds: graceful TERM wait2seconds, then revalidated KILL/reap wait3seconds. If either remaining work budget or termination allowance expires, fail and retain possible-survivor evidence; no success is possible merely because a supervisor returned.

All pipe fds use nonblocking reads/writes under selectors and guest watchdog. Pipe chunk size is <=64KiB, at most2 queued chunks (128KiB) per stdin/stdout/stderr direction, no unbounded producer or diagnostic queue. File/executable hashing uses <=1MiB chunks; captured control JSON is <=64KiB, streamed stderr log <=1MiB, raw archive <=max_bytes plus one sentinel for detection. The sentinel marks failure and is never accepted as part of an archive. Partial writes advance only the written view; EAGAIN waits only until the deadline and zero-progress/error fails. Transport-owned live byte buffers/control captures are capped at8MiB per host/guest adapter process (excluding interpreter baseline and OS pipe storage), independent of archive size. Tests measure incremental growth as payload size increases and inspect read/queue sizes; no archive capture_output/communicate/read_bytes. Potentially blocking regular-file copy/hash/fsync work runs in supervised registered worker processes, so a blocked staging file operation cannot strand the supervisor. Stalled input, full downstream pipe and worker/reap waits are deadline-cancellable; uninterruptible survivors are recorded rather than waited forever. Failure/completion persistence also has bounded supervised writes; inability to persist is failure recorded by host, never implicit success.

Each owned worker/command uses a dedicated child session (`start_new_session=True`, SID=PGID=child PID), disjoint from adapter/daemon/inherited WSL groups. A frozen launcher handshake pauses before command exec: parent independently reads launched PID/starttime/PGID/SID, binds kernel identity to Popen PID plus exclusive operation nonce, durably records ownership, then acknowledges the launcher to exec exact nsenter/env argv. Handshake wait is <=min(5seconds, remaining operation duration). No command starts before ownership registration; if identity cannot be established, fail without group signaling. Nonce alone is not OS ownership evidence. Before each group signal, revalidate live leader PID/starttime/PGID/SID against the registered identity; never signal an unverified/reused PGID. Record TERM/escalation/exit and survivors. If leader exits while descendants retain pipes, close local pipe endpoints at deadline, retain observations and fail with possible survivors, without group signaling through a missing/unverified leader. Host disconnect/EPIPE triggers immediate guest cancellation; silent client loss still ends at the independent guest deadline. Host closing WSL never proves Linux descendants stopped.

Ownership bookkeeping is bounded by active registered identities, never cumulative completed work. A registered parent may request retirement only after actual reap and durable immutable registration/terminal evidence; the owner authenticates the actual parent credentials, exact original parent/child identities and registration nonce, then independently requires the original leader to be gone and its group existence probe to return ESRCH. Existing descendant groups, recycled or uncertain identities and incomplete evidence remain owned possible-survivor failures. Release active maps/nonces only after verified retirement; retain per-worker registration/terminal sidecars and bounded cumulative counters/hashchain. All retirement and evidence work consumes the original shared deadlines.

Operation record uses exact keys `{schemaVersion,operationNonce,action,status,child,bytes,sha256,sealSha256,error,possibleSurvivors,observations}`. schemaVersion is integer1; operationNonce is lowercase32hex; action is exactly receive or export (completion retrieval returns that original action); status is exactly started/succeeded/failed. child is null before spawn or exact `{pid:Int>0,starttime:decimal String,pgid:Int>0,sid:Int>0}`, with pgid=sid=pid and starttime retaining the original /proc decimal-string semantics; bytes is nonnegative integer (not bool); sha256 is lowercase64hex or null before data; sealSha256 is null for input or externally pinned SHA for export; error is null before failure or a UTF-8 string capped at4KiB on failure; possibleSurvivors is boolean; observations is a list of at most32 identity/phase record refs (full path/bytes/hash). No arbitrary nested diagnostic object can exceed the64KiB record cap. Completion requires status=succeeded, exact nonce/identity/digests/count/seal and zero possibleSurvivors, post-check success and exclusive fsync. Missing/oversized/changed completion fails. Failed artifacts/partial data/logs/spools and all resources remain retained, with no in-place retry or accepted partial evidence.

`scripts/boot/source_build_guest.py`: guest coordinator `run(spec: dict, output: Path) -> dict`, run via admitted `exec_environment` with an absolute Python argv and exclusive operation evidence. It creates no nested Docker daemon and never mounts a daemon socket into compiler/import/cache workers. It may use its own admitted management socket to orchestrate them.

`scripts/boot/source_build_seal.py`: `seal(root: Path, *, context: dict, output: Path) -> dict` and `verify(root: Path, seal: Path, seal_sha256: str) -> dict`. Seal excludes itself by defining a separate `payload/` root and sibling `export-seal.json`. It includes sorted exact directories and regular files, POSIX modes, sizes, SHA256, no links/specials/hardlinks, schema and provenance context. All paths are relative POSIX paths with canonical component rules. Reopen requires statvfs RO for payload, seal and verifier module root. Reject writable input even when bytes match.

`scripts/boot/source_build_toolchain.py`: `observe(image: str, expected: dict, output: Path) -> dict` and `verify(observation: dict, tools_root: Path, expected: dict) -> dict`. Observations occur in independent new RO-root/no-network containers using actual resolved executable files. Never compare only version strings or caller-supplied hashes.

## Endpoint wrapper and existing consumers

New generated executable wrapper is a single absolute path supplied as `docker=` to `developer_cache.restore` and `options['docker']` to `kotlin_delivery.run`. It loads the separately frozen `docker_transport.DockerTransport`, builds `transport.argv(sys.argv[1:])`, and execs the absolute binary with its sanitized environment. Endpoint/config/executable are bound to the accepted S2 handle: current guest contract uses `/usr/local/bin/docker`, `unix://<generation-run>/docker.sock`, `<generation-base>/client`. Derive actual paths through admitted `inner_transport(generation)`, then compare with accepted identity. Reject supplied `--host`, `--context`, `--config`, TLS overrides and polluted DOCKER/CONTAINERD environment using transport policy. The wrapper itself does not emit normal stdout, preserving captured Docker output. Pin wrapper bytes plus transport module and actual Docker executable before/after phases. Passing a string containing Docker flags is invalid; passing a callable marks current Kotlin results fake-control, so production must use the actual executable path and `docker=None` injection parameter.

The present `DockerTransport._environment()` strips DOCKER_/TLS_ and proxy/certificate variables, but does not strip CONTAINERD_. The new wrapper must additionally drop every CONTAINERD_ variable rather than claiming the existing sanitizer already does so. The wrapper compares environment keys with uppercase before dropping every CONTAINERD_ prefix, including mixed/lowercase variants, and tests those hostile keys plus all supported connection-override spellings; the accepted S2 transport module is not edited for it.

S1 runs `import_handoff.import_restore(archive_path, archive_pin, handoff_pin, source_descriptor_pin, output_directory)` in a new Tools worker against original ext4 archive RO, separate harness RO and new output. Another container calls `reopen(...)` with archive/imported/harness all RO. Complete source descriptor validation includes dirs/modes/hashes; no Windows extracted tree participates.

Cache uses exactly imported `delivery/rebuild-inputs/cache/{developer-inputs.json,dependencies.tar,gradle-baseline.json}` as RO siblings and calls `developer_cache.restore(inputs, descriptor_sha, output, docker=wrapper)`. Require `developer-cache-restored-consumers-readonly-verified`, actual-docker, descriptor pin, restored anonymous-volume verification, separate projection and RO consumer verification, statvfsReadOnly=true, Cargo/Gradle local volume inspection tied to this endpoint. Before and after Kotlin, independently reverify both restored consumer trees; Gradle original cache is RO and unchanged. Cargo consumer remains reserved for S4, not compiled in S3.

Kotlin calls `kotlin_delivery.run(options)` twice, with options `repo=<S1 source-repo>`, `output=<new per-profile directory>`, `baseline=<imported gradle-baseline.json>`, `original_cache=<restored gradleVolume>`, `profile=<explicit profile>`, `docker=<absolute wrapper>`. Neither `synthetic_controls` nor injected callable is permitted in actual acceptance. Each invocation creates separate new source/cache/work/output volumes. Profiles and labels: developer-workflow=[notes-demo,animated-demo]; uninstall=[read-file,image-viewer,text-entry-demo,uninstall], where uninstall maps to persistent-uninstall-demo sample. Require six real commands, six success receipts, offline/rerun-tasks/no-build-cache/no-configuration-cache, executed compiler tasks, predecessor chains, raw logs, package verification, source inventories, dependency comparisons, and separate `verify-all` RO container. Notes SDK6 and Motion SDK5 evidence must survive export.

Existing `docker cp` copies a stopped verify-all container's `/out` to guest `packages-and-evidence`. Treat it as a transport, not a seal or provenance oracle. Independently compare complete copy inventory, directories/modes/bytes to RO output-volume inventory, and replay against that exported copy in another container. Only then seal it for Windows export. Windows modes are not a POSIX oracle: retain canonical binary export archive; authoritative modes come from Linux original/copy/seal and are checked in archive headers. Host directory bytes/count are additionally checked; never bless Windows mode guesses. Optional archive return can additionally recheck modes on Linux.

## Confinement, capacity and tools

Import/replay/observation workers: no network, RO root/input/harness, UID/GID65534, all caps dropped, NNP, seccomp, 2CPU/4GiB/256pids and bounded tmpfs. Root exists only for verified fresh output volume ownership initialization, with CHOWN alone where needed. Reuse cache's existing stricter 128pids contract; do not silently change it to 256. Kotlin retains its exact existing 256pids checks. Observe both inspect and effective `/proc`, cgroup, interfaces=[lo], mountinfo/statvfs; retain all identities and observations, including failures. Manager is explicitly not a restricted worker.

No new global capacity budget overrides S4's 32.5GiB rule. S3 preflight accounts for retained archive, restored delivery/source/cache, two independent Kotlin private copies, evidence export and configured reserves against actual host available space and ext4 bavail. Minimum Linux observation at each Kotlin prepare remains existing 8GiB. Reserve and export limits are explicit pinned inputs, never silently reduced after allocation. No cleanup to make space; failure stops and retains all objects. Observe actual Windows VHD path/identity and filesystem free bytes from accepted S2, actual Linux statfs/mountinfo and volume inspections; no drvfs/9p/Windows bind for source/cache/work/output. This is S3 storage evidence, not the future S4 cold capacity receipt.

Observe actual fixed Kotlin/Tools/Rust images from accepted S2 daemon. Kotlin worker resolves Node process.execPath and JAVA_HOME/bin/java, opens regular actual files and hashes them; independently compare with every package evidence/toolchain.json. Check Node24.22.1, JDK21, Gradle8.14.5 distribution archive digest against locked wrapper and restored descriptor; record wrapper jar and Kotlin compiler dependency archive bytes. Rust/Tools worker observations preserve actual rustc/cargo/Node realpaths, binary hashes, versions and fixed image IDs for S4 handoff without claiming Rust compilation. Resolve declared symlinks to canonical regular executable within the image and record both logical and resolved paths. Independent replay reopens actual same-image files RO and rejects evidence claiming different path/hash even when version text matches.

## Evidence, failures and acceptance oracle

New generation directories and all outputs use exclusive creation. Receipt is persisted before first external action and after every allocation/phase; terminal `failed` records exception, phase, exact owned IDs, partial files/logs and any observation errors. Retain all existing and new resources. Stop only a still-running exact owned container after identity/nonce verification on timeout; record before/after state, never remove it. No in-place retry, prune, distro unregister, VHD deletion, volume/container/image removal or log truncation. Seal only successful complete payload; failure receipt is never accepted as build evidence.

Success receipt `source-cache-kotlin-built-export-reopened` binds accepted S2 qualification/environment/source/verifier hashes, original archive/manifest/source pins, full source reopen, cache descriptor/restore/RO verifies, frozen harness, wrapper/endpoint observations, both Kotlin result hashes and generation IDs, six package hashes and compiler/evidence chains, independent toolchain byte check, complete guest-copy seal, canonical export archive SHA/bytes, host export comparison, independent Linux RO copy replay result and new daemon before/after identities. Include full object IDs, not only names. Exact expected artifact set derives from current complete inventories, not old historical counts.

Required S3 actual acceptance uses another new restricted container on the accepted daemon, mounting the independently copied export tree and separately frozen verifier/source/cache references RO. It verifies full directory/mode/byte inventory against the output-volume seal and replays all six packages from the copied exported bytes. Host export additionally verifies complete archive member bytes, directory/mode headers and archive digest against that seal, without treating Windows filesystem modes as authoritative. No compiler executes during replay. Both original source full inventory and filtered per-profile snapshot mapping are checked. Every real compilation, endpoint, source, cache, toolchain and transfer link must agree; pipeline-completed or a byte-only seal is insufficient.

A canonical host archive return to a fresh ext4 path, strict extraction in one new restricted container and full RO replay in another is an optional extra handoff check, not an additional S3 completion condition. If run, record its result/pins separately and retain all failures; never substitute it for the mandatory source/cache/compiler replay. It does not claim S4 build or S6 application persistence acceptance.

Required negative tests: rejected S2 qualification; arbitrary-generation incompatible proof/receipt/helper or missing accepted chain (include historical 554bde65 as a regression fixture, without token-specific product logic); stale namespace/socket/helper; binary NUL/high-byte stream; short/extra/wrong-hash stream; path/symlink/existing output; mutating source fd; poisoned Docker context/override; imported source change; cache baseline mutation; compiler failure/timeout; actual-vs-fake label rejection; Node/Java byte substitution retaining version text; missing log/package/extra file/mode change; writable replay mount; corrupted/truncated host archive; malformed archive paths/duplicates/specials; export max/capacity failure. Failures retain exact evidence and never produce accepted success.

## Publication, remaining work and limitations

Task 1 may implement only the frozen local spec/gate and harness-freeze requirements. Tasks 2–7 retain their sequential implementation/review gates and actual S3 remains unaccepted. New runtime operations require qualified exact packet bytes, original ready validation and fresh admission. Fixed dependencies are not source-bootstrapped; scope is same physical host/new WSL daemon, not independent-host reproducibility. Formal P01 remains open through S6.

Original source/cache, six actual Kotlin compiles, independent RO exported-copy replay and sealed host export remain mandatory; archive return remains optional. S4 must separately export/pin actual raw Node executable bytes/path/mode/digest and verify its RO consumer; S3 observations alone do not satisfy it. No failed generation is reclassified and no sealed/failed asset may be removed.

## Downstream S6 first outer Tools load

S6 public single entry must obtain the fixed outer Tools archive from pinned input when absent, strictly load it and verify exact image identity. Manually preloaded/existing outer Tools cannot close P01. Keep first outer Tools load proof separate from inner daemon empty-store and three-image-load evidence. This is a downstream obligation, not S3 runtime work.








