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
- 下列命令中 `/repo` 为本代 Linux 冻结副本；`/prep` 为新代执行器目录。新 CLI 是待实现接口，须完成阶段3控制测试后才能调用，不暗示文件已存在。

## 阶段1：实际工具链镜像与依赖来源

**Files:** 读取 `scripts/linux/Dockerfile`、`Cargo.lock`、`scripts/boot/{Dockerfile.recovery,recovery-packages.lock,packages.lock,infra-packages.lock}`；新增 `scripts/boot/Dockerfile.linux-host-tools` 和 `scripts/boot/linux-host-packages.lock`；证据写入本代 `source-preparation/`。

- [x] 完成真实开发镜像构建，保留日志与实际 image ID（见 [2026-11-07 环境记录](../../development-environment-20261107.md)；尚未封存正式 deb 闭包）：

```powershell
docker build --pull=false --progress=plain -f scripts/linux/Dockerfile -t mingos-linux-win-20261107 scripts/linux
docker image inspect mingos-linux-win-20261107
```

- [x] tools Dockerfile 从实际固定 digest 构建，独立新 lock 下载280个 deb，保存 SHA、installed-packages、apt sources、QEMU/Weston/OpenSSL/kernel 版本。固定 image ID 与真实 smoke 见环境记录；签名索引原件尚未封存，不宣称完整离线 APT 闭包。
- [x] 专属新 Linux cache volume 联网锁定 fetch、禁网 metadata 均返回0，完整缓存 hash 清单已保存。target 没有编译产物，但 metadata 生成 `.rustc_info.json`；严格空断言失败保留，正式构建须另建字面空 target。
- [x] 开发镜像实际运行、boot 三工具动态库、280个 deb hash、OpenSSL 正负验签及无 kernel QMP smoke 已验证并独立复核。正式工具链闭包/freeze 仍在阶段3完成，当前不能准入 Task9。

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

**Files:** 新增 `scripts/boot/linux-host-qualification.ps1`（Windows 协调）、`scripts/boot/linux_host_qualification.py`（Linux stages）、`scripts/boot/test_linux_host_qualification.py`；复用 `scripts/boot/{infra.py,bundle.py,preflight.py,persistent_update_receipt.py}`，新代独立保存 checker/driver/review/freeze。

- [ ] 新接口固定 `--stage source-check|storage-admission|prepare|build|bundle|admission|pair|export|verify --root /prep --repo /repo`；Windows wrapper 参数为 `-Stage`、`-Root`、`-Repo`。历史 driver 作为合同参考，不能改写其 Darwin/APFS 或缓存校验。
- [ ] 先为拒绝网络、错误 UID/owner、symlink/目录替换、额外 writer、缺空间、错误 image/source/fixture hash、非唯一 summary、重用旧盘和失败后续跑编写控制反例；Linux 执行 `python3 -B -m unittest discover -s scripts/boot -p test_linux_host_qualification.py`，再实现到全通过。
- [ ] 在实际 volume 上执行真实 create/write/fsync/rename/dir-fsync/delete、owner/mode/no-follow/单链接、锁竞争、容器重开稳定性；记录 Windows 承载盘与 Linux filesystem 类型和空间，预算覆盖冷构建全部峰值及至少2GiB余量。空间不足停止并保留观测。
- [ ] 完整 inventory 冻结源码/实际新文件/include_bytes/SDK/Compose/coroutines/WASI/Gradle/adapter/driver/checker/review/lock、镜像/工具动态闭包、Cargo/Kotlin/bootstrap receipts；冻结前后集合及 bytes hash 一致。执行接口：

```sh
python3 -B scripts/boot/linux_host_qualification.py --stage storage-admission --root /prep --repo /repo
python3 -B scripts/boot/linux_host_qualification.py --stage prepare --root /prep --repo /repo
```

- [ ] 独立核对控制反例、真实存储证据和 freeze，提交后再正式构建；不得仅通过 mock/import 检查即准入。

