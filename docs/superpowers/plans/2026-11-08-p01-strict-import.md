# P01 S1 Strict Handoff Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供公开、参数化、失败留存的 Linux handoff 导入/源码恢复/只读重开模块，完成 P01 的 S1；P01其余阶段仍须独立完成。

**Architecture:** 将封存交付中的严格导入逻辑提升为公开 Python 模块，并复用现有 source/handoff/runtime 验证器。组合接口恢复到新目录并写有限范围收据；另一个只读调用完整重开，不耦合 Docker 资源管理或历史固定路径。

**Tech Stack:** Python 3 标准库，Linux descriptor/no-follow/statvfs，unittest，固定 Tools 镜像进行真实 Linux 控制。

**Spec:** `docs/superpowers/specs/2026-11-08-p01-clean-bootstrap-design.md`，尤其“S1 公共导入合同”和“验收证据”。

## Global Constraints

- 原交付目录、历史失败代、容器、卷、磁盘和日志保持原样。新增输出只能使用新代，不清理、不重置、不原地重试失败代。
- 不放宽历史资格 driver，不替换旧冻结 probe 的固定摘要；新开发 probe 使用自己的来源合同。
- `formalQualification=false`、`finalSystemAccepted=false` 在本项目保持。
- 最多4096个逻辑成员、每文件16GiB、manifest 8MiB、提取总量32GiB；超过上限立即失败。
- 兼容实际封存 PAX 归档中的 `path/mtime/atime/ctime`，拒绝其他 PAX 元数据、链接和特殊文件；不使用 `extractall`。
- 不删除或修改 sealed 交付目录；生产源码只能依赖公共模块，不能 import artifacts 下的 helper。
- 沿用专用 `codex/persistent-update` 分支实施，保留现有共享证据；不在 main/master 实施，不新增无必要的源码长路径。

## Task 1: 公共严格导入、组合恢复和只读重开

**Files:**
- Create: `scripts/boot/import_handoff.py`
- Create: `scripts/boot/test_import_handoff.py`
- Create: `docs/p01-strict-import.md`
- Read: `scripts/boot/current_source.py`, `scripts/boot/handoff_manifest.py`, `scripts/boot/strict_ustar.py`, `scripts/boot/test_linux_handoff.py`
- Read only as historical reference: `artifacts/mingos-vm-dev-20261107-0c9f60f/tools/import-restore/frozen-assembly/import_handoff.py` and `frozen-review/verify_restored_source.py`

**Interfaces:**
- Consumes: `current_source.verify(source, descriptor_raw, descriptor_pin)`, `current_source.restore(source, descriptor_raw, descriptor_pin, destination)`, `current_source.open_directory(path)`, `current_source.source_inventory(root, expected)`, `handoff_manifest.verify(root, raw, pin)`.
- Produces: `restore(archive_path, archive_pin, handoff_pin, destination) -> dict`, `import_restore(archive_path, archive_pin, handoff_pin, source_descriptor_pin, output_directory) -> dict`, `reopen(archive_path, archive_pin, handoff_pin, source_descriptor_pin, imported_directory) -> dict`.
- `import_restore` output layout: `delivery/`, `source-repo/`, `import-restore-result.json`; output directory is absent before invocation. `reopen` checks read-only mounts for archive, imported root and `Path(__file__).parent`, emits no file.
- Successful import status: `handoff-imported-and-current-source-restored`; reopen status: `handoff-and-source-readonly-reopened`. The import receipt has exactly `status`, `archiveSha256`, `handoffSha256`, `sourceDescriptorSha256`, `importVerification`, `sourceRestore`, and every key of `current_source.FLAGS` with false values. `importVerification` equals the actual `handoff_manifest.verify(...)` result. `sourceRestore` has exactly `status='current-source-exclusively-restored-and-verified'`, `fileCount=len(verified_source['manifest']['files'])`, `repositoryCommit=verified_source['manifest']['repositoryCommit']`, and every `current_source.FLAGS` key false, matching `current_source.restore`'s existing result. Store exact canonical bytes from `current_source.encoded`; reopen rejects unknown fields, noncanonical bytes, incorrect values, and any mismatch from these recomputed results. Reopen result has exactly `status`, the same three pins and every `current_source.FLAGS` key false; it is emitted to stdout, never saved into the imported root.

- [ ] **Step 1: Write real fixture and failing behavior tests.** Build a tiny valid sibling tree with all eight components, genuine `current_source.export` output and the minimal runtime provenance fixture pattern from `test_linux_handoff.py`; create a PAX tar rooted at `delivery` with actual modes and known hashes. Keep test fixture generation inside tests. The initial test asserts the public module/functions exist, then test positive import and use corrupted copies for negative controls. Core examples:

