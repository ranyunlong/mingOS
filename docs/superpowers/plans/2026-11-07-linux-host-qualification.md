# 当前宿主 Linux 构建与资格实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从当前 Windows + Docker Linux 环境交付新的真实 MingOS 构建，并完成持久更新 Task9 与同盘双启动证据。

**Architecture:** Windows 负责协调和导出，Linux 承担构建、POSIX 存储、checker 与 guest 执行。联网来源准备与禁网正式资格分开，新代不依赖旧 APFS/cache，也不修改历史证据。

**Tech Stack:** Rust1.99.1、Gradle8.14.5、Kotlin2.3.20、JDK21、Node、Python3.12+、Docker Linux、QEMU TCG/Weston/Pixman、guest ext4。

**Spec:** [新宿主资格设计](../specs/2026-11-07-linux-host-qualification-design.md)。Task9 原合同见 [持久更新计划](2026-11-04-persistent-update.md)。

## 全局约束

- 历史 driver/freeze/lock/失败盘只读保留；新代命名 `persistent-update-20261107-linux-<唯一ID>`，不覆盖原 image/tag。
- 正式执行 UID65534:GID65534、2CPU/4GiB、pids256、禁网、read-only、cap-drop/no-new-privileges、单写者 target；Rust `+1.99.1 --offline --locked --release`。
- guest2vCPU/2048MiB、watchdog300秒、pointer5秒、日志8MiB；boot900秒/轮、slack45秒、pair1920秒、container1950秒不变。
- 失败停止并保留，不自动重试或扩大预算；本计划完成也不自动令 `finalSystemAccepted=true`。
- 下列命令中 `/repo` 为本代 Linux 冻结副本；`/prep` 为新代执行器目录。现有接口按 source-check、freezer、build、runtime、pair 和 export 分离；Windows 公共入口负责挂载、阶段锁存与收据，不能把它们合并成旧计划中的单一 `--stage` 接口。

## 阶段1：实际工具链镜像与依赖来源

**Files:** 读取 `scripts/linux/Dockerfile`、`Cargo.lock`、`scripts/boot/{Dockerfile.recovery,recovery-packages.lock,packages.lock,infra-packages.lock}`；新增 `scripts/boot/Dockerfile.linux-host-tools` 和 `scripts/boot/linux-host-packages.lock`；证据写入本代 `source-preparation/`。

- [x] 完成真实开发镜像构建，保留日志与实际 image ID（见 [2026-11-07 环境记录](../../development-environment-20261107.md)；尚未封存正式 deb 闭包）：

```powershell
docker build --pull=false --progress=plain -f scripts/linux/Dockerfile -t mingos-linux-win-20261107 scripts/linux
docker image inspect mingos-linux-win-20261107
```

- [x] tools Dockerfile 从实际固定 digest 构建，独立新 lock 下载280个 deb，保存 SHA、installed-packages、apt sources、QEMU/Weston/OpenSSL/kernel 版本。固定 image ID 与真实 smoke 见环境记录；签名索引原件尚未封存，不宣称完整离线 APT 闭包。
- [x] 专属新 Linux cache volume 联网锁定 fetch、禁网 metadata 均返回0，完整缓存 hash 清单已保存。target 没有编译产物，但 metadata 生成 `.rustc_info.json`；严格空断言失败保留，正式构建须另建字面空 target。
- [x] 开发镜像实际运行、boot 三工具动态库、280个 deb hash、OpenSSL 正负验签及无 kernel QMP smoke 已验证并独立复核。仅此工具准备不能准入 Task9；正式工具链闭包/freeze 和成功冷构建见阶段3、4。

## 阶段2：真实 Kotlin 来源

**Files:** `scripts/build-kotlin.mjs`、`scripts/wasm-sdk-adapter.mjs`、`gradle/wrapper/gradle-wrapper.properties`；新增 `scripts/boot/build-linux-host-kotlin.mjs`，输出本代 `kotlin/` 及 `artifacts/{read-file,image-viewer,persistent-update-demo}/` 的受控来源包。

