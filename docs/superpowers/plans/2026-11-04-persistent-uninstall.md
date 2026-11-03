# Persistent Uninstall Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Only use subagents when separately authorized, and respect file ownership. 本计划不增加用户审批点；已授权范围内自动完成实施、验证、独立复核和阶段提交。

**Goal:** 用户通过真实 Dock `U` 确认卸载后装 app2，正常关机后同盘冷启动仍保持 removed、无 Dock 项和 worker，既有非空私有数据完整保留。

**Architecture:** 复用持久安装根、verified registry 排他锁、pending、精确 registry/catalog 恢复及不确定状态保护；新增窄卸载事务，只有同步、交叉校验和 pending 清理均确认后返回磁盘已提交。桌面当前 ID 绑定与永久运行历史分开；新增独立 `persistent-uninstall` profile、数据 fixture、只读快照及 raw checker，旧安装资格保持原义。

**Tech Stack:** Rust 1.99.1、现有 Rust/Wasmtime Host、Kotlin/Compose SDK6、Python boot tools、Linux ext4、固定禁网 Docker 构建环境、QEMU TCG/Weston/Pixman；不新增第三方依赖。

**Spec:** `docs/superpowers/specs/2026-11-04-persistent-uninstall-design.md`（已提交于 `ae7c1fa`；执行前同时读取当前内容）。

## Global Constraints

- 持久根 `/data/mingos/install-root`，catalog 为根下 `catalog.json`；不回落 RAM，不自动补包、修复已有根或清除 pending。
- bootstrap app1 为 `dev.ming.text-entry-demo@0.1.0`、UI ID1，身份/Wasm 不变，`autostart=[1]`；正式卸载目标 app2 为 `dev.ming.install-demo@1.0.0`。
- 只新增持久普通卸载；持久 update/purge 继续写入前拒绝。普通开发 U/D/update 兼容行为保持。
- SDK6、最多4个活动应用、单可信父进程、Strict 资源边界、300秒 worker watchdog、5秒 pointer-ready 等待不放宽。
- 持久新安装从 `2..=4` 选择最小空 ID；剩余活动 ID 不变。历史 records/trace/native 索引不删除重排，generation 不归零。
- pending 最大4KiB，catalog 16KiB，registry 64KiB；原安装 `ContentSnapshot` 和 pending schema 保持原义。
- `rolled-back` 只适用于 pending 尚未完成同步清理时的业务阶段失败，必须精确恢复且同步清理 pending；持久阻断须有确认的原标记或一次补偿；否则 `commit-uncertain`、`persistentBlockConfirmed=false`。pending 清理后的最终回读失败只做一次封锁补偿并 fatal，不能回滚或重试。
- 严格重开不得创建 `.ming-installed-packages.lock` 或 `.ming-publisher-keyring.lock`；首次初始化创建锁与严格重开获取已有锁分开。
- 磁盘已确认提交后的收据/state/log 输出失败属于会话/资格失败，不能假回滚、提示重试卸载或凭空创建 pending。
- helper 各初始化动作：`boot1.pre=1`，`boot1.post=boot2.pre=boot2.post=0`；UI 安装/卸载 commit 另计。中间观察只读，初始化计数为0。
- 同 bundle、同新盘、两份预审输入、两次 fresh QEMU；第二轮无安装源、无 `--events`、无初始化/补包/重装。
- 不编辑冻结旧 checker、历史 evidence 或失败盘；QEMU 输出优先沿用此前已验证的 Docker 卷路径，不能把诊断 I/O 相关性宣称为已证明根因。
- 物理鼠标专项继续暂停；`finalSystemAccepted=false`，不声称断电恢复或完整日常系统验收。
- 执行继承用户已有自动实施、测试、提交和授权环境内真实资格的授权；不因进入新 Task 或 skill 流程重复请示。只有实际超出授权、不可替代的外部权限/环境条件才报告具体阻碍；先完成不受影响的工作。
- 本文的命令、代码片段和预期是待执行计划，不是已运行证据。Linux 专属断言须在固定 Linux 环境实际执行，零匹配、macOS 的 cfg 跳过或旧结果均不算通过。

## 文件与顺序

| Task | 文件 | 独立交付物 |
| --- | --- | --- |
| 1 | `crates/ming-app-host/src/{package_install,persistent_install,publisher}.rs`、`tests/{persistent_install,publisher}.rs` | 卸载磁盘事务、历史包重新验签、无创建严格重开与 ID 空槽 |
| 2 | 同上及现有 transaction 单测 | 创建/写入/回滚/补偿/最终回读的确定性失败边界 |
| 3 | `src/{app_data,ming_desktop}.rs`、新 `examples/prepare_persistent_uninstall_fixture.rs`、新 `samples/persistent-uninstall-demo/`、`settings.gradle.kts`、`scripts/build-kotlin.mjs` | 适配后的非空数据 UI fixture、动态 Ready→Close 测试与无写入快照/helper |
| 4 | `src/{ming_desktop,desktop_native,desktop_catalog}.rs`、`src/graphics/desktop_shell.rs` | 真实 U 确认、ID 绑定隔离、提交后输出失败、专用收据 |
| 5 | `scripts/boot/{bootlib,payload_profiles,bundle,guest,vm,cold-boot,wayland_receipt}.py`、定向 tests | 独立 profile、阶段初始化计数和原始收据 relay |
| 6 | 新 `artifacts/persistent-uninstall-20261104-prep/` 下 checker、tests、driver、inputs/review | 冻结的新双启动验收器与可审阅执行合同 |
| 7 | 新实际 evidence 目录、`docs/continuous-development.md`、本计划完成记录 | 当前源码 Linux 检查、真实双启动、独立重放和归档 |

