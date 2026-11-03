# Persistent Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 子代理仅按用户已有授权使用。本文仅准备实施计划，不触发源码修改、构建、提交、Docker 配置变更或 VM 执行。

**Goal:** 同一非 bootstrap 应用经明确确认更新、一次完整新版本运行确认和正常重启后保留新版本与数据；确认前失败可验证恢复旧状态，进程中断后通过显式恢复入口完成正确的回滚或提交清理。

**Architecture:** catalog schema v3 原始字节不变；一个最大 4 KiB 的外层 `desktop-update-pending-v1.json` 协调既有 registry update journal 和私有数据迁移。外层 `prepared` 只允许恢复旧状态，持久 `commit-decided` 只允许完成新状态；普通严格重开不修复活动事务。数据原缺失、已有空树、已有非空树均为正式支持路径。

**Tech Stack:** 现有 Rust 1.99.1/Wasmtime、Kotlin/Wasm SDK6、Python 3.12+ boot tools、Linux ext4、固定禁网 Docker、QEMU TCG/Weston/Pixman；不增加第三方依赖。

**Spec:** [持久更新与失败恢复设计](../specs/2026-11-04-persistent-update-design.md)，已提交于 `7126a5b`；执行时同时读取当前文件及已有更新迁移设计。

## Global Constraints

- 持久 registry 根 `/data/mingos/install-root`、catalog 为其 `catalog.json`，data root `/data/mingos`；恢复参数由可信调用者提供，记录中的路径绑定不作为打开指令。
- 更新对象为同 packageId 的 active 更高版本、SDK6、UI ID2..4；bootstrap ID1 的版本/指纹/Wasm、catalog 原字节/title/UI ID/autostart、trust 原字节保持不变。removed 对象不能更新。
- outer 最大 4 KiB，inner 现有上限 16 KiB，catalog 16 KiB、registry 64 KiB；完整私有树遵守 8 MiB/1024 项/16 层现有边界。缺失与空目录不同；符号链接、特殊文件、硬链接和读取期间替换均拒绝。
- `prepared` 在写任何候选/迁移业务状态之前同步并回读；持久 `commit-decided` 在清理旧 backup 之前同步并回读。提交之后的输出失败不回滚磁盘、不显示更新未保存。
- 所有正常/恢复入口均按 registry 根锁→data-root authority 顺序取得或复用现有排他权。恢复不创建缺失根、锁、policy、trust、registry、catalog 或原数据。
- 活动事务阻止安装、卸载、purge、第二次更新与目标第二运行代；普通 `open_existing` 仍只读拒绝。显式恢复不处理旧安装/卸载 pending，也不把孤立 inner journal 收编为新事务。
- 原 files 不存在的更新也必须支持：试运行可写入新非空 files，回滚要受限恢复原缺失状态；原命名空间不存在时只删除认可目标树并 rmdir 已验证为空的新命名空间。
- 完整启动确认要求精确 package/version/fingerprint、UI ID/generation/window、PID、inputReady、呈现帧、正常完成、Result 接受、native exit 成功、reap、私有 session 已释放。仅 Ready 不提交。
- 原历史包路径集合与 bytes 保留；只有原本不存在的精确新候选目录可作为合法增量，原已存在的候选仍受旧内容摘要约束。
- 更新器不改兄弟私有数据、公共文件或公共授权。协调记录必须绑定公共 policy slot 更新前的存在性及原始字节 hash；原先存在的 slot 丢失或变更须拒绝，首次缺失仍为默认空授权。运行中兄弟可自行写数据；不变性测试使用静止 sentinel。新 fingerprint 继续匹配原公共授权策略，不继承旧 UI ID 的权限。
- 物理鼠标专项暂停，旧 profile/checker/evidence/失败盘不改写；`finalSystemAccepted=false`。进程中断恢复、正常重启和断电保证分别记录，本阶段不声称断电恢复。
- candidate2 的 1214 项冻结文件与其已有 bundle 保持原样。实现必须在独立开发 checkout 中进行，并新建源码冻结、Host/helper、fixture、review、bundle 和资格盘；新 checkout 的具体创建遵守当前工作区授权与工具规则。
- 当前只授权计划准备。实施时核对已有授权的具体构建、盘/卷/VM 和预算；计划中的后续命令不自行扩大授权。授权已覆盖的常规本地检查与任务步骤不重复请示。

## 已核实入口与文件划分

| Task | 文件 | 可独立检查的结果 |
| --- | --- | --- |
| 1 | `src/app_data.rs`、`tests/app_data.rs` | 完整只读树观察、无创建恢复 authority |
| 2 | `src/app_data.rs`、`tests/app_data.rs` | 含原缺失状态的事务数据准备、回滚和提交清理 |
| 3 | 新 `src/persistent_update.rs`，`src/{lib,persistent_install}.rs`，新 `tests/persistent_update.rs` | outer schema、保留内容摘要、严格事务快照 |
| 4 | `src/package_install.rs`，相关 registry 单测与 `tests/package_install.rs` | 受限 coordinated journal 接口，普通模式兼容 |
| 5 | `src/persistent_update.rs`、新 `tests/persistent_update.rs` | 正常协调器、显式恢复、真实故障和跨进程矩阵 |
| 6 | `src/{ming_desktop,desktop_native,desktop_catalog}.rs`、桌面原生/控制器单测 | 真实确认、Dock 试运行、完整确认、提交后输出分类 |
| 7 | 新 `examples/prepare_persistent_update_fixture.rs`，新 `samples/persistent-update-demo/`，构建入口与集成测试 | 确切签名 fixture、受控 helper、真实 worker 验证 |
| 8 | boot profile/guest/receipt/checker/driver 及其 tests | 独立双轮合同、合成反例、完整冻结和执行准入 |
| 9 | 新阶段 artifacts、阶段记录与本计划 | Linux 当前源码验证、独立正常双启动与原始重放 |

