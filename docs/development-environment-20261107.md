# 当前开发环境恢复记录

日期：2026-11-07。范围：Windows + Docker/Linux 开发工具准备；正式 Task9 构建、恢复矩阵和正常双启动仍未完成，`finalSystemAccepted=false`。

## 已实际验证

- 使用现有 `scripts/linux/Dockerfile` 在独立 tag `mingos-linux-win-20261107` 构建成功；固定基础为 `rust:1.99.1@sha256:931c2c0531a8b143e044f0f3914dfe802f15bb3d1955f0b657e9359327ac2ffc`。
- 当前 Docker 的 image inspect ID 为 `sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80`。这是本次来源，不等于历史 qualification 镜像。
- 以该 ID 在 `--network none --read-only --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges` 下实际执行工具：Rust/Cargo 1.99.1、Python 3.13.5、Weston 14.0.2，Debian 13、amd64。工具执行成功不能替代 Host 构建或 guest 验收。
- Gradle 8.14.5 归档 SHA-256 为 `6f74b601422d6d6fc4e1f9a1ab6522f642c2fdcbc15ae33ebd30ba3d7198e854`，与仓库 wrapper 固定值相同。官方分发经 curl 下载在300秒超时；独立镜像下载取得相同 hash 后，由原 wrapper 解包并成功运行 `--version`，JDK 为 Microsoft 21.0.13。
- `scripts/dev-env.ps1` 的 `GRADLE_USER_HOME` 统一为 `.cache/gradle`，与 Kotlin/Compose 构建脚本相同。实际 dot-source 检查已确认一致，避免依赖预热落入另一目录。

原始镜像构建日志、完整 image inspect、installed-packages、apt sources、禁网工具输出、Gradle 下载与版本日志、命令/退出码/hash 收据位于本机 `artifacts/environment-rebuild-20261107/`；索引为 `toolchain-observation.json`。大产物不提交。现有 Dockerfile 的 apt 选择不是新的固定依赖闭包，正式资格前仍须封存包/deb 来源；当前结果只证明开发工具可用。

## 接续工作

按照[新宿主资格设计](superpowers/specs/2026-11-07-linux-host-qualification-design.md)与[实施计划](superpowers/plans/2026-11-07-linux-host-qualification.md)，准备独立 Cargo cache、真实 Kotlin 与 bootstrap 来源、boot tools 和 POSIX 存储准入，再冻结新执行器与完整输入。来源准备允许联网，正式构建与运行禁网。任何仍在执行的下载、构建或仅有启动日志的任务均不计作通过。

旧 macOS/APFS 环境和完整备份目前不可访问；不修改历史 driver 的摘要/宿主合同，不续跑旧失败代。原恢复工具当前仅提供归档 hash 匹配检查，写入入口临时拒绝，其安全实现仍待完成。
