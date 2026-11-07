# Boot 基础设施 API v1 与历史有界诊断

最新基础 API 见 [INFRA-API-v1.md](INFRA-API-v1.md)，交接见 artifacts/boot-infra-20261025/summary.json 和 freeze.json。

已实际完成：派生 ext4 工具镜像构建、UID65534 owned workspace、128MiB regular ext4/fsck/独占锁、两次同盘暂停 QEMU 附加/UUID/哈希检查。数据盘/guest cgroup/state-driven QMP/cold runner 已实现。主线程已授权的独立 strict-v1 bundle 构建返回0，尚未执行 kernel。

随后 Docker 环境变化：镜像/缓存先消失；从官方固定 Debian 基础恢复时遭遇 BuildKit RPC EOF；最后只读检查 images/containers/volumes/cache 全部为0，新任务volume也不存在。具体是删除还是数据根切换未知。本任务未执行 prune/rm/reset/restart。新盘和 strict-v1 bundle 原位字节当前不可用；原始日志、manifest和下面 v4 的本地完整导出均保留。

当前尚未验证新 guest cgroup/data 挂载、真实持久 marker/两次新bootId、live QMP 输入和 strict kernel/resource probe。等待 Docker 数据环境稳定后，使用 Dockerfile.recovery（固定 Debian digest + recovery-packages.lock，无Rust）恢复工具，再创建新的 workspace/disk generation 和 bundle。不能将新建盘冒充之前的盘；不修改旧review或自动批准最终持续host。

## Qualification 资产恢复

默认模式和 `-ValidateOnly` 仅校验归档文件与 manifest 的 SHA-256 是否一致，以及 manifest 声明的 rust、tools、openssl image ID 是否等于历史合同。成功状态为 `archive-hashes-matched`；这不证明 tar 内容有效、归档包含声明的镜像或资产来源可信。这两个模式均不调用 Docker。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/boot/restore-qualification-assets.ps1 -AssetRoot C:\path\to\qualification-assets -Manifest C:\path\to\qualification-assets\manifest.json -ValidateOnly
~~~

`-ValidateOnly`、`-LoadImages`、`-RestoreVolume` 三个显式模式互斥。`-LoadImages` 通过 Python 3 helper 先验证全部三个 Docker save 归档：每档唯一镜像、config 原始字节 SHA-256 等于历史固定 image ID、config 的每个 `rootfs.diff_ids` 等于对应 layer 解压后的 SHA-256。支持 legacy `layer.tar`、blob 路径和 gzip layer；含 OCI index 的归档只接受能绑定相同 config/layers 的单一引用链。多镜像、多平台、外部引用、重复成员/JSON key、路径穿越、link/device 和额外 payload 均拒绝。OCI-only 导出不在支持范围。

全部归档通过后，helper 在私有临时文件中生成只含已验证 config/layers 和空 `RepoTags` 的 tar，通过 stdin 顺序执行 `docker image load --quiet`，每次随后 `docker image inspect` 核对固定 ID。源标签及其他引用不会传给 Docker；工具不提供删除、覆盖 volume 或替换固定摘要的功能。失败立即停止并输出已发生操作和首个失败，保留已加载镜像。成功状态为 `image-load-verified`，`formalQualification=false`、`finalSystemAccepted=false`。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/boot/restore-qualification-assets.ps1 -AssetRoot C:\path\to\qualification-assets -LoadImages -PythonExecutable C:\path\to\python.exe
~~~

可省略 `-PythonExecutable`，入口优先使用 Codex bundled Python，再查找 PATH 中的 Python 3。镜像加载不能补足历史宿主缓存或构成正式资格验收。

### RestoreVolume 合同与入口

`-RestoreVolume` 已实现，要求外层 schemaVersion 1 manifest 中仍有三个固定历史 image ID、对应归档及 SHA-256，并具有 `volume` 对象（`name`、`archive`、`archiveSha256`）。此模式核对三个镜像归档的文件哈希，但不加载镜像，也不以此证明其镜像内容有效；镜像内容绑定由独立 `-LoadImages` 模式处理。`volume.name` 仅记录来源，绝不作为目标卷名。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/boot/restore-qualification-assets.ps1 -AssetRoot C:\path\to\qualification-assets -RestoreVolume -ReceiptRoot C:\existing-evidence\new-restore-run -PythonExecutable C:\path\to\python.exe
~~~

`-ReceiptRoot` 必须是父目录已存在的全新目录；省略时使用仓库 `artifacts/qualification-restore/` 下的唯一目录。helper 固定为本地 Linux amd64 image `sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80`，所有 create 使用 `--pull never`。`-VolumeHelperImage` 默认即此 ID，任何不同值均拒绝，不下载或替换缺失 helper。

卷归档只接受未压缩的严格 USTAR，必须恰好包含一个根级 `restore-manifest.json` 和清单中的全部 payload。清单示例：

~~~json
{"schemaVersion":1,"uid":65534,"gid":65534,"entries":[{"path":"cache","type":"directory","uid":65534,"gid":65534,"mode":493},{"path":"cache/data","type":"file","uid":65534,"gid":65534,"mode":420,"size":5,"sha256":"2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824","nlink":1}]}
~~~

