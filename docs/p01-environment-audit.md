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

P01 S1公共严格导入已通过实际大包导入和独立只读重开及独立复核；S2公共环境随后在全新f4244bac通过21门实际独立验收，详见本文末尾。S3–S6源码/缓存消费、真实编译、新包装配和持久化验收继续。全部11项新项目仍为0项完成并发布；原开发VM交付与正式验收标志保持原范围。


## S2公共入口与首次正式代的真实阻断（2026-11-08）

Task4公共create/observe/exec已完成实现与独立复核，提交`f00331c`。原完整冻结Linux控制为129项运行、128通过、1项Windows junction专属跳过；attach异常后最终inspect修复的受影响Linux guest控制另为33/33。它们是两次独立执行，不合并成一次完整最终测试。

正式公共create只在全新`MingOS-P01-20261108-554bde65`执行一次，证据目录`artifacts/p01-s2-formal-20261108-554bde65/`。create退出0，新VHD/daemon、首次空store、三固定镜像load/inspect及有效smoke均有原始记录，但独立审计发现first-empty与最终info中的`Containerd.Namespaces.Plugins`实际均为`plugins.moby`，配置/收据意图却为本代`-plugins`。因此本代明确为**S2 NOT ACCEPTED**；不原地改配置、重启或重载后改判。

固定Docker29.8.1官方源码的JSON键为单数`containerd-plugin-namespace`；原实现写成复数且未验证info返回的实际namespace。修复同时校验实际Containers/Plugins两值，覆盖初始launch、每次live handle admission、observe、保留first-empty及宿主ready receipt回放；默认、缺失、异代和格式错误必须拒绝，历史loader保持原字节。真正RED为8项测试17个断言失败；修复后Windows受影响72项运行、67通过、5项Linux专属跳过。冻结完整Linux公共/导入回归在`artifacts/p01-s2-public-controls-20261108-a1b29ded/`运行139项、138通过、1项Windows junction专属跳过、零失败/错误，精确容器`0fe7dac7910f830b27e644abb8235976a57b23695705ad575d5c48ea56b068cc`退出0且非OOM。修复已通过独立spec/quality复核；另一个全新实际环境代仍待通过。

首次独立观察器还发现自身过严的共享mount、覆盖挂载和无标签镜像listing假设；原失败完整保留。修订后的独立证据须真实读取O_PATH/fdinfo可见mount、逐namespace核对内部owned peer无外部传播，并用实际info计数和三个固定index完整inspect核对store，不能用假设fixture填成实际通过。公共observe/exec仍须在修复后新代另取独占证据。S2未关闭，P01及11项目总交付保持0/11；正式标志均false。


## 固定引擎JSON/CLI解析差异与第二次正式代（2026-11-08）

已复核的首轮修复提交`d71b79d`在全新`8039baf3`执行正式create。该次于launch失败，尚未load或smoke；实际stderr明确拒绝`containerd-plugin-namespace`，因此不是实际namespace检查被放宽后成功。发行版随后停止，诊断先核对running清单并拒绝自动启动；以`wsl --export`另取只读文件系统归档后读取原始日志，导出后仍未运行该发行版。归档`offline-log-export-2e7ebe6f/failed-rootfs.tar`为1460387840字节、SHA256 `a92445cc455cc2028c42f93d98ea8c53797308811c1e0d01a8a53a37027316de`，原盘/配置/日志不改，原代不重试。

进一步核对固定版本源码发现JSON struct tag为单数，known-option检查却按复数CLI注册名查找：单数JSON被拒绝，复数JSON虽可解析却未赋给实际字段。这解释了554bde65实际使用默认值和8039baf3启动失败。官方字段标签本身不足以证明配置可用，先前源码级修复结论不等于运行时修复成功。

