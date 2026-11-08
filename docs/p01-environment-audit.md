# P01 环境可行性记录

日期：2026-11-08。本记录是部署环境调查证据，不是 P01 完成交付记录。完整门槛见 [P01 设计](superpowers/specs/2026-11-08-p01-clean-bootstrap-design.md)。

## 已实际运行的独立 guest

固定 Tools 镜像 `sha256:a085efa9e8b4958b1943ee87857313f3d2666f696f730f242daef643347b0091` 包含 Linux 6.12.113+deb13-amd64、QEMU、静态 BusyBox 和所需模块，但未包含 Docker 引擎。新增开发环境输入为 Docker29.8.1 static archive，86055881 bytes，SHA256 `d8db66739d2e28d4933786d73e918d9be643a67fbd835db1bf740d650a259e70`；已记录 HTTPS 来源和成员摘要，不宣称生产发布者签名认证。

实验使用非特权、无宿主 socket、只读根、UID65534、drop ALL capabilities、no-new-privileges、network none 的外层容器，QEMU TCG 启动独立 Linux guest。每代新建独立2GiB ext4磁盘；旧盘从未重跑。

| 实验 | 实际结果 | 范围 |
| --- | --- | --- |
| guest-a | daemon启动且store为空，容器start因 `pivot_root: invalid argument`失败，PID1退出导致panic | 失败。即使QEMU/外层exit0，也不能改判成功 |
| guest-b | `DOCKER_RAMDISK=true`单变量诊断后，BusyBox容器实际运行/exit0，daemon正常停止并关机 | 证明initramfs根限制的原因；overlay2模式不满足最终OCI index合同 |
| guest-c | switch_root到新tmpfs，不用DOCKER_RAMDISK；containerd snapshotter启用，空store，真实容器运行/exit0，daemon停止、ext4卸载和ACPI关机 | 独立内核及新daemon的最小容器可行性通过；尚无固定三镜像加载或冷构建结论 |

guest-c daemon ID：`81326b10-1096-4e41-a8a0-0303a61dd95e`；driver `overlayfs`，driver-type `io.containerd.snapshotter.v1`，cgroup2/cgroupfs。磁盘 UUID：`f29087cd-b382-4214-8147-162b2cb01f23`。QEMU耗时55.00秒，无timeout或OOM。

保留证据在本机 `artifacts/p01-guest-audit-20261108/`，含 `REPORT.md`、每代脚本/模块输入、命令、串口、结果、容器inspect与独立磁盘。b/c另保留精确initramfs；a的tmpfs initramfs未成功导出，只保留构造脚本及输入，不宣称其原始字节保留。

## 完整构建路径的下一步

单次新容器的 `/dev/kvm` 探测在启动前失败，设备不存在；保留Created容器和日志，未修改Desktop/WSL。TCG实验未做Rust/Kotlin编译基准，不能从最小容器的55秒推断冷构建性能。

为执行完整实际冷构建，已验证独立WSL distro及其新VHD、新daemon和独立containerd store。普通沙箱下的 `E_ACCESSDENIED` 是访问限制，不是宿主WSL损坏证据。WSL版本2.7.14.0，kernel6.18.33.2-2，Windows10.0.26200.9711。

新distro实验不得修改默认distro、全局WSL配置、现有Desktop、现有socket或存储，不执行全局shutdown或unregister。禁用新distro的Windows盘自动挂载和interop；先实测cgroup/共享挂载边界，再决定是否启动只使用新配置及Unix socket的daemon。WSL共用基础设施，不声明独立内核或物理宿主隔离。最终部署方案以实际工具链加载、资源限制和冷构建证据选择。

## WSL实际新存储与容器实验

证据根为`artifacts/p01-wsl-audit-20261108-8a1c51f2/`。新distro `MingOS-P01-20261108-8a1c51f2`使用该目录下新`distro/ext4.vhdx`；rootfs从固定Tools镜像的保留stopped容器导出后，加入固定Docker static binaries和禁用automount/interop/systemd的`wsl.conf`。原export SHA256为`89112ff53cacdae8ffe1b3c7c3653b6ac144b25863bca3847132feab0bfb8a74`，派生WSL tar SHA256为`74b938e0c30b07f14103ed138cfd5d2fe87e37365bd7719e5f65c10471b597b0`。该实验明确依赖外层已有固定Tools image，不是无DockerDesktop前提的源码自举。

