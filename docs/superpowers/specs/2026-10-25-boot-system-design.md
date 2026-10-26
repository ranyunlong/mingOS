# MingOS 可启动开发系统设计

日期：2026-10-25。范围为启动构建前置，与主线程 7D 并行；只写 scripts/boot/、本设计、对应计划和 artifacts/boot-system-preflight/。

## 本轮状态和边界

已真实构建项目专用工具镜像，安装 Debian kernel、QEMU 和 initramfs 组装依赖；已以 nobody 在降权限容器中验证暂停机器的 QMP 与 screendump。另在 256 MiB tmpfs 中实际复制最小 DRM/kiosk 运行库闭包，1082 个常规文件合计 80,405,907 bytes（不含 app payload、kernel modules 或最终 initramfs），成功后释放。没有装载 kernel，没有 boot 到 MingDesktop，没有生成包含 MingDesktop 的最终 bundle，没有确认长期会话。暂停机器的空白 PPM 不是桌面验收截图。

源码目录不含可用 .git；git status 原始结果为 fatal: not a git repository。未提交、回退、清理任何其他线程文件。Rust、Kotlin、SDK、既有脚本/文档均未修改。

## 实际环境证据

- Windows Docker CLI/Engine 29.8.0，Docker Desktop 4.92.0，context desktop-linux，daemon linux/amd64，WSL2 kernel 5.15.153.1-microsoft-standard-WSL2。
- WSL 仅列出运行中的 docker-desktop；未发现单独开发发行版。Windows PATH 未找到 qemu-system-x86_64/qemu-img。任务容器未暴露 /dev/kvm，采用 TCG，不据此断言整个宿主不存在 KVM。
- 原任务镜像 mingos-linux-task3:b89a9dc7cdce 的 image ID 精确匹配 sha256:e91ad5a3eae8964aab759b9db30997a49be1a57fb97e63529f59fa5b16bb2722。
- 镜像 /etc/os-release 直接报告 Debian GNU/Linux 13 (trixie)、DEBIAN_VERSION_FULL=13.7；apt 对 Debian 官方 trixie/trixie-security 源成功获取 InRelease/Packages，解析结果标识 Debian:13.7/stable。这是选择 Debian 13 的直接证据，不依靠 stable 名称随时间漂移。
- 原镜像 rustc 1.99.1、Weston 14.0.2；实际 weston --help 确认 drm、pixman、kiosk-shell 相关入口。主线程历史冻结包位于 artifacts/linux-desktop-runtime-20261025/frozen/，本轮不将其自动选作最终启动产物。
- 原 cache volume mingos-linux-task3-b89a9dc7cdce-cache 存在，约 6.7 GB，所有本轮挂载均 readonly。/cache/target/release/ming-app-host 和另两份构建存在；不把任一缓存文件自动视作主线程批准版本。
- 容器 df 报约 897 GB 可用，但 Docker VHDX 实际在 C:\Users\ranyunlong\AppData\Local\Docker\wsl\disk\docker_data.vhdx。首次 Windows 只剩约 2.18 GB；D: 约 214.9 GB。工具构建报告结束时约 1.90 GB，随后并行环境/日志导出时下降至约 0.95 GB。只以实际运行前新探针判断剩余空间，不使用容器 df 推断宿主余量。

原始证据见 artifacts/boot-system-preflight/probe-20261025/、tools-build-20261025-localbase/、tools-inspection-20261025/、qmp-paused-20261025/。

## 最小路线

固定 x86_64、Debian 13 kernel + RAM root initramfs，QEMU 直接使用 -kernel/-initrd/-append 真正启动 Linux。无需分区表、GRUB、ISO、loop mount 或宿主块设备。ISO/持久 ext4 是后续有实际需求再引入的替代方案。

QEMU q35 + TCG + CPU max，2 vCPU、2048 MiB guest RAM；virtio-vga 提供 DRM/KMS 输出，virtio keyboard/tablet 只接收 QMP 虚拟事件。guest 使用 Debian virtio-gpu、virtio-input，PID1 只在 guest 中挂载 devtmpfs/proc/sysfs/tmpfs，启动 udev/seatd。Weston drm + pixman + kiosk-shell 接管虚拟 DRM，MingDesktop 是单一 native Wayland fullscreen client，内部承载真实 Kotlin Compose 和 Motion 两个 Wasm worker。应用能力继续由现有 Wasmtime/Broker/SDK 控制；本轮不宣称完成 Linux UID/seccomp/cgroup 生产隔离。

