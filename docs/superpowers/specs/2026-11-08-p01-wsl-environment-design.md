# P01 S2：独立 WSL 环境与 Docker transport 设计

日期：2026-11-08。范围仅为 [P01 总体设计](2026-11-08-p01-clean-bootstrap-design.md) 的 S2；S1、S3–S6 的要求全部保留。用户已授权持续实施与子 Agent 协作，本文和对应计划交由 controller 审查后实施，不另设重复确认门槛。本文不是已通过的实施报告。

## 决策及实际依据

采用新 WSL2 distro、新 VHD、新独立 Docker29.8.1/containerd store，以私有 mount/net/cgroup namespace 启动 daemon。WSL 共用内核及基础设施，不声称独立内核、另一物理宿主或安全虚拟机隔离。QEMU TCG 方案已有最小容器成功证据，但完整构建性能未知，且多一层 guest/磁盘 transport；现阶段不作为正式 S2。直接复用 Desktop daemon 不能证明空 store，排除。自行展开 OCI layers 为 rootfs 会新增 whiteout、链接及 metadata 解释器，当前没有必要，排除。

实验来源：`artifacts/p01-wsl-audit-20261108-8a1c51f2/` 的 `prepare-rootfs.py`、`start-daemon.sh`、各 `*.command.json` 和 `audit-summary.json`；镜像实验在 `image-load-c62467b1/image-audit.py`。新 distro `MingOS-P01-20261108-8a1c51f2`、daemon `20bdecf8-a496-402a-8171-13fa874dc3c3` 实际首次 images/containers/volumes 均为空，containerd snapshotter 和三个固定 index 的 `load_all` 已成功。初次 smoke create 失败是继承的 `XDG_RUNTIME_DIR=/mnt/wslg/runtime-dir` 被私有 tmpfs 遮蔽，TMPDIR/TMP/TEMP 当时未设置。该失败保留；`smoke-g2.py` 只在 daemon 私有 `p01-private-wslg` tmpfs 新建0700/root runtime-dir 后创建新容器，无重启/重载，新容器 c62467b2 已通过，实际 cpu.max=`200000 100000`、memory.max=`4294967296`、pids.max=`256`、cap全0、NNP1、仅lo、exit0。正式实现从启动环境根治，不能将诊断脚本直接冒充公共入口验收。

实验 `audit-summary.json` 的 `outerVolumesUnchanged=false`、`comparison-assertion.json` 的失败必须原样说明：同期 S1 创建了额外容器/卷，不能写成全列表未变。正式入口采用旧 ID 保全比较及新增资源归属清单；未知并发差异报告 `inconclusive`，不能无条件豁免。

## 宿主输入与信任边界

宿主要求 Windows PowerShell、Python3、WSL2 可用、可创建新 distro，Docker Desktop daemon 可读且已有固定 Tools image。不要求 Desktop 空，S2 单独不承诺从空 Desktop 自举。外层 Tools 是明确的环境 rootfs 导出依赖；冷环境验收对象是内层新 daemon。Tools 缺失时 S2 前置检查在任何创建前失败，绝不自动 pull、自动更换摘要或放宽验证。

S6 单入口的宿主准备层必须补齐首次载入：从外部 archive pin 验证后的 handoff，以固定完整成员名白名单、固定 bytes/SHA 提取三个 image archives 到独占新目录，持有同一输入句柄并核对前后身份/hash；用现有严格 development loader 在原 daemon 缺 Tools 时载入并校验固定 Tools，随后调用本 S2 API。若 Tools 已存在也须同样校验 identity/config/rootfs。该宿主准备层属于 S6 的明确完成 gate，不能用“手工安装 Tools”关闭 P01；不需要新增 OCI layer extractor。外层首次载入允许的新增固定镜像应另记准备层基线，S2 的旧资源保全基线在该层完成后建立。S2 当前只负责显式报出缺失前提及可组合接口。

外层 Tools 必须以 `development_image_migration.FIXED['tools']['imageId']` 查询，并核对 OCI index Descriptor、linux/amd64、Config 和 RootFS 与已经严格验证的 tools archive 相同。仅创建 `--pull never --network none`、指定固定 image 的 stopped container，禁用 restart 和 autoremove，inspect 必须证明 State.Running=false、Mounts 为空；`docker export` 仅导出该 ID。保留该 container 及 export tar，不启动它、不删除它。三个 image archives 直接来自调用者提供的 S1 完整导入 `delivery/rebuild-inputs/images/`；S2 运行 `verify_all` 并记录输入摘要，不以 S2 替代 S1 完整恢复或只读重开。