## 阶段4：正式 Task9 构建与真实进程矩阵

**Files:** 新 Linux 执行器；现有 `crates/ming-app-host/{src,tests,examples}`；本代 `build/` 原始证据。

- [ ] 禁网固定镜像内由执行器串行执行每个1800秒上限的命令，首次非零停止：

```sh
cargo +1.99.1 test --offline --locked --release -p ming-app-host -j1 --all-targets
cargo +1.99.1 check --offline --locked --release -p ming-app-host -j1 --all-features --all-targets
cargo +1.99.1 check --offline --locked --release -p ming-app-host -j1 --no-default-features --all-targets
cargo +1.99.1 build --offline --locked --release -p ming-app-host -j1 --bin ming-app-host
cargo +1.99.1 build --offline --locked --release -p ming-app-host -j1 --example prepare_persistent_update_fixture
```

- [ ] 设置实际 Host/helper/bootstrap/old/new/absent Wasm 路径（沿用历史 `run-stage.py:797–802` 的环境变量名称）；每项900秒显式执行三个 `ming_desktop::persistent_update_runtime` controller：`migrated_and_absent_write_commit_only_after_real_ready_close_reap`、`absent_nonempty_runtime_failure_rolls_back_original_missing_namespace`、`killed_host_recovers_in_independent_process`，命令为 `cargo +1.99.1 test --offline --locked --release -p ming-app-host --lib <完整测试名> -j1 -- --exact --nocapture --ignored`，各唯一 summary 精确 `1/0/0/0`。
- [ ] 执行 public 与完整父恢复矩阵，分别精确 `1/0/0/0`、`18/0/1/0`：

```sh
cargo +1.99.1 test --offline --locked --release -p ming-app-host --test persistent_update_runtime separate_process_roles_and_real_stable_host_refuse_active_update_without_writes -j1 -- --exact --nocapture --ignored --test-threads=1
cargo +1.99.1 test --offline --locked --release -p ming-app-host --lib persistent_update::recovery_tests:: -j1 -- --nocapture --test-threads=1
```

- [ ] 验收实际匹配数、退出码、完整日志、源码稳定、Host/helper ELF 闭包与 Kotlin/signature 绑定；零匹配/cfg跳过/ignored 未执行均不可记通过。失败保留并修复后创建新冻结代；通过后封存并提交。

## 阶段5：bundle、无 guest 准入、同盘双启动与独立导出

**Files:** 新执行器、现有 boot bundle/checker/profile；本代 `bundle/`、`admission/`、`pair/`、`export/`、`verify/`。

- [ ] 依次运行 `--stage bundle` 与 `--stage admission`，真实验证 OpenSSL 正/负验签及动态库来源、完整 preflight、initramfs 内容、UID空间、tmpfs/QMP/lease/锁及安全退出；明确拦截 VM 边界。独立 review 绑定完整 freeze 与 bundle。
- [ ] 新建128MiB盘，运行 `--stage pair`；boot1五步、boot2两步，保持原输入和全部时间/资源参数。首次失败不跑第二次，保留盘/日志；禁止自动重试/格式化。验证第一次完整更新生命周期和正常同步卸载，第二次同盘身份、版本、私有数据与 catalog。
- [ ] 执行 `--stage export`、`--stage verify`，独立 checker 从导出副本验证并保存真实结果；封存同盘身份、命令/退出码、tree/模块/hash/signature、UI因果和正常关机证据。仅在其明确合同通过时记录 `persistentUpdateNormalRebootVerified=true`，继续 `finalSystemAccepted=false`。
- [ ] 更新 `docs/continuous-development.md` 与原持久更新计划的当前边界并提交/推送真实完成阶段；另行列出已批准但尚未完成的 qualification 资产恢复工具与完整交付项，继续实现，不以本计划的测试/文档结束整体交付工作。