Task 1→2→3→4→5→6→7 顺序执行；每项验证通过后做一个范围明确的提交，再推进下一项。不要为本任务主动新建 worktree 或切换用户工作区；执行前检查当前分支和已有修改，按已有项目约定保持本次改动可分离。artifact 目录若被忽略，不为提交而强制纳入大文件或历史 evidence；阶段提交包含实际源码/测试/文档，证据 manifest 保存在本阶段独立目录。

## 统一验证命令约定

下列 Rust 命令在已授权、固定 Rust 1.99.1 的 Linux checkout 内执行；使用现有 Docker 构建驱动和缓存，不假设历史 image/volume 名仍可用。先只读核对实际 image ID、工具链、磁盘空间、挂载和源码 hash，再运行命令。`--offline --locked` 失败时读取原始缺件信息，不放开网络或更新 lockfile 规避。

```sh
cargo test --offline --locked --release -p ming-app-host --lib persistent_install -j1
cargo test --offline --locked --release -p ming-app-host --test persistent_install -j1
cargo test --offline --locked --release -p ming-app-host --test package_install -j1
cargo test --offline --locked --release -p ming-app-host --test publisher -j1
cargo test --offline --locked --release -p ming-app-host --test desktop_catalog -j1
```

Python 使用支持 `hashlib.file_digest` 的 bundled Python 3.12+。本机已知路径为 `/Users/ranyunlong/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`；在 Linux 内使用该固定环境的实际 Python 3.12+ 入口，不把 macOS 路径传入容器。

```sh
PYTHONPATH=scripts/boot python3 -B -m unittest test_profiles test_boot test_infra test_input_lifecycle test_wayland_receipt -q
```

每项记录实际匹配数、exit code、源码 hash 和完整原始失败；若已有相关失败或缺件，明确区分，不用无关重试替代判断。代码片段中的新接口是本计划定义的生产者/消费者合同；执行时如发现真实现有类型冲突，先作保持语义的最小统一修正，并在同一 Task 更新本计划，不能让后续任务引用不存在的名称。

### Task 1: 窄卸载事务、精确 removed 身份与空槽分配

**Files:** 修改 `crates/ming-app-host/src/{package_install,persistent_install,publisher}.rs`、`tests/{persistent_install,publisher}.rs`；transaction 单测留在 `persistent_install.rs`，复用已有 `transaction_tests::fixture`，不创建通用事务框架。

**Interfaces:** `package_install.rs` 新增内部校验，利用已有 `parse_records`、records mutex 和 verified package：

```rust
pub(crate) fn verify_removed_transition(&self, before: &[u8], package: &Package) -> Result<()>;
pub(crate) fn load_removed_package(&self, package_id: &str) -> Result<Package>;
pub(crate) fn open_verified_existing(root: impl AsRef<Path>) -> Result<Self>;
```

它要求磁盘完整记录与内存一致，旧目标为精确活动身份，新目标只改变为 removed，其他完整记录不变。`persistent_install.rs` 定义下列类型/接口，结构体使用严格 camelCase serde，字段完整定义如下：

```rust
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RemovedPackageIdentity {
    pub package_id: String,
    pub version: String,
    pub fingerprint: String,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct UninstallCommit {
    pub schema_version: u32,
    pub status: &'static str,
    pub removed: RemovedPackageIdentity,
    pub registry_root: PathBuf,
    pub catalog_path: PathBuf,
    pub content: ContentSnapshot,
}
#[derive(Debug)]
pub(crate) struct CommittedUninstall {
    pub catalog: crate::desktop_catalog::Catalog,
    pub receipt: UninstallCommit,
}
pub(crate) fn uninstall_existing(registry: &PackageInstallRegistry, package: &Package)
    -> std::result::Result<CommittedUninstall, InstallFailure>;
pub(crate) fn uninstall_existing_with_hook(registry: &PackageInstallRegistry, package: &Package,
    hook: &mut impl FnMut(&'static str) -> Result<()>)
    -> std::result::Result<CommittedUninstall, InstallFailure>;
pub fn snapshot_removed_package(registry: &PackageInstallRegistry, package: &Package)
    -> Result<RemovedPackageIdentity>;
```

`snapshot_removed_package` 先严格 snapshot，再调用 `load_removed_package`：从 registry 的 removed record 派生历史内容路径，实际 `load_package` 读取磁盘 manifest/Wasm，核对 record 的 id/version/storage fingerprint，并对实际 stored package 调用 `verify_publisher` 验证 publisher signature。传入的预期 package 仅作额外身份对照，不能以 `verify_package_identity`+`is_removed` 或内存预期签名替代磁盘重载验签；失败不能等同缺失或成功。`UninstallCommit.status="committed"`、schemaVersion1；新 `UninstallPending` 为独立 schemaVersion1 类型，operation 固定 `uninstall`，其余字段与设计的精确身份/旧 registry/catalog hash/新 catalog hash 对齐。原安装 Pending 类型不改。

`publisher.rs` 新增窄 `pub(crate) fn open_existing(root: impl AsRef<Path>) -> Result<Self>`，复用现有 `open_lock(root, false)` 与记录校验；registry 的 `open_verified_existing` 同时使用该 trust opener 和不带 `create(true)` 的 registry lock opener。`persistent_install::open_existing` 只走这条 no-create 路径；普通 open/provision 与首次 initialize 保持原创建行为。严格重开验证两个已有锁的权限、owner、regular file、单链接及无 symlink，并仅获取其排他锁。

