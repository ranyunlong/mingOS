# Boot 基础设施 API v1 与历史有界诊断

最新基础 API 见 [INFRA-API-v1.md](INFRA-API-v1.md)，交接见 artifacts/boot-infra-20261025/summary.json 和 freeze.json。

已实际完成：派生 ext4 工具镜像构建、UID65534 owned workspace、128MiB regular ext4/fsck/独占锁、两次同盘暂停 QEMU 附加/UUID/哈希检查。数据盘/guest cgroup/state-driven QMP/cold runner 已实现。主线程已授权的独立 strict-v1 bundle 构建返回0，尚未执行 kernel。

随后 Docker 环境变化：镜像/缓存先消失；从官方固定 Debian 基础恢复时遭遇 BuildKit RPC EOF；最后只读检查 images/containers/volumes/cache 全部为0，新任务volume也不存在。具体是删除还是数据根切换未知。本任务未执行 prune/rm/reset/restart。新盘和 strict-v1 bundle 原位字节当前不可用；原始日志、manifest和下面 v4 的本地完整导出均保留。

当前尚未验证新 guest cgroup/data 挂载、真实持久 marker/两次新bootId、live QMP 输入和 strict kernel/resource probe。等待 Docker 数据环境稳定后，使用 Dockerfile.recovery（固定 Debian digest + recovery-packages.lock，无Rust）恢复工具，再创建新的 workspace/disk generation 和 bundle。不能将新建盘冒充之前的盘；不修改旧review或自动批准最终持续host。

以下为此前真实 v4 诊断记录，镜像/volume的存在状态以新的环境证据为准。

# MingOS boot：有界 kernel/DRM 诊断

2026-10-25：已按主线程明确授权，使用 7C 不可变 frozen payload 和 15 秒 bounded 会话，真实启动 Linux kernel → guest PID1 → udev/seatd → Weston DRM/Pixman/kiosk → MingDesktop → Compose/Motion 两个独立 Wasm worker。最终成功诊断为 bundle-v4/boot-v4；这是诊断证据，不是持久系统、生产隔离、性能或长期会话验收。

## 当前产物和结果

- 工具镜像：mingos-boot-tools:20261025-trixie13，ID sha256:703a0c3e26cff6d9f63375ee9dbdd7b4ad438784db123cdf69443af5a6e57ac5。
- owned volume：mingos-boot-system-20261025-bundle，最终子目录 /work/bundle-v4；bundle-v1/v2/v3 保留对应失败现场。
- 已导出可复验 bundle：artifacts/boot-system-preflight/diagnostic-20261025/bundle-v4/，含 vmlinuz、initramfs.cpio.gz、bundle.json、kernel.config、locks/。导出后再次核验 SHA-256。
- kernel：6.12.113+deb13-amd64，12,154,816 bytes；initramfs 43,071,736 bytes。
- 最新证据：diagnostic-20261025/boot-v4/，原始 kernel-serial.log、guest.jsonl、qmp.json、25 个 PPM；boot-v4-decoded/ 为无损解码的 Weston/seatd/desktop 日志。
- QMP scanout-023.png 实际可见 Motion 中文“帧动画 / 渐显文字 / 动画完成”，Compose 在后方部分被遮挡。PNG 从原始 PPM 无损编码，不来自应用 snapshot。
- MingDesktop parent PID 187，worker PID 190/191，/proc 同时观测到两个 worker 且 PPid=187。Compose/Motion 首次 ready 约为 host 启动后 154.205/187.305 秒；TCG 冷启动慢，不构成性能通过。
- host activeTimeMs=15052，framesComposed=12，framesPresented=14，offscreenFrames=0，native Wayland/softbuffer；desktop exit=0，activeWorkersAfterShutdown=0，guest 正常 power down。
- bounded 结束时两 worker 收据为 APP_CANCELLED，packageId 为 null；不声称应用自然完成。应用身份由 bundle 全文件哈希、catalog/receipt、app ID→worker/ready/frame 收据交叉建立。
- 存在非致命 xkbcommon locale C Compose-file 警告；未做 QMP 输入/IME/持久数据/同一 bundle 冷重启回归。

完整结论见 artifacts/boot-system-preflight/diagnostic-20261025/summary.json 和 boot-v4/diagnostic-report.json。

## 已确认的 payload 和会话

来源 artifacts/linux-desktop-runtime-20261025/frozen/，只复制 ming-app-host、catalog.json、packages/ 到 artifacts/boot-system-preflight/staged/payload/，未带 events.json。

review 位于 artifacts/boot-system-preflight/review/session-review.json：reviewed=true，reviewer='Codex primary; 7C bounded diagnostic authorization'，sessionMode=bounded，exact extraArgs=['--duration-ms','15000']。host/catalog SHA-256 均由实际字节计算。bundle.py 在创建 rootfs 前校验该文件、ELF/Wasm/receipt 和路径边界；vm.py 在启动前核验 bundle/review。当前授权不扩展到取消 fuel/watchdog 或长期会话。