| 身份/观察 | 实际值 |
| --- | --- |
| 新daemon ID | `20bdecf8-a496-402a-8171-13fa874dc3c3` |
| daemon/containerd PID | 12 / 28，单独后续调用仍存活 |
| socket | `unix:///run/mingos-p01-8a1c51f2/docker.sock` |
| data-root | `/var/lib/mingos-p01-8a1c51f2/docker` |
| 初始store | 0镜像、0容器、0卷 |
| driver | `overlayfs`，`io.containerd.snapshotter.v1` |
| namespace边界 | 新mount/net/cgroup namespace；专用management/workloads子树；共享祖先controllers/subtree_control前后不变 |

启动已关闭bridge/iptables/ip6tables/forwarding/masquerade/proxy。只有daemon私有mount namespace遮蔽`/mnt/wsl`、`/mnt/wslg`并建立新cgroup视图；未修改原WSL共享挂载、默认发行版或Desktop配置。

三固定工具链归档从原handoff只读逐一提取到新目录，整包摘要前后匹配，各归档固定bytes/SHA匹配，经binary stdin传入新distro的ext4输入路径。现有`development_image_migration.load_all`与`qualification_image_archive`代码保持原样、分别记录hash；wrapper始终指向新socket。一次load的完整结果位于`image-load-c62467b1/load-result-receipt.stdout.log`，状态`development-toolchain-load-inspect-verified`，rust/tools/kotlin各完成load及inspect，精确OCI index、ordered RootFS diffIDs、runtime config均匹配，没有platform剥离或index改写。attestation只验证字节及subject绑定，不声明签名真实性。

成功smoke新代`mingos-p01-wsl-tools-smoke-c62467b2`的容器ID为`42952f845737ce9d2dd0a53df2517ee3b5698cacd84aec4eec5848cd9062ebc3`。实际读取UID/GID65534、只读根、五组capabilities全0、NoNewPrivs=1、`cpu.max=200000 100000`、`memory.max=4294967296`、`pids.max=256`，网络只有lo且无路由，Desktop共享路径不可见，tmpfs写入成功，精确容器exit0且非OOM并保留。

首smoke在create时因`XDG_RUNTIME_DIR=/mnt/wslg/runtime-dir`指向被私有tmpfs遮蔽后的缺失目录失败；TMPDIR/TMP/TEMP均未设置。失败命令及stderr保留。核实`/mnt/wslg`确为daemon私有`p01-private-wslg` tmpfs后，仅独占新建其`runtime-dir`（0700/root），再用全新smoke名验证；daemon未重启、镜像未重载。正式launcher必须在启动前清除GUI运行目录继承、显式设置受控TMPDIR并校验路径，不能靠手工修补关闭S2。

完整汇总为`image-load-c62467b1/load-and-smoke-summary.json`。主执行者独立重核原loader六次操作、三个原index/config/rootfs、smoke原始输出/inspect、helper hashes及共享祖先未变，并在随后只读调用中确认同一daemon ID、3镜像/1容器。外层Desktop ID和镜像集合保持；无原容器/卷删除。并行S1 import从Running正常变为Exited0及新增reopen容器均单独归因，不宣称完整外层清单字节相同。

## S2公共输入模块的实际验收

公开`docker_transport.py`已完成显式endpoint/config、环境清理及原loader适配。独立复核发现的组合短选项准入缺口已修复；9项transport控制和16项迁移回归通过，复核批准仅覆盖该模块。

公开`wsl_environment.py`与`wsl_environment_guest.py`的输入冻结、rootfs构造、命令记录和Linux二进制接收器已完成独立复核。补齐有效首成员后的重复检查及header-only超大成员控制后，Windows14项通过；最终冻结Linux快照运行18项，17项通过、1项Windows junction专属控制明确跳过，四项真实Linux接收器控制均执行。证据为`artifacts/p01-s2-linux-green-20261108-31a4a996/`，精确容器`d2a0e4af516ef18e63c0fcf637bef543af793f815677e32f53a70e0bf3796349`退出0且非OOM，资源保留。

实际公开`prepare_rootfs`消费原保留export及固定Docker bundle，独立tar遍历比较全部17,278个原成员的内容、模式、属主与链接语义，精确新增8个二进制及`wsl.conf`，输出17,287成员。输出1,460,418,560字节、SHA256为`74b938e0c30b07f14103ed138cfd5d2fe87e37365bd7719e5f65c10471b597b0`，来源输入前后摘要不变；结果与原实验字节一致，但验收按独立成员比较完成，没有采信历史结果替代本次执行。完整证据为`artifacts/p01-s2-actual-rootfs-20261108-e5a18d06/`。