- [x] wrapper 下载校验 Gradle8.14.5 SHA；实际 Linux JDK21/Node/compiler/Maven/Wasm 依赖完整清单与逐包前后变化已保存。Windows wrapper 下载属于来源准备，不能代替正式禁网证明。
- [x] 新 runner 调用当前 adapter/打包约束，Gradle 加 `--offline --rerun-tasks --no-build-cache --no-configuration-cache`；为 read-file/viewer 保存与更新 fixture 同等的 compiler.wasm 和真实 receipt。六包真实 Linux 禁网强制重编及独立重放通过，包含下列五包及 text-entry-demo：

```sh
node scripts/boot/build-linux-host-kotlin.mjs read-file
node scripts/boot/build-linux-host-kotlin.mjs image-viewer
node scripts/boot/build-linux-host-kotlin.mjs persistent-update-demo old
node scripts/boot/build-linux-host-kotlin.mjs persistent-update-demo new
node scripts/boot/build-linux-host-kotlin.mjs persistent-update-demo absent-write
```

- [x] 核对每次任务实际执行、命令/日志/退出码、工具链及输入前后 hash、adapter 重放、ABI/import/capability、raw/packaged/manifest hash；来源包108个文件的完整导出 hash 已独立核对，runner14项控制通过。详见环境记录中的实际代与摘要。

## 阶段3：新执行器、存储准入与完整冻结

**Files:** `scripts/boot/linux-host-qualification.ps1` 与 `linux_host_qualification.py` 保留 source-check 和共同安全控制；`linux_host_storage.py` / `linux_host_freeze.py` 承担实际存储和冷冻结，`linux_host_build.py` 执行十步构建；`new-linux-host-build.ps1` / `snapshot_linux_host_source.py` 提供公共冷构建入口。独立 runtime freeze 由 `linux_host_runtime.py` 与 `new-linux-host-runtime.ps1` / `snapshot_linux_host_runtime.py` 管理，历史 driver 的 Darwin/APFS、缓存与原始证据不改写。

- [x] 分离上述实际接口，固定镜像、请求与有效 mount、nonce/CID/权限、只读源码/registry、UID65534/0700 转交和首失败锁存；公共入口不依赖开发者硬编码工作区。Windows source-check wrapper 35 项、公共冷构建 PowerShell 25 项与快照 4 项控制通过。
- [x] 为拒绝网络、错误 UID/owner、symlink/目录替换、额外 writer、缺空间、错误 image/source/fixture hash、非唯一 summary 和失败后续跑建立真实控制反例；固定 Rust 镜像综合 63 项及后续冷构建封存 30 项控制通过，不以 mock/import 检查代替准入。
- [x] 实际 volume 完成 create/write/fsync/rename/dir-fsync/delete、owner/mode/no-follow/单链接、锁竞争与独立容器 reopen，记录 Windows 承载盘、Linux filesystem、空间及峰值预算。失败代保留；成功冷代 `fd90bae03bd34a57` 使用全新字面空 target 与独立冻结。
- [x] 冷冻结绑定完整源码、Cargo registry、六包真实 Kotlin、重放输入、镜像/Node/工具链及容量收据；runtime freeze 另绑定成功十步结果、真实几何 Cargo ELF 来源、原 checker/native 输入、实际 source-v2 探针构建和固定 tools 文件闭包。源码/输入前后集合与原始 bytes hash 已核对，review 与 bundle 在后续 runtime 阶段各自绑定。现有 Linux 接口为：

```sh
python3 -B scripts/boot/linux_host_freeze.py --stage storage-probe --root /prep
python3 -B scripts/boot/linux_host_freeze.py --stage storage-reopen --root /prep
python3 -B scripts/boot/linux_host_freeze.py --stage prepare --root /prep --repo /repo \
  --cargo-home /cargo-cache/cargo --packages-root /packages \
  --image-receipt /runtime/rust-image.json --capacity-receipt /cold-receipts/capacity.json \
  --node /runtime/node --node-sha256 "$NODE_SHA256" \
  --toolchain-receipt /runtime/toolchain-observation.json
python3 -B scripts/boot/linux_host_build.py --root /prep --repo /repo \
  --freeze /prep/freeze.json --cargo-home /cargo-cache/cargo \
  --target /target --packages-root /packages
```