静态引擎 tar 是额外外部输入：Docker29.8.1，86055881 bytes，SHA256 `d8db66739d2e28d4933786d73e918d9be643a67fbd835db1bf740d650a259e70`。记录官方 HTTPS 来源及所有成员摘要，不声称 publisher signature。只接受 `docker/` 单层、唯一普通文件及目录，拒绝链接、特殊成员、路径逃逸、额外文件及重复成员；精确文件集合由首次可信 tar 固化到模块常量并测试，至少含 docker、dockerd、containerd、containerd-shim-runc-v2、runc、docker-init。持有同一输入句柄，复制前后复核 hash/size/stamp。

外层导出 tar 允许 Linux rootfs 必需的链接，不能错误套用 S1 无链接源码合同；不在 Windows 解包它。逐成员重写 tar，保留原 rootfs 的受控 metadata，仅替换 `/etc/wsl.conf` 及 `/usr/local/bin/` 中固定引擎 binaries；拒绝绝对/traversal/重复名、特殊设备及冲突替换目录。链接的相对目标必须在 rootfs 内，绝对目标按 guest 根解析；禁止后续替换路径经过链接目录。输出 rootfs 用 exclusive create、fsync，记录来源 image、container ID、export/rootfs SHA256 和 binaries SHA256。

## 公共模块及接口

创建 `scripts/boot/docker_transport.py`，只处理显式 executable/endpoint 和命令记录，不创建环境。`DockerTransport(executable: str, endpoint: str, config_directory: str)` 提供 `argv(args: list[str]) -> list[str]`、`run(args: list[str], *, timeout: int, stdin=None) -> subprocess.CompletedProcess` 和 `migration_runner(receipt, key, command, timeout, **kwargs)`。endpoint 必须显式 `unix:///` 或外层明确的 `npipe:////./pipe/`，executable及config_directory必须为无traversal/control字符的绝对路径，跨平台识别POSIX和Windows绝对形式；不读当前Docker context，不使用shell。args不得包含第二组`--host/-H`、`--context/-c`、`--config`或TLS连接覆盖选项（含`=value`形式），防止后续argv改写目的地。`migration_runner`校验command[0]等于配置executable，再插入`--host endpoint --config config_directory`，只允许原loader实际使用的stdin kwarg，其余执行覆盖参数拒绝；调用原capture helper时传入清理后的环境及shell=False，维持现有`(result, operation)`返回合同。调用`load_all(root, docker=transport.executable, runner=transport.migration_runner, result_path=...)`，不修改历史loader或现有严格index/config/rootfs合同。

创建 `scripts/boot/wsl_environment.py` 为 Windows controller/CLI；`scripts/boot/wsl_environment_guest.py` 为 Linux 接收、启动、观察及执行边界。公共 controller API：

```python
create_environment(*, generation: str, distro: str, output_directory: Path,
                   wsl_executable: Path, outer_docker: DockerTransport,
                   docker_bundle: Path, archive_root: Path) -> dict
observe_environment(environment_receipt: Path, output_directory: Path) -> dict
exec_environment(environment_receipt: Path, argv: list[str], *,
                 output_directory: Path, timeout: int) -> dict
```

CLI `create` 显式接受 `--generation --distro --output-directory --wsl-executable --outer-docker-executable --outer-docker-endpoint --outer-docker-config --docker-bundle --archive-root`；`observe`/`exec` 接受 `--environment-receipt --output-directory`，exec 另以 `--timeout N -- argv...` 接收 Linux argv。成功 JSON stdout，失败非零且保留失败 receipt。generation 仅 `[a-z0-9][a-z0-9-]{0,47}`，distro 仅 `MingOS-P01-[A-Za-z0-9-]+`；禁止选中既有 distro。所有 Windows 路径必须绝对、现有输入无链接/reparse，输出目录原先不存在。由 generation 唯一派生 guest base `/opt/<generation>`、run `/run/<generation>`、data `/var/lib/<generation>`、cgroup `/sys/fs/cgroup/<generation>` 和 socket `unix:///run/<generation>/docker.sock`，禁止用户提供旧 data-root。

`exec` 是后续 S3–S6 的 Linux argv transport，不把 `C:\...` 转为 `/mnt/c/...`。Windows source/handoff 使用二进制 stdin transfer；guest 源码实际路径由后续 S1 消费/恢复决定并显式传入冷构建，S2 不调用当前依赖 Windows 路径的 `cold-boot.py`，不预先替它宣称 Linux 支持。