上表 `src`、`tests`、`examples` 均相对 `crates/ming-app-host/`。任务按 1→2→3→4→5→6→7→8→9 执行；接口消费者开始前检查生产者实际测试。阶段提交只包含该阶段源码/测试/必要文档，不强制提交大体积 artifacts。

现有 `app_data::snapshot_private_read_only` 只检查非空、平面、最多32项/64 KiB 的卸载 fixture，不能承接完整树合同。`PackageStorageAuthority::open_default_private` 会打开/创建 policy 目录，不能当作无创建恢复入口。`package_install` 普通 update 可能自动恢复并保留 RecordPublished 的试运行机会；新协调路径必须绕开该策略。

现有 `persistent_install::{open_existing,snapshot,snapshot_inner,collect}` 负责严格快照，`ming_desktop::{begin_install,respond_storage_prompt,launch_resolved,finish_update_startup,run}` 是更新分发和恢复接线点。`UpdateStartupEvidence::confirms` 已存在。`lib.rs` 当前 Linux+desktop 才导出 `persistent_install`；新协调模块采用同一 cfg，保持无默认 features 构建边界。

## 执行与验证约定

以下命令在固定 Rust 1.99.1 的 Linux checkout 中执行，采用已有驱动、`--offline --locked` 和单写者 target。执行前检查实际镜像 ID、工具链、空间、挂载和源码 hash；不假定历史标签或卷可用。Linux `cfg` 跳过、零匹配测试、合成结果和计划中的预期均不能写作 runtime 通过。

```sh
cargo test --offline --locked --release -p ming-app-host --test app_data -j1
cargo test --offline --locked --release -p ming-app-host --test package_install -j1
cargo test --offline --locked --release -p ming-app-host --test persistent_install -j1
cargo test --offline --locked --release -p ming-app-host --test persistent_update -j1
PYTHONPATH=scripts/boot python3 -B -m unittest test_profiles test_boot test_infra test_wayland_receipt -q
```

记录完整原始输出、实际匹配数、退出码和运行前后源码 hash。新 API 在下列任务中定义，未定义前的符号缺失可作为首个 RED；之后必须使用行为反例证明 RED，而非持续依赖编译错误。`Result` 在 app_data API 指其既有 `AppDataError` 结果，其余 Rust 接口使用 `anyhow::Result`，事务分类接口另行显式返回 `UpdateFailure`。

### Task 1: 完整私有树摘要与无创建 authority

**Files:** 修改 `crates/ming-app-host/src/app_data.rs`、`crates/ming-app-host/tests/app_data.rs`；单测放在 `app_data.rs`，以使用既有 pinned `platform::{Root,Directory}`。

**Interfaces:** 新增 Linux 内部类型与方法，全部带严格 camelCase serde；摘要版本固定1，hash 使用 SHA-256。`files=None` 表示缺失，`Some(TreeDigest)` 可表示空树。

```rust
pub struct TreeDigest { pub entries: u32, pub bytes: u64, pub sha256: String }
pub struct PrivateUpdateBaseline {
    pub schema_version: u32, pub package_id: String,
    pub namespace_exists: bool, pub files: Option<TreeDigest>,
    pub non_files_sha256: String,
}
pub fn observe_private_update_read_only(root: &Path, package_id: &str)
    -> Result<PrivateUpdateBaseline>;
impl PackageStorageAuthority {
    pub(crate) fn open_update_recovery_existing(root: &Path) -> Result<Self>;
    pub(crate) fn observe_update(&self, package_id: &str) -> Result<PrivateUpdateBaseline>;
}
```

- [ ] **写行为 RED。** 增加 `update_observation_preserves_missing_namespace`、`update_observation_distinguishes_missing_and_empty_files`、`update_observation_includes_nested_empty_directories`、`update_existing_authority_never_creates_policy_or_lock`。直接创建0700测试根/0600文件，记录所有 inode/type/mode/bytes，观察前后精确比较；缺根、缺任一已有 policy 锁必须拒绝并保持缺失。
- [ ] **运行 RED。** `cargo test --offline --locked --release -p ming-app-host --lib update_observation -j1` 以及 `--lib update_existing_authority -j1`；确认实际匹配且失败原因是未实现合同。
- [ ] **实现摘要。** 递归采用 pinned no-follow 句柄，规范列表显式含目录（含空目录）和文件相对路径/类型/长度/content hash；按 UTF-8 路径字节排序，域分离前缀 `ming-private-update-tree-v1\0`，严格编码规范列表后 hash。files本身的存在性由 Option绑定，不把空树hash当缺失；非files原内容用独立域分离摘要。保留当前 8 MiB/1024项/16层、路径/owner/mode/单链接检查；重开名字和前后目录 stamp 验证读取期替换。
- [ ] **实现已有根打开。** 在现有 authority 内部构造已有 policy 的 no-create 打开和锁获取；不得通过 `grant_directory` 便利路径创建目录。registry锁先取得，再调用此方法；session中的正常更新直接复用已经持有的 authority。
- [ ] **公开只读边界。** TreeDigest/PrivateUpdateBaseline为窄public DTO，公开observe_private_update_read_only复用同一pinned观察器，不创建根/锁/目录。example可调用其输出摘要，integration可检验前后状态；它本身不授予mutation或同会话authority。协调器使用已持锁authority方法，helper只在worker停止的静止阶段读；读取期变化必须拒绝。
- [ ] **边界 GREEN。** 覆盖嵌套、空目录、中文/引号路径、1024项和8MiB边界、超限、symlink/hardlink/FIFO、读取中替换/增加/删除及authority竞争；运行上述定向测试与 `--test app_data`，保存结果后提交此任务。

### Task 2: 原缺失数据、迁移交换与受限可重入清理

**Files:** 修改 `crates/ming-app-host/src/app_data.rs`、`crates/ming-app-host/tests/app_data.rs`。保持旧 `begin_private_data_migration_with_backup`、`rollback_private_data_migration` 和 `commit_private_data_migration` 的普通模式语义。

