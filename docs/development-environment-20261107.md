# 当前开发环境恢复记录

日期：2026-11-07。范围：Windows + Docker/Linux 开发工具、真实 Kotlin 来源与 Rust 冷构建、持久更新正常双启动及独立导出回放已完成；进程中断恢复按具体范围通过，完整系统仍未验收。最新边界见 [当前状态](current-system-status.md)，`finalSystemAccepted=false`。

## 已实际验证

- 使用现有 `scripts/linux/Dockerfile` 在独立 tag `mingos-linux-win-20261107` 构建成功；固定基础为 `rust:1.99.1@sha256:931c2c0531a8b143e044f0f3914dfe802f15bb3d1955f0b657e9359327ac2ffc`。
- 当前 Docker 的 image inspect ID 为 `sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80`。这是本次来源，不等于历史 qualification 镜像。
- 以该 ID 在 `--network none --read-only --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges` 下实际执行工具：Rust/Cargo 1.99.1、Python 3.13.5、Weston 14.0.2，Debian 13、amd64。工具执行成功不能替代 Host 构建或 guest 验收。
- Gradle 8.14.5 归档 SHA-256 为 `6f74b601422d6d6fc4e1f9a1ab6522f642c2fdcbc15ae33ebd30ba3d7198e854`，与仓库 wrapper 固定值相同。官方分发经 curl 下载在300秒超时；独立镜像下载取得相同 hash 后，由原 wrapper 解包并成功运行 `--version`，JDK 为 Microsoft 21.0.13。
- `scripts/dev-env.ps1` 的 `GRADLE_USER_HOME` 统一为 `.cache/gradle`，与 Kotlin/Compose 构建脚本相同。实际 dot-source 检查已确认一致，避免依赖预热落入另一目录。

原始镜像构建日志、完整 image inspect、installed-packages、apt sources、禁网工具输出、Gradle 下载与版本日志、命令/退出码/hash 收据位于本机 `artifacts/environment-rebuild-20261107/`；索引为 `toolchain-observation.json`。大产物不提交。现有 Dockerfile 的 apt 选择不是新的固定依赖闭包，正式资格前仍须封存包/deb 来源；当前结果只证明开发工具可用。

## boot、Kotlin 与 Cargo 来源准备

新 `scripts/boot/Dockerfile.linux-host-tools` 已实际构建，image ID 为 `sha256:a085efa9e8b4958b1943ee87857313f3d2666f696f730f242daef643347b0091`。独立新 lock 保留实际 kernel `linux-image-6.12.113+deb13-amd64=6.12.113-1`，移除已无法取得的旧 `linux-image-amd64=6.12.113-1` 元包，记录 OpenSSL 和 CA 的实际版本；三个历史 lock 的内容不变。新增 LF 属性避免 Windows CRLF 进入 `xargs apt-get` 的版本参数。

固定 Debian base 使用 Aliyun HTTP 的 Debian 签名索引，保留 APT 签名、hash 验证与 `APT::Update::Error-Mode=any`。本轮全部280个下载 deb 保存在镜像 `/opt/mingos-boot/debs`，SHA、安装包、来源、压缩安装日志和版本在 `/opt/mingos-boot/locks`。尚未保存签名索引原件，因此不能宣称完整、可独立验证的离线 APT 重建闭包。

独立 UID65534、禁网、只读 root、2CPU/4GiB smoke 返回0：280个 deb SHA 匹配，QEMU10.0.13、Weston14.0.2、OpenSSL3.5.7 及三个工具的动态库可用；Ed25519 正验签0、篡改拒绝1；10秒内完成无 kernel 的暂停 QMP capabilities/quit。唯一 kernel 的 SHA 为 `4336c75fc1a6a58e7bda66cbe9f4042f88064e7abc93faaea9e2aceb19459561`。未启动 guest。证据在 `artifacts/environment-rebuild-20261107/boot-tools-smoke/`。此前把 gzip 当 UTF-8 读取的诊断失败和各次失败 build 均保留，不记作通过。