guest内部生命周期接口为`launch_environment(generation: str, *, ready_timeout=30) -> dict`，只从generation派生本代路径；`admit_environment(handle: dict, argv: list[str], *, timeout=30, stdin=None)`验证身份、让helper进入management后再次验证，再执行显式Linux argv并返回`subprocess.CompletedProcess`。可选stdin仅转发同一binary文件FD供smoke使用，不放宽argv或transport连接策略。`run_smoke(handle: dict) -> dict`不接收调用者name，由新generation和固定generation label关联本代新容器；`observe_guest(handle: dict, load: dict, smoke: dict) -> dict`显式接收既有load/smoke证明作只读核对。controller内部`create_empty_environment`接收与完整create相同的关键字参数，初始阶段成功仍只返回`creating/empty-daemon-observed`；它不是最终ready入口。

## 创建过程与隔离

1. 创建前完成参数、输入、Tools、WSL能力、目标 distro 不存在及冻结代码核验。输出目录以 exclusive mkdir 占用，一旦存在绝不复用。先落盘请求及外层 daemon/context/image/container/volume 基线，所有命令保存 argv、timeout、stdin SHA/size、stdout/stderr、exitCode 和时间；捕获超时/异常也落盘。
2. 导出/构造 rootfs；仅 `wsl.exe --import <new-distro> <new-output>/distro <new-rootfs> --version 2`，观察新 VHD 的路径/size/identity。wsl.conf 禁用 automount、mountFsTab、interop、appendWindowsPath，systemd=false、default=root。禁止 default-distro、global shutdown、global WSL config、unregister、Desktop settings 操作。
3. 记录 guest mountinfo、namespace IDs、cgroup root controllers/subtree_control、现有 sockets。只在新 generation 目录创建 management/workloads 两个 cgroup leaf。launcher 先进入该 generation cgroup，再 unshare mount/net/cgroup；mount propagation private，重新挂 cgroup2/nsdelegate，以新 generation 作为 namespace root。launcher 移入 management，namespace root 无进程后仅在本代 root/workloads 启用 cpu/memory/pids。不得写共享祖先的 subtree_control；控制器不可用立即失败。
4. 在私有 namespace 用专属 tmpfs 遮蔽 `/mnt/wsl`、`/mnt/wslg`，核验 Desktop shared path、默认 Docker/containerd sockets 不可见。`env -i` 仅保留 PATH、HOME=/root、LANG=C.UTF-8、TMPDIR=/run/<generation>/tmp，显式新建0700 tmp，移除 XDG/GUI、WSLENV、Windows PATH、DOCKER_*、CONTAINERD_*、代理环境。daemon/client config directory 均本代新目录。记录 dockerd 实际 environ，证明 TMPDIR 可写且 XDG_RUNTIME_DIR 等未继承。
5. daemon 配置固定 containerd-snapshotter=true、native.cgroupdriver=cgroupfs、default-cgroupns-mode=private、cgroup-parent=/workloads、独立 namespace/plugins namespace、新 data-root/exec-root/pidfile/unix socket；bridge=none、iptables/ip6tables/ip-forward/ip-masq/userland-proxy=false。不连接外部 containerd。记录受管理 containerd 的命令行、root/state/socket 属于新 generation，不能仅凭 DockerRootDir 推断 containerd 独立。
6. 30秒内通过显式 endpoint 查询 info，校验版本29.8.1、Linux/amd64、cgroup2、cgroupfs、overlayfs/io.containerd.snapshotter.v1。首次查询 images、ps -a、volumes 均退出0且零行、info计数0。首次空证据必须在任何 load/create 前 fsync。失败停止，不重新启动该代。
7. 代码/helper/三个 archives 通过 `subprocess.run(..., stdin=binary_file)` 送至新 guest，接收器逐父目录 `openat(O_DIRECTORY|O_NOFOLLOW)`，终端 `O_CREAT|O_EXCL|O_NOFOLLOW`，流式限制总字节、检查hash、fchmod、fsync。短传、超长、hash错误保留部分文件并使该代失败。禁止 PowerShell 文本管道、base64大包、共享盘挂载。冻结 helper hash 清单含当前 modules 和依赖，每次执行前验证 guest 实际字节；收到的文件不能自报其信任 hash。
8. load helper 进入已验证的 daemon mount/net/cgroup namespace，先移动 helper 至 management，再 nsenter。每次核对 PID starttime、exe hash、namespace IDs、daemon ID 和 socket inode/owner，防止陈旧 PID/receipt。调用现有 `load_all`，所有三包先验后加载，保留 index、Config、RootFS 和 attestation 合同；三角色完成清单必须精确 rust/tools/kotlin。
9. 创建全新 Tools smoke：pull never、network none、read-only、user65534:65534、init、cpus2、memory4g、pids-limit256、cap-drop ALL、no-new-privileges，无 bind/volume/socket、无 autoremove/restart。inspect 前后核对全部参数；容器内读取 UID/GID、所有 capability 集为0、NoNewPrivs=1、只读根、cpu.max quota=2*period、memory.max=4294967296、pids.max=256、仅lo、空route及Desktop路径不可见。start --attach 成功还必须 inspect ExitCode=0、OOMKilled=false、Running=false。保留容器。
10. 核对外层旧资源及 shared cgroup controls，输出成功 receipt 和独立观察。后续复核只读取 receipt 和实际环境，不修改输入、不重试 load/create、不修复失败代。