上述路径由实际冻结与挂载决定，不能复用失败代。Windows 公共入口 `new-linux-host-build.ps1` 接受 `-Repo`、`-EvidenceRoot`、`-RuntimeInputs`、`-PackagesVolume`、`-CargoVolume`、`-BackingVhd` 与可选 `-StartBuild`，负责上述顺序及异步构建收据；`build-started.json` 只表示启动，须独立检查最终结果。

- [x] 独立核对控制反例、真实存储、完整 freeze 和最终阶段产品；成功冷代的独立核验见 `artifacts/environment-rebuild-20261107/cold-independent-verification/`。历史 APFS 资格与本代 Linux 合同分别保留，不以新环境冒充历史恢复。

## 阶段4：正式 Task9 构建与真实进程矩阵

**Files:** 新 Linux 执行器；现有 `crates/ming-app-host/{src,tests,examples}`；本代 `build/` 原始证据。

- [x] 禁网固定镜像内由 `linux_host_build.py` 串行执行每个1800秒上限的命令，首次非零停止：

```sh
cargo +1.99.1 test --offline --locked --release -p ming-app-host -j1 --all-targets
cargo +1.99.1 check --offline --locked --release -p ming-app-host -j1 --all-features --all-targets
cargo +1.99.1 check --offline --locked --release -p ming-app-host -j1 --no-default-features --all-targets
cargo +1.99.1 build --offline --locked --release -p ming-app-host -j1 --bin ming-app-host
cargo +1.99.1 build --offline --locked --release -p ming-app-host -j1 --example prepare_persistent_update_fixture
```

- [x] 设置实际 Host/helper/bootstrap/old/new/absent Wasm 路径（沿用历史 `run-stage.py:797–802` 的环境变量名称）；每项900秒显式执行三个 `ming_desktop::persistent_update_runtime` controller：`migrated_and_absent_write_commit_only_after_real_ready_close_reap`、`absent_nonempty_runtime_failure_rolls_back_original_missing_namespace`、`killed_host_recovers_in_independent_process`，命令为 `cargo +1.99.1 test --offline --locked --release -p ming-app-host --lib <完整测试名> -j1 -- --exact --nocapture --ignored`，各唯一 summary 精确 `1/0/0/0`。
- [x] 执行 public 与完整父恢复矩阵，分别精确 `1/0/0/0`、`18/0/1/0`：

```sh
cargo +1.99.1 test --offline --locked --release -p ming-app-host --test persistent_update_runtime separate_process_roles_and_real_stable_host_refuse_active_update_without_writes -j1 -- --exact --nocapture --ignored --test-threads=1
cargo +1.99.1 test --offline --locked --release -p ming-app-host --lib persistent_update::recovery_tests:: -j1 -- --nocapture --test-threads=1
```

- [x] 验收实际匹配数、退出码、完整日志、源码稳定与 Host/helper ELF、Kotlin 来源绑定。`persistent-update-20261107-linux-fd90bae03bd34a57` 于 UTC06:27:28–06:48:17 完成全部十条命令，退出0、非OOM；all-targets 为773 passed、0 failed、24 ignored、58组，三个 controller 与 public 各 `1/0/0/0`，recovery 为 `18/0/1/0`。零匹配或 ignored 未执行不计显式控制通过；实际 ELF 闭包、签名及 guest 资格另由阶段5验证。

前代 `f6a4e8d8e9c44c82` 在前五条成功后因 Cargo 正常硬链接与单链接封存门冲突而失败，未执行后五条；其源码、target、日志和阶段结果保留。修复仅对固定两产物做受限独立复制、原子替换和两时点封存。成功代的 pre-explicit 与最终 Host 摘要分别绑定各自 seal，不能跨时点混用；冷 target 与冻结仍保持原字节。

## 阶段5：bundle、无 guest 准入、同盘双启动与独立导出

**Files:** 新执行器、现有 boot bundle/checker/profile；本代 `bundle/`、`admission/`、`pair/`、`export/`、`verify/`。