JSON mode 为十进制整数（493=`0755`，420=`0644`）；tar header 的 owner/mode/size 和文件内容必须与清单一致。每层父目录须显式列出；文件仅支持 `0600/0644/0700/0755`，目录仅支持 `0700/0755`，实际文件要求 regular、单链接和 UID/GID65534。拒绝 PAX/GNU、gzip、link/device/FIFO、路径穿越、重复成员/JSON key、隐式父目录、非零 padding 和额外 payload；普通 Docker tar 导出不保证符合此格式。上限为归档64GiB、单文件32GiB、嵌入清单16MiB、payload 100000项。

完整归档在任何 Docker 调用前经过私有副本验证，Linux worker 在写 payload 前再次独立验证。creator 用省略 source 的 `volume-nocopy` mount 原子分配全新 Docker 匿名卷，随后核对精确 CID、nonce、镜像及 requested/effective mounts。根与 `control/` 为 UID/GID65534、`0700`；root initializer 仅保留 CHOWN，实际恢复和验证 worker 为 UID/GID65534、cap-drop ALL、禁网、只读 root。缓存位于 `payload/`，收据明确 `subpath="payload"`；`control/` 内的私有归档、lease 和完成收据不属于缓存。

写入使用相对目录描述符、NOFOLLOW/EXCL、协作独占 lease、完整字节哈希和 file/directory fsync；不接纳已有目标，也不覆盖或续跑失败卷。确认 writer 正常停止后，另一个容器以真实 read-only volume mount 重开，通过 `ST_RDONLY`、外部完成收据绑定及完整 inventory/owner/mode/nlink/内容哈希核验。成功状态为 `volume-restored-readonly-verified`。每个命令前后保留顺序收据及完整 stdout/stderr；失败或超时立即停止后续阶段，私有副本、部分输出、所有容器和卷均保留，不执行 rm/prune/删除或回滚。Windows 主机目录 fsync 不可用时收据明确 `hostDirectoryFsync=false`。

2026-11-07 已通过两路并发的真实公共入口恢复，各获得不同匿名卷并经独立容器只读重开；外层来源名故意指向已有 sentinel 卷，其 inode、owner/mode、nlink 和内容 SHA 在操作前后不变。真实固定镜像 Linux 综合控制110项、108通过、2个非Linux边界跳过。证据在 `artifacts/environment-rebuild-20261107/restore-volume-root-controls/restore-live-b144eca66993/` 的 `result.json`、`public-{1,2}.stdout.log` 与 `linux-controls-run.stderr.log`。这些输入是 synthetic 控制数据，`historicalAssetsRestored=false`，没有恢复历史缓存或加载历史镜像。工具及控制成功始终保持 `formalQualification=false`、`finalSystemAccepted=false`。

### 历史 Task9 的实际依赖

`artifacts/persistent-update-20261104-prep/run-stage.py` 还要求 Darwin/APFS、原宿主 `.cache/linux-rust/{cargo,target/release}`、历史 Kotlin 构建及网络隔离收据、bootstrap/更新/回归 fixture 和 OpenSSL 来源。prepare 从宿主缓存复制到全新 build volume，并拒绝已有目标卷；一个归档恢复卷不能替代宿主缓存。仅取得三个镜像和一个卷不足以执行该驱动。

2026-11-07 用户确认目前无法访问原 macOS/APFS 环境或完整备份。后续在当前 Windows + Docker/Linux 环境建立独立工具链、fixture 来源及存储合同，以新代构建和资格证据推进；历史失败代、摘要和验收状态保持原样。新环境完成依赖准备也不代表正常重启或完整系统验收通过。

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


## 关闭诊断的 worker 资源收据（2026-10-26）

新源码在启用 input diagnostics 的 profile 中，沿用每秒一次的 `process-table`，为
`__worker` 记录增加 `resources`。普通 profile 和关机前残留 worker 检查保持原字段与完整枚举。
`worker_resources.py` 随新 bundle 打包并计入 `scriptSha256`；历史冻结 bundle 不受影响。
当前只完成本地测试，尚未构建或启动包含此变更的新 kernel bundle。

- `sampleStartMonotonicNs` / `sampleEndMonotonicNs`：guest 的 monotonic 采样窗口；各文件不是原子快照。
- `process.pid` / `startTicks`：识别 PID 复用；`userTicks` / `systemTicks` 是累计 CPU tick，单位由
  `clockTicksPerSecond` 明确给出。比较样本还须匹配同一 boot、PID、startTicks 和 cgroup。
- `cgroup.cpuStat`：原始累计 `usage_usec`、`user_usec`、`system_usec`、`nr_periods`、
  `nr_throttled`、`throttled_usec`，区分 CPU 使用量和限流时间；`cpuMax` 只读当前 quota/period，
  单位微秒，`quotaUsec=null` 表示 `max`。不能把限流累计值直接当作本次 Close 的延迟。
- `status=sampled` 才表示读取成功。`outside-owned-cgroup`、`identity-changed`、
  `unavailable`（带异常类型）和 `invalid-data` 显式保留未知；不得用缺失样本或零值代替回收证据。