## 证据、失败及交付标准

`environment-result.json` 包含 schemaVersion=1、generation/distro、VHD identity、rootfs/export/bundle/helper/archive pins、outer endpoint/daemon ID、inner executable/endpoint/config/data/exec/containerd paths、PID/starttime/ns/socket identities、first-empty 清单、load receipt、smoke ID/有效限制、旧资源比较、所有命令日志索引及失败 phase。状态只允许 `creating`、`failed`、`environment-ready`；另含 `formalQualification=false`、`finalSystemAccepted=false`、`coldBuildExecuted=false`、`resourcesPreserved=true`。阶段文件 exclusive create；总 receipt 创建一次后原句柄 fsync 更新，不宣称断电原子日志。

外层基线用 immutable IDs（镜像 index digest、container ID、volume name+CreatedAt+Driver+Options+Labels）及容器配置/挂载/状态保全；忽略 RunningFor 等时钟文案。未知旧资源删除、配置/挂载/状态变化阻断成功，新增资源逐一记录调用者 labels/ID；本入口仅允许自己的 stopped export container。并发已授权任务新增资源记录归属与证据，无法归属为 inconclusive。context名称/endpoint、daemon ID、旧镜像集合必须保持。共享 cgroup root 控制位前后逐字相同。

2026-11-08独立设计复核批准一条精确的配置语义比较规则：仅在前后实际外层`ServerVersion`均严格等于字符串`29.8.1`、绑定endpoint/context/daemon身份不变时，两个字典`HostConfig`中均存在的精确`Dns`键可将`null`与实际空数组视作等价，双向均适用。只比较副本，保留完整原始对象、命令日志和收据；不得写旧资源API或磁盘以归一化。每次实际等价必须记录policy ID、container ID、JSON path、原始before/after、两次版本及固定语义源码引用；完全相等时等价列表为空。缺键、错误类型、非空或重排DNS、缺失/变更/不支持版本、`DnsSearch`/`DnsOptions`及所有其他HostConfig键的存在和值、Config/Mounts/State、身份、镜像/卷、成员集合、新增资源和共享cgroup仍按原合同严格比较。

语义依据为固定官方Moby v29.8.1源码归档SHA256`94be9d6940b613676335fc494e617b4acf98676435b3744f32649ed72114bd58`：`daemon/container/container.go`的`readHostConfig`调用`InitDNSHostConfig`，将nil DNS初始化为空切片；`daemon/container_operations.go`仅在长度大于0时设置容器DNS覆盖。版本门是兼容性限制，不是外层引擎二进制认证。实际日志支持两次失败区间内VM唤醒和daemon容器加载，未确定精确触发API/行为者；不声称inspect自身执行归一化。该规则不改判任何历史失败代。必须经实现负控、独立源码复核、提交和另一个全新代实际create/observe/exec及独立验收，才能关闭S2。

任何失败都保存 partial outputs/WSL/VHD/容器/卷/日志，不 delete/prune/reset/unregister、不原地重试。超时后观察到进程还在运行时记录 `processMayRemain=true`；不能根据外层命令退出0判定 guest成功。只有正式 create 入口在另一个新代完成全部步骤、单元负控及独立只读 observe 才能关闭 S2。该成功不关闭 S3–S6、不把11项目进度提升、不声明冷构建/持久化端到端完成。