这些结果关闭S2计划的transport及输入任务。生命周期初始阶段随后新增`create_empty_environment`、`launch_environment`及`admit_environment`：固定endpoint与完整Tools核对、新distro/VHD导入、私有namespace/cgroup配置、首次空store留存、实际daemon环境及managed-containerd路径检查、PID/starttime/exe/ns/socket/daemon身份复核。初始阶段成功仍为`creating/empty-daemon-observed`，不返回最终ready状态。

生命周期提交`9f4d0f3`已完成独立spec/quality复核。Windows控制41项运行、37通过、4项Linux接收器明确跳过；完整冻结Linux闭包含9项transport控制，共50项运行、49通过、1项Windows junction明确跳过，四项Linux接收器全部执行。精确容器`6ceb3601a2b5d9f7b166858d84749a6e4896ff04ac7a75bf3b74019896731762`退出0且非OOM；证据为`artifacts/p01-s2-lifecycle-controls-20261108-79c8c7e0/`。首Linux快照`e119d19f`因测试把Python符号链接当普通输入而失败，保留失败容器和日志，仅将测试夹具改为解析后的真实文件，生产nofollow策略不变。

另从S1已验收的只读重开容器复制三固定镜像至`artifacts/p01-s2-s1-inputs-20261108-febd2fdb/archives/`，严格`verify_all`通过，来源容器前后inspect一致。首复制准备代`cd6b8798`因controller误用导入容器的挂载点而在只读inspect处失败，尚未复制；原代保留，按实际只读重开挂载点`/imported`使用新输出代完成。

以上生命周期验证是实现与模拟控制验收，不证明已经通过公共入口创建实际新WSL环境。完整create/observe/exec、严格三镜像加载、有效smoke及另一全新代真实集成仍须完成，不构成S2或P01整体验收。

## 状态

P01 S1公共严格导入已通过实际大包导入和独立只读重开及独立复核。WSL实验已完成三镜像加载和实际受限容器，但S2公共化及S3–S6源码/缓存消费、真实编译、新包装配和持久化验收继续。全部11项新项目仍为0项完成并发布；原开发VM交付与正式验收标志保持原范围。


## S2公共入口与首次正式代的真实阻断（2026-11-08）

Task4公共create/observe/exec已完成实现与独立复核，提交`f00331c`。原完整冻结Linux控制为129项运行、128通过、1项Windows junction专属跳过；attach异常后最终inspect修复的受影响Linux guest控制另为33/33。它们是两次独立执行，不合并成一次完整最终测试。

正式公共create只在全新`MingOS-P01-20261108-554bde65`执行一次，证据目录`artifacts/p01-s2-formal-20261108-554bde65/`。create退出0，新VHD/daemon、首次空store、三固定镜像load/inspect及有效smoke均有原始记录，但独立审计发现first-empty与最终info中的`Containerd.Namespaces.Plugins`实际均为`plugins.moby`，配置/收据意图却为本代`-plugins`。因此本代明确为**S2 NOT ACCEPTED**；不原地改配置、重启或重载后改判。

固定Docker29.8.1官方源码的JSON键为单数`containerd-plugin-namespace`；原实现写成复数且未验证info返回的实际namespace。修复同时校验实际Containers/Plugins两值，覆盖初始launch、每次live handle admission、observe、保留first-empty及宿主ready receipt回放；默认、缺失、异代和格式错误必须拒绝，历史loader保持原字节。真正RED为8项测试17个断言失败；修复后Windows受影响72项运行、67通过、5项Linux专属跳过。冻结完整Linux公共/导入回归在`artifacts/p01-s2-public-controls-20261108-a1b29ded/`运行139项、138通过、1项Windows junction专属跳过、零失败/错误，精确容器`0fe7dac7910f830b27e644abb8235976a57b23695705ad575d5c48ea56b068cc`退出0且非OOM。修复已通过独立spec/quality复核；另一个全新实际环境代仍待通过。

首次独立观察器还发现自身过严的共享mount、覆盖挂载和无标签镜像listing假设；原失败完整保留。修订后的独立证据须真实读取O_PATH/fdinfo可见mount、逐namespace核对内部owned peer无外部传播，并用实际info计数和三个固定index完整inspect核对store，不能用假设fixture填成实际通过。公共observe/exec仍须在修复后新代另取独占证据。S2未关闭，P01及11项目总交付保持0/11；正式标志均false。