- [ ] **写失败测试并运行。** 在 `uninstall_transaction_tests` 新增 `uninstall_commits_removed_record_and_catalog_together`、`bootstrap_uninstall_never_creates_pending`；在原 fixture 安装 app2 后调用新事务。至少断言：目标 removed 精确身份、其他记录不变、app1仍活动、autostart `[1]`、目标不在 catalog、历史包文件仍在、pending 不存在。用前述 lib `persistent_install` 命令确认 RED 是缺失本合同。

```rust
#[test]
fn uninstall_commits_removed_record_and_catalog_together() {
    let (_temp, opened, package) = transaction_tests::fixture();
    install_new(&opened.registry, &package).unwrap();
    let before = snapshot(&opened.registry).unwrap();
    let committed = uninstall_existing(&opened.registry, &package).unwrap();
    assert!(opened.registry.is_removed(&package.manifest.id).unwrap());
    assert_eq!(committed.catalog.autostart, vec![1]);
    assert_eq!(committed.catalog.apps.len(), 1);
    assert_eq!(committed.receipt.content.apps.len(), 1);
    assert_eq!(committed.receipt.content.files.len(), before.files.len());
    assert!(!opened.registry.root_path().join(PENDING_NAME).exists());
}
```
- [ ] **实现 preflight 与准备。** 对旧根严格 snapshot；已验证 bootstrap 包不得卸载；当前目标须在 catalog 且活动/签名/版本/指纹相符；保存64KiB registry、16KiB catalog 原始字节；候选 catalog 只删目标。新 pending 最大4KiB，独占创建并同步验证后才写业务状态。
- [ ] **实现提交与回滚。** 按 `registry-uninstall`→catalog 各阶段→`commit-readback`→pending 同步删除→`catalog-final-load`→`final-readback` 返回 `CommittedUninstall`。沿用现有 `create_pending`、`replace_catalog`、`remove_pending`、`block_after_failure`；新增卸载专用 while-pending 校验，不把原“目标活动”规则改成宽松可选。只有 pending 清理前的业务失败才尝试两份精确恢复，完整旧快照相等且 pending 同步删除才给 `rolled-back`。清理后的 `catalog-final-load`/`final-readback` 失败不得进入恢复路径，只做一次封锁补偿并 fatal；补偿成功须确认标记，失败则 `commit-uncertain`/false。
- [ ] **写历史包与无创建重开反例。** removed 历史 manifest 的 id/version、Wasm 或指纹不符均拒绝；单独只篡改磁盘 `publisherSignature` 也须拒绝，即使 storage fingerprint 不变。正常历史包由独立进程重开并重新验签成功。分别删除 registry lock、publisher lock 后严格重开须失败；比较完整树证明缺失锁仍缺失且未创建任何文件。另覆盖锁 symlink/hardlink/权限异常，首次 initialize 正常创建两锁保持通过。
- [ ] **写空槽及重开失败测试，再修改分配。** `install_new_with_hook` 使用下式选槽，满4应用或没有槽时写入前拒绝，其他 ID 不变：

```rust
let id = (2u16..=4)
    .find(|id| !candidate.apps.iter().any(|app| app.id == *id))
    .context("PERSISTENT_APP_ID")?;
```

构造 `[1,2,3,4]`，卸载中间2后安装另一可信包，断言 `[1,2,3,4]` 且 app3/app4身份不变；另测最高槽复用、同包同版本重装和满槽拒绝。用独立子进程重开卸载结果和重新安装结果，不能只读同一 registry 缓存。
- [ ] **真实验收并提交。** 运行统一 Rust 命令组，检查旧安装事务、publisher、removed-history 正反例及缺锁无写入反例实际匹配并通过，`git diff --check`。保存本阶段完整结果，再仅暂存本 Task 文件并提交 `feat: transact persistent uninstall state`。没有运行 VM，不记录正常重启通过。

### Task 2: pending 创建、精确回滚与不确定性故障矩阵

**Files:** `crates/ming-app-host/src/{persistent_install,package_install}.rs` 及其 transaction 单测；不改 UI、boot 或旧资格。

**Interfaces:** 消费 Task 1 的 `uninstall_existing_with_hook`。沿用 `InstallFailure::{status,stage,persistent_block_confirmed,compensation_attempted,compensation_stage}`，`fatal()` 规则保持；业务层测试不得把初始 hook 返回 Err 冒充 rename 已完成后的失败。registry 如需细分注入，仅新增 `#[cfg(test)]` 的同步/rename hook，公开 uninstall API 不变。

可处理业务写入失败的最小测试体如下；创建、补偿和真实 rename 后失败另按下列矩阵分别构造，不能都套用此期望：

```rust
let (_temp, opened, package) = transaction_tests::fixture();
install_new(&opened.registry, &package).unwrap();
let before = snapshot(&opened.registry).unwrap();
let failure = uninstall_existing_with_hook(&opened.registry, &package, &mut |stage| {
    if stage == "registry-uninstall" { anyhow::bail!("injected registry-uninstall"); }
    Ok(())
}).unwrap_err();
assert_eq!(failure.status, "rolled-back");
assert!(!failure.fatal());
assert_eq!(snapshot(&opened.registry).unwrap(), before);
```