```python
def test_public_module_exists(self):
    self.assertIsNotNone(importlib.util.find_spec('import_handoff'))

def test_restore_refuses_existing_destination(self):
    destination = self.base / 'existing'
    destination.mkdir()
    marker = destination / 'keep'
    marker.write_bytes(b'original')
    with self.assertRaises((ValueError, FileExistsError)):
        self.api.restore(self.archive, self.archive_pin, self.handoff_pin, destination)
    self.assertEqual(marker.read_bytes(), b'original')

def test_partial_failure_cannot_be_retried(self):
    # The fixture mutation updates archive hash but leaves a payload hash wrong.
    archive, archive_pin = self.archive_with_corrupt_payload()
    output = self.base / 'partial'
    with self.assertRaises(ValueError):
        self.api.import_restore(archive, archive_pin, self.handoff_pin,
                                self.source_pin, output)
    self.assertTrue(output.exists())
    self.assertFalse((output / 'import-restore-result.json').exists())
    with self.assertRaises((ValueError, FileExistsError)):
        self.api.import_restore(self.archive, self.archive_pin,
                                self.handoff_pin, self.source_pin, output)
```

Cover all three pin mismatches, duplicate/missing/extra entries, path traversal, symlink/hardlink/special/sparse, unknown PAX, type/size/mode/hash errors, descriptor/standalone inventory mismatch, symlink parent, archive mutation and caps. For caps patch module constants to small values instead of allocating multi-GiB fixtures. Linux restore tests must use actual filesystem; cross-platform tests still run pin/CLI/API admission. For readonly mount semantics use a small adapter around `statvfs` for control tests, and separately real RO mounts for integration; tests must not treat the adapter as real mount evidence.

- [ ] **Step 2: Run RED and preserve its output.**

```powershell
& 'C:/Users/54951/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B -m unittest discover -s scripts/boot -p test_import_handoff.py -v
```

Expected meaningful failure: missing public module/API, not malformed fixture. Save the invocation and result in a new `artifacts/p01-import-controls-*` evidence directory. The task implementer can run Linux tests in a new retained fixed Tools container with readonly verifier/tests input and tmpfs `/tmp`; no `--rm` or resource deletion.

- [ ] **Step 3: Implement the three public functions and CLI.** Reuse the frozen strict allowlist algorithm while removing environment-specific imports. Iterate bounded member collection, validate exact path/type/size/mode before writing; maintain one opened regular no-follow archive handle, stable file identity and pre/post hash, and no-follow directory operations. The composition is:

```python
def import_restore(archive_path, archive_pin, handoff_pin,
                   source_descriptor_pin, output_directory):
    # Validate all external pins and fresh absolute output before mutation.
    # Create only the new output root; every failure leaves it in place.
    imported = restore(archive_path, archive_pin, handoff_pin,
                       output_directory / 'delivery')
    incoming = output_directory / 'delivery' / 'current-source'
    with (incoming / 'source.tar').open('rb') as source:
        restored = current_source.restore(
            source, (incoming / 'source-descriptor.json').read_bytes(),
            source_descriptor_pin, output_directory / 'source-repo')
    # Verify standalone inventory and complete restored byte/mode/path sets.
    # Atomically create the success receipt only after every check succeeds.
```

The code above specifies composition, not a complete implementation: the spec supplies mandatory pin, descriptor, identity, mode and result checks. Keep private helpers focused: opened archive admission, allowlist admission, complete source recheck, and readonly admission. `reopen` must verify saved receipt content and exact output root set, delivery, source and external archive pin. Do not change public existing source/handoff contracts or inject unearned claim flags.

- [ ] **Step 4: Run GREEN and regressions.**

```powershell
& 'C:/Users/54951/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B -m unittest discover -s scripts/boot -p test_import_handoff.py -v
& 'C:/Users/54951/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B -m unittest discover -s scripts/boot -p test_current_source.py -v
& 'C:/Users/54951/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -B -m unittest discover -s scripts/boot -p test_handoff_manifest.py -v
```

Linux retained Tools container additionally runs `test_import_handoff.py`, `test_linux_handoff.py`, `test_current_source.py`, `test_handoff_manifest.py`, `test_qualification_volume_archive.py`. Report actual totals and skips separately. Negative RO reopen controls must reject writable input, added root/delivery/source path, source bytes/mode change and changed receipt.

- [ ] **Step 5: Document and review.** `docs/p01-strict-import.md` records exact Linux CLI, absent output requirement, external trust pins, readonly reopen mounts, failure retention, and the S1/P01 distinction. Self-review against spec; report exact files, RED/GREEN logs, remaining concerns and commit. Controller produces a BASE..HEAD diff package and dispatches an independent spec/quality reviewer. Fix material findings before proceeding.

- [ ] **Step 6: Actual handoff integration and checkpoint.** Controller or assigned integration worker freezes the reviewed public closure and uses unique new containers/volume. Import the real sealed archive without mounting original checkout writable, then reopen it from a different read-only container. Record before/after volume identity, fixed image/config/argv, all source bytes/modes and exact final output sets. Keep logs/resources even on failure and retry only in new generations. Publish evidence summary, update changelog and P01 S1 status, commit and push to authorized remote branch; keep P01 open for S2–S6.

**Controller completion test:** S1 is complete only after independent review and real archive import/reopen pass. This plan does not close P01, P02, historical qualification or formal system acceptance.