**Interfaces:** 使用 Task1 baseline、既有 `DataMigration` 和 `PrivateMigrationTransaction`；新方法只允许父侧协调器调用。

```rust
pub(crate) struct CoordinatedPrivateUpdate {
    pub transaction_id: String, pub baseline: PrivateUpdateBaseline,
    pub backup_name: String,
}
pub(crate) enum PrivateUpdatePhase {
    BeforeSwap, SwappedUnconfirmed, RestoringOld, CommittedCleaning,
}
impl PackageStorageAuthority {
    pub(crate) fn prepare_coordinated_update(&self, context: &CoordinatedPrivateUpdate,
        migration: Option<&DataMigration>) -> Result<()>;
    pub(crate) fn observe_coordinated_update(&self, context: &CoordinatedPrivateUpdate)
        -> Result<PrivateUpdateBaseline>;
    pub(crate) fn rollback_coordinated_update(&self, context: &CoordinatedPrivateUpdate,
        phase: PrivateUpdatePhase) -> Result<()>;
    pub(crate) fn finish_coordinated_update(&self, context: &CoordinatedPrivateUpdate,
        phase: PrivateUpdatePhase, confirmed: &PrivateUpdateBaseline) -> Result<()>;
}
```

- [ ] **写缺失分支 RED。** `coordinated_absent_files_trial_write_rollback_restores_absence` 和 `coordinated_absent_namespace_trial_write_rollback_removes_only_created_namespace`：记录baseline，准备更新后经默认私有session写入非空文件，再drop所有session，rollback并断言原存在性精确恢复；兄弟/公共sentinel不变。已有namespace另放原非files元数据，rollback必须保留。
- [ ] **运行 RED。** `cargo test --offline --locked --release -p ming-app-host --lib coordinated_ -j1`；额外覆盖目标活跃session时拒绝，无业务写入。
- [ ] **实现交换。** baseline.files存在时复制完整旧树到staging、应用声明步骤、同步、files→backup→staging交换；不存在且无migration时不创建files，存在migration时从空staging执行，缺源copy/rename失败并回到原缺失。`backupExpected`严格等于baseline.files.is_some，backup名/staging/discard皆为现有合法派生名，拒绝预存碰撞。
- [ ] **受限事务观察。** baseline的observe_update不忽略任何临时前缀；事务中的observe_coordinated_update只在协调器已验证outer+inner阶段后，分离该context精确backup/staging/discard名字，再重算其余non-files摘要。未知临时名/额外条目拒绝，backup另按认可阶段校验。8MiB/1024项是每个业务files树的上限，合法files+backup/staging多副本须分别有界检查；按最多3份完整树+受控目录核对峰值空间，不把合法双份简单合计成8MiB超限，也不取消副本预算。
- [ ] **实现旧树恢复。** backupExpected=true且尚未确认时先匹配完整旧backup摘要，再使用已核实状态恢复旧files；backup缺失只能接受“尚未交换旧files精确匹配”或“已恢复旧files精确匹配”的认可阶段。缺backup且新files存在不得创建空树补救。
- [ ] **阶段来源约束。** PrivateUpdatePhase由Task4在同一operation mutex持有区内读取/校验inner后赋予：Planned→BeforeSwap、DataSwapped/RecordPublished/StartupAttempted→SwappedUnconfirmed、RollingBack→RestoringOld、StartupConfirmed→CommittedCleaning。rollback在写RollingBack前先按原阶段校验backup合法性，不能先改阶段再用RestoringOld放宽缺失判断；CommittedCleaning拒绝rollback。Task5不得直接调用data mutation跳过该检查。
- [ ] **实现原缺失恢复。** 目标session全部释放后，只删除同context绑定的files及认可staging/discard；验证整个目标树有界、no-follow/单链接/owner/mode，绝不从outer读取任意路径。保留baseline.non_files_sha256对应内容；原namespace不存在时只rmdir已验证为空的本次新namespace，出现额外条目则拒绝。每次unlink/rmdir/rename和父目录同步设测试checkpoint，重复执行与中断后重开安全。
- [ ] **实现提交清理。** 先验证当前files存在性/完整摘要等于confirmed，只有外层已持久决定且inner已StartupConfirmed的调用方才能请求cleanup；允许认可backup部分删除后的继续清理，保持新files不变。调用权限由Task4/5的受限状态接口保证，不能把此方法接到普通guest服务。
- [ ] **GREEN与数据证据。** 增加原空/非空、原files缺失但namespace存在/不存在、trial不写/写非空、migration从空树ensureDir成功/缺源失败、清理中断重入、错误context、非files漂移、symlink替换、删除/sync失败测试；运行 `--lib coordinated_` 和 `--test app_data`，通过后提交。

### Task 3: outer schema、历史内容摘要与严格事务快照

**Files:** 新增 `crates/ming-app-host/src/persistent_update.rs` 和 `crates/ming-app-host/tests/persistent_update.rs`；修改 `src/lib.rs`、`src/persistent_install.rs`。不把已有大文件整体拆分。

**Interfaces:** 新模块定义以下数据合同；所有结构严格camelCase serde，缺失/未知/重复key拒绝，hash为64位小写十六进制；新模块仅Linux+desktop导出。