- [ ] **写并运行 pending 创建反例。** 逐项注入 `pending-create`、`pending-identity`、`pending-write`、`pending-file-sync`、`pending-directory-sync`、`pending-readback`。覆盖 EEXIST、空/部分标记、原 inode 被替换、创建成功后失败；逐项检查没有 registry/catalog mutation、当前失败分类、一次补偿以及准确的 confirmed flag。创建前失败且确证标记不存在才可干净拒绝。
- [ ] **写并运行业务与恢复反例。** 逐项注入 registry 临时写/同步/rename/目录同步，catalog 创建/写/同步/rename/目录同步，`commit-readback`、两份恢复、`rollback-readback`。至少一个测试先实际 rename 再使目录 sync 失败，证明即使内存已回退也恢复磁盘。两份恢复失败同时保留两条错误，不短路；成功恢复要求旧 ContentSnapshot 全部相等。
- [ ] **写并运行 pending 清理/补偿反例。** 覆盖 unlink 失败但原标记仍完整、namespace 已无标记、inode 替换、unlink 成功后目录 sync 失败，以及补偿 create/identity/write/file-sync/directory-sync/readback 失败。确认阻断分支由独立进程实际重开拒绝；未确认分支只断言当前进程 fatal、一次尝试、`commit-uncertain` 和 false，不断言下一进程必然拒绝。
- [ ] **写并运行最终回读反例。** 分别在 pending 已完成同步删除后注入 `catalog-final-load`/`final-readback` 失败，断言恰一次封锁补偿、fatal、零 rollback 调用、无重试提示。补偿确认时阻断下次严格重开；补偿任一步失败则 `commit-uncertain`、`persistentBlockConfirmed=false`，不保证下次重开拒绝。即使之后读到完整旧状态也不降级为 `rolled-back`。所有失败均无卸载成功收据、无新 worker、无自动清除/补包。
- [ ] **写并运行真实事务的数据隔离集成测试。** 在固定 Linux 环境为目标 app2、兄弟包私有目录及公共目录分别写入非空 sentinel，记录字节/hash、私有 policy 与 public grants/binding 的原始状态。通过 `uninstall_existing` 完整卸载 app2，断言三个 sentinel 均逐字节保留，兄弟私有授权和既有公共授权/binding 不变；独立进程严格重开后再次核对。不能以裸 `registry.uninstall` 的既有测试替代事务测试；卸载对象的桌面缓存清理由 Task4 另测。
- [ ] **仅修复反例证明的问题。** 对照实际错误与磁盘状态修改最小路径；保留不确定性，不把每个 I/O Err 强行归类为 rolled-back 或 persistent-blocked。重复运行统一 Rust 检查组，保持原安装故障分支通过。
- [ ] **真实验收并提交。** 形成逐阶段 expected/observed 矩阵与独立进程输出，`git diff --check` 后提交 `test: qualify persistent uninstall failure boundaries`；若修复包含生产代码，提交描述准确写出修复点。没有 VM 资格声明。

### Task 3: 非空私有数据 fixture、只读快照和独立 helper

**Files:** 新增 `samples/persistent-uninstall-demo/{app.json,build.gradle.kts,src/wasmWasiMain/kotlin/Main.kt}`，修改 `settings.gradle.kts`、`scripts/build-kotlin.mjs`；修改 `crates/ming-app-host/src/app_data.rs`；新增 `examples/prepare_persistent_uninstall_fixture.rs`，在其 example 单测和 `tests/persistent_install.rs` 覆盖 helper/只读边界；`src/ming_desktop.rs::input_backpressure_tests` 增加可动态驱动 Session 的 lib fixture 生命周期测试。现有 `prepare_persistent_install_fixture.rs` 的模式与输出不改。

**Interfaces:** 在 `app_data.rs` 定义严格 serde 类型 `PrivateContentFile { path: String, size: u64, sha256: String }` 和 `PrivateContentSnapshot { package_id: String, files: Vec<PrivateContentFile> }`；新增 Linux 父侧只读接口：

```rust
pub fn snapshot_private_read_only(root: &Path, package_id: &str)
    -> std::result::Result<PrivateContentSnapshot, AppDataError>;
```

复用内部 `package_directory_name` 与 pinned directory/`O_NOFOLLOW` 读取能力，不复制 namespace 编码、不获取/创建 grant。本阶段采用单层普通文件快照，不做通用递归：`files` 下总 entries 最多32（枚举目录、文件、隐藏项均计入上限），总读取64KiB；拒绝任何子目录（包括额外空目录）、隐藏项、临时项、symlink/hardlink、缺目录/文件和不稳定回读。先完整有界枚举、再校验，不能过滤隐藏项或目录后伪造相等；只读观察不得创建 root、package/files、policy 或 lock。普通文件清单排序，正式 fixture 精确为 `note.txt` 一个文件，内容 `alpha`（5字节，无换行）。

- [ ] **验证样例选择并写 RED。** 当前文本样例不写数据，现有 typed 私有探针写 `alpha` 后立即结束、没有桌面 Ready 生命周期；使用独立最小 Compose fixture。先给只读接口写缺目录/已存在目录、symlink/hardlink、总 entries 超32（含目录）、字节超限、额外文件、隐藏项/临时项、额外空目录及前后树相等测试，运行聚焦 lib `app_data`，确认失败由新接口缺失造成。
- [ ] **实现只读接口。** 读取现有 root/package/files 的 descriptor；不复用 `with_files` 中可能 `child("files", true)` 的路径。固定读取上限及前后元数据/字节核对，返回内容快照，任何异常都明确失败。根缺失反例必须证明仍缺失。
- [ ] **创建实际 UI fixture。** 复用 text-entry-demo 的 Kotlin/Compose Gradle 配置，root settings 注册 `:persistent-uninstall-demo`；manifest 固定 SDK6、`dev.ming.install-demo@1.0.0`、无 publicStorage/迁移。Kotlin 主体如下，窗口保持到真实关闭：

```kotlin
import ming.compose.*
import ming.sdk.MingPrivateFiles

fun main() {
    val bytes = "alpha".encodeToByteArray()
    val written = MingPrivateFiles.write("note.txt", bytes)
    check(written.code == 0 && written.directorySynced == true)
    val read = MingPrivateFiles.read("note.txt", 0, bytes.size)
    check(read.code == 0 && read.bytes!!.contentEquals(bytes))
    val result = MingTextApplication.run {
        MingColumn { MingText("Private data saved", emPx = 24) }
    }
    check(result.code == 0)
}
```

