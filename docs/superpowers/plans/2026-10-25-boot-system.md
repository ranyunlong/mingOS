# MingOS Boot System Implementation Plan

> **For agentic workers:** 使用本计划与设计逐项执行；主线程会话/输入实现由其原线程负责。本范围的紧密依赖任务在当前线程直接完成，不派发重叠编辑。

**Goal:** 在不修改宿主系统或 Rust/SDK 的条件下，准备可复现的 Debian kernel/initramfs 和隔离 QEMU runner，待主线程确认后真实启动到 MingDesktop 与两包 Kotlin 应用。

**Architecture:** 复用不可变 Debian 13 任务镜像构建工具；客体 RAM root 由 guest PID1 启动 udev/seatd/Weston DRM/Pixman/kiosk/MingDesktop。构建和启动绑定人工确认文件、payload 哈希及会话参数，通过 serial、virtio-serial 和 QMP 交叉验收。

**Tech Stack:** Debian 13/trixie，Linux 6.12.113，QEMU 10.0.13 TCG，Weston 14.0.2，Python 3.13，cpio/gzip，现有 Kotlin/Wasm 与 Ming SDK。

**Spec:** docs/superpowers/specs/2026-10-25-boot-system-design.md

## Global Constraints

- 只写 scripts/boot/、本计划、对应设计及 artifacts/boot-system-preflight/。
- 不改 Rust、Kotlin、SDK、既有脚本/文档、Windows/WSL/registry 配置。
- 源码与既有 cache volume 只读；所有依赖安装在任务镜像，运行容器无网络、不 privileged。
- 未获主线程产物/会话确认，不组装最终 bundle 或启动 kernel。
- bounded duration <=60000ms；long-lived 参数与 budgetEvidence 由主线程提供，保留 yield/fuel/watchdog。
- 完整 boot 前 Windows Docker backing drive 至少 2 GiB 可用；运行前重新检查；本轮不启动 kernel。
- QMP 只操作 VM 设备；不使用 ComputerUse 或物理输入。
- 不把容器/Weston headless/暂停机器截图当作 Linux boot 验收。

## Task 1：环境与版本依据

文件：scripts/boot/preflight.py、scripts/boot/packages.lock、artifacts/boot-system-preflight/probe-20261025/。

- [x] 读取四份用户指定文档，检查源码工作区、Windows PATH、Docker/WSL、磁盘与任务 cache。
- [x] 沙箱权限拒绝后使用标准 escalation 获取只读证据；确认原镜像精确 ID、Debian 13.7、Weston 14.0.2。
- [x] 使用 tmpfs 包索引解析 Debian 官方源；固定 46 个精确新增包版本，保存原始命令/退出码/下载大小。

复现命令（输出目录必须全新）：

~~~powershell
python scripts/boot/preflight.py --output artifacts/boot-system-preflight/probe-repeat --resolve-packages
~~~

## Task 2：隔离工具镜像与无 boot 实测

文件：scripts/boot/Dockerfile.tools、build-tools.py、inspect-tools.py、bootlib.py、vm.py、test_boot.py。

- [x] 构建前验证基础 tag ID 与 1800 MiB 空间门槛，安装锁定依赖并保存 .deb/哈希/版本/日志。
- [x] 记录首次 FROM sha256 解析错误与 403，改为已核验的本机 tag 后真实构建成功。
- [x] 重新核验 46 个 .deb SHA-256、10 项工具/模块/语法命令，均成功。
- [x] 以 nobody + cap-drop ALL 在只读/无网络容器启动 -S 空 QEMU，query-status=prelaunch/running=false，screendump 成功。
- [x] 将导出 245.7 MB 原始 APT 警告日志无损压缩，记录原 SHA-256；保留实际成功构建输入。
- [ ] 下次空间足够且确有必要时重建工作区 Dockerfile 的日志压缩修订；当前已验证镜像仍使用 result.json 中的不可变 ID。

已执行命令和日志详见证据 summary.json、tools-build-20261025-localbase/result.json、tools-inspection-20261025/result.json、qmp-paused-20261025/result.json。无 boot runner 调用见 scripts/boot/README.md。

## Task 3：最终组装入口与会话确认门槛

文件：scripts/boot/bundle.py、init、guest.py、weston.ini、review.example.json、extract-evidence.py、README.md。

- [x] 实现 ELF/Wasm/receipt 检查、catalog 相对路径约束、两包自启动、无 symlink 的 payload 入口。
- [x] 实现 runtime ELF 依赖拷贝、guest root、virtio 模块闭包、cpio/gzip、bundle/review 哈希。
- [x] 实现 guest PID1/udev/seatd/DRM/Pixman/kiosk、独立 app PID/PPid 与增量日志证据导出。
- [x] 实现拒绝 pending review、哈希变更、越界路径及超长 bounded session 的单元检查，及 guest 日志二进制 round-trip/截断检测。
- [x] tmpfs 实测修复 Weston 私有库解析与全插件闭包 ENOSPC；最小 kiosk runtime 80.4 MB 通过，无 payload/最终 archive/kernel boot。
- [x] 工具镜像外部消失后，从保留 BuildKit 缓存全 CACHED 恢复同一 image ID，原始事件和恢复日志保留。
- [ ] 主线程选择最终冻结 payload，确认 exact host/catalog hashes；确认 bounded 首启或已验证 long-lived 参数。
- [ ] 将 review.example.json 复制为任务 evidence 下的 session-review.json，填写真实哈希；确认之前保持 reviewed=false。确认后由主线程明确写 reviewer/reviewed 与会话证据。
- [ ] 检查 Windows backing drive >=2GiB，使用 README 命令在独占 volume 组装 vmlinuz/initramfs，检查 bundle.json 和 cpio 内容。

## Task 4：首次真实 kernel boot 与交叉验收（尚未运行）

- [ ] 在降权限工具容器中挂载 bundle volume readonly，执行 vm.py --bundle /bundle --review /review/session-review.json --output /evidence/first-boot --timeout 180。
- [ ] 检查 kernel-serial.log 的 Linux 版本/rdinit/关机与 guest.jsonl 的 boot ID/DRM/Weston/host/worker PIDs。
- [ ] 使用 extract-evidence.py --input first-boot/guest.jsonl --output first-boot-decoded 无损复原日志，交叉比对两真实包收据、帧数和多个 worker 生命周期。
- [ ] 检查 QMP scanout-*.ppm 的 MingDesktop 与两应用变化；不能使用 MingDesktop snapshot 或 headless 模式替代。
- [ ] 用 QMP input-send-event 测窗口/launcher/关闭恢复，在 QMP transcript 与 guest 生命周期证据中对应行为。
- [ ] 正常关机再冷启动，检查新 boot ID、相同 bundle 哈希、无残留任务进程；超时/强制 quit 作为失败证据。
- [ ] 只有以上均满足才记录真实 boot 通过；长期可用性、Linux 生产隔离和真实硬件另行验收。