```rust
pub const UPDATE_PENDING_NAME: &str = "desktop-update-pending-v1.json";
pub enum UpdateDecision {
    #[serde(rename = "prepared")] Prepared,
    #[serde(rename = "commit-decided")] CommitDecided,
}
pub struct UpdatePackageIdentity {
    pub package_id: String, pub version: String, pub fingerprint: String,
    pub manifest_sha256: String, pub wasm_sha256: String, pub migration_sha256: String,
}
pub struct UpdateLifecycleProof {
    pub app: u16, pub package: UpdatePackageIdentity, pub generation: u64,
    pub window: u64, pub worker_pid: u32, pub input_ready: bool,
    pub presented_source_sequence: u64, pub completed: bool,
    pub result_accepted: bool, pub native_exit_ok: bool, pub reaped: bool,
}
pub struct UpdateRecord {
    pub schema_version: u32, pub operation: String, pub transaction_id: String,
    pub app: u16, pub old: UpdatePackageIdentity, pub new: UpdatePackageIdentity,
    pub catalog_sha256: String, pub bootstrap_sha256: String, pub trust_sha256: String,
    pub sibling_records_sha256: String, pub preserved_content_sha256: String,
    pub candidate_content_existed_before: bool, pub data_root_binding_sha256: String,
    pub private_before: PrivateUpdateBaseline, pub backup_expected: bool,
    pub decision: UpdateDecision, pub confirmation: Option<UpdateLifecycleProof>,
    pub private_confirmed: Option<PrivateUpdateBaseline>,
}
pub fn parse_update_record(raw: &[u8]) -> anyhow::Result<UpdateRecord>;
```

- [ ] **写schema RED。** 在新集成文件加入可直接运行的边界反例，再覆盖合法prepared/commit-decided roundtrip；prepared必须confirmation/private_confirmed均None，commit-decided必须两者Some且所有生命周期字段有效。

```rust
#[test]
fn update_record_rejects_truncated_duplicate_and_oversized_json() {
    use ming_app_host::persistent_update::parse_update_record;
    for raw in [b"{".as_slice(), b"{\"schemaVersion\":1,\"schemaVersion\":1}".as_slice()] {
        assert!(parse_update_record(raw).is_err());
    }
    assert!(parse_update_record(&vec![b' '; 4097]).is_err());
}
```

- [ ] **运行RED。** `cargo test --offline --locked --release -p ming-app-host --test persistent_update update_record -j1`。实现严格解析、数值/摘要/身份/备份布尔一致性检查，并测试最大合法记录仍≤4096字节；记录只存hash与固定字段，不塞完整列表。
- [ ] **构建保留内容摘要。** 对更新前全部受控历史包规范路径/type/length/hash集合做版本化摘要，记录candidate原存在性；复验时只允许一个原不存在且精确验签的新候选目录作为增量，原已存在候选纳入旧hash。缺原文件、额外历史包、类型变化均失败。完整清单单独用于evidence，恢复判断不依赖外部evidence。
- [ ] **严格入口与事务内部入口分离。** `open_existing`遇新outer/任一inner/旧pending继续无写拒绝。新模块经显式认可的UpdateRecord进入有限事务快照；`persistent_install`只增加crate内部受限验证入口复用原边界，不能把journal加入通用忽略名单。保持bootstrap、catalog bytes、active集合/removed完整记录、锁和签名检查。
- [ ] **GREEN与兼容。** 测试未知文件/额外目录、孤立inner、缺root/锁、hardlink/symlink、错误候选增量、root/候选替换、UI ID1/removed拒绝；运行新target与 `--test persistent_install`，`cargo check --offline --locked -p ming-app-host --no-default-features` 保持cfg边界，通过后提交。

### Task 4: registry 的 coordinated journal 原语

**Files:** 修改 `crates/ming-app-host/src/package_install.rs` 和 `tests/package_install.rs`，测试private状态留在既有registry单测模块。使用Task2 context，不让registry自行决定outer策略。

**Interfaces:** 暴露crate内部journal阶段DTO（六个variant与现有UpdatePhase一一对应），以及以下方法；它们不自动调用普通 `recover_pending_update`。

```rust
pub(crate) struct CoordinatedJournal {
    pub phase: UpdatePhase, pub package_id: String,
    pub old_version: String, pub old_fingerprint: String,
    pub new_version: String, pub new_fingerprint: String, pub backup_name: String,
}
pub(crate) struct CoordinatedRegistryContext {
    pub package_id: String, pub old_version: String, pub old_fingerprint: [u8; 32],
    pub new_version: String, pub new_fingerprint: [u8; 32],
    pub data: CoordinatedPrivateUpdate,
}
impl PackageInstallRegistry {
    pub(crate) fn inspect_coordinated_journal(&self) -> anyhow::Result<Option<CoordinatedJournal>>;
    pub(crate) fn publish_coordinated_update(&self, package: &Package,
        authority: &PackageStorageAuthority, context: &CoordinatedRegistryContext) -> anyhow::Result<()>;
    pub(crate) fn rollback_coordinated_update(&self, authority: &PackageStorageAuthority,
        context: &CoordinatedRegistryContext) -> anyhow::Result<()>;
    pub(crate) fn finish_coordinated_update(&self, authority: &PackageStorageAuthority,
        context: &CoordinatedRegistryContext, confirmed: &PrivateUpdateBaseline) -> anyhow::Result<()>;
}
```

`UpdatePhase`调整到crate可见但不改变现有序列化值；`Package`/`PackageStorageAuthority`为现有类型。Task5由已校验outer构造CoordinatedRegistryContext，transaction ID保存在data中；Task4在同一operation mutex下绑定context全部旧新身份、inner backup名和target record，随后才向Task2传认可PrivateUpdatePhase，避免检查/调用间更换身份。`publish`消费者是Task5，`finish`只能在Task5验证outer commit决定之后调用；现有 `begin_update_startup` 继续完成StartupAttempted写入。

- [ ] **写RED。** 增加 `coordinated_publish_does_not_run_legacy_recovery`、`coordinated_missing_files_uses_bound_baseline`、`coordinated_finish_resumes_partial_backup_cleanup`、`coordinated_finish_rejects_cleared_journal_with_backup`。搭建旧/新真实签名包，核对已加载package在源文件被替换后仍提交相同bytes。
- [ ] **运行RED。** `cargo test --offline --locked --release -p ming-app-host --lib coordinated_ -j1`；根据日志核对匹配数与Task2测试区分。
- [ ] **实现窄原语。** operation mutex内核对内存records与磁盘、精确旧新record与context；inner先Planned，Task2数据操作后DataSwapped，再发布record并写RecordPublished。rollback先写RollingBack，恢复目标record和数据后同步清inner；finish只接受StartupAttempted/StartupConfirmed或已清inner且稳定新态，先持久StartupConfirmed才删backup，最后清inner。已清inner却有backup/临时残留拒绝。
- [ ] **故障GREEN。** 注入journal write/file-sync/rename/root-sync、record rename已发生后失败、inner unlink后dir-sync失败、partial backup cleanup等真实磁盘窗口；禁止忽略rollback/cleanup错误。运行新定向、`--test package_install`及旧 `pending_update_`/`startup_` tests，确认普通RecordPublished重开试运行与普通rollback保持原合同，然后提交。