- [ ] **构建适配后的 fixture。** 将新样例加入 `scripts/build-kotlin.mjs` 的 requested allowlist、帮助文本和调用 `adaptKotlinWasi(raw)` 的适配分支；保持既有 desktop/all 默认集合，单独显式构建本样例。固定 Linux Rust 镜像未安装 Java 和 Node；使用已核实的本机 JDK、Node 与 Gradle 缓存离线构建，仅对新样例传 Gradle `--offline`，并记录实际工具版本和网络限制，再在固定 Linux Host 中运行验证。执行 `node scripts/build-kotlin.mjs persistent-uninstall-demo`；脚本无 `--offline` CLI，不虚构该参数。若单独诊断 Gradle，使用 `bash ./gradlew --offline :persistent-uninstall-demo:compileProductionExecutableKotlinWasmWasi`，不顺带 chmod。唯一 raw productionExecutable 必须经现有 `adaptKotlinWasi(raw)` 后输出 `artifacts/persistent-uninstall-demo/main.wasm`；零个或多个 raw 候选都失败。保留 build-info 的实际 rawPath/rawSha256/packagedSha256/adapterVersion/rewrites，签名、Host 测试和 bundle 都只消费 packaged Wasm；适配拒绝未知 WASI import 的行为保持。
- [ ] **动态驱动实际 fixture 的 Ready→Close。** 在 `ming_desktop.rs::input_backpressure_tests` 新增 ignored lib 测试 `persistent_uninstall_fixture_writes_private_data_and_closes`，`MING_UNINSTALL_FIXTURE_WASM` 指向上述 packaged 绝对路径。直接建立真实 Host/Session，autostart fixture，在有界 deadline 内反复 `tick()`、按需 `compose()`/`accepted(frame.sequence, true)`；观察目标当前 record 的 `first_ready_ms` 非空及 view 的实际 input-ready/presented source sequence 后，才 `perform(DesktopAction::Close { app })`，继续 tick 到正常 Result/reap、无 instance 并正常 shutdown。轮询可短暂 sleep 调度，但 Close 条件不能依赖固定 sleep。现有 `tests/persistent_session.rs` 固定脚本 harness 没有动态 Ready 后 Close 能力，不引用它替代此测试。将 packaged 绝对路径赋给 `task_fixture_wasm`，实际执行 `MING_UNINSTALL_FIXTURE_WASM="$task_fixture_wasm" cargo test --offline --locked --release -p ming-app-host --lib input_backpressure_tests::persistent_uninstall_fixture_writes_private_data_and_closes -j1 -- --ignored`，核对实际恰一匹配；要求数据写入/读回成功、directorySynced=true、firstReadyMs非空、正常 Result/reap、snapshot恰有note.txt=alpha。默认 ignored 未运行或 Kotlin 编译通过均不算通过；lib offscreen Close 不替代后续真实 QMP Close 资格。
- [ ] **实现独立 helper。** 固定开发 key 与原 fixture 相同，仅测试使用；复用严格输入读取/签名/首次 initialize 原语。CLI 固定三个模式：`initialize-first <root> <initial-wasm> <new-wasm> <source-manifest> <catalog-template> <catalog-sha256> <receipt>`；`open-uninstalled <root> <new-wasm> <data-root> <receipt>`；`snapshot-private <data-root> <receipt>`。后两者不写根/包/数据或 source。`open-uninstalled` 通过 Task1 no-create opener 严格重开，从登记 record 派生历史路径，重载磁盘 manifest/Wasm 并验证实际 stored publisher signature；冻结 packaged new-wasm 构造的内存身份仅作额外对照。验证 removed 精确身份、仅app1及私有快照。输出严格 `{schemaVersion,status,actions,content,removed,privateContent}`，初始化时 removed/privateContent 为 null；只读快照模式只输出私有快照及零初始化 actions。receipt 仍只可独占写在被观察根外。
- [ ] **真实验收并提交。** 运行只读矩阵、独立 helper 的首次/只读重开/CLI错参、stdout/file一致、仅篡改磁盘 publisherSignature、分别缺任一锁且仍缺失的测试；执行 `cargo test --offline --locked --release -p ming-app-host --example prepare_persistent_uninstall_fixture -j1` 和 helper release build。保存实际 raw/packaged 双 hash、build-info、Host/helper hash及动态 fixture运行记录，提交 `feat: add persistent uninstall data fixture and snapshots`。

### Task 4: 桌面 U 接入、ID 绑定隔离及原生因果收据

**Files:** `crates/ming-app-host/src/{ming_desktop,desktop_native,desktop_catalog}.rs`、`src/graphics/desktop_shell.rs`；对应现有 lib/native/shell 单测。消费 Task1 事务和 Task3 私有快照，不改 boot。

**Interfaces:** `DesktopOptions` 新增独立 `persistent_uninstall_receipts: bool`，仅 `--qualification-receipts persistent-uninstall-pointer-ready-v1` 设为 true；旧 `persistent-install-pointer-ready-v1` 的字段、限制和输出保持。`Session` 新增：

```rust
current_record_floor: BTreeMap<u16, usize>,
fn current_record(&self, app: u16) -> Option<&AppRecord>;
fn reset_catalog_binding(&mut self, app: u16);
fn commit_persistent_uninstall(&mut self, app: u16, package: crate::package::Package) -> Result<()>;
```

