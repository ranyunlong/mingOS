# 当前开发环境恢复记录

日期：2026-11-07。范围：Windows + Docker/Linux 开发工具准备；正式 Task9 构建、恢复矩阵和正常双启动仍未完成，`finalSystemAccepted=false`。

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

## 接续工作

按照[新宿主资格设计](superpowers/specs/2026-11-07-linux-host-qualification-design.md)与[实施计划](superpowers/plans/2026-11-07-linux-host-qualification.md)，准备独立 Cargo cache、真实 Kotlin 与 bootstrap 来源、boot tools 和 POSIX 存储准入，再冻结新执行器与完整输入。来源准备允许联网，正式构建与运行禁网。任何仍在执行的下载、构建或仅有启动日志的任务均不计作通过。

旧 macOS/APFS 环境和完整备份目前不可访问；不修改历史 driver 的摘要/宿主合同，不续跑旧失败代。原恢复工具当前仅提供归档 hash 匹配检查，写入入口临时拒绝，其安全实现仍待完成。
