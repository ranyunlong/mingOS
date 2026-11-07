# Windows 宿主上的新 Linux 构建与持久更新资格设计

日期：2026-11-07。状态：新来源代的执行设计；没有声明构建、恢复矩阵或双启动已通过，`finalSystemAccepted=false`。

## 目标与边界

在当前 Windows + Docker Linux 环境从可用源码重新构建 MingOS，完成持久更新 Task9 的真实 Linux 验证与同盘正常双启动。环境准备、构建和交付属于用户已授权的持续工作；技术路线不再依赖不可访问的原 macOS、APFS、备份、旧镜像或历史编译缓存。

历史 `artifacts/persistent-update-20261104-prep/run-stage.py` 绑定固定旧镜像、Darwin/APFS、`/Users/ranyunlong/.../.cache/linux-rust` 和缓存复制合同。这些字节与失败收据保留，不改写、不放宽、不续跑。新代使用独立 generation、执行器、镜像、cache、freeze、review、磁盘和证据目录，不把本次结果写成历史资格通过。

## 来源准备与正式执行

选择 Windows 仅协调、Linux 容器负责文件系统敏感操作。Windows 记录 Docker 命令、容器身份、退出码、宿主容量和证据导出；`fcntl`、POSIX owner/mode、目录 fsync、锁、编译、checker 和 VM 执行位于 Linux。Windows Gradle 可作为依赖准备或开发诊断，但正式 Kotlin 来源须有可复核的禁网真实重编证明。

来源准备允许联网，明确记录边界：固定 Rust 1.99.1 base digest、apt sources/包版本/deb hashes、Cargo.lock 对应下载、JDK/Node/Gradle/Kotlin 与 Wasm 工具依赖。`scripts/linux/Dockerfile` 构建新的 `mingos-linux-win-20261107`，不覆盖旧 tag。boot tools 不能引用缺失的旧私有 tag；从新固定 base 重建并保存完整包闭包。锁定 apt 版本若实际不可下载，生成独立新 lock 与说明，不修改历史 lock 冒充复现。

正式执行禁止网络，使用已封存依赖、只读源码、单写者 Linux target，Rust 命令固定 `+1.99.1 --offline --locked --release`。新 cache 的来源是本代下载与清单；不要求或伪造历史 `target/release`。从空 target 的冷构建成本须如实纳入新代准备与容量准入，不用 debug/opt0 替代正式 release。

Kotlin 固定当前 Gradle wrapper 8.14.5 及其 SHA、Kotlin/Compose plugin 2.3.20，记录实际 JDK/Node 与编译器依赖版本和完整 hash。禁网执行 `--offline --rerun-tasks --no-build-cache`，真实生成 read-file、image-viewer 和 persistent-update-demo 的 old/new/absent-write。保留 compiler.wasm、adapter 输出、manifest、build-info、原始 stdout/stderr、命令、退出码和 build receipt；重放 adapter、校验 ABI/import/capability、原始与 packaged hash。bootstrap 也必须有本代明确来源与签名绑定。

## 存储与隔离合同

记录实际链路：Windows filesystem → Docker Desktop Linux VM → 指定 Linux volume filesystem → guest ext4 数据盘。不得将 NTFS、Docker volume 或 Linux filesystem 记作 APFS。源码可从 Windows 只读挂载；cache、target、运行根和资格磁盘放独立 Linux volume，证据经受控导出保存到本代目录。

准入在实际拟用镜像、volume 和 UID65534/GID65534 下运行：验证 owner/mode、普通文件/目录身份、no-follow、单链接约束、创建/写入/file fsync/同目录 rename/directory fsync/删除、协作 writer 锁竞争、只读边界、容器重开后的同一对象身份与内容。负例必须拒绝 symlink、额外 writer、错误 owner、目录替换、缺少权限或空间；不能以 Windows 路径存在或 `chmod` 成功推断 Linux 语义。

同时观测 Windows 实际承载盘与 Docker Linux filesystem 的可用量。独立容量合同覆盖冷 Rust target、Cargo/Gradle cache、Kotlin 输出、镜像准备、产物/导出副本、128MiB 新资格盘、失败现场保留及至少 2GiB 余量；逐项记录实际已分配量与预算，不沿用旧缓存复制公式。空间不足停止，不清旧卷、不改 Docker 容量设置绕过。

正式容器保持 UID65534、2CPU/4GiB、pids256、禁网、read-only root/source、cap-drop ALL/no-new-privileges；运行 tmpfs、QMP、lease 和安全退出在实际路径验证。guest 保持 2vCPU/2048MiB、300秒 watchdog、5秒 pointer 等待、8MiB 日志限制与原 worker 预算。