### Task 5: 协调器、显式恢复与跨进程故障矩阵

**Files:** 修改 `crates/ming-app-host/src/persistent_update.rs`、`tests/persistent_update.rs`。所有测试fault hook仅测试可用，不进入包manifest或guest SDK。带failpoint/pipe的故障及真实跨进程矩阵放该源码的 `#[cfg(test)] mod recovery_tests`；外部integration只测试public接口且无hook。库作为integration依赖时没有cfg(test)，不能从integration调用单测钩子。

**Interfaces:** 使用既有Catalog/ContentSnapshot与Task3 record，定义UpdateFailure为 `{status:String, stage:String, error:String, persistent_block_confirmed:bool, compensation_attempted:bool}`，所有阶段返回结构化结果。

```rust
pub struct TrialUpdate { pub record: UpdateRecord, pub catalog: Catalog }
pub struct UpdateCommit { pub record: UpdateRecord, pub content: ContentSnapshot }
pub enum UpdateRecovery { RolledBack(ContentSnapshot), Committed(ContentSnapshot) }
pub fn begin_existing_update(registry: &PackageInstallRegistry,
    authority: &PackageStorageAuthority, package: &Package, app: u16)
    -> std::result::Result<TrialUpdate, UpdateFailure>;
pub fn confirm_existing_update(registry: &PackageInstallRegistry,
    authority: &PackageStorageAuthority, proof: &UpdateLifecycleProof)
    -> std::result::Result<UpdateCommit, UpdateFailure>;
pub fn recover_existing_update(root: &Path, data_root: &Path)
    -> std::result::Result<UpdateRecovery, UpdateFailure>;
pub(crate) fn recover_loaded_update(registry: &PackageInstallRegistry,
    authority: &PackageStorageAuthority, expected_transaction_id: &str)
    -> std::result::Result<UpdateRecovery, UpdateFailure>;
```

`Catalog`来自desktop_catalog，`ContentSnapshot`来自persistent_install；辅助函数在该模块内定义。recover_existing_update先无创建取得registry，再无创建authority，读outer transaction ID后调用recover_loaded_update核心；正常session begin/confirm及失败rollback复用已有实例，不另开同根锁。recover_loaded_update先核对expected ID，按同一状态表完成outer+inner处理，不能让desktop只调Task4绕过outer。状态/输出的session封闭由Task6消费失败分类。

- [ ] **写正常路径RED。** `persistent_update_preserves_catalog_and_all_old_content`、`persistent_update_commit_is_after_complete_proof`、`persistent_update_rollback_preserves_missing_layout`：旧目录四组合、migration/无migration、原新候选已存在/不存在分别对照；commit前backup必保留，commit后旧包文件仍在。
- [ ] **写状态表RED。** 精确枚举设计中outer缺失/Prepared/CommitDecided与inner缺失/六阶段的每个组合。Prepared+RecordPublished也只能rollback，CommitDecided+RecordPublished拒绝；inner孤立、旧install/uninstall pending拒绝；Prepared+已清inner仅旧状态精确一致时完成清outer。
- [ ] **实现begin/confirm。** begin先完整baseline/候选检查，再O_EXCL创建Prepared、文件sync/根sync/身份回读，然后Task4 publish并返回trial。confirm验证proof全字段、释放session后受限observe_coordinated_update的新摘要、原不变量，再原子持久CommitDecided并回读，才调用Task4 finish；验证stable新态、清outer并根sync，最后原严格snapshot。正常final检查复用持有registry的snapshot，不再次调用会重新加锁的open_existing；独立新进程才调用open_existing。
- [ ] **实现recover。** 执行设计完整状态表；保留内容/非files/hash及候选增量每次从磁盘重算。Prepared恢复原数据（含原缺失），CommitDecided只finish新态；StartupAttempted旧backup需完整，StartupConfirmed部分backup可继续清理，inner已清且backup残留拒绝。无自动worker、无循环恢复。
- [ ] **确定性故障矩阵。** 每个outer create/write/file-sync/root-sync/readback、candidate staging、inner阶段、数据rename后、record发布后、decision替换后sync/readback、cleanup/最终回读、一次补偿处均注入。区分业务失败且完整rollback→rolled-back、恢复/清理失败→fatal、决定后待完成→不rollback、无法持久阻断→commit-uncertain。不能以pre-call Err代替rename/unlink实际发生后的窗口。
- [ ] **真实进程矩阵。** 使用同一lib unit-test binary的专用子测试入口（`current_exe`+`--exact persistent_update::recovery_tests::child_process --ignored --nocapture`+环境传case/root），仅父测试创建tempdir；子进程在checkpoint通过有界pipe发READY后等待，父观察后SIGKILL，wait/reap，再第三个进程recover并第四进程严格只读reopen。child_process只供父矩阵启动，父case检查每个子命令与退出状态，不能把ignored child计作通过。每个合法阶段、原缺失/有数据、cleanup每个中断点覆盖；不得用仅改JSON的合成收据替代真实写入/中断。恢复进程自身再次中断至少覆盖缺失树删除、backup部分删除、inner/outer清理。
- [ ] **GREEN与证据。** `cargo test --offline --locked --release -p ming-app-host --lib persistent_update::recovery_tests -j1 -- --test-threads=1` 运行实际父故障/跨进程矩阵，`--test persistent_update -j1`运行public无hook集成。逐case记录旧新选择、真实磁盘摘要、临时文件/marker、sync与reap结果，不预填固定通过数；独立复核后提交。