使用同一固定bundle的dockerd二进制，在独立受限容器仅执行`--help`和`--validate`：复数JSON退出0、单数JSON退出1并复现原错误；省略plugin JSON键、在CLI传`--containerd-plugins-namespace <generation>-plugins`退出0。证据`artifacts/p01-engine-config-controls-20261108-7f603208/`，精确容器`0421e333c7766bdd082f7dd04636a385fa1df5fa3587cd20c6d1f7cfd297dc31`保留，`daemonStarted=false`、`actualNamespaceAcceptance=false`。下一实现采用已确认的CLI路径，仍保留全部实际namespace验证；独立复核和另一全新运行代必须继续通过。两个原代均未接受，S2/P01仍未完成。

CLI最小修复只修改guest配置和实际dockerd启动两行，host及全部实际namespace验证不变。新RED两项测试出现三个断言失败；Windows guest40项运行、35通过、5项Linux控制跳过，focused namespace7/7。最终受影响冻结Linux控制`artifacts/p01-s2-guest-controls-20261108-004c3929/`为40/40、零跳过/失败/错误，精确保留容器`7773e58857f03a42dcd7c78d8fed6620f61545d7c070971935b44c57ffcee956`退出0且非OOM。此前完整139项和本次受影响40项是不同快照执行；独立CLI修复spec/quality复核已通过，新的实际代仍为后续门槛。

## 两次新代的实际namespace通过与保全失败（2026-11-08）

提交`28a67af`的正式新代`dbdcae92`及随后`4b1bac35`均实际完成精确generation Containers/Plugins namespace、rust/tools/kotlin三次严格载入及inspect、有效受限smoke并exit0；整体create却均在outer-preservation退出1，不能算S2成功。两次无timeout/processMayRemain。完整证据分别在`artifacts/p01-s2-formal-20261108-dbdcae92/`和`artifacts/p01-s2-formal-20261108-4b1bac35/`。

独立原始全对象比较：dbdcae92为8镜像/610→611容器/224卷，旧0421和7773两容器仅`/HostConfig/Dns`从null变为[]；4b1bac35为8镜像/611→612容器/224卷，仅上一代export9160的相同字段变化。其余所有旧对象完整字段均相同，唯一新增各为该代owned stopped export。两代仍NOT ACCEPTED，不能因其余步骤成功而覆盖失败。两次间另有完整只读稳定快照；它未保证后续唤醒期间字段保持，故不再重复无改动试跑。

`artifacts/p01-desktop-idle-evidence-20261108-b5833be7/`保留Desktop idle及VM日志。两次stoppingVM均在各自create前；startingVM分别03:02:04.801Z/03:11:31.092Z，dockerd启动和容器加载分别03:02:08–09Z/03:11:34–35Z，发生在各自before容器inspect之后、运行区间内。固定官方Moby29.8.1源码归档22153906字节、SHA256`94be9d6940b613676335fc494e617b4acf98676435b3744f32649ed72114bd58`显示磁盘加载调用readHostConfig/InitDNSHostConfig，nil DNS初始化为空切片；DNS覆盖仅在长度大于0时设置。证据支持加载期初始化解释，未确定精确触发API/行为者，不证明inspect自行归一化或外层二进制与源码加密等同。

独立设计复核批准[绑定设计](superpowers/specs/2026-11-08-p01-wsl-environment-design.md)中的精确规则：只有双方实际ServerVersion均为29.8.1、身份不变、两字典均有精确Dns键时允许null/实际空数组双向语义等价。只比较副本，原始证据不改，每个例外显式记录container/path/raw值/版本/源码pin；所有其他字段、资源、namespace和共享控制仍严格保护。实现、负控、源码复核及新代实际验收仍须完成，历史四失败代永不改判。

该规则的宿主实现已完成真实RED7项运行/3断言失败、focused GREEN7项和完整Windows host39项通过；冻结Linux公共闭包`artifacts/p01-s2-public-controls-20261108-7467939a/`为146项运行、145通过、1项Windows junction专属跳过、零失败/错误，容器`1325701bc1635a3ab3657db6b8abe4a86ed40895e95f96bcb4f06261a5289dcf`退出0且非OOM。原始对象不改，返回独立`equivalences`数组，每次例外记录固定policy/source及raw值/版本。此为实现与控制证据，源码独立复核和新代实际验收仍单独进行。