guest 模块包括 virtio_pci、virtio_rng、virtio_gpu、virtio_input、evdev、virtio_console，核验 CONFIG_HW_RANDOM_VIRTIO/INPUT_EVDEV 等；QEMU 使用 rng-random(/dev/urandom) + virtio-rng-pci，无伪造 entropy。记录到 crng initialized 和 guest entropy_avail=256。

## 重放当前已导出的诊断 bundle

在项目根目录 PowerShell 执行，需要标准 Docker daemon 审批。输出目录必须全新。当前配置固定 2GB guest RAM、2 vCPU、TCG、15 秒 bounded app session，外层 480 秒；没有网络、privileged、host 设备或物理输入。

~~~powershell
if ((Get-PSDrive C).Free -lt 2GB) { throw 'BOOT_BLOCKED: Docker backing drive needs at least 2 GiB free' }
$taskRoot = (Resolve-Path .).Path
$tools = 'sha256:703a0c3e26cff6d9f63375ee9dbdd7b4ad438784db123cdf69443af5a6e57ac5'
$vmArgs = @('run','--rm','--pull','never','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user','65534:65534','--pids-limit','128','--memory','4g','--cpus','2','--tmpfs','/tmp:rw,size=32m','--mount',"type=bind,source=$taskRoot\scripts\boot,target=/src,readonly",'--mount',"type=bind,source=$taskRoot\artifacts\boot-system-preflight\review,target=/review,readonly",'--mount',"type=bind,source=$taskRoot\artifacts\boot-system-preflight,target=/evidence",'--mount',"type=bind,source=$taskRoot\artifacts\boot-system-preflight\diagnostic-20261025\bundle-v4,target=/bundle,readonly",$tools,'python3','-B','/src/vm.py','--bundle','/bundle','--review','/review/session-review.json','--output','/evidence/diagnostic-20261025/boot-v4-repeat','--timeout','480')
docker @vmArgs
~~~

runner 正常结束仍标记 guest-exited-needs-acceptance 并返回 1，防止自动宣告验收；需要阅读 guest/desktop 的退出码和交叉证据。system_powerdown 仅是超时尝试，不能保证 ACPI 用户态响应；正常结束由 guest poweroff，超时记为 timeout-unaccepted 后强制 quit。

解码、交叉核验和 PNG 转换入口（实际本轮已执行）：

~~~powershell
python -B scripts/boot/extract-evidence.py --input artifacts/boot-system-preflight/diagnostic-20261025/boot-v4/guest.jsonl --output artifacts/boot-system-preflight/diagnostic-20261025/boot-v4-decoded-repeat
python -B scripts/boot/analyze-diagnostic.py --run artifacts/boot-system-preflight/diagnostic-20261025/boot-v4 --decoded artifacts/boot-system-preflight/diagnostic-20261025/boot-v4-decoded --bundle-manifest artifacts/boot-system-preflight/diagnostic-20261025/bundle-v4/bundle.json --payload artifacts/boot-system-preflight/staged/payload
python -B scripts/boot/scanout-to-png.py --directory artifacts/boot-system-preflight/diagnostic-20261025/boot-v4
python -B -m unittest discover -s scripts/boot -p test_boot.py -v
~~~

## 构建和失败证据

所有实际 Docker argv/exitCode/耗时保存在 diagnostic-20261025/*.command.json。原始 bundle-v1 创建、检查、启动后，按原始错误逐项修复并生成全新目录：

1. v1：真实 kernel/DRM/udev 到达；guest 缺 dmesg applet，在 Weston 前失败。
2. v2：seatd session control、DRM、Pixman 到达；缺内核 evdev 模块，Weston 无 event devices。
3. v3：1280x800 DRM/Pixman/kiosk 与输入到达；冻结 host 缺 libgcc_s.so.1，native exit=127。
4. v4：加入 host ELF 动态依赖闭包后，桌面、两包 frame/ready、原生呈现与正常关机均有证据。

构建不编译或改 Rust/Kotlin；copy_runtime 只取 DRM/kiosk 最小闭包，另解析选定 host 的 ELF 依赖；不需要 host chroot/mknod/loop mount。所有 rootfs、kernel module 加载、udev/seat/DRM 操作都发生在构建数据或 VM guest 内。

工具预检入口仍为 preflight.py、build-tools.py、inspect-tools.py、check-runtime.py、vm.py --probe-paused。工作区 Dockerfile 的日志压缩修订未重建；当前已验证工具镜像对应冻结输入 tools-build-20261025-localbase/inputs/。原 image 曾外部消失，已从保留 BuildKit 缓存恢复同一 ID。不要据容器 df 判断宿主空间，不清理其他任务资源。

下一步由主线程完成 7D 后冻结新 payload/会话接口，再更新 review、生成新版本 bundle，开展 QMP 输入、长期预算、同 bundle 冷启动、持久数据和 Linux UID/namespaces/seccomp/cgroup 验证。