### Task 6: 桌面更新入口、Dock试运行、生命周期及收据

**Files:** 修改 `crates/ming-app-host/src/ming_desktop.rs`、`desktop_native.rs`、`desktop_catalog.rs`；优先复用 `input_backpressure_tests`、现有原生单测和StoragePrompt安装/更新绘制，不改无关UI。

**Interfaces:** 新资格 `--qualification-receipts persistent-update-pointer-ready-v1`，有界前缀 `MING_PERSISTENT_UPDATE `；只在显式新资格且持久根/state-file/原生模式齐全时启用。复用现有begin_install/response提示和UpdateStartupEvidence，成功转换为Task3 proof后调用Task5。

- [ ] **写session RED。** 原 `persistent_install_rejects_update_and_purge_before_writes` 拆为新持久update成功合同与purge仍拒绝；覆盖ID1/removed/closing/unreaped/pending launch、错package/source替换、未呈现/错双沿、取消/重复确认不写入、journal存在时所有mutation及第二launch拒绝。普通模式原测试保留。
- [ ] **接入两层入口。** 入口和确认均核对active ID2..4+精确旧新包对象并调用begin；保持catalog原字节与标题，刷新当前解析；试运行只能由真实Dock down路径启动一次，不能由script/autostart/旧实例restore启动。历史records/trace/native索引保留，generation为ID历史max+1；旧current binding不携带上版本Ready/PID/错误/public_denied。
- [ ] **完整确认与失败恢复。** launch前持久StartupAttempted，reap和drop运行代session后逐字段验证旧已有confirms，再调用confirm。证明失败经recover_loaded_update(registry,authority,expected ID)执行完整outer+inner rollback，成功后恢复旧catalog解析，等待新用户动作，不自动重启旧worker；trial尚未launch就取消/关机也走同一Prepared rollback核心。加入同会话失败proof、取消trial及保持根锁时不自锁竞争测试。任何不能确认恢复的错误设置封闭state并拒绝后续路径。`run`对新outer保持普通只读拒绝，不插入自动recover。
- [ ] **真实因果收据。** 分开记录当前提示确认、trial-published、native Dock cause、startup-confirmed和durable commit，绑定boot/session/PID、package旧新身份、UI ID/generation/window/PID、presented revision、press/release、raw state锚点和tree摘要。commit可先于完整up输出，checker按cause绑定而非假定日志输出先后；沿用Dock down激活语义。
- [ ] **资格完成门。** 新资格state增加有界可选persistentUpdate对象，字段固定为transactionId/app/packageId/newVersion/status，status只接受trial/commit-decided/committed/rolled-back/blocked/uncertain。仅在清inner/outer并最终strict snapshot成功后发布committed；blocked/uncertain不得假报。旧profile不输出该新对象，普通产品UI仍使用既有状态提示。
- [ ] **提交后输出失败。** 注入commit/state/log失败，验证内存先对齐稳定新状态，磁盘不回滚、不凭空创建pending、无UNSAVED/RETRY误提示；保留session/资格失败。decision/cleanup失败与单纯输出失败按Task5真实边界区分。
- [ ] **GREEN。** 运行 `--lib ming_desktop::input_backpressure_tests`、`--lib desktop_native`、`--test desktop_shell`、`--test persistent_update`，以及旧install/uninstall定向；逐个proof字段反例与取消回归必须实际匹配，通过后提交。

### Task 7: 签名fixture、只读helper和真实worker恢复证据

**Files:** 新增 `crates/ming-app-host/examples/prepare_persistent_update_fixture.rs`、`samples/persistent-update-demo/`、`crates/ming-app-host/tests/persistent_update_runtime.rs`；修改 `src/persistent_update.rs`、`src/ming_desktop.rs`（真实controller单测）、`settings.gradle.kts`、`scripts/build-kotlin.mjs`仅接入新样例，不改旧fixture字节。

**Interfaces:** helper CLI明确分离 `initialize-first`、`open-existing`、`recover-update`，参数均为显式可信绝对install/data根和独立receipt路径；恢复不接收新包源，marker/hash必须已匹配。库调用分别使用既有初始化+Task5事务/恢复；helper不可用“读取失败→补包”逻辑。

新fixture初始化窄public入口定义于persistent_update，供example调用；普通事务/data/registry mutation保持crate内部。

```rust
pub fn initialize_update_fixture_first(root: &Path, bootstrap: &Package,
    old_target: &Package, key_id: &str, public_key: &[u8; 32],
    initial_catalog: &[u8]) -> anyhow::Result<ContentSnapshot>;
```