新 `scripts/linux/Dockerfile.kotlin` 构建的 image ID 为 `sha256:5fba561826e826a26e5179d8b58425d0b5125d679e7dc64459d38bad0c6e417f`，固定 Node24.22.1 base digest 与 Debian JDK/JRE21.0.13。使用固定 Node 内置 CA 引导 APT HTTPS，TLS 和 APT 签名校验开启。禁网只读 UID65534 的 Node/java/javac probe 返回0；此 probe 没有运行 Linux Gradle 或 Kotlin。安装包/索引 hash 保留，但未保留 deb 实体与索引原件，边界见 `scripts/linux/kotlin-builder.md`。证据在 `artifacts/environment-rebuild-20261107/kotlin-linux-toolchain/`。

新 Cargo 卷 `cargo-linux-20261107-d3cf3114387a-cache` 的锁定联网 fetch 与禁网 `metadata --offline --locked` 均返回0，metadata 记录228个包；完整缓存文件 SHA 已保存。target 没有编译产物，但有 Cargo 自动生成的2179字节 `.rustc_info.json`，严格空目录断言失败原样保留，正式冷构建将使用另一全新 target。证据在 `artifacts/environment-rebuild-20261107/cargo-source-preparation/`。

Windows 来源准备已真实编译 read-file 和 image-viewer，`npm run test:kotlin` 为3通过、0失败。更新 old 首次离线编译因缺 `atomicfu:0.27.0` 失败，随后独立联网 Gradle 编译成功；失败输出另存 `kotlin-update-old-offline-failed/`，联网编译不能当作禁网来源资格，也没有替代正式打包收据。这些日志均在上述环境证据根目录，后续仍须执行 Linux 禁网强制重编。

## Linux Kotlin 来源和执行器控制

六个包已在固定 Kotlin 镜像中顺序完成真实禁网强制重编译：read-file、image-viewer、text-entry-demo、persistent-update-demo 的 old/new/absent-write。使用 UID/GID65534、只读源码与 root、外部工作目录及 Gradle cache；命令包含 `--offline --rerun-tasks --no-build-cache --no-configuration-cache`。每包保存 compiler.wasm、main.wasm、app.json、build-info/build-receipt 和完整日志、输入与依赖清单。独立容器只读重放六包通过，导出108个文件的 SHA 与大小由主执行者再次核对；runner 控制14/14通过。

| 包 | main.wasm SHA-256 |
| --- | --- |
| read-file | `7bdc9facab6cb8cf9e593f0653733c8a02f9452b41d15572a384fbbc2d22c25e` |
| image-viewer | `2eb888508c0fb4ac0a27df4bdbe8dfff1b402c46c1318dff56b6297c5988de21` |
| text-entry-demo | `a38bf6b3cc2b10738fb508bb0524b44b7a5307e62159ab5e969ed0b4bdd64e3f` |
| old | `2e523c6b5dfb9650e397af798eb34b8ef9d017bae7bd59f614c746f81a7b7e8a` |
| new | `f3c543c80da2bed51d9e150485f23b8dd3c0bcb1bac035567a4175853728e61a` |
| absent-write | `a1cac4c9b3e3a3e96acc94433b9245794d39bcbf3c7b0bc6d4478b03211a02ee` |

证据代为 `artifacts/environment-rebuild-20261107/kotlin-linux-rebuild-8a4e2c49b74549c78c46654e205845e6/`。初始所有权设置失败、此前只读 Gradle 目录失败均保留；成功代没有将失败任务重记为成功。离线依赖准备的完整清单包含2426文件、673546220字节，SHA为 `b53a460468a58aaf56151994b6ef51f7ccc919d79d25588cf3a1dd059d599ed3`。后续每次强制重编重新封存完整清单，下载实体保持不变，派生 cache 的变化另行记录。

新增 Linux 存储、源码、命令回收、冻结与冷构建控制模块。最终固定 Rust 镜像中的综合控制63/63通过；Windows wrapper35项控制通过，真实 `source-check` 代 `persistent-update-20261107-linux-250620c042ae4f97` 返回0。实际观测为 UID/GID65534、仅 lo、capability为0、no-new-privileges/seccomp开启、2CPU/4GiB/pids256、源码/root只读。存储使用独立0700目录、0600 marker/lease、真实子进程锁竞争、fsync/rename/delete及跨容器重新打开；这些结果不证明恶意宿主无法写入。