`current_record_floor` 初始空，缺省起点0；`current_record` 只查对应起点之后的该 ID 记录。成功卸载/新包绑定后 `reset_catalog_binding` 设置起点为 `records.len()`、清该 ID 的 public_denied/grants/待启动项；不动记录向量、trace、native索引或用于 generation 的全历史。普通更新不重置绑定。`StoragePrompt::uninstall_protected(id: &str) -> Result<Self>` 使用新 mode23、单 CLOSE 按钮和 `SYSTEM APP CANNOT BE UNINSTALLED`，不复用 mode12 的 CLOSE APP FIRST 文案。

原 `native_receipts_enabled` 对新资格明确取两个receipt标志之一，仍要求持久根/state-file/非offscreen；所有安装、native-input和native-launch输出在新资格会话中统一使用新前缀，便于新relay完整重放，旧资格仍输出原前缀。不得只给卸载commit换前缀而遗漏它之前的安装/Dock生命周期。

- [ ] **写并运行 session RED。** 覆盖 bootstrap 正确拒绝提示、运行/关闭/未回收目标、pending launch、取消/未呈现、确认期间身份替换、重复确认。普通 U 成功路径保留，持久模式只解除普通卸载分支的两层拒绝，update/purge维持。
- [ ] **接入磁盘事务。** 入口与确认都复核无目标活动/待启动及精确 catalog/registry身份；调用 `uninstall_existing`。非fatal失败保持全部当前绑定状态并显示 mode11重试；fatal设置现有封闭标志与明确 state_error，所有安装/卸载/launch入口均拒绝。事务成功后先发布 catalog并reset绑定，再做日志/state/收据输出。
- [ ] **实现并测试 ID 复用隔离。** `app_status`/`state_snapshot` 使用 current_record，generation保留全历史最大值+1。构造旧ID2 failed/PID/Ready、public_denied/legacy grant，卸载成功后在槽2装新包，未launch时当前state不含旧运行，第一次public launch仍走新包自己的授权；同包重装数据命名空间不变。取消/回滚不提前清状态；records及native record索引全不变。
- [ ] **测试磁盘提交后输出失败。** 增加仅测试的输出 fault hook，分别让 commit receipt、state-file 和日志输出失败。断言磁盘已卸载、内存catalog/绑定已对齐、会话fatal/资格RED、无rolled-back/重试提示、无新pending或虚假confirmed；释放registry后独立进程严格重开成功。不要把该 hook 引入事务 rollback。
- [ ] **实现原生卸载因果。** 在 native U release 和确认 release 路径记录有界 `uninstall-request`/`uninstall-confirm` 收据，字段为 app、package身份、releaseSequence、pressSequence、presentedSequence、promptRevision和before/after原始state锚点。`persistent-uninstall-commit` 绑定同一确认cause、Task1 receipt及Task3 privateBefore/privateAfter；baseline在目标停止后、业务写前读取，缺失时资格会话失败且不写业务状态；after在磁盘成功后读取，失败时与提交后输出失败同类，不回滚或造pending。仅新资格会话要求sentinel，普通产品卸载不要求note.txt。新资格输出前缀 `MING_PERSISTENT_UNINSTALL `，旧前缀和旧native输入schema不变。commit可能发生在完整up收据输出前，校验以cause绑定，不虚构日志先后。
- [ ] **验证真实 controller 路径。** 使用几何函数计算两应用 U、窗口 Close 和确认按钮；真实 matching down/up才触发，不以直接session方法测试代替原生测试。加入旧释放跨解绑/再绑定、错帧/错区域、只down、重复up反例；既有Dock down启动语义不改。
- [ ] **真实验收并提交。** 分别运行 lib `input_backpressure_tests`、`desktop_native::input_tests`、`graphics::desktop_shell` 和 package/catalog/persistent 定向测试，检查实际匹配数及旧ignored。提交 `feat: integrate persistent uninstall desktop lifecycle`。

### Task 5: 封闭 boot profile、零写入重开与阶段计数

**Files:** `scripts/boot/{bootlib,payload_profiles,bundle,guest,vm,cold-boot,wayland_receipt}.py`；`test_profiles.py`、`test_boot.py`、`test_infra.py`、`test_wayland_receipt.py`、`test_input_lifecycle.py`；需要隔离复杂relay时新增 `scripts/boot/persistent_uninstall_receipt.py` 及其专用 `test_persistent_uninstall_receipt.py`，并显式纳入bundle script hash。

**Interfaces:** 独立常量 `PERSISTENT_UNINSTALL_PROFILE="persistent-uninstall-pointer-ready-v1"`，review.profile=`persistent-uninstall`；固定root/catalog/data-root与原持久根相同，helper为 `/opt/mingos/prepare-persistent-uninstall-fixture`，receipt为 `/run/evidence/persistent-uninstall-fixture.json`；extraArgs沿用Strict长会话，替换为新qualification标识。新review使用 `persistentUninstallFixture` 严格对象和既有 `inputScripts.boot1Sha256/boot2Sha256`，无单hash回落。

`persistentUninstallFixture` 固定字段为 helper、installRoot、initialWasm、sourceWasm、sourceManifest、receipt；除上述新helper/receipt外，值分别为 `/data/mingos/install-root`、`/opt/mingos/packages/initial/main.wasm`、`/opt/mingos/packages/new/main.wasm`、`/opt/mingos/install-source/app.json`。真实私有内容期望由新profile固定为目标package的 `note.txt`、5字节 `alpha`；review冻结helper/Wasm/两输入hash，不能让review提供任意私有观察路径。