独立源码复核现为Spec PASS/Task quality Approved，无Critical/Important/Minor；核对精确版本/key/type门、其他字段和资源严格性、raw不变/证据不别名，以及同一显式transport的endpoint绑定。主执行者另重算全部18个冻结源/测试文件与当前工作树pin一致。接下来须从已复核提交创建全新代，完成独立observe/exec和完整实际复核；源码批准不接受S2。

## S2正式新代f4244bac独立验收通过（2026-11-08）

实现提交`5ac0edd5feb525951e44faffbc935e4b4eee586f`的干净源码在全新`MingOS-P01-20261108-f4244bac`执行一次正式create，退出0；证据根`artifacts/p01-s2-formal-20261108-f4244bac/`。原始receipt14222031字节、SHA256`60a8b23dd0118a6e37f5b6b924f32ee9ef21c2b12b31b0eea3fd36e1d92d0ea6`。随后独立冻结witness前后观察、公共observe及exec全部成功；证据子目录`independent-observe-b9b7080d18a34edd9cdb192527044d60/`。collector保持其原s2Accepted=false，独立审查另给ACCEPTED结论。

独立实际21门全部通过、无未解决项：S1完整来源、固定bundle/rootfs/Tools、全部五模块冻结、首次空store及fsync/顺序、三次严格index/Config/有序RootFS载入、实际专属Containers/Plugins、daemon环境、PID/starttime/exe/ns/socket与managed containerd、O_PATH可见私有tmpfs、外部无owned mount peer、private cgroup及共享祖先未变、有效受限smoke、完整旧资源保全和公共followups。实际daemon`68548c05-9c7f-459e-b6d7-73466f58bd4c`，smoke`e067393473ff9f4e3b7408b1a0c02cdf85f5f0120e31c9d33cc15a55940463b2`退出0且非OOM。8镜像、613旧容器、224卷的完整对象严格相同，唯一新增为本代stopped export`5aed299cd0b395992106e0867d1408bb75e3be14a2a4e61a775625fc9ba2100a`。等价列表为空，本次没有实际使用DNS例外。

`qualification-evidence/manifest.json`62529字节、SHA256`77be28e82cf1380fd7c1c2f3cd78d42a1520a148724d9bccac60b30a090072bf`封存365文件/8目录，独立精确枚举并全部重hash。明确排除live VHD子树和自引用输出；VHD路径/device/inode与增长后的大小已有原始观察，不能声称盘字节不变。

独立Markdown报告14118字节、SHA256`440278c9d358767118adf28119d0e6dc2b1dabdb6493b92a246ab26a89bf6d71`；独立机器verdict v2为7062字节、SHA256`3af53d027f2fbd8f3ec0c858064adad01df9e8bb946137d2b5d5f4f8d1b7497b`，均在`.superpowers/sdd/2026-11-08-p01-wsl-environment/`。原JSON对修改前Markdown的引用被controller拒绝，未写packet；原件保持，独立新v2仅修该ref，controller核对所有refs及两版唯一语义差异。`qualification-evidence/packet-v2/s2-acceptance.json`699字节、SHA256`44a5c2a4056288c75400aae012b2970c9994f144cdab8bf6e2e210c7d1e75fda`及source-closure SHA256`6912751518fa06e67f1779063028878a691236389bd4f02063aae67d3c55b241`将精确环境、源码和独立verdict关联，S3使用前仍需冻结格式复核及当前live admission。

S2只在记录的观察时点接受。WSL共享内核、无publisher signature/外层引擎二进制认证结论，live VHD不在不可变清单内。历史554bde65/8039baf3/dbdcae92/4b1bac35保持失败。S3–S6及S6首次外层Tools载入仍必须实现，P01不关闭，全部11项仍0/11；正式资格/最终系统/冷构建/P01完成标志保持false。