内核 6.12.113+deb13-amd64 的 CONFIG_DEVTMPFS/RD_GZIP/SERIAL_8250_CONSOLE 均为 y；DRM_VIRTIO_GPU/VIRTIO_INPUT 为 m，VIRTIO_CONSOLE/VIRTIO_PCI 为 y。组装器接受内置或模块形式，按 modprobe --show-depends 拷贝模块闭包，使用 depmod 生成客体索引。

## 固定依赖与复现

scripts/boot/packages.lock 固定本轮解析的全部 46 个新增包版本。主要版本：linux-image 6.12.113-1、QEMU 1:10.0.13+ds-0+deb13u1、udev 257.13-1~deb13u1、cpio 2.15+dfsg-2、busybox-static 1:1.37.0-6+b9。Weston/seatd 来自不可变原镜像。

已构建工具镜像 mingos-boot-tools:20261025-trixie13：

~~~text
sha256:703a0c3e26cff6d9f63375ee9dbdd7b4ad438784db123cdf69443af5a6e57ac5
~~~

镜像保留 /opt/mingos-boot/debs/ 的原始 46 个 .deb 和 locks/ 的 SHA-256、installed-packages.tsv、官方源配置及安装日志。导出后重新计算全部下载包 SHA-256，全部匹配。版本锁使依赖变化时失败；远端将来移除精确版本时，使用本镜像保留的 .deb 回放，不自动升级或假定 snapshot URL 存在。镜像只在本机存在，没有发布或推送。

实际成功构建的 Dockerfile.tools/packages.lock 原文冻结在 tools-build-20261025-localbase/inputs/，result.json 记录输入 SHA-256。之后工作区 Dockerfile 增加 APT::Sandbox::User=root 和日志压缩，防止重复本地包调度警告膨胀；因为磁盘不足未重建该修订，现有已验证镜像仍以上述 ID 和冻结输入为准。

## 构建接口与产物

1. 主线程提供可信 frozen payload：Linux release ming-app-host、重写为包内相对路径的 catalog.json、packages/1 与 packages/2 的 app.json/main.wasm/compiler.wasm/build-info.json/build-receipt.json。不能直接使用引用 ../../artifacts 的源码 catalog。
2. 主线程确认 review JSON：reviewed=true、reviewer、hostSha256、catalogSha256、sessionMode、exact extraArgs。当前 bounded 模式严格要求 --duration-ms 1..60000；long-lived 必须由主线程提供已验证参数和 budgetEvidence，不能在这里删除 fuel/yield/watchdog。脚本不自动把示例 reviewed 改为 true。
3. bundle.py 在写入 rootfs 前检查确认文件、ELF/Wasm magic、catalog 双应用自启动和 Kotlin receipt hashes。只接受无 symlink 的冻结包；包路径不得越界。它不编译或修改 Rust/Kotlin。
4. 在任务 volume 组装运行时文件及动态库闭包、字体/键盘布局/udev 数据、virtio 模块。归一化 mtime、cpio owner=0:0、gzip mtime=0；没有主机 chroot、mknod、mount 或特权容器。
5. 输出 vmlinuz、initramfs.cpio.gz、bundle.json、kernel.config、locks/ 和可检查 rootfs/。bundle.json 记录启动二进制哈希、payload 全文件哈希、脚本哈希和 review SHA-256。当前尚未执行这一步，因此不声称已经生成可引导 bundle。
6. vm.py 在 kernel 启动前再次核验 bundle/review。无配置确认不得运行；--probe-paused 另有无 kernel/initrd 的明确诊断路径。

## 权限、网络、存储

Docker named pipe 与 WSL 枚举在沙箱最初返回 Access denied/E_ACCESSDENIED；只通过标准 require_escalated 获准后继续，没有绕过审批。构建只需要已有 Docker daemon、对 Debian 官方源的网络读取和任务镜像写入；运行容器 --network none --read-only --cap-drop ALL --security-opt no-new-privileges；QMP 暂停探针还使用 --user 65534:65534。没有 --privileged、--device、宿主磁盘映射、Docker socket 映射或端口发布。