- [ ] **真实fixture固定。** 新app2包ID `dev.ming.persistent-update-demo`，旧 `0.1.0`、新 `0.2.0`、SDK6；bootstrap沿用text-entry-demo@0.1.0、ID1/autostart=[1]。样例构建属性 `-PpersistentUpdateFixture=old|new|absent-write` 分别选择独立源码/输出目录：old读取note.txt并呈现0.1.0，new读取state/note.txt并呈现0.2.0，两者均检查alpha后等待Close；absent-write用于无migration原缺失runtime，写入并同步新note再呈现。旧数据由首次helper通过父侧私有API写note.txt=alpha并同步，新签名manifest声明 ensureDir(state)+renameFile(note.txt,state/note.txt)。新旧真实raw/packaged Wasm各自绑定版本签名，rollback后实际运行old模块验证旧布局，不用只支持新路径的同一模块冒充可运行旧版本。
- [ ] **首次初始化RED/GREEN。** 只允许新盘boot1角色初始化。新入口在同盘可信父目录的唯一私有staging路径调用既有initialize_first建立仅bootstrap根，再持有该staging的registry调用crate内部install_new加入精确old_target，验证catalog为ID1/2和autostart=[1]、签名/内容/bootstrap/trust及所有同步后，整体no-clobber发布正式根并同步父目录。对用户可见正式根从未发布单包中间态；helper记录packagesInstalled=2，其余初始化计数按真实物理动作核对（catalog先初始写入再添加app2，因此catalogWrites=2、rootPublished=1，publisherProvisioned/bootstrapPublished各1），后续只读全部0。根已存在（包括空根/损坏根）拒绝首次；不把staging内seed记作UI更新。
- [ ] **helper只读/恢复合同。** open-existing仅strict snapshot+Task1 observation，不创建任何业务对象；recover-update仅显式Task5，receipt外置。首次创建必要policy锁与strict recovery获取已有锁分别验证。receipt严格区分初始化动作、恢复动作和UI更新commit，不合并计数。
- [ ] **构建与adapter来源。** Gradle离线/禁网真实构建，保存rawSha256、packagedSha256、adapterVersion、rewrites、build-info、Kotlin/Compose/SDK源码hash；实际签名和bundle module绑定packaged hash。读取实际SDK路径，按SDK-only边界验证导入，不依赖旧JSON大包或旧fixture假冒新源码。
- [ ] **真实Host RED/GREEN。** 私有desktop controller路径的真实supervisor测试放ming_desktop.rs既有内部测试模块，动态等待inputReady、确认至少一帧已呈现后Close、等待Result/native exit/reap，再验证持久commit与数据。外部新integration target仅通过public helper CLI/Task5 facade、只读observer及独立Host子进程运行，不能从example/integration调用pub(crate) controller或authority。再独立重开同根运行新包。无migration+原无files的fixture试运行写非空新数据后分别正常commit、协议/trap失败rollback与进程kill/recover，断言原namespace/files缺失恢复；测试不能只覆盖有目录路径。
- [ ] **验证。** `cargo test --offline --locked --release -p ming-app-host --example prepare_persistent_update_fixture -j1`、`--test persistent_update_runtime -j1`，内部真实controller项以 `--lib persistent_update_runtime -- --ignored --test-threads=1` 显式运行；验证每项实际执行，不能把ignored计通过。完成独立复核后提交。

### Task 8: 独立boot合同、checker、完整冻结和受控driver

**Files:** 修改 `scripts/boot/{bootlib,payload_profiles,bundle,guest,vm,cold-boot,wayland_receipt,infra,check-runtime}.py`及相应现有tests；新增 `scripts/boot/persistent_update_receipt.py`、`test_persistent_update_guest.py`、`test_persistent_update_receipt.py`。新准备目录 `artifacts/persistent-update-20261104-prep/` 内创建 `run-stage.py`、`verify-persistent-update-evidence.py`、`test_run_stage.py`、`test_persistent_update_evidence.py`、双轮inputs/review/freeze。

**Interfaces:** profile=`persistent-update`，qualification=`persistent-update-pointer-ready-v1`，guest helper=`/opt/mingos/prepare-persistent-update-fixture`，outer路径固定install根。新checker `pair(root: Path) -> dict`，CLI `--pair <root> --output <json>`；新driver `--stage prepare|build|bundle|pair|export|verify --root <absolute-root>`，import/--help不启动Docker/VM。

- [ ] **profile RED。** 新profile完整接入review/session合同、PERSISTENT_PAYLOAD_PROFILES、双轮hash/角色、bootlib.qemu_command的独立kernel前缀/角色、payload验证、bundle脚本集合/qualification、guest、check-runtime和checker，相关文件均明确修改；禁止套用既有 `data-text-update` 的 `PERSISTENT_UPDATE_INSTALL_ROOT` 旧常量含义。旧update、install、uninstall的hash、helper和角色规则不变。guest先验证挂载盘身份，boot1才initialize-first，boot2只有open-existing，不传更新源或events，不调用recover。
- [ ] **固定实际输入与POWER门。** 在新1280×800两应用场景核对当前Shell/Dock函数：boot1为INSTALL→更新确认→app2 Dock→app2 Close→POWER共5步，boot2为app2 Dock→POWER共2步；每步保留leading ABS、5秒pointer-ready、配对双沿和呈现条件。infra.InputPlan新增默认false的persistent_update_gate参数，仅已验证新profile可启用；该模式允许close步expect除原closed条件外匹配Task6 persistentUpdate完整对象的精确app/package/version/transaction及committed状态。validate_state严格校验可选对象，事务ID绑定trial收据，等待任一项不符不推进POWER。旧模式继续拒绝新expect字段。加入closed但尚未commit、错误事务/版本、blocked和commit正确后推进的测试；不能用任意额外sleep或脚本launch掩盖。坐标由实际几何单测生成并冻结。
- [ ] **guest收据。** boot1.pre是旧0.1.0+note.txt，trial阶段新0.2.0+state/note.txt且outer Prepared/inner待确认，boot1.post稳定新态且所有journal/backup无残留；boot2.pre/post与boot1.post安装内容/新数据精确一致。envelope独立绑定role/marker链/UUID/boot/session/PID/paths/raw offsets。init计数按Task7，不把helper first seed与UI一次更新混淆。
- [ ] **raw checker正反例。** 正例包含commit早于完整up输出；反例覆盖缺确认/旧提示/错帧/错press、script/autostart启动、未完整生命周期却commit、错误generation/PID/clock、错误旧新版本/签名/原候选存在性、保留历史删除、空/错/多数据文件与空目录变化、inner/outer/backup残留、boot2补包/迁移/恢复、初始化非0、不同盘/bundle/marker、raw offset断裂/截断、forced stop/缺sync/unmount/PowerDown。对actual sibling sentinel/public授权不变的证据绑定Task5/7，不用合成收据替代磁盘测试。
- [ ] **完整源码冻结。** 使用 `git ls-files` 加实际未跟踪新源、编译include_bytes资产、`.cargo`、SDK/Compose及WASI runtime/coroutines源码、Gradle/settings/build脚本、adapter、所有模块/fixture/helper/checker/inputs/review/lock与实际执行器。捕获单次读取原bytes，明确新增文件集合校验；构建前后、执行前后hash/集合核对。原有driver只按crates/scripts/samples/probes枚举不足以覆盖新Kotlin依赖，不照抄遗漏；prepare/build/bundle结果按完整产物集合串联封存。
- [ ] **driver与预算。** 固定UID65534、禁网、read-only源码、cap-drop/no-new-privileges、2CPU/4GiB外层、guest2vCPU/2048MiB、300秒watchdog、5秒pointer等待、8MiB日志和旧worker限制。沿用实际driver公式 `minimum=300+steps*(45+5)+120`，boot1五步=670秒、boot2两步=520秒，均小于每轮900秒；另冻结既有vmSupervisorSlackSeconds=45、pairTimeout=2*900+120=1920、containerTimeout=2*900+150=1950。实际runner计时须与该合同一致；不满足则停止并给出具体差额，不能隐式加预算。第一轮失败不跑第二轮，保留盘和原日志，不自动重试/格式化。
- [ ] **真实无guest准入。** 在实际拟用tools容器内执行完整preflight和最终checker的真实正/负验签，核对OpenSSL二进制/动态库来源hash和只读容器路径；检查UID可用空间≥2GiB、运行根tmpfs/QMP/lease、协作writer锁、实际绑定路径权限与安全退出。不得只测模块import就推断完整依赖可用；准入测试拦截VM边界。输出存储采用当前已验证且获授权的路径，缺空间时不清卷/改Docker配置绕过。
- [ ] **GREEN。** `PYTHONPATH=scripts/boot python3 -B -m unittest test_profiles test_boot test_infra test_input_lifecycle test_wayland_receipt test_persistent_update_guest test_persistent_update_receipt -q`，再运行新checker/driver unittest；旧缺失冻结探针单独报告，不换成新探针假通过。完整准备独立复核后提交，仍标formalRunAuthorized与scope事实，不伪造reviewed。