## 冻结和验收顺序

完整 source inventory 包含 git tracked 与实际新源码、`.cargo`、include_bytes 资产、Rust/SDK/Compose/coroutines/WASI runtime、Gradle/wrapper/settings、adapter、fixtures、helper/checker/driver、lock/review/inputs。toolchain inventory 单独记录镜像 IDs、工具可执行文件与动态库、下载闭包和 receipts。所有集合和单次读取 bytes 的 hash 在构建前后、执行前后一致；外部 fixture 也纳入来源清单，不能只冻结 Rust 文件。

执行持久更新 Task9 原顺序：release all-targets tests、all-features/all-targets check、no-default/all-targets check、Host/helper build；准备真实 worker/helper/module 环境后显式运行三个 controller、public ignored 集成、完整恢复矩阵。controller/public 各精确 `1 passed/0 failed/0 ignored/0 measured`；恢复矩阵精确 `18/0/1/0`，其中 ignored child 只由真实父进程启动。每步保存原始输出、唯一 summary、实际匹配数、退出码和源码稳定证明。

构建后核对 Host/helper ELF 动态闭包、OpenSSL 来源、实际 initramfs 文件、模块签名与 bundle，完成独立 review 和无 guest 真实准入，再执行新 128MiB ext4 盘上的两次正常启动。首次更新确认必须具备真实 UI 因果与完整 ready/close/native exit/reap 证明；正常关机同步卸载后第二次挂载同盘，验证版本、数据与 catalog 保持。每轮900秒；minimum 公式 `300+steps*(45+5)+120`，五步670秒、两步520秒；supervisor slack45秒、pair1920秒、container1950秒不变。

任何首次失败即停止后续步骤，第一轮失败不跑第二轮；保留命令、日志、磁盘、卷与失败记录，不自动重试、格式化或扩大预算。修复后以新的完整冻结代执行。最终独立导出 checker 只证明其明确覆盖的正常重启资格，不将其升级为整个系统交付。

## Qualification 资产恢复工具与证据边界

用户批准的开发资产恢复入口已实现。默认/`-ValidateOnly` 仍只报告 `archive-hashes-matched`；独立 `-LoadImages` 在导入前绑定唯一 config、固定历史 image ID、layer diffID 与 OCI 引用，以私有无标签副本顺序加载并核对精确 ID。`-ValidateOnly`、`-LoadImages`、`-RestoreVolume` 互斥，不改变历史 driver 或固定摘要。

`-RestoreVolume` 在任何 Docker 调用前核对外层 manifest、镜像归档文件哈希及严格 USTAR 缓存归档，并冻结私有副本。它不加载镜像，也不把镜像归档的文件哈希核对写成镜像内容证明。固定本地 helper 为 `sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80`，create 使用 `--pull never`。creator 通过省略 source 的 `volume-nocopy` mount 原子取得新匿名卷；`volume.name` 仅为 provenance，不能选择或复用现有卷。根和 `control/` 为 UID/GID65534、0700；缓存单独放入 `payload/`，成功收据明确 `subpath="payload"`。root initializer 仅有 CHOWN，实际 worker 为 UID/GID65534、cap-drop ALL、禁网、只读 root。

归档嵌入完整清单，只允许明确父目录及 regular 单链接文件，绑定 UID/GID65534、受限 mode、size 和 SHA-256；拒绝 PAX/GNU、link/device、路径穿越、重复成员及额外 payload。Linux worker 先独立验证私有字节，再用 NOFOLLOW/EXCL、目录描述符、独占 lease 和 fsync 写入，随后核验完整 inventory。writer 确认正常退出后，独立容器以真实 RO mount 重开，核验 ST_RDONLY、外部收据与完整 owner/mode/nlink/身份/内容。成功仅为 `volume-restored-readonly-verified`。任一失败或超时停止后续阶段，私有副本、顺序收据、日志、部分 payload、容器与卷原样保留，不删除、覆盖或自动续跑。

真实控制代 `artifacts/environment-rebuild-20261107/restore-volume-root-controls/restore-live-b144eca66993/` 的两个并发公共入口各恢复到不同匿名卷，并独立只读核验；来源名所指的已有 sentinel 卷前后身份、权限、链接数和字节不变，Linux 综合控制110项、108通过、2个非Linux边界跳过。这是 synthetic 控制输入，`historicalAssetsRestored=false`，不证明历史资产已取得或恢复。工具成功、依赖重新下载、fixture 重建、更新失败回滚和 checker 各自只证明其明确范围；所有恢复收据保持 `formalQualification=false`、`finalSystemAccepted=false`，不能据此声明整个 MingOS 已交付。