采样仅访问 `/mingos/app-<app>-g-<generation>` 对应的 owned cgroup，每个资源文件最多读取
4096 字节及 1 字节溢出探测。采样前后复核进程启动标识和 cgroup 路径；采样失败不删除进程表中的 worker。
这些时间戳不能直接减去 Rust 自有 `Instant` 原点。父侧共用时钟的 Close/SDK/Result 时间线仍须补齐。

本地完整回归（需要 Python 3.11+；本轮使用 3.12.14）：

~~~sh
python3 -B -m unittest discover -s scripts/boot -p 'test_*.py'
~~~

## Windows 宿主的新 Linux 冷构建入口

`new-linux-host-build.ps1` 创建唯一的新 source/prep/target 卷，检查固定 Rust 镜像、
Node 字节和容器实际隔离条件，再执行存储 probe、独立容器 reopen 与六包重放冻结。
它要求先准备经过校验的 Linux Kotlin 包卷、Cargo 离线卷和 runtime inputs；具体当前
来源见 `docs/development-environment-20261107.md`。它不会下载或修补缺失的输入。

~~~powershell
pwsh -NoProfile -File scripts/boot/new-linux-host-build.ps1 `
  -RuntimeInputs C:\absolute\verified-runtime-inputs `
  -PackagesVolume verified-linux-kotlin-packages `
  -CargoVolume verified-linux-cargo-cache `
  -BackingVhd C:\absolute\docker_data.vhdx `
  -StartBuild
~~~

`-EvidenceRoot` 可指定 `repo/artifacts` 下全新的证据目录。`-StartBuild` 异步启动，
返回只表示已启动；须按 `build-started.json` 中的精确 CID 查看容器退出状态和
`/prep/stages/build/result.json`。失败代不续跑、不复用 target，所有输出和卷保留。

独立 runtime CLI 为 `linux_host_runtime.py --stage prepare|fixture|bundle|admission`，
prepare 要求显式的成功冷构建、源码、六包、checker、几何执行、原生输入、tools 和
source-v2 probe 路径；后续阶段使用 `--root /prep --repo /repo`。
`linux_host_pair.py --root /prep --repo /repo` 仅接受已经完成 admission 的同一冻结。
外层协调器仍须实施1950秒容器限制及既定 mount/UID/资源检查。这些入口与控制测试
不能替代实际双启动与独立导出验收。

## Windows 宿主的运行时与可迁移重放入口

`new-linux-host-runtime.ps1` 使用成功冷构建的 `generation.json`、已核验的
assets/packages 卷及 runtime inputs，依次执行下面的独立阶段。每阶段只允许一次尝试，
失败保留容器、卷、盘和原始输出。源码快照隔离后续工作区开发；工具清单默认为
`tools-manifest3.stdout.log`，也可通过 `-ToolsManifestName` 明确选择一个叶文件名，
选择随状态冻结，后续不能替换。

~~~powershell
$state = 'C:\absolute\new-runtime-state.json'
$coordinator = '.\scripts\boot\new-linux-host-runtime.ps1'
& $coordinator -Stage new -StatePath $state `
  -ColdStatePath C:\absolute\cold-generation\generation.json `
  -RuntimeInputs C:\absolute\verified-runtime-inputs `
  -AssetsVolume verified-runtime-assets
foreach ($step in 'storage-probe','storage-reopen','prepare','fixture') {
  & $coordinator -Stage $step -StatePath $state
}
~~~

fixture 导出 `review-base.json` 与实际签名核验结果。独立审阅具体 payload、源码、
输入、仪表和预算后，在完整字段一致的副本上明确设置 `reviewed=true` 和非空
`reviewer`，再用 `-Stage bundle -ReviewPath C:\absolute\review.json`。
协调器和 helper 不会生成批准。之后逐步运行 `admission`、`pair`、`export`、
`verify`、`replay`；pair 每轮900秒、外层1950秒，第一轮失败不执行第二轮。

export 创建独立输出卷；verify/replay 仅挂载密封导出为只读与新的结果卷，使用
导出自身的 stdlib bootstrap 和 checker。阶段性的 `/prep` 副本不是 portable export。
公共冷构建把容量收据放在 `receipts/` 目录，运行时协调器保留 `/cold-receipts`
映射用于独立重放。实现与控制测试通过不表示实际双启动或完整系统已验收。

卸载使用独立入口 `new-linux-host-uninstall-runtime.ps1`，阶段和显式 review 流程相同。
其冷构建必须由 `new-linux-host-build.ps1 -ColdProfile persistent-uninstall-linux-host`
产生，包卷包含 `read-file`、`image-viewer`、`text-entry-demo`、`uninstall` 四个目录。
`uninstall` 必须来自 `persistent-uninstall-demo` 的真实离线编译，身份为
`dev.ming.install-demo/1.0.0/SDK6`。七条冷构建命令、卸载 helper、七步/一步输入、
专用 checker 与实际几何执行收据全部独立绑定；update 冷代不能准入卸载运行。
专用实现与合成回放通过仍不表示已执行真实卸载双启动或保留私有数据的资格。