- [ ] **写并运行 profile/argv RED。** 严格接受上述路径/身份/args及 helper/Wasm hash；拒绝旧fixture字段、RAM根、events、重复/未知字段、错qualification、单hash、交换输入、unknown bootNumber、错误role/marker和输出路径别名。旧profile正反例保持。
- [ ] **实现独立profile分支。** bundle/vm/cold runner只将两输入/轮次机制扩展到明确的两个持久profile，不用名称前缀匹配泛化。guest在已确认数据盘身份后，boot1.pre运行initialize-first；boot1.post、boot2.pre/post运行open-uninstalled。boot2不传install-source、不传events、不调用任何写入helper模式；new/main.wasm 必须是 Task3 经适配、签名和测试的 packaged 字节。相同输入字节有界读取一次后共用于解析/hash/报告。
- [ ] **实现严格阶段收据。** 新content期望：boot1.pre仅app1且无removed/private；安装中间收据app1+app2；卸载后和boot2仅app1、removed精确app2、历史文件仍在且私有快照非空。envelope绑定boot/session/PID、role、marker链、disk UUID、phase、paths与raw offset。完整每项初始化actions仅boot1.pre为1，其余0；UI的install/uninstall commit计数独立。
- [ ] **实现 bounded raw relay。** 只解析新前缀与严格schema，最大行262144字节、128收据、64原生输入边沿，普通Wayland仍64KiB、完整日志预算不变；原始stderr offset/base64完整保留。新relay接受install→app2 native launch/ready→close/reap→U/confirm→uninstall commit→normal终态；普通失败/未知kind、丢输入/trace、错PID/身份/序号或截断均拒绝。
- [ ] **验证无QEMU控制流。** mock helper/process transport验证每阶段实际argv和writes计数，boot2安装/重装/source创建均不能发生；测试commit先于完整up收据的合法顺序、不同generation历史和raw身份绑定。用实际 helper 的 Linux 定向反例补充 mock：分别缺任一锁必须严格重开失败且锁保持缺失，历史 publisherSignature-only 篡改、私有隐藏项/额外空目录/entry 超限都拒绝；前后树无变化，不能以零 actions 摘要替代。运行统一Python组及新relay组，不启动VM。
- [ ] **真实验收并提交。** 保存测试计数和dry-run JSON，提交 `feat: add persistent uninstall boot contract`。现阶段不能填写真实cold-pair通过。

### Task 6: 冻结输入、专用 driver 与独立 raw checker

**Files:** 新 `artifacts/persistent-uninstall-20261104-prep/{run-stage.py,verify-persistent-uninstall-evidence.py,test_persistent_uninstall_evidence.py,input-boot1.json,input-boot2.json,review.json,freeze.json}`。所有生成物在本阶段独立目录，不覆盖旧 prep/checker。必要的可复用源码测试只在新profile分支修改。

**Interfaces:** checker提供 `pair(path: Path) -> dict` 和 `main()` CLI `--pair <evidence-root> --output <json>`，结果必须包括 `persistentUninstallNormalRebootVerified`、`privateDataPreserved`、`finalSystemAccepted`；未通过抛出具体检查错误并nonzero，不能默认false后继续通过。driver CLI为 `--stage prepare|build|bundle|pair|export|verify --root <absolute-prep-root>`，每阶段先检查上阶段冻结manifest及实际授权环境，不在读取模块时启动Docker/VM。

- [ ] **冻结具体InputPlan。** boot1按 INSTALL→ALLOW→app2 Dock→app2 Close→U→UNINSTALL→POWER，均真实pointer+left down/up；窗口Close前等app2 Ready、Close后等精确gen/PID的正常result/reap与sentinel-before，再进行U。boot2仅POWER及正常关机所需动作，不以合法坐标但app2已在运行冒充启动。使用当前1280×800几何函数实际核对位置；每次leading ABS后仍等5秒pointer-ready，按钮与提示呈现握手不改。
- [ ] **绑定私有快照与初始化计数。** 核对before/after commit、boot1.post、boot2.pre/post的privateContent精确为唯一 note.txt=alpha（5字节），清单、长度、hash、规范路径精确相等；隐藏项、额外文件/目录及 entry 超限由只读 helper 拒绝，不允许先过滤后比较。installed history文件保留，removed精确身份不可缺失；严格重开须使用 Task1 无创建锁路径并重载登记历史包验实际 stored signature。每个helper action计数按四阶段表逐字段校验，不信noWrite摘要。boot1/post与boot2/pre/post的持久content精确相等；合法不同envelope分别验证，不能整份receipt求相等或删字段。
- [ ] **实现原始因果。** 从完整guest/raw stderr重放新relay及pointer收据，QMP唯一成功回复、真实双沿、presented frame/press-release链、确认cause与commit、generation/PID/Ready/reap一一绑定；host和guest时钟不跨域比较。boot2完整state/history/trace和worker进程表无app2，不只检查最终snapshot。
- [ ] **写并运行合成正反例。** 新 `test_persistent_uninstall_evidence.py` 至少包含合法完整pair、合法commit早于up输出、合法不同envelope/有界旧历史；以及删removed记录、变版本/指纹、只改内存、空/错/多私有文件、隐藏项/额外空目录/entry 超限失败收据、历史签名篡改或缺锁的失败收据、counter1被改0、post初始化改1、boot2补包、无U/无up/错frame、跨PID/clock伪因果、提前启动或未reap卸载、错marker/盘/bundle、raw截断/offset断裂、forced stop、缺sync/unmount/PowerDown、磁盘已提交但收据缺失。磁盘篡改/缺锁的真实性由 Task1/3 的实际文件测试证明，不能以合成收据测试替代。合成通过不代表实际VM通过。
- [ ] **实现可审阅driver并冻结预算。** prepare记录源码/fixture/checker/input hash、当前image ID和配置；fixture freeze 导出 rawSha256、packagedSha256、adapterVersion、rewrites 和 build-info hash，实际签名、source manifest、测试清单与 bundle new/main.wasm 均绑定 packagedSha256。build使用固定禁网UID65534环境，bundle独立输出；pair只启动一次两轮cold runner，第一轮失败不启动第二轮，失败保留盘/日志且不自动重试/格式化。依据新七步输入实际计算外层总预算，写入review/freeze，不扩大guest watchdog/内存/CPU或pointer等待。已有授权覆盖的具体本地构建/卷/VM操作自动推进，不把reviewed字段当作新用户确认请求；只有主执行代理完成实际检查后才标记reviewed，不能复制旧签名或伪造review。
- [ ] **实际验收与阶段提交。** 运行 `python3 -B -m unittest discover -s artifacts/persistent-uninstall-20261104-prep -p 'test_*.py'`，同时运行旧raw-audit/boot定向组，记录匹配数；对driver作无VM参数/路径/失败终止测试。冻结manifest，自查spec覆盖、类型/schema及恶意输入反例。提交本计划的实际准备记录或必要源码测试，建议 `test: freeze persistent uninstall qualification checks`；不强制把被忽略的大型artifact纳入Git。