### Task 9: 当前源码Linux验证、独立正常重启资格与归档

**Files:** 新 `artifacts/persistent-update-20261104-qual1/`，更新 `docs/continuous-development.md`和本计划的实际完成记录。任何失败都新建独立代保留，不能回写旧成功或卸载候选。

- [ ] **执行前确认事实与授权。** 在独立实现checkout核对源码/lock/fixture/checker/driver冻结、镜像和依赖、空间/安全退出/锁、实际动作与既有用户授权是否一致。构建、VM、盘/卷动作未获覆盖则完成全部可审准备后报告具体缺项；本文不替代该授权。
- [ ] **正式当前构建。** 新freeze下运行release all-targets、all-features/all-targets check、无default features check、Host/helper build、Task7显式真实worker，以及 `cargo test --offline --locked --release -p ming-app-host --lib persistent_update::recovery_tests -j1 -- --test-threads=1` 的实际父矩阵。独立核对退出码/匹配数/源码稳定、ELF动态闭包、模块adapter/signature、initramfs实际文件。失败先读原始错误，不换release为debug、增加watchdog或关掉边界来通过。

```sh
cargo test --offline --locked --release -p ming-app-host --all-targets -j1
cargo check --offline --locked --release -p ming-app-host --all-features --all-targets
cargo check --offline --locked --release -p ming-app-host --no-default-features
cargo build --offline --locked --release -p ming-app-host --bin ming-app-host --example prepare_persistent_update_fixture
```

- [ ] **正式一组双启动。** 执行Task8封存driver pair，一次新盘、同bundle、两次fresh顺序QEMU；boot1真正当前更新确认、Dock新版本、关闭完整确认和durable commit；boot2无源/无events/无初始化/无迁移/无恢复，只读重开新态、Dock运行。每轮必须worker全部自然回收、sync/unmount、kernel PowerDown。需要恢复helper才能继续的运行不能算正常重启资格。
- [ ] **独立重放与归档。** 对导出原始证据重算全文件集合/hash、签名与bundle链、各轮raw offsets、确认/Dock/生命周期因果、marker/UUID/角色/计数、内容与树摘要；复核启动输入前后不漂移。包括失败盘/原始RED、完整stderr、QMP/Wayland/raw receipts、pre/post states、process/reap证据和压缩归档hash，不以最终截图代替。
- [ ] **按实际scope更新状态。** 只有Task1..8和本Task全部实际通过，才设置 `persistentUpdateNormalRebootVerified=true`，并把显式恢复通过的具体阶段/分支另列为 `persistentUpdateExplicitRecoveryVerified` 及覆盖清单；缺任一恢复测试不设完整恢复通过。始终 `finalSystemAccepted=false`，明确TCG/虚拟输入/仪表和无断电保证。提交当前阶段源码/文档，归档大产物遵循仓库约定。

## 设计覆盖与最终核对

- 原缺失/空/非空、完整摘要、无创建authority与删除边界：Task1/2/5/7。
- 4KiB严格outer、历史包集合/候选原存在性、普通open-existing无写拒绝：Task3/5。
- 六阶段inner、Prepared固定rollback、CommitDecided只finish、部分backup清理与孤立状态拒绝：Task4/5。
- 当前提示真实确认、唯一Dock试运行、所有生命周期字段、session释放、历史generation及提交后输出失败：Task6/7。
- 严格first/open/recover分离、原始计数、真实Kotlin与独立签名、完整源码/构建/执行冻结：Task7/8。
- 不修改candidate2、独立新正式资格、无恢复普通boot2、真实进程恢复与双启动分别验收：全局约束及Task5/8/9。

此计划完成表示后续工作可逐项执行和审阅；不表示这些接口、测试、构建或资格已经完成。任何执行中发现的新窗口必须在其所属Task补充行为反例与状态分类，保持设计支持范围，不能删除无旧目录或中断恢复路径以换取通过。