- [x] 全新 runtime `5f056322a31e4974` 完成真实 fixture、独立 review、bundle 及无 guest admission；UTC07:02:41–07:02:57 admission 退出0、非OOM，freeze SHA256 为 `ddf9e15f153b7b5c2eda9ebe2940f57191ef2f9b7552eee8c2efaeb6bbd6c881`。实际7个 ELF 依赖、Ed25519正负验签、完整 gzip/newc 必需字节、tmpfs真实子进程锁/fsync/socket、暂停无kernel QMP退出、guest Python runtime copy、原checker24项与driver31项通过；VM边界拦截一次并证明首失败阻止第二轮。此项不表示 guest UID namespace 或双启动通过。
- [ ] 新建128MiB盘，使用 `linux_host_pair.py --root /prep --repo /repo`；boot1五步、boot2两步，保持原输入和全部时间/资源参数。首次失败不跑第二次，保留盘/日志；禁止自动重试/格式化。验证第一次完整更新生命周期和正常同步卸载，第二次同盘身份、版本、私有数据与 catalog。
- [ ] 使用 `linux_host_export.py create|verify|replay`，独立 checker 从密封导出副本验证并保存真实结果；封存同盘身份、命令/退出码、tree/模块/hash/signature、UI因果和正常关机证据。当前公开可迁移导出仅完成含完整冻结/探针/原checker强raw链的合成控制，未完成真实成功pair的导出与重放。仅在其明确合同通过时记录 `persistentUpdateNormalRebootVerified=true`，继续 `finalSystemAccepted=false`。
- [ ] 更新 `docs/continuous-development.md` 与原持久更新计划的当前边界并提交/推送真实完成阶段；另行列出已批准但尚未完成的 qualification 资产恢复工具与完整交付项，继续实现，不以本计划的测试/文档结束整体交付工作。

现有 Linux runtime 接口为 `linux_host_runtime.py --stage prepare|fixture|bundle|admission --root /prep --repo /repo`；`prepare` 还须显式提供成功冷构建、target、六包、checker、两轮输入、tools receipt/manifest 与 probes 的全部路径。Windows 公共入口 `new-linux-host-runtime.ps1 -Stage new` 消费 `-Repo`、`-EvidenceRoot`、`-RuntimeInputs`、`-ColdStatePath`、`-AssetsVolume`、`-PackagesVolume`、`-ToolsManifestName`，生成新 `generation.json`；随后使用 `-StatePath` 顺序执行 `storage-probe`、`storage-reopen`、`prepare`、`fixture`、`bundle`、`admission`、`pair`、`export`、`verify`、`replay`。`bundle` 通过 `-ReviewPath` 消费独立批准且字段精确匹配的 review，入口不自行批准。每阶段只允许首次尝试，失败代不续用。

阶段5的实际边界：`63c6be41bb5d4f8d` 因 tools manifest2 漏列实际 Rust ELF 的 `libgcc_s.so.1` 在 admission 拒绝；保留失败后使用同一固定镜像重新观察6042文件的 manifest3，再创建上述成功 admission 代。该代随后首次实际 pair 已启动 boot1：kernel6.12.113、virtio/DRM/input、cgroup、数据盘 UUID 挂载及 strict smoke 到达，但 guest uptime9.095秒因 Weston socket 未就绪失败，RawGuard 立即回收进程树，boot2 未运行。实际 initramfs 的隔离 userspace 复现 `weston.ini` CRLF 导致 `malformed section header`；现仅132→122字节 LF规范化、精确属性规则与有界 guest-error 日志诊断修复通过控制，尚未证明修复后的真实 guest 成功。原盘、source/prep/work、容器、raw/QMP/kernel/scanout 与首失败证据保留，无正常关机或正常双启动成功记录。

公共入口新代 `b471` 在 `new` 阶段复制单条收据时因 PowerShell `cp` 参数记录展开失败，未启动 guest；主执行者已完成入口修复，独立21+25项控制与代码复核通过，实际执行须使用新代继续。不能把控制通过或该失败代记为新的 runtime、pair 或 export 通过。所有上述阶段保持 `finalSystemAccepted=false`。