本次解析下载 135 MB、安装增加 255 MB；实测 APT 警告日志原文 245,688,429 bytes，已无损压缩到 957,364 bytes，原 SHA-256 为 6f9c2cd9569b8ed72b582c9a5d81ccffb8db04c15b1941c8be38225f6397df26。镜像内仍保留原始日志。宿主导出/后续 bundle 会压缩日志，避免复制巨大明文。

后续完整组装/boot 前重新要求至少 2 GiB Windows backing drive 可用，建议额外预留 1 GiB 工作量，guest 容器可设 --memory 4g --cpus 2 --pids-limit 128。不删除其他项目/主线程镜像和缓存；迁移 Docker 数据盘、改 WSL 配置或使用 D: 的新目录都超出本轮写授权，需要具体授权。

## 独立验收设计

- kernel serial：QEMU file:kernel-serial.log 包含 Linux kernel 自身版本/启动行、PID1 marker、panic/关机；不能来自容器 uname。
- 扫描输出：QMP screendump 读取虚拟 GPU 的 scanout，每 10 秒捕获 PPM；要见到 MingDesktop chrome、Compose/Motion 内容与变化。MingDesktop --snapshot、offscreen buffer 或 headless Weston 均不能替代。
- 客体证据：virtio-serial org.mingos.evidence 单向导出 guest boot ID、/proc/cmdline、DRM devices、Weston/seatd/desktop argv 和 PIDs、/proc 中独立 worker/PPid、桌面退出状态。日志按 offset/base64 增量导出，强制退出前已有证据仍保留；extract-evidence.py 无损复原。
- 应用真实性：包 receipt/编译器 Wasm hashes 与主线程冻结清单一致；guest desktop 输出内的 app started/frame/worker 生命周期收据与 /proc PIDs、扫描截图对上。只有进程名/两个矩形不够。
- 输入：后续只用 QMP input-send-event 给 virtio tablet/keyboard 发送 guest 事件，验证 Dock/窗口焦点/关闭恢复与帧变化；不操作物理鼠标。QMP 归一化绝对位置使用 0..32767，键盘事件按实际 query-qmp-schema 核对。
- 关闭/恢复：正常桌面结束后 guest 导出最终日志并 poweroff -f；runner 有 10..1800 秒独立 wall deadline。超时或强制 quit 记为 timeout-unaccepted，不能当正常关机通过；随后全新 VM 冷启动复验。
- runner 的真实 boot 结果只标记 guest-exited-needs-acceptance，由独立检查上述交叉证据。截图、进程退出和脚本返回码都不自动判整体系统通过。

## 已知错误、修正与剩余条件

首次 Dockerfile 使用 FROM sha256:...，BuildKit 将它解析为 docker.io/library/sha256，镜像代理 HEAD 返回 403 Forbidden。已改本机 tag，并在 build-tools.py 先核验其 ID；不修改镜像代理或 registry 设置。首次完整原始失败日志保留于 tools-build-20261025/。

外部网页读取工具本轮未返回内容；版本依据来自本机原镜像、官方 apt 源解析和实际下载包/内核，未伪造网站引用。一次追加探针发现工具镜像 tag/ID 被外部移除，返回 No such image；本线程未执行删除或 prune，也无法确定移除来源。随后只读检查发现原 696.1 MB BuildKit 层仍在，C: 空间恢复至约 3.37 GB；用冻结原输入约 0.47 秒全 CACHED 恢复同一 image ID，无下载。证据 tools-cache-restore-20261025/。

运行库探针还实际发现 Weston 私有 libexec_weston 默认 ldd 路径不足，以及复制所有插件时 256 MiB tmpfs ENOSPC；已加入私有库解析，并将闭包缩小到 weston/DRM/kiosk/udev/seatd/Python。失败原文保留于 runtime-copy-20261025/、runtime-copy-private-libs-20261025/，修复后通过记录在 runtime-copy-kiosk-20261025/。

完整 boot 仍待主线程确认 payload 和 session 接口，并在运行前复查宿主空间。静态脚本或暂停探针不能证明 DRM seat/scanout、Wasm guest 内存预算、长期会话和关机恢复已通过。