构建准入要求原始固定镜像检查、可重放六包、完整源码/registry hash、真实存储收据和 Windows Docker backing VHDX/双层剩余容量，额外预算32.5GiB。Cargo registry 必须只读，Cargo 自身锁与全局索引可写。Rust target 必须是新卷中的字面空目录；前五条与后五条显式测试分别使用固定环境和1800/900秒上限。失败保留并拒绝同代重试。完整源码副本使后续开发不修改正在构建的输入。

公共 `new-linux-host-build.ps1` 已提供参数化冷构建协调与源码快照，包含source/storage/freeze/prepare/build入口；旧 linux-host-qualification.ps1 wrapper 仍仅source-check。真实fd90代由保留的协调脚本完成十步冷构建，独立核对通过：all-targets773/0/24、三个controller与public integration各1/0/0/0、recovery18/0/1/0。独立source-v2探针和真实几何来源已完成；公共完整runtime/pair/export协调仍待集成，未启动guest，`finalSystemAccepted=false`。

## 接续工作

按照[新宿主资格设计](superpowers/specs/2026-11-07-linux-host-qualification-design.md)与[实施计划](superpowers/plans/2026-11-07-linux-host-qualification.md)，准备独立 Cargo cache、真实 Kotlin 与 bootstrap 来源、boot tools 和 POSIX 存储准入，再冻结新执行器与完整输入。来源准备允许联网，正式构建与运行禁网。任何仍在执行的下载、构建或仅有启动日志的任务均不计作通过。

旧 macOS/APFS 环境和完整备份目前不可访问；不修改历史 driver 的摘要/宿主合同，不续跑旧失败代。获批的镜像恢复入口现已实现：显式 `-LoadImages` 在任何 Docker 调用前核对全部三个归档的外层 tar、唯一 manifest/config、固定历史 image ID、每层 diffID 和 OCI 引用，再以私有无标签副本经 stdin 加载并检查精确 ID。只读模式仍只报告 `archive-hashes-matched`。当前没有旧归档，未实际调用 daemon 加载历史镜像，因此不声称历史资产已恢复。

`-RestoreVolume` 已集成，和 `-ValidateOnly`、`-LoadImages` 互斥。它先验证外层 manifest、三个镜像归档文件哈希及严格 USTAR 的嵌入完整清单，不加载镜像；通过固定 helper `sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80`、`--pull never` 创建全新 Docker 匿名卷。来源 `volume.name` 不作为目标名。缓存位于 `payload/`，收据明确 `subpath="payload"`；根与 `control/` 为 UID/GID65534、0700。Linux worker 独立验证私有归档，以 NOFOLLOW/EXCL、lease、owner/mode/nlink/字节哈希和 fsync 写入；确认 writer 正常停止后，独立容器以真实只读挂载和 ST_RDONLY 重开核验完整 inventory 与外部收据。失败或超时停止后续操作，保留所有私有副本、日志、失败收据、部分 payload、容器和卷，不删除、覆盖或续跑失败代。命令及完整归档合同见 `scripts/boot/README.md`。

实际控制代 `artifacts/environment-rebuild-20261107/restore-volume-root-controls/restore-live-b144eca66993/` 已通过两路并发公共入口恢复和各自独立只读重开，两路匿名卷不同；manifest 来源名指向的已有 sentinel 卷前后 dev/ino、UID/GID、0600、nlink1 和内容 SHA 全部相同。固定镜像 Linux 综合控制110项、108通过、2个非Linux边界跳过。`result.json` 明确 `assetOrigin="synthetic-control"`、`historicalAssetsRestored=false`：这证明新恢复工具的控制路径，不是历史缓存恢复，也不替代历史 Task9 的宿主来源合同。所有结果仍为 `formalQualification=false`、`finalSystemAccepted=false`。

实际完整冷构建后，新runtime5f056322a31e4974已通过独立存储、fixture、root review、bundle和无guest admission。tools manifest3覆盖6042文件，补入实际Rust ELF依赖的libgcc_s.so.1；原manifest2的6041文件及首runtime63c6失败均保留。原checker24/24、driver31/31及真实VM拦截通过。当前只证明无guest运行时准入，guest UID namespace、正常双启动及实际导出尚未完成。