### Task 7: 当前源码 Linux 验证、真实双启动、独立重放与交付

**Files:** 新 `artifacts/persistent-uninstall-20261104-qual1/` 和本次专用卷/数据盘；验收后更新 `docs/continuous-development.md` 与本计划完成记录。不得编辑任何旧证据。

- [ ] **检查当前授权与冻结输入并自动继续。** 只读核对固定Linux镜像/缓存、磁盘空间、source/lock/helper/checker/input hash，确认与freeze相同；保留已有用户授权与具体资源范围。不从旧资格归档直接启动被修改盘；新建本阶段独立数据盘，保持失败盘不动。
- [ ] **运行当前源码实际检查并构建。** 除前面已完成的聚焦组外，固定Linux执行下列命令；只在失败、新改动或新疑点时重复已充分通过的检查：

```sh
cargo test --offline --locked --release -p ming-app-host --all-targets -j1
cargo check --offline --locked --release -p ming-app-host --all-features --all-targets -j1
cargo build --offline --locked --release -p ming-app-host --bin ming-app-host -j1
cargo build --offline --locked --release -p ming-app-host --example prepare_persistent_uninstall_fixture -j1
python3 -B -m unittest discover -s scripts/boot -p 'test_*.py'
```

核对Host/helper ELF依赖、Linux boundary检查和Kotlin raw/packaged双hash及build-info；Task3 动态 Session lib ignored 测试必须显式执行，Close 由观察到的 Ready 触发。核对 packaged fixture 实际Ed25519验签、removed历史磁盘 signature-only 篡改拒绝、两锁分别缺失仍不创建、真实事务兄弟私有/公共 sentinel 与授权保持、私有隐藏项/额外空目录/总 entries 边界的实际匹配结果。明确记录现有ignored或历史缺件，不把过滤后零匹配记为通过。依照driver的build/bundle阶段生成不可变新bundle和review。
- [ ] **运行唯一正式pair。** 使用driver的pair阶段及冻结两输入，QEMU输出置专用Docker卷。boot1完成安装/写sentinel/关闭回收/U确认卸载/正常Power；boot2严格重开，仅app1及正常Power。任一预算、输入、worker、同步或校验失败都保留RED、盘和原始stderr/QMP/guest，不修改当前证据判绿；后续如需修复，形成新freeze与新独立资格世代，不自动重跑同盘掩盖失败。
- [ ] **独立验证实际原始证据。** 运行冻结checker的 `pair()` 与CLI，比较完整结果；按证据manifest逐项重算hash，独立核对签名、输入因果、初始化计数、同bundle/盘/marker链、跨轮content和私有数据、normal terminal、worker回收、sync/unmount/PowerDown。证据输出或checker不完整时资格失败，即使磁盘状态正确。
- [ ] **导出与再核对。** export包含bundle/review/inputs/checker/源码freeze、fixture raw/packaged双hash与build-info、raw日志、盘manifest/必要盘归档、pair结果和全文件hash清单；重算 packaged hash 并核对签名、测试输入及 bundle new/main.wasm 完全一致。归档regular成员与export逐项一致，禁止混入socket、symlink或本阶段之外的文件。只读核对相关容器Stopped/exit0/OOMfalse、无遗留VM/worker。哈希包含本次实际归档和盘字节，不引用旧值。
- [ ] **记录真实完成并提交。** 只有checker及独立重放全部通过才写 `persistentUninstallNormalRebootVerified=true`、`privateDataPreserved=true`、`finalSystemAccepted=false`，在continuous-development列实际源码/Host/bundle/checker/盘/归档hash、测试数、证据路径、限制与历史RED。若失败只记录实际失败及原因，计划保留未完成项；不把文档勾选当作验收。提交 `docs: record persistent uninstall reboot qualification`。

## 完成条件核对

- Task1/2提供完整登记仅active→removed、磁盘历史包重载验签、bootstrap保护、空槽再用、无创建锁的严格新进程重开、最终回读一次补偿且fatal，以及真实事务兄弟私有/公共数据与授权保持。
- Task3/4提供适配后签名/测试的真实非空数据fixture及raw/packaged双hash、动态Ready→Close、总entries有界且拒绝隐藏/目录的无写入观察、原生当前帧确认、当前绑定清理/历史保留、提交后输出失败分界。
- Task5/6提供独立profile/两输入、四阶段计数、原始跨层因果、严格checker与破坏反例；旧安装资格不放宽。
- Task7提供当前源码实际Linux检查、新盘正常双启动和独立归档审计。所有声明以实际结果为准；物理鼠标暂停和 `finalSystemAccepted=false` 保持。
