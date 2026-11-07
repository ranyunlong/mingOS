# 连续开发路线与阶段验收

更新：2026-10-27。用户授权每个阶段完成后直接进入下一阶段，直到整体任务结束。阶段验收通过只表示该阶段结束，不表示整个操作系统已经完成。

## 2026-11-07 归档恢复、应用复建与公开外层入口

新增公开 `developer_cache.py` / `developer_cache_worker.py`、`source_archive.py`，以及 `developer-vm-outer.ps1` / `developer_vm_driver.py`。缓存工具执行外部摘要固定、严格归档验证、全新匿名恢复卷、消费者投影和两次独立只读复核；源码数据层使用别名归档，保留完整路径和模式映射。公开外层固定可信源码、Tools 镜像、挂载、命令和资源上限，单次 `new → pair` 保留全部尝试，不提供失败代续跑或重置。

实际缓存归档恢复完成 Cargo 12495 / Gradle 2496 文件，42 次操作均成功；源码恢复完成 1487 文件 / 392 目录，另独立核对长、短 Windows 宿主副本。恢复后的短源码与新 Gradle 消费者已通过公共 Kotlin 入口实际重新编译 Text Entry、Motion；新代 `8b03063e70404607a6bcd631ee8cabf8` 的只读容器回放和主执行者宿主独立回放均通过。宿主导出 46 文件、7 目录、13745862 字节；封印 SHA256 `90e463b188474c460700c6f8a570d269bebb4c455c0d81f7206888660b9b24e9`。当前公共包验证器和编排收据链重新执行通过，原缓存及封印源码保持。

公开缓存/源码集成控制在固定 Linux 镜像中 52 项：51 通过、1 项 Windows 专属跳过；独立复核与冻结原型逐字节一致。外层 PowerShell 控制 34/34、独立 Linux driver 控制 8/8，通过默认仓库路径及可选 Docker 字段边界。它们不启动 guest。来源归档独立审阅和两份宿主投影复核通过；三个工具链归档的新版只读 OCI 检查通过，旧验证器拒绝 OCI index 的原失败记录保留，未实际加载到新 daemon。

新卸载代 `28aef8a17bbb44d2` 使用完整运行输入，已完成同盘双启动及公共 export / verify / replay。portable manifest SHA256 `b7e35a77f3ebbd49f7b3b4442b2c57128940c3482d3b27ce9acb608c33543df6`；原始证据回放报告正常重开及卸载后私有文件保留。原 f500a8a3 缺少三项工具输入的失败代保持不变。新恢复输入的 Rust 冷构建 `f7e24f3a64814d49` 仍在执行。

开发 bundle `developer-public-bundle-e512a7d9` 已实际生成，并经另一只读脚本复验全部 newc 流：1368 项、131574714 字节，54 项必需内容及内核/配置匹配。完整开发包仍在组装，开发 guest 尚未执行。现有 Text Entry 只保存内存状态，不能用该预览证明文字内容保存；另行推进笔记应用和持久内容流程。`formalQualification=false`、`finalSystemAccepted=false` 保持。

本阶段证据根仍为 `artifacts/environment-rebuild-20261107/`；独立报告包括 `root-restored-kotlin-review-kr-83b7a1e2.json`、`public-data-migration-review-6431ef277915420da30f32037afc3b8d/`、`source-archive-independent-review-64e9f831/`、`developer-public-bundle-review-6a98cf20/`。大产物不提交 Git。

## 2026-11-07 当前源码应用重建与独立缓存副本

已完成阶段：公共Kotlin入口从独立封印源码`artifacts/kd-ff1e822e`实际编译两代应用。卸载代`1e136eb1a8b04d49b112ef9f4af5dfeb`包含read-file、image-viewer、text-entry-demo、uninstall；开发代`73428b51767246849f6122604aacea1c`包含text-entry-demo、animated-demo。强制离线编译实际执行5/5/19/19及19/19项任务，每代另起只读容器回放；主执行者独立重验导出文件及编排证据，并用当前公开runner重放包/来源/依赖收据链，结果与容器回放一致。原Gradle缓存不变，封印源码1487文件的摘要、大小、模式及目录模式前后匹配。

证据根为`artifacts/environment-rebuild-20261107/`。两代宿主封印`kotlin-delivery-uninstall-export-seal-ff1e822e.json`和`kotlin-delivery-developer-export-seal-ff1e822e.json`的SHA256分别为`a803dde2aecd7123adb6fa795db79bb10e16328bd0064998b465b501f291a0e4`、`fa121ee306726a5a4ec838930fb29979dffd7b7af54d607567d70e1e23d443d9`；根侧独立结果分别为`root-kotlin-uninstall-review-ff1e822e.json`、`root-kotlin-developer-review-ff1e822e.json`。完整汇总`kotlin-fresh-build-summary-ff1e822e.json` SHA256为`610021b9b14e9ef7e1403a5c52e9bee2ef863f3f997cc1e2ba175a1470a8e4c2`。

已完成独立Cargo home副本`cargo-independent-clone-8e4dd0ca04a5-cache`：仅从原只读registry复制12495个文件、245308651字节，核对全部文件摘要、目录、模式、UID/GID及nlink；7个0640文件原样保留，未复制原派生锁和全局缓存。原registry前后匹配，result SHA256为`540e39de0d76284b73590cccdc35ec2926040e189fafcbfc41dd4d2c6c81aeeb`。这次是独立副本验证，依赖归档导出/恢复仍在实现。

下一阶段已开始：cold`persistent-uninstall-20261107-linux-9436c4cf1c0e4ade`使用相同封印源码、新四包、新Cargo home及空target执行七命令构建。本文记录时仍在执行；未将其算作成功cold，也未复用原失败卸载代。两代应用编译及缓存副本都不证明开发guest或最终系统已交付；`formalQualification=false`、`finalSystemAccepted=false`保持。

## 2026-11-07 开发VM生命周期入口

新增`developer_vm.py`及两组控制，提供完整包准入、固定128MiB新盘、私有不可变实例、双锁、只读inspect、连续尝试记录、正常关机marker/session链及失败代锁存。启动仅调用独立可信安装的既有vm.py；固定15秒developer profile，不开放资格角色、输入脚本、任意磁盘和超时选项。实现与现有vm/data_disk结果格式独立核对；实际同盘guest、应用保存及外层封装仍未完成。

原型Linux37/37通过，根侧保留旧umask的实际子进程RED（0644而非0600）及修复GREEN。整合复核另发现wait被KeyboardInterrupt/普通异常打断时未清理自有进程组，先保留3项/5失败RED，再补仅自有PGID的SIGKILL、5秒reap和中断收据；清理失败不声明进程组已空。集成后Windows40项中31通过、9项Linux专属跳过；主执行者在固定Tools、UID65534、禁网、只读root/源码的独立容器中验证40/40、零跳过，源码摘要前后不变。

证据：`developer-vm-root-controls-29e7803ee863`、`developer-vm-interruption-integration-20261107-b8762a10`及`developer-vm-integrated-controls-bbf865bbdfee`，均在本机证据根内。集成Linux CID为`85598ce064d39a1c6995e870cbceb106bc0095b7d131f9563296029944f16af3`；生命周期源码SHA256为`0dfe144b3f6cacfa7025a8091eb90296b3a812e7d6c922b14301e57da65cd378`。上述控制不运行真实guest，不赋予最终系统验收含义。

独立子agent已只读复核规格、可信VM接合、磁盘lease、marker/session链、连续历史及中断清理，未发现明确P1/P2问题。外层隔离与真实guest仍作为下一阶段门槛；该审阅不取代实际运行。

## 当前工程基线

Linux底座；普通应用使用Kotlin/Wasm，通过Ming SDK获取外部能力；父进程持有授权和系统资源，独立worker执行Wasm。保留真实Kotlin样例和旧SDK回归。当前先验证x86_64；Windows用于开发与硬件GPU验证，Linux容器/Wayland用于原生执行验证，两者都不替代可启动系统或真实Linux硬件验收。

## 顺序

1. 应用运行与受控能力：已完成原型及Rust迁移。
2. 图形原型：已完成图片查看器、CPU参考和Linux/Wayland原生运行。
3. GPU场景计算：已完成硬件计算与像素对照。
4. GPU直接呈现与缓存：已验收；原生非截图会话readbackBytes=0，重复图像不重复上传，故障可诊断、资源可回收。证据见gpu-surface-verification.md。
5. 字体与文本：已验收；固定OFL字体、中文和拉丁文字、Q6测量/布局、SDK3、CPU/GPU coverage、真实Kotlin样例及Windows原生/Linux五路径通过。见typography-verification.md。
6. 视觉效果和UI基础：当前实现中；第6A项SDK4圆角抗锯齿已验收，包含真实Kotlin九CPU/GPU场景、原生最大化点击与零像素读回、Linux七路径。第6B项有界阴影已验收，含8组真实Kotlin CPU/GPU精确对照、原生零像素读回与Linux九路径，见shadow-verification.md。后续继续动画、组件与输入状态；组件路线已按用户确认选择真正Compose Compiler/Runtime配MingOS组件，固定官方Runtime1.10.0的wasmWasi发布物缺失已实测，JVM真实对照已执行，用户最终明确放弃JVM、继续Wasm，官方源码WASI适配已跑通无窗口Compose状态/重组/销毁并获父线程独立重放；第6C项Ming组件实现及自动验收已通过（真正Compose、8CPU/GPU场景、原生API零读回、Linux11路径）；物理鼠标专项因用户Escape停止而暂挂，未冒称全交互通过；第6D真实HostFrame/Compose异步动画实现与自动验证已通过（SDK5、8CPU/GPU场景、native25帧/零读回、Linux13路径177fixtures），见animated-verification.md；framed Kotlin最终独立审阅现已补齐，发现的异常链P2经最小修复、真实WASI合同7通过及独立复核关闭；当前动画包另完成complete/earlyClose两后端回归，历史native/Linux记录保持历史范围；第7阶段GPU多应用context与合成核心已推进，物理专项仍暂停；JVM记录仅作历史证据；按测量结果调整渲染架构。
7. 最小桌面：7A同device独立GPU contexts已完成实现及独立复核；7B合成核心与内容scene已接入正常宿主库，当前Windows全features含硬件309通过无skip、Linux定向CPU84通过及全features编译通过，见desktop-compositor-verification.md。7C原生event loop、真实Kotlin多worker、系统chrome/Dock及关闭/故障/重启已验：Windows333 Rust/98 minimal、Linux release真实offscreen/Wayland两路径；24个实际worker跨六轮回收，五张终态图一致，见desktop-runtime-verification.md。7D正在实现受控持续会话、输入/中文IME与基本恢复；启动系统构建前置已并行开始。
8. 平台服务与隔离：文件选择和应用数据、能力授权/撤销、包管理与工具、Linux进程/挂载/网络/系统调用/资源限制。
9. 可启动开发系统：固定Linux基础，构建可启动镜像，在虚拟机启动到Ming桌面并运行Kotlin应用；建立启动日志、关机、恢复和回归。
10. 独立系统能力：针对确定的日常任务补齐浏览器、设置、媒体、更新和恢复，并开展指定真实硬件验收。

后续每阶段先把具体设计和边界落入文档，再开发、复核和执行验收，随后自动推进。工程默认选择必须可追溯、可回退；实际缺少的设备和未验证能力持续标明。

## 2026-10-26 本机后续推进

7D 已补充 SDK 服务等待的正常 Close 误取消修复，以及快速收尾时显式 Cancel 被成功 Outcome 覆盖的修复。
后者真实 Wasmtime 探针130/130，release库196/196、帧时钟7/7、桌面恢复1/1；持续会话原1800ms RPC用例仍RED。
真实 Kotlin 持续会话完成强制终止后重启、两代trap隔离、请求限额32769拒绝、故障后输入及Counter0→1→2→3验证。
这属于本机Linux CPU offscreen推进，不结束整体目标或旧kernel资格；详见[persistent-recovery-verification.md](persistent-recovery-verification.md)。

## 2026-10-27 关闭诊断续进

7D 已补齐统一父侧时钟的 Close、SDK5+ 终态回复写出、Result 接受、期限取消/跳过和原生回收收据。
Result 状态与时间在同一把锁中，最终 join 后补记晚到收据，日志去重不改变期限检查。
release 库203/203、桌面1/1、帧时钟7/7、无默认 features 编译通过；真实 worker 取消130/130、冻结 Kotlin Text 关闭/重启/输入审计35/35。
原 persistent_session 本轮重跑仍10/11，requests在Result接受前到期取消；时限和断言未变。
本轮不改变 Strict2s/Off200ms 或资源预算，不完成严格 kernel 恢复资格；范围及证据见[关闭生命周期时间线](close-lifecycle-verification.md)。

## 2026-10-27 IPC 与回归前置续进

公共IPC增加8KiB写缓冲，小Call/Reply的writer调用从32/14次降到各1次，仍逐条flush。
扩大回归后修正3个测试目录的私有权限准备，并补建真实read-file/image-viewer；全量加定向复验合并338/339。
新版真实worker取消130/130、冻结Kotlin Text恢复审计35/35；原持续会话时序项仍RED，两个版本独立洪泛均约3秒到32769次请求。
请求限额、原1800ms关机与关闭宽限保持；不能把写入优化称为该RED已解决，详见[持续会话验证](persistent-recovery-verification.md)。

## 2026-10-27 AppData进程崩溃续进

补齐生产Parent存储API的真实强杀与新进程重开验证：4次写入中观察后终止、4次提交回执后终止，16个子进程全部确认回收。
slot1/slot2及兄弟数据完整，4个孤儿文件在恢复前后字节与私有元数据保持，独立审计90/90、13组反例拒绝、两个根目录保护通过。
生产app_data.rs和预算实现未改；本轮限于进程崩溃，原持续会话RED、kernel恢复和断电耐久性仍保留各自状态。
详情及复现见[AppData进程崩溃验证](app-data-process-crash-verification.md)。

## 2026-10-27 交付主线恢复

固定279包已校验并禁网构建出QEMU/Weston工具镜像，版本、运行库闭包和暂停QMP均通过。
当前运行时修复已重建进可信系统Host，库203/203、原生CPU/Wayland共6实例和49项审计通过；正式包与16步kernel候选输入已准备，legacy-v1 review 仍未批准。
legacy-v1 严格 kernel 路径仍需要旧Windows导出的原探针和盘快照，不改原SHA门槛；用户批准的 source-v2 新探针/新盘路径已完成当前 persistent-RPC Host 的严格 worker SIGKILL 两次 fresh QEMU 资格。两条路径不能互相冒充；其余平台服务、物理输入/GPU、断电耐久性与整体系统验收仍未完成。
具体输入和证据见[交付主线恢复检查点](boot-delivery-recovery-20261027.md)。

## 2026-10-29 应用私有存储基础

以下段落依次记录当时进展，早期“尚未接入”等状态以本节末尾的最新记录为准。

按用户要求，应用经授权后应能读写自己的私有存储，应用之间隔离。现有 SDK6 的八个数据槽不是完整目录，静态 catalog 授权也不是用户授权。新增父侧按包 ID 派生隔离目录的基础入口，旧键目录与新包目录分离；Linux 存储测试 19/19 和包清单测试 1/1 通过。
后续新增父侧授权协调器，默认拒绝并支持同时撤销同一应用各运行代的授权。Linux 持久模式保存授权记录，支持跨进程锁、记录损坏拒绝和同步状态回执；撤销写入失败时当前进程拒绝访问，但旧记录可能残留，须重试。Linux 存储测试 30/30 通过，包括真实子进程重开及锁互斥；协调器尚未接入实际用户交互，Windows 尚无持久模式。
Linux 父侧新增授权检查的目录创建、列举和空目录删除，文件读取、原子写入、删除和不覆盖重命名，使用逐级 no-follow 目录句柄；同一协调器内检查每应用容量和条目数。当前单文件和应用总量各限制 8 MiB，旧数据槽仍为 64 KiB；全系统单协调器假设尚未强制，未接 SDK 或用户授权 UI，不能视为完整私有文件功能。此前并发测试曾出现持久授权重开 `Io`；受控 fork 测试复现描述符继承导致的锁延迟释放，管理器现显式解锁，测试通过，但不能证明它就是此前失败的唯一原因。
新增绑定可信包身份的父侧文件会话：关闭会话不影响其他会话，撤销后重新授权不会恢复旧会话；每个包最多保留 64 个尚未释放的会话。会话尚未连接实际 worker，身份也尚未由可信安装系统验证。
新增 Linux 父侧私有文件 RPC 适配器，拒绝身份注入并限制消息、传输和目录分页；支持分块暂存并原子提交最多 8 MiB 的文件，进程内暂存预算 64 MiB。宿主已加入默认拒绝的 `private_file_call` 分发与运行代结束清理；worker 的 SDK6 可选 Wasm 导入已实现，数据宿主测试 11/11 通过。Kotlin SDK 新增底层字节请求封装：真实 Kotlin/Wasm 应用在实际宿主验证默认拒绝；测试父进程显式授权两个身份，直接转发两个真实 worker 的文件请求，验证读写、跨应用隔离、同进程管理器重开和撤销。另有经 supervisor 消息循环运行的授权探针；这些测试均不经过桌面授权 UI，尚无类型化文件 API。桌面尚未发放会话，真实授权验收待完成。
另以测试父进程显式授权，经真实 supervisor 消息循环运行 Kotlin worker，验证文件读写和退出时会话关闭；启动前会检查会话与加载包的 ID 一致，错配测试通过。这个检查不等同可信安装或发布者认证，生产桌面仍未创建用户授权会话。
另构建真实 Kotlin/Wasm 大文件探针，经测试父进程及 supervisor 两条路径分别验证分块写入和完整读取 131,064 字节；授权仍是测试直接设置，并非桌面用户授权。
supervisor 的文件会话现绑定父进程提供的内存包快照，启动时对比 manifest 字段和 Wasm 字节；测试覆盖同 ID 的模块替换和版本更改被拒绝、无 worker 启动及会话关闭。该快照不是持久安装身份或发布者认证，也不证明用户已经同意。
新增 Linux 持久内容绑定授权记录：SHA-256 覆盖加载后的 manifest 字段及 Wasm，新式会话和 supervisor 拒绝旧 ID-only 授权或内容不符的包。测试覆盖重启、混合旧记录、记录损坏、撤销和重新授权；包更新需撤销并重新批准，数据按原包 ID 保留。指纹不认证发布者，可信安装和用户授权 UI 仍缺失；旧程序无法读取新记录格式。
新增可信父进程授权决定后台：待确认项持有不可变包快照，拒绝不产生授权，普通允许不能覆盖不同包的授权；测试覆盖磁盘替换及竞争决定，并用模拟允许接通真实 Kotlin supervisor。尚未实现实际系统提示、可信输入或冲突撤销交互。
manifest 新增可选 `privateStorage` 布尔声明，默认为 false、仅 SDK 6 可声明；授权接口拒绝未声明的包，声明纳入内容指纹。真实 Kotlin 探针更新后通过 supervisor 测试，声明本身不等于用户同意。
桌面外壳已增加私有存储提示绘制及按钮布局，显示包 ID、指纹和未验证发布者，冲突状态不提供普通允许按钮。CPU 绘制测试、GPU 构建及 WGSL 静态校验通过；实际 GPU 绘制、桌面状态、可信输入和用户授权尚未接入或验收。
Linux 桌面现将提示和授权后台接入配置了 `--data-root` 的启动路径。提示必须经原生呈现并完成同一按钮的按下和释放才可决定；提示期间阻止脚本动作和应用输入，离屏未授权时默认拒绝并继续启动。桌面控制器模拟点击后真实 Kotlin worker 读写及重开测试通过，离屏默认拒绝的真实 worker 测试通过；这不等同真实窗口、物理输入、硬件 GPU 或完整系统验收。授权查看与撤销、冲突处理、可信安装、多用户及单权威约束仍待完成。
可信父侧现支持读取授权快照并按授权代撤销，旧确认无法撤销重新授予的权限；撤销写入失败或目录同步未确认时禁止当前进程重新授权，允许同一快照重试。若写入失败后未完成重试就退出，旧磁盘授权仍可能在重启后恢复。Linux 桌面已提供应用图标 `S` 存储管理入口和失败重试交互，控制器模拟输入与重开测试通过，真实窗口和 GPU 像素尚未验收；可信安装身份、类型化目录与文件 SDK、多用户及系统级配额权威仍未完成。设计及后续验收见[应用私有存储方向](superpowers/specs/2026-10-29-private-app-storage-design.md)。此前的 source-v2 Host/QEMU 资格绑定的是修改前的冻结 Host，本次代码未重建到该 bundle。

2026-10-30 补充：当前源码的 debug Host（SHA-256 `94cad70d68488824cd2c27b275cd49b4553b1243b48ffd5f112c94d5319518e2`）在禁网容器的 Weston headless/Pixman 下实际以 Wayland 呈现两个桌面帧，并正常结束；程序自身桌面快照显示 `S` 入口。单应用首次授权运行时的状态文件报告已呈现帧，但没有提示截图或真实输入证据。`weston-screenshooter` 请求被合成器以 unauthorized 拒绝，没有绕过权限。摘录证据位于 `artifacts/private-storage-wayland-20261030/`，其桌面快照不是合成器 scanout；这些结果不覆盖硬件 GPU、物理输入或 kernel 新盘验收。

Kotlin SDK 新增类型化私有文件 `read` 和 `write`，后者自动分块并返回目录同步状态。真实 Kotlin worker 诊断验证了两身份隔离和 131,064 字节分块写入；该阶段的目录接口和错误路径尚未完整验证。引入的 kotlinx JSON 库使该小文件探针从约 98 KB 增至 778 KB，在双 CPU 容器内 debug worker 到首个请求约 41 秒，超过原测试的 20 秒等待；诊断测试单独使用更长等待，不作为启动性能合格证据。旧探针在原 20 秒门槛下仍通过。生产 release 与严格隔离下的实际启动时间尚未测量，必须解决或明确验证后才能交付。

后续补充类型化目录列举、创建、删除空目录、文件重命名和删除。真实 Kotlin worker 诊断验证带引号的文件名、目录列举、重命名、缺失文件以及清理；另一探针验证撤销后读、写、列举和创建目录均返回拒绝。这些探针同样使用较长诊断等待，异常宿主回复和实际桌面输入等路径尚待验证。

后续受控宿主回复测试覆盖损坏 Base64、错误目录项类型、无效同步结果和分块回复异常后的中止。新构建 release Host 在相同双 CPU 容器中单次测得：旧低层探针首请求 0.280 秒，类型化探针 2.389 秒；该类型化探针构建于目录接口加入之前。详见 `artifacts/private-storage-sdk-performance-20261030/RESULT.md`。单次、非 strict 测量不构成系统性能验收，类型化探针的体积和启动开销仍需处理。

为降低应用对通用 JSON 库的依赖，新增 Linux SDK6 worker 二进制导入 `ming_private_files_v2.call`，worker 将有界请求转换为现有父侧 JSON 协议并严格检查回复；v1 导入保留。转换层测试覆盖全部文件操作、路径转义、长度及损坏回复；worker 测试覆盖版本、内存范围、默认拒绝、真实往返及错误回复。数据宿主 13/13 与转换层 4/4 通过。Kotlin SDK 尚未切换到该导入，不能据此声称体积或启动开销已经改善；需切换后重新验证各路径及性能。

后续 Kotlin 类型化接口已切换至 v2，低层 v1 接口仍保留，移除 JSON 库依赖。禁网 Linux 容器内，类型化 worker 的读写隔离、大文件分块、目录操作和撤销拒绝 4/4 通过；受控异常回复及分块中止探针通过，旧 v1 worker 的读写隔离回归通过。新类型化读写 Wasm 为 99,892 字节，SHA-256 为 `d70b941fbddf42614a940eb5d82e691408aff51cc3301c8fa1fb0261cbb21981`；此前 JSON 版本为 777,944 字节，二者不是同一二进制。当前 release 宿主在非 strict 双 CPU 容器内单次首请求诊断为 0.303 秒；类型化测试仍使用 90 秒诊断等待，尚未完成 strict 启动性能资格。真实桌面输入、可信安装、多用户、单权威、撤销持久化失败后重启以及 kernel 新盘等验收仍未完成。

之后转换层补充非法路径、UTF-8、逐字节截断和空读取异常回复检查，并修正空读取携带 payload 时未拒绝的问题。数据宿主 13/13、转换层 6/6 通过；类型化 worker 四项测试将每条消息的等待从 90 秒恢复为 20 秒，在双 CPU 禁网容器串行通过，总耗时 27.43 秒。该等待并非整个测试的总时限，也不证明 strict 系统性能达标。此前 0.303 秒 release 诊断针对本次转换层修复之前的宿主构建，不能用作修复后新二进制的测量值。

使用 v2 类型化普通和大文件探针重新运行真实 supervisor：模拟父侧允许后通过包绑定会话读写，身份不匹配拒绝，131,064 字节上传及会话关闭均通过（2/2）。桌面授权控制器模拟按钮按下和释放后启动同一类型化 worker，重开持久授权并读取文件通过（1/1）。这些测试不使用 strict 隔离，也不是真实窗口输入或可信安装认证；source-v2 旧 bundle 绑定先前 Host，不能作为当前代码的 kernel 验收。

用户随后澄清：应用自身私有目录默认可访问，仅公共目录等共享内容需要授权。Linux 桌面现对 SDK6 应用自动发放绑定包快照的私有文件会话，不依赖 `privateStorage` 声明或既有授权决定；旧声明和旧授权记录不再控制该默认会话，旧文件目录原样保留。移除桌面私有存储授权入口，并拒绝同一 catalog 中重复的包 ID。真实 Kotlin worker 验证有声明、无声明和 offscreen 均无需提示且可读写；父侧测试验证两身份隔离、重开和旧撤销不影响默认会话。SDK6 和配置 `--data-root` 仍是当前实现前提，公共目录授权尚未实现；自报包 ID 及 catalog 重复检查并非可信安装或发布者身份，仍不能声称生产级跨应用隔离。

随后为配置加载与启动之间的身份变化增加检查：catalog 保存加载时的包 ID，Linux 桌面使用数据根启动应用前重新加载并比较；不一致时在创建 worker 和私有会话前拒绝，现有 supervisor 仍核对已加载包快照。针对清单 ID 替换的回归先失败后通过；Linux 桌面库 167 项、数据宿主 13 项、catalog 4 项、CPU 桌面外壳 15 项通过，另 5 项 Kotlin worker 集成测试单独通过。这是当前桌面会话的防替换检查，不是跨重启的可信安装身份、发布者认证或公共目录授权。

默认私有会话新增真实 worker 崩溃恢复测试：真实 Kotlin worker 的写入由父侧提交并确认目录同步后、向 worker 回复前发送 SIGKILL；关闭会话及权威后，独立新操作系统进程重新打开私有目录，验证应用文件与另一应用原有文件均完整。该测试通过；它不模拟写入中途断电、真实 kernel 新盘或物理设备，也不解决可信安装身份问题。

进一步固定当前桌面会话的包内容：catalog 加载时保存包指纹，使用数据根启动应用时重新比较完整 manifest/模块；即使包 ID 不变，加载后代码被替换也会在私有会话及 worker 创建前拒绝。此回归先失败后通过；Linux 桌面库 168 项、数据宿主 13 项、catalog 4 项、CPU 外壳 15 项，以及 6 项独立 Kotlin worker 集成测试通过。包更新需要重新加载桌面配置；此内存快照不能验证跨重启身份或可信发布者。

默认私有存储初始化现不读取旧授权记录，因此旧记录损坏不会阻止应用访问自身目录，也不会修改旧记录；旧持久授权接口仍拒绝损坏记录，新旧管理器使用同一排他锁。损坏记录回归先失败后通过，新旧锁互斥测试通过；Linux 库及 app_data、data_host、catalog、CPU 外壳回归通过，6 项真实 Kotlin worker 测试另行通过。当前仍需要 SDK6 和 `--data-root`，公共目录授权、可信安装身份、多用户和真实 kernel 新盘验收尚未完成。

已用当前源码重新预编译 Counter/Text 并构建新的 release 系统 Host；冻结输入、摘要与离屏结果见 `artifacts/boot-delivery-20261027/system-image-default-private-v1/README.md`。嵌入模块的源码 release 库测试 217 项通过、6 项跳过；非 strict CPU/offscreen 运行中两应用均从 system-image 加载并正常关闭。新 Host 尚未组装或复核为新 bundle，也未进行 kernel/QMP、新盘或真实输入验收，旧 Host 的系统资格不能转用。

随后当前 Host 已组装新的 source-v2 bundle，使用独立新盘完成两次 fresh QEMU 启动；盘标记从 1 到 2，启动 ID 不同。两次均观测到指定 Text 第一代 worker SIGKILL、Counter 存活、Text 第二代关闭和 cgroup 清理，以及资源探针、数据同步卸载和 kernel 关机；导出的原始证据通过 17 项交叉检查。探针仍使用其原始锁文件，与当前 Host 的新锁文件分开保留，不修改原探针门槛。这只覆盖 `strict-worker-sigkill-v1`，未在 kernel guest 测试新私有文件 SDK，也不覆盖公共目录授权、可信安装、其他服务恢复、物理设备或完整交付。

2026-10-30 私有文件 kernel 诊断：首次三应用 cold-pair 因沿用两应用 Dock 坐标 `x=676` 而在三应用布局中点击空白，第一轮 guest 已启动并完成私有探针但输入握手超时；失败卷和 v1 bundle 保留。随后建立独立 v2 输入、review、bundle 和新 ext4 数据盘，使用三应用有效坐标 `x=640` 完成两次 fresh QEMU。两轮均验证 SDK6 私有探针无 `privateStorage` 声明仍从 `system-image` 运行，事件 `7102=1`，应用私有目录默认读写成功；同时验证两应用隔离、Text 第一代 SIGKILL、Counter 兄弟存活、Text 第二代重启、strict cgroup 清理、资源探针、输入完成、marker `1 -> 2`、数据同步卸载和 kernel PowerDown。独立审计 20/20 通过。证据见 `artifacts/boot-delivery-20261027/system-image-private-kernel-v2/`；该结果仍不覆盖公共目录授权、可信安装身份、多用户、物理输入/GPU、断电耐久性、其他平台服务恢复或整体系统验收，`finalSystemAccepted=false`。

## 2026-10-30 公共目录授权基础

按用户最终权限模型，SDK6 应用的私有目录由桌面启动时默认发放并按包 ID 隔离；公共目录改为独立的共享命名空间，不能借用私有默认会话。父侧新增 `PublicFileSession`、持久授权记录、显式 `PublicStorageConsent` 请求、撤销和旧会话失效；公共文件 RPC 与 worker import 使用独立 capability，但复用相同的路径、配额、原子写入和分块协议。两个已授权应用可共享公共文件，未授权、撤销后旧会话和路径穿越均 fail-closed。

当前 Rust 验证：公共目录授权/重启/隔离 3/3、公共 RPC 1/1、公共 consent 1/1；私有 RPC 8/8、私有 consent 4/4、持续会话 14/14。`app_data` 回归中仅有一个既有特权环境测试因容器具备 `CAP_DAC_OVERRIDE` 失败，不能作为本次代码回归失败结论。公共目录真实桌面授权的允许/拒绝路径和真实 Kotlin worker 已通过；kernel 新盘验收在独立 artifact 中完成，`finalSystemAccepted` 继续保持 `false`。

本轮继续推进：修复桌面公共授权点击“允许”后仍把应用当作拒绝、导致 worker 不获得公共会话的问题；新增回归覆盖真实实例公共 RPC 写入共享目录。`ming-app-host` 全量测试为 218 项通过、1 项因 Docker 特权环境差异失败、6 项忽略；`cargo check --locked --tests` 通过。新增 Kotlin `MingPublicFiles` 类型化 v2 读写、分块写入、分页列目录、目录创建/删除、文件重命名/删除，以及 public/public-large/public-denied 探针。真实 Kotlin worker 的共享目录、目录操作和 131,064 字节分块写入 2/2 通过；桌面控制器模拟提示呈现和匹配按钮按下/释放后，允许和拒绝路径各 1/1 通过。宿主机 Gradle 架构问题已通过仓库离线 wrapper 绕过；kernel 新盘公共目录诊断随后单独完成，详见下一条记录。

2026-10-30 公共目录 kernel 诊断：修正 bounded public probe 的严格 session 参数，使 guest 挂载新数据盘并使用 `/data/mingos`；移除无故障注入 bundle 对 `fault_injector` 的无条件导入；以当前 Host、source-v2 strict probe 和全新 UID65534 数据盘完成两轮 fresh QEMU。两轮均在桌面启动前记录 `public-storage-preauthorized`，真实 Kotlin 公共 probe 完成写入、读取、目录创建/列举、重命名、删除和目录删除，事件 `7201=1`，并完成 strict cgroup/resource 清理、marker `1 -> 2`、数据同步卸载和 kernel PowerDown。独立审计 21/21 通过，证据见 `artifacts/boot-delivery-20261027/system-image-public-kernel-v2/`；这只覆盖 guest 预授权公共目录路径，不等同于真实物理用户授权、可信安装身份、多用户或整体系统验收，`finalSystemAccepted=false`。

2026-10-30 数据根单一 authority：桌面 SDK6 默认私有目录现在通过 `open_default_private` 持有数据根排他锁，同时恢复公共授权策略；同一数据根的第二个桌面/持久 authority 在 worker 启动前拒绝，私有默认访问仍不读取旧私有授权记录。`ming-app-host` lib 220/220、公共存储 4/4 通过；该阶段只解决同一数据根的可信父侧串行化，不等同可信安装身份、多用户隔离、真实物理输入/GPU 或最终 kernel 系统验收。

2026-10-30 catalog 安装身份绑定：新增 catalog schema v2 的每应用 `identity.packageId` 与十六进制 SHA-256 字段，加载时与实际 manifest/Wasm 完全比较，错误 ID 或指纹在创建桌面会话前拒绝；schema v1 保留为开发兼容格式。`ming-app-host` lib 221/221、catalog 集成 4/4 通过。该记录是可信父侧配置契约，不是发布者签名或完整安装器；多用户、更新/卸载生命周期和真实系统安装身份仍待完成。

2026-10-30 父侧安装登记：新增 Linux `PackageInstallRegistry`，使用父进程拥有的私有目录、排他锁、原子登记文件和精确 `packageId + version + SHA-256` 绑定。支持同版本幂等安装、更高版本更新、降级拒绝、显式卸载标记和卸载后重装；卸载不删除应用数据。桌面增加 `--install-root`，配置后在 catalog 加载阶段拒绝未登记、已卸载或内容被替换的包，并在会话期间持有登记根锁。库 `222/222`、登记集成 `3/3`、catalog 集成 `4/4` 通过。该阶段是本地父侧信任锚，不是发布者签名、证书链、安装包复制、多用户或更新迁移系统；未配置登记根的 schema v1/v2 仍是开发兼容入口。

随后登记根增加按版本和指纹保存的父侧包内容。`install_from_manifest` 完整加载源包后原子写入私有内容目录；catalog schema v3 只按 `packageId` 从活动登记解析包，更新保留旧内容，卸载不删除历史内容。库 `223/223`、安装登记集成 `4/4`、catalog 集成 `4/4` 通过。发布者签名、多用户、更新迁移和真实安装 UI 仍未完成。

2026-10-30 发布者签名强制接入：父侧新增 Ed25519 publisher trust root，支持 active/revoked key、轮换、原子 keyring 写入和固定 canonical signature envelope。`PackageInstallRegistry::open()` 保留开发兼容的无签名模式；`open_verified()` 在安装、活动 manifest 解析和包校验时强制验证发布者签名。Linux 桌面配置 `--install-root` 现在使用 verified registry，缺少 `trusted-publishers-v1` 时在启动前 fail-closed。固定 Rust 1.99.1 镜像、禁网、只读源码下，publisher `4/4`、package_install `6/6`、桌面回归 `1/1` 通过；`ming-app-host --all-targets -j1` 通过，库 `224 passed, 8 ignored`，其余集成目标均通过。固定镜像未安装 `rustfmt`，因此 `cargo fmt --check` 未执行成功；`git diff --check` 通过。当前签名覆盖仍是本地父侧信任根，不是证书链、远程密钥发布、多用户安装身份或完整安装 UI；更新迁移、卸载数据策略、多用户隔离和真实安装交互仍待完成，`finalSystemAccepted=false`。

2026-10-30 卸载数据策略基础：默认卸载继续只撤销安装登记并保留应用私有数据；新增父侧 `purge_private_data` 作为独立、显式的破坏性清除入口。清除要求所有私有运行代已关闭，使用 Linux pinned descriptor、`O_NOFOLLOW` 和递归 no-follow 删除，只删除目标应用私有目录，不删除兄弟应用或公共共享目录。固定 Rust 1.99.1 禁网环境下 `app_data` 全量 `49/49` 通过，包含活跃运行代拒绝清除、清除后私有数据不可恢复以及公共文件保持可读。设计见 [安装生命周期与应用数据策略](superpowers/specs/2026-10-30-install-lifecycle-data-policy.md)；真实卸载 UI、安装服务事务、多用户身份和跨平台实现仍待完成，`finalSystemAccepted=false`。

2026-10-30 安装服务接入数据策略：`PackageInstallRegistry::uninstall_with_data_policy` 接入默认保留和显式清除两条父侧路径。清除失败时保持已卸载、数据保留，可在关闭运行代并撤销旧式 grant 后重试；重复调用不会重新激活登记。`package_install` 全量回归 `8/8` 通过；桌面卸载 UI、系统确认交互、多用户安装身份和跨平台实现仍待完成，`finalSystemAccepted=false`。

2026-10-30 公共目录授权管理：新增公共授权代快照和按代撤销，桌面 `S` 管理入口优先显示公共授权并支持持久化失败重试；撤销只使旧公共 session 失效，不删除共享文件，重新授权后数据仍可读。公共授权管理 `5/5`、桌面 Shell `16/16`、`ming-app-host --all-targets -j1` 库 `226/226` 通过；设计见 [公共目录授权管理](superpowers/specs/2026-10-30-public-storage-management.md)，`finalSystemAccepted=false`。

2026-10-30 桌面卸载确认：Dock 为每个应用增加 `U` 卸载入口，保留 `S` 公共目录管理入口；卸载必须经过当前已呈现帧对应的按下/释放，运行中的应用显示 `CLOSE APP FIRST`，普通卸载只撤销安装登记并保留私有数据。登记写入失败时提示 `RETRY`，成功后从当前 desktop catalog、autostart 和待启动队列移除应用；未配置安装登记根时 fail-closed。CPU Shell、原生 Dock 输入、卸载登记移除和私有数据保留回归均通过；固定 Rust 1.99.1 禁网环境下 `cargo test --offline --locked -p ming-app-host --all-targets -j1` 完成，库 `230 passed, 0 failed, 8 ignored`，全部已启用集成目标无失败；GPU feature 的可信 Shell shader 定向测试 `1 passed, 0 failed`。设计见 [桌面卸载确认](superpowers/specs/2026-10-30-desktop-uninstall-confirmation.md)，永久删除私有数据 UI、完整安装交互、多用户和跨平台安装服务仍待完成，`finalSystemAccepted=false`。

2026-10-31 桌面永久删除私有数据继续实现：新增独立的 `PurgePrivateData` 桌面确认流程，Dock `D` 入口与 `U` 普通卸载、`S` 公共目录管理分离；删除确认、运行中阻止、失败后 `PRIVATE DATA NOT DELETED` 重试、catalog/autostart 移除和当前 presented frame 的按下/释放约束均已接入。`begin_uninstall_with_purge` 在缺少 `--data-root` 时现在于显示确认前 fail-closed；WGSL 的删除按钮标签改用非保留标识符 `delete_label`。本机 Windows 固定 Rust 1.99.1 工具链验证：`cargo check --offline --locked --package ming-app-host --lib` 通过；`cargo test --offline --locked --package ming-app-host --tests -j1` 以 `EXIT_CODE=0` 完成，44 个测试目标组累计 `350 passed, 0 failed`；trusted Shell shader 定向测试 `1 passed, 0 failed`。新增的 purge session、Linux 原生 D Dock 和 data-root 回归均受 `target_os = linux` 限制，本机 Windows 只完成编译过滤审计，未执行 Linux 断言；Windows `--all-targets` 仍会触发 Linux-only `isolation_probe_v2` 的 `libc` 编译阻塞。Docker Server 和 Linux Rust target 当前不可用。当前改动尚未重建 Host/bundle 或进行新的 Linux/QEMU 验收，`finalSystemAccepted=false`。当前回归证据见 `artifacts/desktop-private-data-delete-20261031/`。

2026-10-31 kernel 关闭诊断宿主回归：`worker_resources.process_table` 增加可选 `clock_ticks` 注入，仅供 Windows 合成 fixture 传入 POSIX 的 `100 ticks/s`；Linux guest 默认仍读取 `os.sysconf("SC_CLK_TCK")`，不改变资源收据或生产行为。此前 Boot unittest 在 Windows 的 80 项中有 2 项因缺少 `os.sysconf` 报错，修复后 Boot `80/80`、Node `21 passed, 3 skipped, 0 failed`。随后为冻结 legacy probe 增加实际文件 hash 回归，当前完整 Boot 套件为 `81/81`，profile 专项为 `8/8`。历史证据见 `artifacts/kernel-close-diagnostics-local-20261031/`，新增核验见 `artifacts/linux-isolation/frozen-verification-20261031.json`；真实 kernel/QMP 复验和 v23 RED 仍未改变。

2026-10-31 冻结隔离输入复核：此前诊断计划中标记为缺失的 legacy-v1 `mingos-isolation-probe` 和 `vm-prepare.sh` 已在 `artifacts/linux-isolation/frozen/` 找回，两个 SHA-256 均与原硬编码门槛匹配；source-v2 profile、源码和构建产物也已核对，隔离 profile 回归 `8/8`。证据见 `artifacts/linux-isolation/frozen-verification-20261031.json`。这解除冻结输入查找前置条件，但 Docker/QEMU、当前源码 bundle 重建和 kernel recovery 仍未执行，`finalSystemAccepted=false`。

2026-10-31 目标恢复后的本地回归：使用当前工作树和 bundled Python/Node，完整 Boot unittest `81/81`、Node `21 passed, 3 skipped, 0 failed`，`git diff --check` 通过。结果见 `artifacts/local-regression-20261031-resumed.json`；这些检查不替代 Linux-only purge、当前 Host/bundle 重建、QEMU 或真实 kernel recovery。

2026-10-31 trusted system-image 构建链修正准备：复核当前 `build-current-private-purge` 后确认它只编译了默认空 `SYSTEM_MODULES` Host，却沿用了要求 `module_source=system-image` 的 bundle 元数据，导致两个 guest worker 在 300000ms 启动看门狗内未 ready。已在独立新目录 `artifacts/linux-current-20261031/system-image-source-v1/` 按当前 `inputs.json` staging 1040 个源码输入，并叠加已核验的 trusted `system_module_table.rs` 与 4 个 `.wasm/.cwasm` 文件；staging receipt 明确 `compiled=false`、`wasmExecuted=false`，历史产物未覆盖。新增 v3 build driver 在 release build 前拒绝空表并校验 table/data SHA-256；Docker Server 仍不可用，因此当前源码 system-image Host、bundle 和 QEMU 重跑尚未完成。

2026-10-31 跨平台构建前置修复：`crates/ming-app-host/examples/isolation_probe_v2.rs` 现在只在 Linux include source-v2 probe，非 Linux 提供明确退出的编译占位入口，保持 Linux probe 内容不变。先用新增回归观察旧 wrapper 失败，再修复后 isolation profile `9/9`、完整 Boot `82/82`、Node `21 passed, 3 skipped, 0 failed`，Windows `cargo test --offline --locked --package ming-app-host --all-targets --no-run -j1` 和全量 `ming-app-host` 测试均退出 `0`。同步更新当前 source-v2 profile 的 wrapper/Cargo.lock hash，生成独立 `artifacts/linux-isolation/frozen-verification-20261031-v2.json`、`system-image-source-v2/` staging 和 `build-current-private-purge-v4-system-image/` 输入；v4 输入 1040 项无漂移，system-image 仍未编译或执行。历史 v1/v3 artifact、legacy-v1 hash 和原始 RED 保持不变；Docker/Linux/QEMU 资格及 `finalSystemAccepted=false` 未改变。

2026-10-31 profile artifact 同步复核：发现工作树 `probes/linux-isolation-source-v2/profile.json` 已更新为 wrapper/Cargo.lock 新 hash，但 v2 staging receipt、staging 内 profile 和 v4 `inputs.json` 仍保留旧 profile 文件 hash；已将三处同步为 `caef762a9c4d81399ab5c241fcf175352986db0703c58f37a8b9f7aa2833451e`，并在 `frozen-verification-20261031-v2.json` 记录工作树/staging/v4 三方一致。source-v2 profile `9/9`、完整 Boot `82/82`、Node `21 passed, 3 skipped, 0 failed`、`git diff --check` 通过；system-image 仍 `compiled=false`、`wasmExecuted=false`，Linux/QEMU 和最终 recovery 仍未执行。

同轮 Windows 当前源码验证：`cargo check --offline --locked --package ming-app-host --lib`、`cargo test --offline --locked --package ming-app-host --tests -j1` 和 `cargo test --offline --locked --package ming-app-host --all-targets --no-run -j1` 均退出码 `0`；仅有已有 dead-code warnings。独立证据见 `artifacts/local-regression-20261031-profile-sync.json`。

执行环境跟进：Docker Desktop 进程仍在运行，但 `com.docker.service` 为 `Stopped`，受控启动返回 `Cannot open ... service`，Docker API 继续因 engine pipe 权限拒绝；WSL 为 `E_ACCESSDENIED`，本机没有 Podman、nerdctl、QEMU 或可执行 Linux Rust runtime。证据见 `artifacts/environment-check-20261031-followup.json`；当前源码 Host/bundle、Linux purge 和 QEMU 仍未执行。

2026-10-31 桌面永久删除本机完整 Rust 回归：在当前工作树复跑 `scripts/rust.ps1 test --offline --locked --package ming-app-host --all-targets -j1`，退出码 `0`，所有可执行 target 通过，仅保留既有 dead-code warnings。此前 `windows-all-targets-red.log` 的 Linux-only wrapper 编译失败作为历史证据保留；本次通过只证明 Windows 编译/测试边界，不证明 Linux purge runtime、当前 system-image bundle、QEMU 或 kernel recovery。新证据见 `artifacts/desktop-private-data-delete-20261031/windows-all-targets-green-v3.json`，`finalSystemAccepted=false`。

同日 GPU Shell 标签修复：发现 WGSL 在 `UNINSTALL`/`DELETE` 模式下先绘制通用 `ALLOW` 再叠加专用标签，可能留下残余字形；新增源级回归并改为 destructive label 优先选择。当前工作树 GPU library `240 passed, 0 failed, 17 ignored`，WGSL parser/validator 与 destructive-label 回归均通过；硬件 GPU runtime 未执行。证据见 `artifacts/desktop-private-data-delete-20261031/windows-gpu-lib-green-v2.json`，`finalSystemAccepted=false`。

同日 CPU Shell focused 回归：`scripts/rust.ps1 test --offline --locked --package ming-app-host --test desktop_shell -j1` 为 `16 passed, 0 failed`，覆盖删除提示、Dock 三入口几何和 CPU glyph 可见性。该证据不替代 Linux native D input 或硬件 GPU runtime，`finalSystemAccepted=false`。

同日 v5 current-source system-image staging：发现 v4 `inputs.json` 与当前工作树有 6 个源码 hash 漂移后，基于当前工作树重新生成 1040 项 manifest，并生成独立 `system-image-source-v3`。staging 完整性按 overlay 优先规则通过 `1039` 个普通输入 + `5` 个 trusted overlay，共 `1044` 文件、`0` mismatch；非空 `SYSTEM_MODULES` 与 4 个 trusted module 文件通过 driver guard。该阶段仍是构建准备，`compiled=false`、`wasmExecuted=false`，不能替代 Linux release build 或 QEMU 验收。证据见 `artifacts/linux-current-20261031/build-current-private-purge-v5-system-image/`。

同日当前工作树 Windows 回归复核：CPU Shell `16 passed, 0 failed`，GPU library `240 passed, 0 failed, 17 ignored`，all-targets 为 `49` 个测试目标组累计 `354 passed, 0 failed, 0 ignored`，退出码 `0`。新证据见 `artifacts/desktop-private-data-delete-20261031/windows-current-regression-20261031.json`；Linux-only purge/native D、硬件 GPU、当前 Host/bundle、QEMU 和 kernel recovery 仍未验证，`finalSystemAccepted=false`。

2026-11-01 Docker/Linux 运行环境恢复后，使用固定 Rust `1.99.1` 的已构建镜像 `mingos-linux-task3:aliyun-20261031`（image SHA-256 `5a083df25a499201c5a1ad15664aaf07c8998e03105cc5121213692ae4c99ad6`）和独立缓存卷重跑当前工作树。先修正两项 Linux purge 测试夹具：公共目录按 Linux shared-public 契约断言，fingerprint 变更用例注入其创建的 `storage_authority`；随后修正 `D` Dock glyph 与居中应用标签的两行像素重叠。最终 release Linux 验收证据 `artifacts/linux-user-session-20261101-postfix2/`：shell/fixture/font/Weston 前置检查全部通过，Rust all-targets 共 `460 passed, 0 failed, 16 ignored`，13 个 headless/Wayland CPU runtime path 全部通过，`--all-features --all-targets` release check 退出码 `0`。该轮仍是 Weston headless/Pixman 与 CPU 渲染，未证明硬件 GPU、物理输入、当前 trusted system-image Host/bundle、QEMU 或 kernel recovery；历史 RED 保留，`finalSystemAccepted=false`。

2026-11-01 当前工作树 trusted system-image 重分阶段：基于 v5 输入集合重新计算当前源码 hash，生成 `system-image-source-v4` 与 `build-current-private-purge-v6-system-image`；`1040` 个普通输入加 `5` 个 trusted overlay 共 `1044` 个文件，独立 staging verification `mismatchCount=0`。同时把正式 `scripts/linux/Dockerfile` 固定到已验证的 HTTPS Debian mirror，避免继续依赖临时 Dockerfile。证据见 `artifacts/linux-current-20261101/system-image-build-v8/staging-verification.json`；仍为 `compiled=false`、`wasmExecuted=false`。首次 driver 容器只因未按脚本固定路径挂载 `/inputs.json`、`/driver.py` 而失败，原始记录保留在 `artifacts/linux-current-20261101/system-image-build-v6/result.json`；随后真实 Docker driver 请求被自动审批服务断流拦截，未据此声称编译失败或通过。当前下一步仍是执行固定路径 driver，随后组装当前 Host/bundle 并做 QEMU 验收。

2026-11-01 Close deadline 根因修复与真实 kernel 复验：父侧在 `ViewBridge::cancel_if_executing` 中区分已写出的正常 `Close` 回复；此时不再在 2s Strict UI grace 到期时注入显式 cancel，仍保留原有 Result 校验、native exit/reap、cgroup cleanup 和 300s watchdog。`ming-app-host` 新增 deadline 回归 `1/1`，既有 deadline cancellation `1/1`，`service_delay_tests` `4/4` 通过。当前修复后的 trusted system-image Host v9 release build、`ldd`、Linux CPU boundary tests 全部通过，Host SHA-256 为 `9ca387ca3586c9ebfd92403c5ba9f3bf9b3463f519948f47d96ca8ad3c226109`。

同日使用该 Host、source-v2 probes、原始未修改七步 InputPlan 和独立新数据盘完成两次 fresh QEMU cold boot。独立审计 `15/15` 检查通过：同 bundle/同盘、不同 boot/session、Counter `1 -> 2`、6/6 生命周期自然完成、`APP_CANCELLED=0`、6/6 `result-accepted`、worker reap、sync/unmount、kernel PowerDown 和无丢失诊断。证据见 `artifacts/linux-current-20261101/current-source-recovery-v3/independent-audit.json` 与 `root-fix-review-v1.json`；bundle identity 为 `79313543769d60c13e4f014f8c30156e2b7d12ece2f2332e5931a0af542f0812`。该结果将 `rootFixVerifiedInKernel=true` 限定在当前 Host + 原始七步 recovery 范围，`finalSystemAccepted=false` 继续保留；物理输入/硬件 GPU、发布者安装生命周期、多用户和跨平台安装服务仍需独立验收。

2026-11-01 当前工作树硬件 GPU 复验：重新构建 `--features gpu` Host，运行 5 组 CPU/GPU image-viewer parity，适配器为 NVIDIA GeForce RTX 4070 Laptop GPU / DX12 / DiscreteGpu / driver `32.0.15.6636`；CPU 与 GPU 每组 PNG SHA-256 完全一致，GPU render 中位数 `1901us`。随后执行全部显式 hardware-GPU ignored tests，当前代码共 `34 passed, 0 failed, 0 ignored`，覆盖 GPU context 隔离、compositor 五层与预算、desktop scene/runtime、Shell、presenter、shadow、SDK GPU 和 device-loss 路径。证据见 `artifacts/gpu-current-20261101/qualification.json` 与 `comparison.json`；该结果只覆盖 Windows 硬件 GPU，不覆盖真实物理输入、Linux 硬件 GPU 或 `finalSystemAccepted`。

## 验证原则

- 软件编译、软件渲染、GPU计算、GPU Surface、Wayland客户端、桌面合成器和系统启动分别记录。
- 使用真实应用与真实执行结果，不把跳过、旧证据或环境变量当作通过。
- 保留每阶段证据；源码/配置改变后，当前阶段重验，历史结果保持原样。
- 应用与SDK边界、故障隔离和性能统计一起验收。GPU资源计时不替代端到端帧率和输入延迟。
- 可启动开发版是中间里程碑；完整日常系统和生产质量仍须通过相应验收。

## 2026-11-02 当前总体复核

本轮在用户授权的 Docker Desktop/Linux 环境中完成当前工作树复核。Docker 镜像 `mingos-linux-task3:71b5bfa149d4` 与独立缓存卷可用；release Linux all-targets 共 `476 passed, 0 failed, 16 ignored`，13 个 headless/Wayland CPU runtime path 全部通过。trusted system-image 当前源码使用 UID65534、禁网、Rust `1.99.1` 完成 release build、`ldd` 和 Linux boundary tests；构建产物 Host SHA-256 为 `d815350c303721002b72bf426f876d2d04d4162ab47e7d27b04729769db4adb1`。本轮新增的安装 catalog 失败精确回滚测试在 Linux system-image lib 运行中通过：`package_install::tests::catalog_failure_rollback_restores_an_absent_install_record`、`ming_desktop::input_backpressure_tests::install_catalog_failure_restores_the_registry_record`。

更新迁移的同 bundle、同数据盘两次 fresh QEMU cold boot 独立审计已通过：`0.1.0 -> 0.2.0`、journal 清理、第二次启动 `already-updated`、兄弟应用保留、marker `1 -> 2`、worker 停止、sync/unmount 和 kernel PowerDown 均通过。当前 Host 原始七步 kernel recovery 仍只在其 exact scope 内通过；Windows RTX 4070 硬件 GPU 仍只代表 Windows 硬件 GPU。完整安装 UI/可信发布者安装生命周期、多用户身份隔离、跨平台安装服务、物理输入、Linux 硬件 GPU/display 和完整 daily-system acceptance 仍未完成，`finalSystemAccepted=false`。详细证据见 `artifacts/overall-gate-audit-20261102.json`。

## 2026-11-03 桌面安装入口实现与验收准备

本阶段完善可信父侧安装入口的失败提示和重试处理，安装登记使用确认时已验证的同一个包对象提交，避免再次读取源包造成身份竞态；补充应用私有数据迁移与回滚覆盖。安装提示状态增加已呈现帧序号，输入计划只在当前提示确已呈现后发送确认。可信安装源允许有界脚本延迟检查待安装应用，实际启动时仍检查登记。

固定 Linux 环境下，冻结源码的 `ming-app-host` release all-targets 测试为 `505 passed, 0 failed, 16 ignored`；release all-features/all-targets check 与 Host、fixture helper 构建通过。真实 fixture 的初始包和源包均通过独立 Ed25519 验签，篡改消息被拒绝。证据分别位于 `artifacts/linux-final-release-20261103-171235/summary.json` 和 `artifacts/install-entry-actual-20261103-171939/fixture-verification.json`。

首次新 bundle 冷启动未通过：QMP 接受 INSTALL 点击，但没有观察到提示；随后脚本启动尚未安装的应用而报 `DESKTOP_UNKNOWN_APP`，第二次启动未执行。原始日志、bundle 和数据盘已保存于 `artifacts/install-entry-actual-20261103-171939/`。首次 pointer 创建竞态是待验证假设，现有记录不足以证明事件在哪一层丢失。本阶段另为安装 profile 增加独立 pointer-ready qualification 标识与提示呈现/握手时序测试；这不代表真实输入问题已解决。

后续必须运行独立被动输入诊断，根据证据修复后重新构建 bundle 并完成两次冷启动与独立审计。诊断专用 Docker 卷的自动审批曾超时，进程未启动，记录见 `artifacts/install-entry-passive-diagnostic-20261103-174309/`。物理输入、硬件显示及完整系统验收仍未完成，`finalSystemAccepted=false`。

输入证据审计准备：为安装 profile 限定专用 pointer-ready qualification，保留原有 policy、诊断开关和资源限制的严格校验；Python boot 相关回归 `76/76` 通过。新增的原始握手证据审计以合成正反例验证 `17/17`，并拒绝历史失败记录；独立复核指出的慢 QMP 回复导致超时窗口误判已修正。审计器尚无真实成功 cold-pair 可验证，其通过不等于 QEMU 验收通过。

2026-11-03 首次 INSTALL 输入被动诊断：在独立 bundle 和新数据盘上运行一次 15 秒诊断，不启用 pointer 握手。QMP 与 QEMU trace 记录 ABS、按下、释放，guest evdev 同样记录双沿；完整 Wayland 客户端日志显示首次 pointer 建立后仅收到释放，没有按下，所有状态均无安装提示。独立复核确认日志 offset 连续、客户端日志完整。证据支持首次 pointer 建立时序存在竞态，但不能定位 evdev 观察点之后的具体丢失位置。观察器在诊断窗口结束时被终止，此次不是安装验收。原始记录与摘要见 `artifacts/install-entry-passive-diagnostic-20261103-181513/`；下一步以现有 pointer-ready 握手执行完整双冷启动，`finalSystemAccepted=false`。

2026-11-03 桌面安装入口 pointer-ready 双冷启动：使用新签名 fixture、新 bundle `c3d2b7191868c0f2eacb124f4091d57542d98d7f0368e65c886c6f630e89255e` 和新数据盘，连续运行两次 fresh QEMU。两轮均证明 mode 16 安装提示已呈现、确认输入经过 pointer-ready 握手、安装 catalog 提交后新应用启动和 ready、worker 回收、数据同步卸载及 kernel PowerDown；同盘 marker `1 -> 2`，boot/session 不同。冻结原始证据审计通过，并经独立复核重放；35 个源码文件运行前后哈希一致。完整证据见 `artifacts/install-entry-pointer-ready-20261103-qual1/`。

该资格使用 TCG、虚拟图形/输入、Wayland 诊断和 pointer-ready 握手；新应用由预审脚本的 30 秒 ready 计时动作启动，实际 trace 显示安装提交先于启动，但该计时不是安装完成依赖。每轮 install-root 在 RAM 重新建立，不证明安装登记跨启动持久化。无仪表输入、物理显示/输入、Linux 硬件 GPU、多用户及完整日常系统验收仍待独立证明，`finalSystemAccepted=false`。

2026-11-03 持久安装下一阶段设计：确认既有安装资格的登记与 catalog 均位于每轮重建的 RAM 根，不能证明跨重启保留。新增 [正常重启后的持久安装设计](superpowers/specs/2026-11-03-persistent-install-design.md) 和 [实施计划](superpowers/plans/2026-11-03-persistent-install.md)，规定同盘持久状态、严格首次初始化与重开、跨文件提交失败边界、两轮不同预审输入及真实 Dock 启动证据。设计经独立复核并修正同步失败的不确定性、Dock 自动启动伪通过和跨时钟域因果证明；尚未实施或验证，`finalSystemAccepted=false`。

2026-11-03 持久安装 Task 1：新增严格持久状态模型、首次 staging/no-clobber 发布、已有状态只读重开和显式 fixture 角色；已有根损坏不能按首次状态补建。独立审查发现并修复磁盘登记与持锁内存记录未严格比较、初始化 catalog 未绑定预审模板 hash 两项问题；均先以失败测试复现。固定 Linux 环境下 persistent 状态 `12/12`、package registry `9/9`、desktop catalog `6/6`、初始化边界 `2/2`、helper `2/2` 通过，helper 构建及实际 CLI smoke 通过。记录见 `artifacts/persistent-install-task1-20261103/README-review-fixes.md`。该阶段尚未接入桌面安装事务或执行持久化双启动；`finalSystemAccepted=false`。

2026-11-03 持久安装 boot 合同准备：新增封闭 profile、两轮输入 hash 与启动角色绑定，以及 guest 首次/重开收据校验；第二轮不提供安装源或脚本动作。独立审查实证并修复输入脚本验 hash 后再次读取的竞态；计划解析、hash 和报告现共用一次有界读取字节。guest 快照同时约束规范路径、受控文件枚举及 256 项上限。定向 Python `91/91` 通过；完整 boot 集合 `116/117`，唯一失败是本机缺少历史 `artifacts/linux-isolation/frozen/mingos-isolation-probe`，未替换旧证据。阶段日志见 `artifacts/persistent-install-task3-review-20261103/`。桌面持久提交事务、安装完成与 Dock 因果收据尚未接入，未运行此 profile 的 QEMU，`finalSystemAccepted=false`。

2026-11-03 持久安装 Task 2：新增受限持久模式及 registry/catalog 安装提交、pending、精确回滚和不确定状态处理；未受该事务保护的更新、卸载和清除在此模式写入前拒绝，普通模式保留。独立复核促成输出路径别名检查和长持久收据的独立有界解析：普通 Wayland 行仍为 64 KiB，完整日志预算不变。Linux 回归分组 `15/12/9/9/1/32/11 passed`，native 另有 5 项原有 ignored；长收据及相关 Python 回归 `100/100` 通过。证据见 `artifacts/persistent-install-task2-20261103/README-review-fixes.md`。输出路径防护覆盖解析时已有别名，不承诺抵御同 UID 在解析后并发替换目录；当前资格限定单可信父进程、严格 worker 不同 UID。尚未完成 Dock 因果收据或持久安装双启动，`finalSystemAccepted=false`。

2026-11-03 持久安装 Dock 收据与审计准备：原生桌面提供有界输入/启动与持锁安装完成收据，guest 绑定 boot/session/PID 和原始 stderr offset 转发。收据仅在显式 `--qualification-receipts persistent-install-pointer-ready-v1` 资格会话启用；普通持久会话不受资格收据边沿上限影响。固定 Linux 七组回归 `37/12/15/12/9/10/1 passed`，native 另有 5 项原有 ignored；Python 相关 `101/101` 通过。独立 checker 的 52 项合成测试及独立复核通过，包含实际 ALLOW 释放先于提交、合法处理中间 revision、签名及跨轮内容约束；合成结果不代表真实双启动通过。准备材料见 `artifacts/persistent-install-20261103-prep/`，真实新 Host/helper、独立数据盘与双冷启动仍待执行，`finalSystemAccepted=false`。

2026-11-03 持久安装正常重启资格：冻结 `e41d906` 的 Linux release 全量测试为 `548 passed, 0 failed, 16 ignored`，all-features/all-targets check 与 Host/helper 构建通过。首次正式运行在输入前因初始 worker 超过 300 秒看门狗而停止，第二轮未执行，失败盘和原始证据保留于 `artifacts/persistent-install-task5-20261103-prep/`。随后独立 A/B 诊断中外层 2 CPU 和 4 CPU 均超时，而外层限流极低；单次 C 对照将 QEMU 输出移至 Docker 卷后约 67 秒 ready，支持输出存储路径或相关 I/O 的影响，但不足以确定具体机制。诊断证据分别见 `artifacts/persistent-install-task5-20261103-cpu-ab1/` 和 `artifacts/persistent-install-task5-20261103-cpu-io-c1/`。

使用新签名 fixture、新 review、新 bundle 和独立新数据盘，将 QEMU 输出放在 Docker 卷，保持 guest 资源与 300 秒看门狗不变，完成唯一一组正式双启动。第一轮通过真实 INSTALL、ALLOW 和 Dock 输入安装并启动应用；正常关机后第二轮不提供安装源或事件脚本，通过 Dock 启动持久安装的应用。两轮同盘同 bundle、不同 boot/session，marker `1 -> 2`，持久内容在第一轮结束及第二轮前后精确一致；两轮均 worker 回收、同步卸载、kernel PowerDown。冻结严格 checker 完整重放通过，独立复核逐项重算 1375 项证据、重验签名与原始输入因果并核对归档，均无差异。证据见 `artifacts/persistent-install-task5-20261103-volume-qual2-prep/pair-verified.json` 和 `evidence-manifest.json`。

该资格限定 TCG、虚拟显示和输入、资格收据及 pointer-ready 仪表化，证明正常重启持久安装与两轮 Dock 启动，不证明断电恢复、物理输入/显示或完整系统验收；`finalSystemAccepted=false`。

2026-11-04 持久普通卸载 Task 1：新增非 bootstrap 应用的 registry/catalog 卸载事务、精确 removed 身份及历史包重新加载验签、空闲 UI ID 分配，以及不补建缺失锁的严格重开。Linux 定向回归为 persistent lib `26/26`、persistent integration `14/14`、package install `9/9`、desktop catalog `10/10`；publisher no-create 专项 `5/5`，独立复核未发现阻断。证据见 `artifacts/persistent-uninstall-task1-20261104/`（publisher 专项输出保留在工具记录）。本阶段事务尚未接入桌面，卸载专属故障矩阵、数据保留及真实双启动尚未完成；`finalSystemAccepted=false`。

2026-11-04 持久普通卸载 Task 2：新增测试专用故障注入，覆盖 pending 创建、registry/catalog 写入与同步、精确回滚、清理及单次补偿。固定 Linux 下 63 个卸载故障场景均符合预期分类，其中 `14 rolled-back`、`21 persistent-blocked`、`28 commit-uncertain`；包含 rename 已发生后磁盘与内存分歧的实际检查。真实事务保留目标和兄弟私有文件、公共文件及授权，独立进程重开再次验证。persistent lib `40/40`、registry lib `6/6`、四组集成 `37/37` 通过；独立复核了原始日志、矩阵和源码 hash。证据见 `artifacts/persistent-uninstall-task2-20261104/`。事务尚未接入桌面或完成真实双启动；`finalSystemAccepted=false`。

2026-11-04 持久普通卸载 Task 3：新增独立 SDK6 样例，写入并同步非空 `note.txt=alpha`，呈现后等待关闭；新增有界、无创建行为的私有数据快照和卸载资格 fixture helper。样例在本机禁外网、Gradle 离线环境构建，经现有 adapter 打包，raw 与 packaged hash 及构建日志已保存；固定 Linux 中快照 `5/5`、helper `4/4`，真实 Host 动态 Ready→Close 测试 `1/1` 通过并验证数据与 worker 回收。独立复核了 helper、快照、适配产物及日志。证据见 `artifacts/persistent-uninstall-task3-20261104/`；这不替代桌面卸载接入或真实双启动，`finalSystemAccepted=false`。

2026-11-04 持久普通卸载 Task 4：桌面普通 U 确认接入持久事务，保护 bootstrap 和未回收应用，并在成功后隔离复用 UI ID 的当前状态、保留历史记录。新资格收据绑定原生按下/释放、提示 revision、精确身份和卸载提交；磁盘提交后输出失败封闭会话而不虚报回滚。固定 Linux 完整桌面状态 `48 passed, 1 ignored`、Shell `10/10`、native `18 passed, 6 ignored`；另显式运行真实 worker 的 Ready→Close→回收→U→确认链 `1/1`，验证私有数据和确认因果。persistent lib `40/40`、四组集成 `37/37` 通过。独立复核了原始收据、源码 hash 和回归日志，证据见 `artifacts/persistent-uninstall-task4-runtime-20261104/`、`artifacts/persistent-uninstall-task4-native-20261104/` 与 `artifacts/persistent-uninstall-task4-regression-20261104/`。本阶段为 CPU native controller 验证，尚未完成 QMP 双启动资格；`finalSystemAccepted=false`。

2026-11-04 持久普通卸载 Task 5：新增独立 boot profile、双轮输入冻结与预检、guest 分阶段严格重开及有界原始收据转发，保持原安装 profile 的合同。独立审查发现的 Strict 状态字段和 bootstrap 保留检查已修复并复核；Task 5 定向 Python `125/125` 通过，34 个 Python 源码文件前后 hash 一致。完整 boot discovery `152/153`，唯一失败为历史 `artifacts/linux-isolation/frozen/mingos-isolation-probe` 缺失；未替换该旧输入。合成 dry-run 未启动 QEMU，证据见 `artifacts/persistent-uninstall-task5-boot-20261104/` 和 `artifacts/persistent-uninstall-task5-guest-20261104/`；专用独立 checker 及真实双启动仍待完成，`finalSystemAccepted=false`。

2026-11-04 持久普通卸载 Task 6：准备独立双轮输入、raw checker 与有界执行器。审查修复了容器权限、路径映射、输入单次读取和源码冻结覆盖；首代冻结遗漏编译嵌入的字体，已保留原记录并在独立 prep2 重新冻结 1211 项（含 assets）。checker/driver 合成与控制测试 `108/108` 通过，两个固定镜像的非 root 无 VM 导入及读写权限预检通过；独立复核了源码、编译包含文件、fixture、输入和预算。有效准备见 `artifacts/persistent-uninstall-20261104-prep2/`。这些只是验收准备，尚未执行本阶段正式构建或双启动，`finalSystemAccepted=false`。

2026-11-04 持久卸载正式构建诊断与 publisher 锁修复：首次构建因 Docker 存储空间不足在复制缓存时停止；新一代使用专用宿主 target 和协作单写者门控，Linux tmpfs 保留真实安装锁。正式 release 测试随后出现一次 `PUBLISHER_KEYRING_BUSY`，失败现场已保留。受控 Linux 探针确认 fork 子进程可在 exec 前继承短期锁，使父进程仅 close 后锁暂时保留；原失败未记录持锁 PID，因此不能断言已捕获其具体持有者。publisher 短期锁改用 RAII 显式解锁，确定性 fork 回归先失败后通过；固定 Linux 实际 crate 的 publisher `6/6`、原失败用例 `1/1`、两线程全 lib `338 passed, 10 ignored`，独立复核通过。证据见 `artifacts/publisher-lock-fix-20261104-t2jjse95/`。修复后仍需新冻结及完整正式构建和双启动，`finalSystemAccepted=false`。

2026-11-04 持久卸载当前源码正式构建与容量阻塞：基于 `5c7874b` 的新冻结在固定 Linux 完成 release all-targets `608 passed, 0 failed, 18 ignored`、all-features/all-targets check、Host/helper 构建和两项显式真实 fixture 测试；双签名及篡改负例通过。独立复核了原始日志、1211 项冻结输入、51 个构建产物与 ELF 依赖。新 bundle 亦已生成并经独立检查 initramfs 内容及静态动态库依赖，证据见 `artifacts/persistent-uninstall-20261104-prep5/`。正式 pair 启动前实测 Docker volume 对 UID 65534 的可用空间为 0 B，低于 2 GiB 准入门槛；因此未创建资格盘、未启动 pair，正式尝试仍为零，证据见该目录的 `task7-pair-capacity-blocked-handoff.json`。未清理历史卷或修改 Docker 配置；正常重启卸载和数据保留的真实双启动尚未验证，`finalSystemAccepted=false`。

2026-11-04 持久卸载宿主存储执行准备：Docker 卷对非 root 的可用空间仍为零；未调整 Docker 配置或删除历史卷。新增显式 Linux tmpfs runtime 目录，将数据盘 lease 和 QMP socket 移出宿主 APFS；默认执行路径保持不变。新增受限 APFS 双启动执行器，保留原构建冻结、review 和 bundle，另以执行冻结绑定宿主变更及运行合同；宿主协调锁仅覆盖遵循同一入口的同 UID 写者。固定 Linux 运行时相关测试 `73/73` 通过，执行器进程控制 `15/15` 通过，并经独立源码及来源链复核。双容器无 guest 实测确认 tmpfs、QMP、两轮测试盘内容读回、锁竞争拒绝及容器停止后解锁；证据见 `artifacts/runtime-host-adaptation-20261104-061817/`、`artifacts/persistent-uninstall-apfs-runner-validation-20261104-062540/` 和 `artifacts/runtime-storage-double-smoke-20261104-062211/`。该预检不证明断电耐久、真实 guest 性能或卸载资格；正式资格盘尚未创建，`finalSystemAccepted=false`。

2026-11-04 持久卸载 APFS 正式尝试与验签依赖修复：新代正式容器在启动前检查中因固定 tools 镜像缺少 `openssl` 而失败；该次 attempt 已封存，不复用。独立审查确认 backing 目录为空，未创建数据盘，QEMU/guest 启动次数为零，容器已停止且 PID 为 0。此前无 guest 存储预检没有执行完整容器内验签链，是本次准入遗漏；失败证据见 `artifacts/persistent-uninstall-20261104-apfs-qual1-f1dbc0f7abc1/`。执行器现要求只读提供原固定 Rust 镜像中的 OpenSSL，绑定二进制及六项动态依赖 hash，保留原 checker 的签名验证。真实 tools 容器内已分别覆盖启动前和最终 checker 的真实正、负验签，以及来源、二进制和依赖漂移拒绝；VM 启动边界被拦截，诊断未启动 guest。来源还限定在容器只读挂载的仓库内，避免容器路径缺失；执行器控制测试 `19/19` 通过，最终无 guest 证据见 `artifacts/openssl-runtime-diagnosis-20261104-064524-readonly-repo/`。该修复和预检不代表真实双启动通过，`finalSystemAccepted=false`。

2026-11-04 持久更新设计准备：持久 catalog 当前仍拒绝更新，普通更新迁移的既有资格不能直接转用。新增 [持久 catalog 下的应用更新与失败恢复设计](superpowers/specs/2026-11-04-persistent-update-design.md)，保持 catalog 原字节，并定义协调记录、完整启动确认、显式中断恢复、原私有目录缺失及备份清理中断的处理；经独立源码与设计复核。设计未实现、未构建或执行 VM，持久更新和正式卸载双启动均未验收；卸载 candidate2 的 1214 项冻结输入未改变，`finalSystemAccepted=false`。

2026-11-04 持久更新实施计划：在已审设计基础上新增 [九项实施与验证计划](superpowers/plans/2026-11-04-persistent-update.md)，覆盖无创建观察、原缺失数据恢复、外层协调及 inner journal、真实故障与跨进程恢复、桌面确认、独立 fixture/boot checker 和正常重启资格。计划经独立复核；其命令和预期不是实际测试结果，本阶段未实施功能或运行 VM。卸载 candidate2 的冻结源码未改变，`finalSystemAccepted=false`。

2026-11-04 持久更新 Task 1（隔离工作区）：新增完整私有树只读摘要和无创建恢复 authority，区分缺失与空目录，并限制描述符占用随目录深度增长；最终核对 policy 目录和锁的名称绑定。锁替换和 soft `RLIMIT_NOFILE=1024` 下 1024 项观察均先以真实行为测试失败，再修复通过。固定 Linux、Rust 1.99.1 下最终 `app_data` 测试目标 `76 passed, 0 failed`，证据见 `artifacts/persistent-update-task1-20261104/`；完整 Host `--lib` 构建主动停止，未计为通过。观察器要求受控写者串行，不能声称任意外部并发写入下的全局原子快照。后续协调器仍须绑定公共 policy slot 原始存在性及 hash。本阶段尚未接入更新事务或完成持久更新资格，`finalSystemAccepted=false`。

2026-11-04 持久更新 Task 2（隔离工作区）：新增原私有目录缺失状态的受限回滚、已有数据备份和迁移交换、阶段约束及提交后清理。审查发现的父目录同步重入、不可达临时布局、原迁移幂等语义及已有目录丢失确认均以行为反例复现后修正。固定 Linux、Rust 1.99.1 下 `coordinated_` 定向 `22/22`、默认 features 的 `app_data` `98/98` 通过；证据见 `artifacts/persistent-update-task2-20261104/`。本阶段的 checkpoint 错误及对象重开不代表真实进程 SIGKILL；后续协调器须在持锁状态下校验原回滚阶段，并按绑定迁移和完成阶段验证新树存在性，提交后部分 backup 清理须走确认状态路径。桌面接线和持久更新资格尚未完成，`finalSystemAccepted=false`。

2026-11-04 持久更新 Task 3（隔离工作区）：新增严格协调记录解析、公共 policy slot 存在性及字节摘要、受控历史内容摘要和仅供协调器使用的内容快照；普通严格重开仍拒绝活动事务。独立审查促成决定/阶段/活动版本矩阵、持有锁的名称身份和事务标记实际存在性检查，并保持既有版本兼容。固定 Linux、Rust 1.99.1 中 `persistent_update` 测试目标 `111/111`（含嵌入的既有模块测试）、`persistent_install` `14/14`、无默认 features 的 `app_data` `98/98` 和无默认 features check 通过，证据见 `artifacts/persistent-update-task3-20261104/`。内容快照不验证全部私有数据/备份布局，也不授予写入权限；事务协调与持久更新资格尚未完成，`finalSystemAccepted=false`。

2026-11-04 持久更新 Task 4（隔离工作区）：新增不调用普通自动恢复的受限 journal 检查、发布、回滚和完成清理接口；每次 mutation 在 operation mutex 内重新绑定真实 outer/inner、旧新身份及数据 authority。补齐原回滚阶段预检、确认前完整 backup、确认后部分清理及 inner 已清后的稳定状态检查；record rename 后同步失败按真实磁盘重载 cache，重载失败封闭当前 registry。独立复核指出的 `StartupConfirmed` 重入持久化遗漏和 fatal registry 启动入口遗漏均由真实行为 RED 复现后修复。固定 Linux、Rust 1.99.1 中定向测试 `18/18`、普通 `package_install` 集成 `9/9`、完整 `persistent_update` 目标 `129/129`（含这 18 项及嵌入的既有模块测试）、无默认 features 的 `app_data` `98/98` 和无默认 features check 通过；普通 pending/startup 合同保持。证据见 `artifacts/persistent-update-task4-20261104/green-result.json` 及同目录 RED、GREEN 原始日志；验证前后源码 hash 一致，独立复核无剩余阻断。这里的 checkpoint 错误、真实 rename/unlink 窗口与对象重开不等于真实进程 SIGKILL 或断电恢复；协调器、跨进程中断矩阵、桌面接线及持久更新双启动资格尚未完成，本阶段未运行 VM，`finalSystemAccepted=false`。

2026-11-04 持久更新 Task 5（隔离工作区）：实现 begin/confirm/显式 recover 协调器、持久 `Prepared`/`CommitDecided` 决定及结构化失败分类；已有实例与无创建恢复入口复用同一状态表，普通 mutation 在 outer 存在时写入前拒绝。协调更新的 candidate/journal/record 临时名绑定 transactionId，显式恢复先核对完整历史内容、authority、阶段和私有数据/备份，再按有界 no-follow 捕获及持有 FD 的身份精确清理，最后重新严格快照；普通安装路径保持兼容。真实 RED 复现旧入口因自身 candidate 临时目录而拒绝合法恢复；修复后，先前64个受自身临时对象阻断的合法窗口均实际恢复旧态，8个没有可信完整 outer 的空记录窗口仍无写拒绝。恢复进程自身在临时对象、旧 record、backup 及 inner/outer 清理中再次 SIGKILL 后，也能由后续独立进程完成恢复和严格重开。

本阶段使用新固定 Linux 镜像 `sha256:868a411f3e53b95fcdfb0604031e0172e51cf7d1ae2bce8aa0ea1c0bb82f0130`、Rust 1.99.1，在禁网、UID65534、只读源码和单 Cargo writer 条件下，最终12条命令全部退出0：11条测试命令合计331项通过，1项 ignored 仅为父矩阵专用 child 入口，另1条无默认 features check 通过。仅新增的 no-default startup outer guard 单测使用 `--config profile.release.package.ming-app-host.opt-level=0`，其余检查保持原 release 配置。原始日志实录615条 case、770次真实 SIGKILL，全部确认 signal 9 并 wait/reap；主矩阵308例中234例回滚、66例提交、8例空 outer 无写拒绝。119项源码/Cargo 文件验证前后 hash 一致，独立复核无剩余阻断，`git diff --check` 通过；证据见 `artifacts/persistent-update-task5-20261104/session8/` 的 RED 及 `session10/verified-result.json`、`case-summary.json` 和原始日志。公共集成只使用 public API，fault/child hook 留在库单测内。本阶段未接入桌面更新生命周期，未完成独立 fixture/boot checker、正式构建或 VM 双启动资格；未运行 VM，也不证明断电耐久性，`finalSystemAccepted=false`。

2026-11-04 持久更新 Task 6（隔离工作区）：桌面当前提示确认接入持久更新事务，入口和确认均重新检查旧新包身份及目标空闲状态，保护 bootstrap、closing、未回收实例及待启动项；保持 catalog 原字节、标题和历史运行记录，仅重置当前绑定。活动 outer 或任一 inner 阶段均封闭普通 mutation/launch，trial 只允许原生 Dock down 启动一次，先持久 `StartupAttempted`，运行代 reap/drop 后逐字段验证完整证明再提交；失败和未启动即关闭的 trial 使用同一持锁恢复入口，不自动重启旧 worker。新增显式 `persistent-update-pointer-ready-v1` 资格收据和有界状态，区分 trial、commit-decided、committed、rolled-back、blocked、uncertain；持久提交后的 state/receipt/log 输出失败先保持内存与稳定新态一致，再封闭会话，不回滚已提交磁盘。公共 policy slot 仍要求存在性和字节不变；若新 fingerprint 需要新增公共授权，恢复旧态并以 `DESKTOP_UPDATE_PUBLIC_AUTHORIZATION_REQUIRED` 拒绝，不在 trial 中新增或转授授权。

精确复原 HEAD 加初始行为测试的 RED 实际编译成功，因 `DESKTOP_PERSISTENT_MUTATION_UNSUPPORTED` 出现 `0 passed, 1 failed`（exit101）；修复后在固定 Linux 镜像 `sha256:868a411f3e53b95fcdfb0604031e0172e51cf7d1ae2bce8aa0ea1c0bb82f0130`、Rust 1.99.1、禁网、只读源码和单 Cargo writer 下，最终 verified 快照五条命令全部 exit0：输入 `63 passed, 1 ignored`、native `20 passed, 6 ignored`、persistent_update 集成 `9/9`，以及默认与无默认 features 的 release check。此前 green2 快照九条命令全部 exit0：输入 `61 passed, 1 ignored`、native `20 passed, 6 ignored`、desktop_shell `16/16`、persistent_update 集成 `9/9`、persistent_install unit `36/36` 和 integration `14/14`、package_install unit `24/24` 和 integration `10/10`，另有无默认 features check。green2 后仅桌面实现增补已封闭状态不重复恢复、恢复失败次数反例、shutdown 输出失败释放 session，以及 rollback uncertain 精确分类和双故障反例；最终快照重跑前述五项，未把 green2 专项描述成最终快照重跑。全部使用原 release 配置，没有 opt-level=0；ignored 均未计为通过，中止的过时代编译也未计通过。最终119项源码/Cargo hash 前后稳定并与提交前工作区一致，独立复核无剩余阻断，`git diff --check` 通过；RED、green2、verified 原始日志和结果见 `artifacts/persistent-update-task6-20261104/`。

本阶段 session 正常提交测试使用人工完成 record/PID（123），shutdown 释放测试使用 `worker=None` 的合成实例；native 实测覆盖真实 controller 提示确认、trial 发布和首次 Dock down，空 Wasm fixture 随后失败并回滚。这些结果不证明真实 native worker 的完整成功提交或 PID 回收；对应真实 worker、独立签名 fixture、boot checker 和正常重启资格仍属 Task 7 及后续工作。本阶段未运行 VM、未取得持久更新双启动资格，也不证明断电耐久性，`finalSystemAccepted=false`。

2026-11-04 持久更新 Task 7（隔离工作区）：新增三个独立源码的 SDK6 Kotlin/Compose fixture：旧 `0.1.0` 读取 `note.txt=alpha`，新 `0.2.0` 读取迁移后的 `state/note.txt=alpha`，无 migration 的 `absent-write` 从原 namespace 缺失状态写入并要求目录同步后读回；均呈现版本并等待 Close。构建保留 raw/packaged Wasm、adapter 改写、实际 SDK 导入及 535 项源码输入 hash，helper 对精确 packaged 字节及对应版本/migration manifest 签名。macOS `sandbox-exec` 下三版均以 Gradle `--offline` 实际重构建，阻止非 loopback 网络、允许 Gradle 所需 loopback；这不是 Linux `network=none`。Node 执行真实编译模块的 `3/3` 测试使用合成私有文件、渲染、输入和 Close capability，只证明模块合同，不代替真实文件、进程或重启验证。Kotlin 来源证据见 `artifacts/persistent-update-demo/`。

新增窄初始化 facade，显式接收可信 `data_root` 及 `UpdateFixtureLayout::{NoteAlpha, Absent}`；先认领唯一私有 guard，在其中的 staging 完成 bootstrap 和旧目标包，在显式 data 根建立必要 policy 锁/seed，再整体 no-clobber 发布正式安装根并同步父目录。正式根不会暴露单包中间态，失败保留 guard 与该代 data 并拒绝重试；支持正式安装根直接位于 data 根内。helper 严格分离 `initialize-first`、无创建 `open-existing` 和不接收源包的显式 `recover-update`，外置 receipt 分列初始化、恢复及 UI commit 动作。初始化行为 RED 实际编译成功，因只有一个包而非两个包得到 `0 passed, 1 failed`、exit101；最终固定 Linux 镜像 `sha256:868a411f3e53b95fcdfb0604031e0172e51cf7d1ae2bce8aa0ea1c0bb82f0130`、Rust 1.99.1、禁网、UID65534、只读源码和单 Cargo writer 下，facade `6/6`、helper `5/5`、显式真实 controller runtime `3/3`、独立 Host/public integration `1/1` 通过，均为 `0 ignored`；事务库回归 `54 passed, 5 ignored`、记录集成 `9 passed, 0 ignored`、桌面输入 `63 passed, 1 ignored`、native `20 passed, 6 ignored`。ignored 单独保留，未计通过；事务回归中的四个 runtime/child 入口由前述显式 runtime 及其子进程执行。release Host/helper 构建、默认与无默认 features check 均退出0，全部使用原 release 配置；121 项 Rust/Cargo hash 验证前后稳定并与提交前源码一致，`git diff --check` 通过，独立复核无剩余阻断。Rust RED、最终九步及额外两项桌面回归的原始日志和结果见 `artifacts/persistent-update-task7-20261104/{red,verified}/`。

真实 controller 测试动态等待新 worker 的 inputReady 与已呈现帧，Close 后等待真实 Result、native exit 和 wait/reap 才确认提交；两种布局提交后均由独立 release Host 严格重开同根并运行新包。seed 与 absent 两分支均在真实写入/迁移、Ready 后发送 Close，随即停止 controller polling；待 supervisor 实际接受 Result 并 reap 后，仍保留 Prepared，再真实 SIGKILL controller 并 wait；后续独立 helper 进程恢复，另一进程严格重开，seed 分支还由独立 Host 实际运行旧包验证旧布局，absent 分支精确恢复 namespace/files 原缺失。另以专门签名 WAT 通过真实 SDK 私有文件调用同步写入非空数据后 trap，验证失败回滚原缺失状态；它不是 Kotlin fixture。bootstrap 明确使用已有旧构建输入并记录 hash，未声称本轮重新构建。上述为 Linux CPU controller/offscreen 与真实子进程证据，未运行 VM、Wayland/QMP 双启动、物理输入或断电测试；boot checker、正式冻结构建及持久更新双启动资格仍待后续任务，`finalSystemAccepted=false`。

2026-11-04 持久更新 Task 8 Rust 前置基础（隔离工作区）：修复首次 helper 与真实 fresh guest data 根的集成冲突。原实现对已有 `lost+found`、`.mingos-boot/current.json` 返回 `UPDATE_FIXTURE_DATA_NOT_EMPTY`，行为 RED 实际编译后为 `0 passed, 1 failed`、exit101。首次初始化仍是显式动作，只接受空根或这两个精确基础设施条目；核对空 `lost+found`、boot 目录唯一 marker、同设备/owner/group、0700/0600、无链接及严格当前 kernel bootId/counter=1，并在认领前和发布后重新检查名称、身份与内容。额外业务数据、非法 marker、链接及读取期间替换均拒绝；既有基础设施不被修改。marker 不能认证新盘，guest 调用前仍须独立核对 UUID/serial、新盘角色和 previousMarker=None。

`trial-published` 新增只读 `trialObservation`，在同一 registry operation mutex 内绑定真实 outer/inner 和已持有的 authority，验证 `recordPublished` 及认可的私有备份布局，双读检查内容和递归私有树稳定。收据携带 outer/inner 原始字节的标准 base64 及 SHA-256，分别上限4096/16384字节；校验者能对同一 bytes 复算摘要并解析身份/阶段，不依赖 JSON Value 重序列化。`privateContent` 是当前 coordinated 递归观察，不使用旧 `privateBefore` 或平面快照冒充迁移后的树；不输出私有文件正文。原262144字节单行硬限继续生效，普通严格重开仍拒绝活动事务。反例覆盖缺失/错误身份/提前阶段的 inner，以及观察期间 marker 同字节替换、安装内容和私有文件漂移。

本轮实际运行的 controller 成功、SIGKILL 后显式恢复和 trap 回滚三项，均使用带上述 fresh 基础设施的根、真实 supervisor/worker 和已冻结输入；继承 Task 7 的 Kotlin/签名 WAT、独立 helper/Host 边界，没有重新宣称 Kotlin 或 bootstrap 构建。Dock 启动前断言真实 raw/hash、内容和当前私有树，三条实际 trial 收据为10360–10671字节，独立复算 base64/hash、outer record 和 inner transaction/phase 一致。实际 Shell geometry、两次 DesktopModel::open 和 native 控件位置单测输出1280×800两应用的几何 JSON：boot1 五点 `(1148,12)`、`(880,478)`、`(676,766)`、`(96,104)`、`(1238,12)`；boot2 为 Dock 与 POWER 两点。输出及三份实际几何源码 hash 绑定于 `verified/input-geometry.json`，不是 Python 重写几何的合成证明。

固定 Rust 1.99.1/Linux 镜像、禁网、UID65534、只读源码、cap-drop/no-new-privileges、2CPU/4GiB 和单 Cargo writer 下，最终十步全部退出0：本包显式 `--config profile.release.package.ming-app-host.opt-level=0` 的 geometry `1/1`、facade `10/10`、helper `6/6`、真实 runtime `3/3`、独立 public integration `1/1`、事务库 `61 passed, 5 ignored`、记录集成 `9/9`；上述各定向目标均0 ignored，事务库的5项 ignored 单独保留，未计通过。三个真实 controller runtime 测试使用先前原 `opt3` build 生成的 worker；随后独立 public integration 的本包测试目标及 Cargo 重建的 Host 遵循显式 `opt0` 配置。生产 Host/helper build 及 default/no-default release checks 均保持原 `opt3` 并退出0，但当前 target 路径的产物不能替代后续正式重构建与冻结。当前 `opt3` lib tests 在 LLVM 单线程优化阶段超过有界等待后停止，未计为通过；诊断曾定位既有 shadow_service 测试的重复循环优化，测试输入 black_box 尝试未证实有效且已完全撤销，没有为此修改生产实现。最终121项 Rust/Cargo hash 前后稳定并匹配当前源码，`git diff --check` 通过，独立只读复核无剩余阻断。证据见 `artifacts/persistent-update-task8-foundation-20261104/{red,verified}/`；最终 `verified/result.json` SHA-256 为 `91ec7982e64d0b347fcaf34ebcb86b92db58620488cc0e8d27382251ad3d50e0`，配置差异在该结果与 `verified-summary.json` 明列。

另从历史 `boot-delivery-20261027/tools-image.tar.gz`（config SHA-256 `2440560d810a834637cfeeb1057f7f471c40caa8e532645e00e063cbc8ec639a`）只读提取 e2fs 工具、loader 和依赖，在固定 Rust 容器内对一次性128MiB raw 执行 `mke2fs`、只读 `debugfs` 及 `e2fsck -f -n`，均退出0；`lost+found` 实测 inode11、0700、uid/gid0、只有`.`/`..`，raw 随后删除。来源和逐项 hash 见 `red/aux-source.json`，没有 pull/load 历史镜像、mount 或 VM；这是 fresh ext4 结构的辅助探针，不是 Task 8 拟用 tools 镜像的正式准入。临时验证容器在证据保存并确认无后续复用后删除，保留镜像、缓存及日志。本次只完成 Rust 前置基础；并行 boot/checker/driver、完整源码/构建/执行冻结及正式双启动尚待后续集成与验证，未取得正常重启或断电资格，`finalSystemAccepted=false`。


2026-11-04 持久更新 Task 8 boot/checker/driver 实施（隔离工作区）：新增独立 `persistent-update` profile 与 `persistent-update-pointer-ready-v1` 合同，guest 在核对新盘身份后仅首轮调用 helper initialize-first，第二轮仅 open-existing。独立 parser 在实际 native 原始日志中验证 trialObservation、outer/inner 原始 bytes/hash、历史内容与迁移后的递归私有树，绑定同 boot/session/PID/raw offsets；InputPlan 只有在真实 trial 身份及 app2 closed/committed 全部匹配后才允许 POWER。两轮输入固定为真实 geometry 单测导出的5步/2步，保留300秒启动、5秒 pointer-ready、完整 down/up、900秒单轮、1920秒 pair、1950秒外层预算；没有放宽 worker 限制。

准备目录 `artifacts/persistent-update-20261104-prep/` 中实现独立 raw checker 和 prepare→build→bundle→admission→pair→export→verify driver。源码捕获覆盖 tracked、未跟踪和必要忽略的 Kotlin/runtime/资产，单次读取并验证完整新增文件集合；阶段结果串联 freeze 与完整产物摘要。host 与容器内盘创建/每次 VM 启动边界均要求绑定 qualification/freeze/新128MiB盘/双启动且无重试的具体授权、真实无 guest admission 和全部前驱产物；第一轮失败永久停止，不跑第二轮。协作 native writer 锁、同 UID Docker 可写 bind 交叠检查、信号停止与停止确认后清理保护实际执行。导出携带精确冻结的7个 boot Python 依赖、固定输入及无自引用的全文件 SHA-256 manifest；导入前及 pair 执行前重算文件集合/hash，source CLI 验 manifest 后隔离执行归档 checker，禁止导出间依赖混用及写入 sealed export。driver verify 从 `/tmp` 使用 `python -I -B` 执行 export 自带 checker，结果保存在 seal 外。

截至本阶段记录，本机 Python boot 定向 `141/141`、driver `17/17`、最后 checker 源码控制 `20/20` 通过，独立 portable 专项 `9/9` 通过。完整合成 pair 使用真实 OpenSSL 验签及原始 QMP/Wayland/native/snapshot 结构，但未执行 helper、Host 或 VM。此前固定 tools 镜像、network=none、UID65534、2CPU/4GiB、read-only/cap-drop/no-new-privileges 的开发代 `development-admission-954a14266635` 实际执行 checker `17/17`、driver `17/17`、rootfs copy 与 isolated guest/parser import、Ed25519 正验与篡改 exit1、tmpfs0700、QMP Unix socket、lease 竞争/释放和 VM Popen 边界截获，均通过；availableBytes 为88102469632。该退出容器确认 ExitCode0、Pid0、非 Running/Restarting、非 OOM 后精确删除，镜像、缓存和日志保留。此开发代早于 checker 最后的输出路径与源码代际边界修复，不能作为最终提交源码的准入证据；最终源码提交后仍须更新 draft 并重新执行无 guest 准入，不预记尚未运行的结果。

拟用 tools 镜像此前本机缺失，经授权从本地历史 `boot-delivery-20261027/tools-image.tar.gz` 恢复，完整归档 SHA-256 为 `a23ebeb8728056018bd66537cda32213812d693e95548b45daa43881fc110511`，8层 diffID 和 config 均核验；恢复 ID 为 `sha256:2440560d810a834637cfeeb1057f7f471c40caa8e532645e00e063cbc8ec639a`，未 pull 或修改 Docker 配置。OpenSSL 来自历史 `sha256:3646133f4b4d2e007dd3eee28d9db6336c29f6ccb31b9f8a49b88bc0a70051be` 的已留证提取输入，执行时核对二进制与六项动态依赖 hash；它不是当前 Rust build 镜像 `sha256:868a411f3e53b95fcdfb0604031e0172e51cf7d1ae2bce8aa0ea1c0bb82f0130`。来源、恢复、开发 probe、inspect 与 cleanup 记录均保存在该准备目录。bootstrap 继续是显式历史外部字节，本轮未重编；Task7三版 Kotlin 保留实际离线构建及 macOS sandbox 网络边界，未称为 Linux network=none。

提交的源码和固定输入可重跑合成控制，但 driver 不会自动下载或重建全部真实准备输入：真实 prepare/build 仍依赖 Task7 Kotlin Wasm/构建和网络 receipt、历史 bootstrap 字节/receipt、OpenSSL 二进制和来源证据、固定本地镜像及显式只读历史 Cargo/cache。缺失或 hash 不符时失败关闭；这些外部二进制、机器运行时、完整日志及动态 draft/inventory 保留为忽略的证据。此时仍只有未封存 draft，没有正式 `freeze.json`、reviewed session、具体授权或新数据盘；正式 build/bundle/admission/pair/export 尚未执行，未运行 guest，也未取得正常重启、物理输入或断电资格，`finalSystemAccepted=false`。

2026-11-04 持久更新 Task 9 首代正式构建失败：随后对提交 `c87b671fb919131a6bfcdc02a06bfb4af9b47923` 完成最终无 guest 开发准入 `development-admission-16a70c6cb970`（checker `20/20`、driver `17/17` 及真实运行时边界），并在本地 prepare/build 授权下生成正式 1425 项冻结与 prepare 成功记录，qualification 为 `persistent-update-20261104-qual1-f677613d9016`。新 build volume 容量预检通过：UID65534 可用 7,173,222,400 B，要求 4,844,613,712 B，实际 write/read/fsync/delete 通过。首条 `cargo +1.99.1 test --offline --locked --release -p ming-app-host -j1 --all-targets` 于 UTC 11:22:33 至 11:52:33 达到固定 1800 秒超时；观测时停留在 `ming_app_host --test -C opt-level=3` 编译，stdout 为0 B，没有测试运行或后续 Cargo 命令通过证据。原始 stderr 没有 Rust 编译错误；最后快照 rustc CPU99.9%、容器约1.988GiB/4GiB、卷仍余4,435,607,552 B，退出记录非 OOM，不能据此断言具体优化 pass 或该编译最终可完成。失败容器已退出并精确删除，实时 inspect 确认不存在；本代 build 与 build-preflight 卷、冻结及原始日志保留，没有重试、降级优化或放宽预算。证据见 `artifacts/persistent-update-20261104-prep/{build/cargo-1,build/diagnostics,stages/build}/`。

独立单文件诊断随后使用同一 Rust 1.99.1、`rustc --test -C opt-level=3`：原 `shadow_service.rs` 在90秒限制内未完成编译；仅在测试模块抽出 `#[inline(never)]` 的 invalid-handle 断言 helper 后约0.479秒完成编译，原7项测试全部通过，生产代码字节保持不变。已核验诊断 summary 所列全部原文件 hash，summary 与输出日志保留在 `artifacts/persistent-update-20261104-prep/build/diagnostics/shadow-opt3-helper/`（原始来源 `/var/folders/t7/8c0zz39j6dx21lhzl0y33kmh0000gn/T/ming-shadow-opt3-helper-12bjdcyt/output/summary.json`）。该对照支持这一测试形状触发局部 opt3 编译瓶颈，但不证明整个 crate 的正式超时已解决；完整 Cargo opt3 验证仍待新代重跑。当前测试与本文档已不同于旧 freeze，旧代只保留失败证据，不得用于当前源码继续构建或资格执行。

新代 driver 路径修复移除了旧 prep 目录的固定映射：容器按 inventory 中 `run-stage.py` 的父目录与实际 `/prep` 的 `samefile` 唯一匹配，重新枚举该代新增输入；冻结环境按同一来源相对路径映射回宿主仓库，完整源码集合/hash 与资源合同仍须匹配。三个新增回归先以2项失败、1项错误复现，再修复；driver `20/20`、checker 与 driver 合计 `40/40` 通过。真实固定 tools 容器只读绑定当前仓库与当前 prep，`samefile=true`，当时 host/container 的1425项完整源码集合/hash一致；没有创建新代、运行构建、启动 guest 或挂载 volume，诊断容器自动删除后另由 inspect 确认不存在。新代目录行为另由 host 和映射根单元控制覆盖，不能把当前 prep 的 bind 检查称为新代完整准入。独立只读复核无阻断；RED/GREEN、完整测试和实际 Linux 原始输出见 `build/diagnostics/preparation-path-fix-*`。旧 `freeze.json` 字节和两个旧代卷在上述修复与检查期间均保持不变。

独立 `prep2` 的首次开发准入 `development-admission-b187dfe4a06c` 随后因新增测试 fixture 调用 `git init`、固定 tools 镜像没有 Git 而失败：checker `20/20` 已通过，driver 的原17项通过、新3项在测试准备中报错；未生成正式 freeze、未开始构建，退出容器已删除，原始失败日志保留。测试现只模拟 host `git ls-files` 边界，严格核对 argv 与 cwd/capture_output/check 参数；实际文件枚举、目录身份、hash 和 freeze 验证均继续执行，未向镜像添加 Git。最终同一固定 tools 镜像中 checker+driver `40/40` 通过，五个执行/测试源码前后 hash 一致，诊断无 guest、无 volume，容器删除已复验；证据见旧 prep 的 `build/diagnostics/git-free-tools-controls-final/`。旧 build 卷只读盘点为17,010文件、实际占用2,737,602,560 B，保留失败编译缓存与中间态；预检卷证明为空且无容器引用后已按授权精确删除，inspect/rm/删除后证明见 `build-volume-admission/{old-volumes-inventory,preflight-volume-cleanup}.json`。新代仍须按提交后的完整输入重新生成 draft、无 guest 准入和容量检查，不能复用失败准入或旧代容量结果。

本代没有生成 payload、review、bundle 或具体 pair 授权，没有创建资格数据盘或启动 VM；正常重启持久更新仍未验收，`finalSystemAccepted=false`。

2026-11-05 持久更新第二代正式构建：首代 `f677613d9016` 失败 build 卷先以只读 PAX 归档、全量文件与元数据 manifest 及独立卷真实恢复逐项核验保存，随后按明确授权精确删除；归档与删除证据见 `artifacts/persistent-update-20261104-prep2/{volume-archives/persistent-update-20261104-qual1-f677613d9016-build,old-build-volume-cleanup-20261105}/`。新代 `persistent-update-20261104-qual1-c90a20436c4c` 基于提交 `f18bdf572615c2dcc4a50226dbfc59aa51674970` 的1435项冻结，通过独立容量检查后正式 build。首条 release all-targets Cargo 在固定1800秒限制内约802.563秒退出101，未超时、非 OOM；opt3 编译完成，lib 为 `450 passed, 0 failed, 15 ignored`，其中 shadow_service 原7项均通过。随后 `graphics_host` 为 `5 passed, 1 failed`：`real_kotlin_viewer_renders_and_handles_scripted_input` 在 `graphics_host.rs:98` 因缺少真实 Kotlin viewer 产物报 `Build real Kotlin viewer first`；未运行到的 host 测试还依赖 read-file 产物。因此完整 Cargo/build 仍失败，后续 Cargo、显式真实恢复/worker 检查及 payload/review/bundle 均未完成。失败容器已退出并精确删除，当前 c90 build 卷保留；原始 command、stdout/stderr、failure/cleanup 与监控在 `artifacts/persistent-update-20261104-prep2/{build/cargo-1,stages/build,build-monitor-20261105}/`，失败冻结不续跑。

2026-11-05 持久更新真实 Kotlin 回归样例补齐：在独立目录按上述历史冻结重建1435项源码副本；两份已被后续授权修改的 driver/test 旧字节从不可变 Git blob 恢复到副本，未覆盖工作区。609项实际 Kotlin/SDK/Compose/Gradle/adapter 输入与当前源码一致，JDK 21.0.13、Node 24.22.1、Gradle 8.14.5、Kotlin 2.3.20 下真实离线强制重编 read-file 与 image-viewer，`BUILD SUCCESSFUL`、8项任务全部执行。网络边界为 macOS sandbox 禁止非 loopback，保留 Gradle 所需 loopback，已用父/子进程 TCP/UDP 拒绝探针及 Java loopback 实测；不能称为 Linux `network=none`。两个包各保留 compiler/main Wasm、manifest、build-info 和来源收据，核对 ABI/import/capability、当前 adapter 重放及工具链1199项前后 hash；viewer packaged SHA256=`2eb888508c0fb4ac0a27df4bdbe8dfff1b402c46c1318dff56b6297c5988de21`，read-file=`83c17b2df1bf7872f3fd8fa5bf64450eaaf33425df08678219d1ebdaf6306c49`。确认目标不存在后已无覆盖安装到 `artifacts/{image-viewer,read-file}/`，独立来源包与26份小证据保留在 `artifacts/persistent-update-regression-fixtures-20261105/`。driver 已补两包来源、收据、冻结及封闭导出检查，经独立审查无阻断；这些样例不是持久更新资格 payload，也不表示失败正式代已重跑。

2026-11-05 持久更新当前固定 Linux 控制：固定 tools 镜像实际执行 checker `20/20`、driver `26/26`，合计 `46/46` 通过；当次1461项输入集合/hash前后相等，执行代码 hash 和原始日志见 `artifacts/persistent-update-20261104-prep2/tools-controls-20261105/`。容器为禁网、UID65534、2CPU/4GiB、只读源码，仅使用 tmpfs，无 volume/资格盘/guest；退出与删除均留证。该检查与真实样例来源链独立复核均无阻断，但不替代提交后的新 draft、完整无 guest 准入、新冻结和容量检查。

2026-11-05 持久更新失败卷保留与后续边界：c90 卷只读盘点为17,022文件、3,174目录、42组 hardlink，唯一文件实际分配2,715,623,424 B，无外部 hardlink、特殊对象或 xattr；无容器引用，当前未获删除授权。宿主容量足够按原流程归档并独立恢复验证；评估时 Docker 文件系统 UID65534 可用4,457,607,168 B，低于既定缓存复制加2GiB保留的4,844,615,156 B，但这不是未来新代的容量准入。此时仅完成 metadata 评估，尚未建立 c90 全内容 manifest、归档或恢复证明，记录见 `artifacts/persistent-update-20261104-prep2/failed-build-volume-assessment-20261105/result.json`。修复后的新冻结代完整构建尚未执行，未创建资格数据盘或启动 VM；正常重启持久更新及完整显式恢复均未验收，`finalSystemAccepted=false`。

2026-11-05 持久更新正式执行器覆盖修复：复核发现原 driver 未显式执行 `tests/persistent_update_runtime.rs` 中被 ignored 的 `separate_process_roles_and_real_stable_host_refuse_active_update_without_writes`；其普通 all-targets 运行会跳过该项，原显式列表也只列出两个恢复父测试。driver 现保留三个真实桌面 controller 检查，另以 `--test persistent_update_runtime`、精确名称及 `--ignored --test-threads=1` 执行 public 集成；恢复步骤改为一次串行执行整个 `persistent_update::recovery_tests::`，不再重复两个单项父测试。当前矩阵准入合同为18个父测试 passed、0 failed、1个 parent-only child ignored、0 measured；child 仍只由有界 SIGKILL 父测试按其协议调用，外层不加 `--ignored`。这些数字是执行器要求，不是本次 Rust 真实执行结果。

2026-11-05 持久更新 summary 与固定 Linux 控制：显式步骤不再按 `1 passed` 子串判断通过，而要求退出码0、仅一条完整 Rust summary、精确解析 passed/failed/ignored/measured；三个 controller 和 public 集成必须为 `1/0/0/0`，完整恢复矩阵必须为 `18/0/1/0`，缺项、多项、11 passed、重复/缺失 summary、ignored/failed/measured 不符均在生成 payload 前拒绝。新增控制覆盖命令、顺序、真实 worker/helper/module 环境、完整矩阵和失败停止路径。固定 tools Linux 镜像实际 checker `20/20`、driver `31/31`，合计 `51/51` 通过，12.335秒；当次1461项源码集合/hash前后一致，driver SHA256=`1fa79ab1efa7f53d61fd5d0569e6d9e07ed32d3dc0b3f1703426898a06593cf3`，tests=`ad4339052803f97035dba3b849ec1e2fe42b135158e996b3be5f3515120b6691`。原始输出、源码 map、命令和容器清理在 `artifacts/persistent-update-20261104-prep2/tools-controls-coverage-20261105/`；容器 Exit0/Pid0/非OOM，已精确删除，未创建卷/资格盘或启动 build/VM。该控制不代表 Rust 真实 public 集成、完整恢复矩阵或完整 build 已通过。driver/tests及文档变化使旧 f677/c90 freeze 不再适用于当前源码；旧 freeze 原字节与失败证据保留，后续须新代完整输入、准入、冻结、容量及正式构建，不能续跑失败代，`finalSystemAccepted=false`。

2026-11-06 持久更新第三代正式构建诊断：按授权归档、恢复验证并精确删除第二代 `c90a20436c4c` 失败 build 卷后，新代 `persistent-update-20261104-qual1-cb9e3931a9c3` 基于提交 `cb906ff38890bdee98fe1ed61fb8045934b39819` 的 1471 项输入完成无 guest 准入和 prepare。五条 Cargo 命令均退出 0；首条 all-targets 汇总 `773 passed, 0 failed, 24 ignored`，三个显式 controller 和 public 集成各 `1/1`，串行 recovery 为 `18 passed, 0 failed, 1 ignored`。随后真实 fixture helper 初始化退出 0，但 checker 对旧版 manifest 必须含 `dataMigration` 的要求与 Rust 对 None 省略该字段的序列化不符，检查失败，整个 build 失败，未生成成功 stage result、review 或 bundle。原始日志、冻结和独立结果核验保留在 `artifacts/persistent-update-20261104-prep4/`；失败容器已精确删除，build 卷保留，未创建资格数据盘或启动 guest。以上通过项不代表正式 build 或双启动资格通过，`finalSystemAccepted=false`。

2026-11-06 持久更新 fixture checker 修复：检查器仅将缺省的可选 `dataMigration` 在局部解析对象中按 null 处理；旧包非空迁移、新包缺失或错误迁移、未知字段以及缺失签名字段仍拒绝，manifest 原始字节 hash 和签名消息规则不变。新增回归先复现原字段错误；最终固定 Linux tools 控制为 checker `24/24`、driver `31/31`，宿主最终签名定向 `7/7`。对保留的真实 fixture 只读诊断通过，失败代 263 个文件及 freeze 未变；原始日志和容器清理证据见 `artifacts/persistent-update-checker-optional-migration-20261106/`。本次没有续跑正式 build，修复后仍须新冻结和完整构建，`finalSystemAccepted=false`。

2026-11-06 开发环境阶段：确认 Node `v24.22.1`、npm `11.19.1`、JDK `21.0.13` 可用；Docker Desktop 启动后，以提升权限只读诊断确认 Docker `29.8.1`、Linux daemon、14 CPU 和约16 GiB 内存。Rust bootstrap 首次在 Windows PowerShell 的 `Invoke-WebRequest -Method Head` 处失败，改为复用已校验的 `curl.exe` 读取 `Content-Length` 后，仓库本地 `rustc/cargo 1.99.1` 安装成功。新增 `scripts/dev-env.ps1` 统一项目内 Gradle/Rust/Cargo/Docker/Java 临时目录；Node 语法检查和宿主测试为 `22 passed, 0 failed, 2 skipped`。Gradle/Kotlin 下载仍因当前网络策略报 `java.net.SocketException: Permission denied: getsockopt`；Windows MSVC Build Tools、Python、QEMU、WSL 尚未可用，不能代替 Linux 正式构建或资格 VM，`finalSystemAccepted=false`。

2026-11-06 持久更新资格预检阶段：使用官方 `python:3.12-slim` 临时 Linux 容器（网络关闭、源码只读）重跑当前 checker/driver 控制，分别为 `24/24` 与 `31/31` 通过。旧 prep 的只读 `--preview` 因 Windows checkout 将 `geometry.stdout.log` 从仓库 LF 转成工作树 CRLF，实际 SHA-256 不再等于输入合同而拒绝；该代不修改、不续用。固定 `mingos-linux-task3:b89a9dc7cdce`、`mingos-boot-tools:20261025-trixie13` 和 `mingos-boot-infra:20261025-g2` 三个 registry pull 均返回 access denied，当前 Docker 存储无镜像、无卷、无容器；因此尚未创建新 qualification、build volume、数据盘或 VM。项目内官方 Python 3.12.10 embeddable 包已放入 `.cache/python`（SHA-256 `4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3`），由 `MING_PYTHON` 暴露；它不替代正式 Linux tools/Rust 镜像，`finalSystemAccepted=false`。

2026-11-06 公开基础镜像边界诊断：固定 Rust 摘要 `sha256:868a411f...` 在 Docker Hub 不存在；公开 `rust:1.99.1` 拉取成功但实际摘要为 `sha256:bc2c4c21...`，固定 Debian 基础拉取成功且摘要与合同一致。两者只用于诊断/临时脚本环境，未改写 driver 的固定摘要、未创建 build volume/资格盘，也未启动 guest；tools、Rust qualification 和 OpenSSL source 镜像仍缺失，`finalSystemAccepted=false`。

2026-11-06 qualification 证据字节稳定性修复：Windows checkout 在通用 `text=auto` 规则下将 `geometry.stdout.log` 等被 SHA-256 绑定的原始 stdout/stderr/jsonl 流改写为 CRLF，导致只读 preview 误报 `geometry raw output changed`。`.gitattributes` 现将 qualification 原始证据流标记为 `binary`，后续新冻结在 Windows 工作树中保持原始字节；旧 prep 不回写、不续用，`finalSystemAccepted=false`。

2026-11-06 固定镜像恢复审计：Git 全历史、不可达对象、用户目录扫描和 Docker Desktop 数据盘均未发现 `mingos-linux-task3`、`mingos-boot-tools`、`mingos-boot-infra` 的镜像归档或 build cache volume；公开 registry 对三个固定名称返回 `access denied/unauthorized`，固定 Rust digest 也不存在。当前 Docker VHDX 只有本轮新拉取的公开基础镜像。后续只能从原机器/备份导出精确 image 与 build cache volume，或在持有私有 registry 凭证的环境恢复；不能重建后修改摘要绕过 driver，Task9 qualification 仍保持未完成，`finalSystemAccepted=false`。
2026-11-06 qualification 资产恢复入口：新增 scripts/boot/restore-qualification-assets.ps1 及真实 PowerShell 子进程测试。默认只读校验 manifest、归档 SHA-256、固定 rust/tools/openssl image ID、路径边界和 reparse point；-LoadImages 与 -RestoreVolume 是显式副作用入口，拒绝覆盖已有 volume，不提供删除或摘要替换。测试覆盖合法 manifest、路径穿越、重复 key、缺少固定镜像，结果通过；该工具不代表资产已恢复，finalSystemAccepted=false。
2026-11-06 qualification 恢复门固定摘要修复：回归测试先证明任意格式正确但错误的 image ID 可绕过只读 manifest 校验；恢复脚本现强制 rust/tools/openssl 三个 ID 等于当前 Task9 合同，错误 ID 在任何 Docker 动作前拒绝。恢复测试、Node 检查和宿主测试继续通过，正式镜像仍未恢复，finalSystemAccepted=false。
2026-11-06 qualification 恢复入口边界修复：回归测试先证明位于 AssetRoot 外部的 manifest 可以被接受；脚本现要求 manifest 本身也必须位于 AssetRoot 内，并经过同一 no-reparse 路径检查。恢复测试通过，正式镜像仍未恢复，finalSystemAccepted=false。
2026-11-06 qualification volume 恢复集成修复：真实临时 Docker 集成先复现 Windows PowerShell 将“volume 不存在” stderr 当 terminating error，脚本在创建目标 volume 前退出；改用 Start-Process 捕获 docker inspect 的 stdout/stderr/exit code，退出码1仅表示可创建，其他错误仍拒绝。合成 tar 的 marker 已真实恢复并读取，唯一临时 volume 已精确删除；恢复测试、Node 检查和宿主测试通过，正式 qualification 镜像仍未恢复，finalSystemAccepted=false。
2026-11-06 qualification 资产交接说明：两个 Docker context 均确认没有固定 image/volume。README 新增源机器导出流程：固定 image 用 docker save，build-cache volume 用只读 bind mount 归档，明确 docker export 不能替代 volume；目标机器先 ValidateOnly，再分别 LoadImages/RestoreVolume。正式 qualification 仍等待原始资产，finalSystemAccepted=false。

2026-11-07 qualification 恢复入口安全遏制与环境路线更正：独立复核发现原镜像加载后仅检查 daemon 已有 ID，不能证明归档包含目标镜像；volume 不存在检查与幂等 create 之间存在并发覆盖窗口，tar 文件名拼接也不安全。现移除写入实现，`-LoadImages` / `-RestoreVolume` 在读取资产和调用 Docker 前失败关闭；保留只读 manifest/固定声明 ID/归档 SHA-256 检查，成功状态明确为 `archive-hashes-matched`，不承诺 tar 内容或来源验证。新增 fake Docker 子进程回归与状态断言，安全恢复功能仍待完成。

此前“恢复三个镜像和 build-cache volume 即可续跑”的说明不完整：历史 Task9 还要求 Darwin/APFS、原宿主缓存及真实 Kotlin/网络隔离/外部输入收据；prepare 必须使用全新目标 build volume，恢复已有卷不满足该合同。用户已确认目前无法访问原 macOS 或完整备份。旧失败代与历史 driver 不续用；当前 Windows + Docker/Linux 将以独立工具链、fixture、存储合同和完整冻结建立新代。该调整保留持久更新、显式恢复和正常双启动要求，`finalSystemAccepted=false`。
本阶段验证：PowerShell 恢复入口测试退出0（包含两个禁用写入入口、fake Docker 未调用与只读状态断言），JavaScript 语法检查通过，Node 测试22通过、0失败、2项POSIX跳过；独立只读代码复核未发现本次遏制的阻断项。上述结果不验证镜像导入、卷恢复、Rust 构建或 guest。

2026-11-07 当前宿主工具链重建：原 scripts/linux/Dockerfile 在新 tag mingos-linux-win-20261107 下真实构建成功，Docker inspect ID=sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80。实际 UID65534、禁网、只读容器确认 Rust/Cargo1.99.1、Python3.13.5、Weston14.0.2 可运行，完整包清单/来源及原始输出与hash收据保存 artifacts/environment-rebuild-20261107/toolchain-observation.json。Gradle8.14.5 镜像归档经仓库固定SHA验证后，原wrapper --version成功；环境入口缓存改为与构建脚本一致的.cache/gradle。新增当前宿主设计与分阶段实施计划，独立于历史APFS/缓存合同。Cargo/Kotlin依赖、boot tools闭包、正式构建与VM尚未完成；工具准备不代表Task9或系统交付，finalSystemAccepted=false。

2026-11-07 boot 与 Kotlin 开发工具准备：新增固定 base 的 tools/Kotlin Dockerfile、独立 boot lock 及 LF 属性。tools 实际构建并通过280个 deb SHA、kernel来源、工具动态库、Ed25519正负验签和无guest QMP smoke；Kotlin 镜像通过UID65534禁网只读Node/JDK probe。Cargo新卷锁定fetch及禁网metadata成功，target仅有rustc元数据，严格空断言失败保留。Windows read-file/viewer真实编译与Kotlin3项测试通过；更新old首次离线依赖失败后独立联网编译成功，未记成正式来源。新镜像IDs、失败与成功证据和APT封存边界见[环境记录](development-environment-20261107.md)。下一步Linux禁网强制重编、POSIX存储准入与新冻结执行器仍未完成；Task9、双启动及已批准恢复工具继续开发，finalSystemAccepted=false。

2026-11-07 Linux Kotlin 来源与构建控制：六个 Kotlin 包在固定镜像内禁网强制重编和独立只读重放均通过，完整108文件导出 hash 已由主执行者核对，runner14/14控制通过。新增 Linux 源码/存储/命令回收/冻结/Task9冷构建控制和 PowerShell source-check 协调；固定 Rust 镜像最终综合63项、Windows wrapper35项控制通过，真实源码隔离准入代250620c042ae4f97返回0。实际包摘要、依赖与各次失败保留边界见环境记录。新冻结准备中的59db2887574f45aa代因仅CHOWN权限的初始化器在转交0700目录后读取target而失败，未进入Cargo；e5b7a1d8da1a411a代在源码复制后因Docker Desktop将Windows bind转换为精确Linux宿主路径的表示差异被准入拒绝，仍未进入Cargo。两失败代保留；f6a4e8d8e9c44c82代使用新空卷，源码复制、存储probe与独立reopen均返回0，继续进行六包重放冻结。公共Windows入口当前仍只集成source-check；完整runtime输入冻结、实际Task9、bundle和guest仍待完成，finalSystemAccepted=false。

2026-11-07 获批镜像恢复工具完成：`-LoadImages` 现先验证全部三档的原始tar envelope、唯一Docker/OCI引用、config固定历史ID及每层未压缩diffID，再生成私有无RepoTags归档，通过stdin逐个load并inspect精确ID；不删除现有资源，不重写标签，不放宽历史driver。独立复核发现tarfile会隐藏GNU控制头，真实RED后补原始512字节扫描并拒绝GNU/PAX/link/device、非零padding/trailer与拼接归档。最终归档23/23及Windows真实子进程suite通过，部分失败输出保留、首失败停止；fakeDocker验证无标签字节与load/inspect顺序，非法tar在Docker前拒绝。只读状态仍archive-hashes-matched，RestoreVolume仍禁用。当前没有固定历史归档，实际daemon load次数0，不能宣称镜像已恢复；证据见environment-rebuild-20261107/restore-controls，finalSystemAccepted=false。

2026-11-07 Linux 运行时与双启动控制基础：新增独立 runtime freeze，绑定成功的完整十步冷构建、真实几何测试及其原 Cargo ELF 来源、六包 Kotlin、固定 tools 文件、原 checker/driver/native 输入和 source-v2 probe。fixture、人工源码审阅后的 review、bundle、无 guest admission 分步执行，首次失败保留；initramfs 按流检查完整 gzip/newc 和实际 payload/脚本/probe 字节。双启动控制沿用原 RawGuard 与首失败锁存，只在第一轮强校验成功后启动第二轮，保留同盘、输入、正常关机、marker 与私有内容连续性，以及 900/945/1920/1950 秒边界。主执行者独立运行19项 runtime 与20项 pair 控制全部通过；其中有真实 Ed25519 正负验签、暂停且无 kernel/磁盘的 QMP 与进程回收控制，没有 guest 或资格数据盘。另在固定 tools 镜像禁网只读诊断中完成真实 rootfs runtime copy（1095文件、80715749字节）与隔离 guest Python import；证据见 environment-rebuild-20261107/runtime-controls。实际 frozen runtime admission 与双启动仍待执行，finalSystemAccepted=false。

2026-11-07 Linux 首代正式冷构建失败保留：f6a4e8d8e9c44c82 代前五条 Cargo 命令均退出0，all-targets 为773 passed、0 failed、24 ignored；两种 feature check 与 Host/helper release build 均通过。随后控制器要求单链接产物而 Cargo 正常将公开 ELF 与带 hash 产物硬链接，封存失败，未执行第6至10条。只读 inode 检查确认两组各2链接，失败 stage、全部输出和 source/prep/target 卷保留，不续用该代。此时不能宣称完整 Task9 通过；修复须独立测试后使用全新空 target 和完整冻结重新构建，finalSystemAccepted=false。

2026-11-07 Cargo 产物封存修复：控制器在第5条命令后及完整矩阵后，仅对固定 Host/helper 两个 Cargo 公开 ELF 执行受限独立复制与同目录原子替换，保留原 alias 字节，写入封存前后 hash/inode 收据。来源须 UID/GID65534、普通可执行 ELF 且不超过512MiB；fd/no-follow、复制前后来源、目标父目录和私有临时文件身份均核对，最终仍要求单链接，未放宽全局 hash/存储规则。真实硬链接 ELF、链接与非 ELF 拒绝、源文件/路径替换、临时文件被替换时保留外来文件、后续 Cargo 再链接等回归先失败后修复；主执行者固定 Rust 镜像独立30项控制全部通过。原失败代没有写入；后续须全新冻结代完整冷构建，finalSystemAccepted=false。

2026-11-07 公开冷构建协调与 Linux init 可移植性：新增参数化 new-linux-host-build.ps1 与快照 helper，真实 image/Node 固定值、请求和有效 mount、nonce/CID/权限、卷空目录、存储 probe/reopen、六包重放和异步 build 均有分步证据；不依赖开发者硬编码工作区。主执行者独立25项 PowerShell 与4项 Linux 快照控制通过。root 仅 CHOWN 的真实隔离初始化先在活动工作树稳定性检查处拒绝，证据保留；随后以不可变冷源码副本完成真实快照及 UID65534/0700 转交，退出0、非OOM。转交通过预先打开的 fd 执行 chmod/chown/fsync，避免转交后 root 无权重开目录。

独立运行时审阅发现实际冷源码 scripts/boot/init 含 CRLF，Linux kernel 会将 shebang 解释成不存在的 /bin/sh\r。现只将该文件 CRLF 转为 LF（751→734字节），补精确 eol 属性和安全复现；主执行者5项 init 与21项 runtime 控制全部通过。runtime source extension 对 init 与 .gitattributes 仅接受已审阅的两组精确 before/after hash+size，不接受任意启动脚本改动，旧冷源码及冻结字节保留。新冷构建 fd90bae03bd34a57 已完成实际存储与六包冻结并在运行；正式 Task9、runtime admission 和 guest 仍未验收，finalSystemAccepted=false。

2026-11-07 Linux 新代完整冷构建及几何来源通过：`persistent-update-20261107-linux-fd90bae03bd34a57` 于 UTC06:27:28至06:48:17完成全部十条固定命令，退出0、非OOM。all-targets为773 passed、0 failed、24 ignored、58组；两种feature check、Host/helper release build通过，三个controller与public integration各1/0/0/0，串行recovery为18/0/1/0。固定1800/900秒、禁网、UID65534、2CPU/4GiB及只读源码/registry均未放宽；完整prep、final inspect、源码/包/阶段产品hash独立核验通过，见cold-independent-verification。当前成功代没有续用旧失败target。

独立导出核对先拒绝Host前半段与最终摘要不同；实际后续Cargo重新生成Host，两个时点的原始seal均正确且helper不变。导出器现分别绑定pre-explicit清单与pre seal、最终清单/实际target与final seal；真实RED后26项控制通过，包含合法重新生成和四种重封装阶段错配拒绝。独立source-v2探针以新空target、固定Rust镜像真实禁网构建成功，二进制970680字节、SHA256=`32a0f022b96cff657c066bd1da40b91ff72d1c957503593a4e731ddccc0f326b`；共享校验绑定精确命令、1800秒、四源码、冷freeze、完整原始日志及两产物，并接入runtime/export。公开可迁移export基础校验清单、封闭导入、十步构建、包、工具、几何、实际payload及原checker强raw证据；合成重放不是实际guest导出。

真实Cargo lib ELF的几何测试1/0/0/0通过，原driver.validate_geometry通过，三份证据已逐字节投影到assets/checker。早期诊断分别因导出器跨时点误断言、历史driver挂载层级过浅而失败，原始失败保留；最终新几何诊断成功，冷冻结和target未改写。主执行者实际冷构建独立核验、最终34项probe/export控制及21项runtime控制证据位于environment-rebuild-20261107；尚未启动guest或创建资格数据盘，实际runtime freeze/admission、pair、portable export仍待执行，`finalSystemAccepted=false`。

2026-11-07 Linux 实际 runtime 无 guest 准入通过：首个runtime `63c6be41bb5d4f8d` 完成fixture/review/bundle后，admission因工具清单未包含实际Rust ELF的libgcc_s.so.1而拒绝。只读复核7个ELF的ldd均无缺库，遗漏仅此一项；固定tools镜像下重新枚举实际Host/helper/probe依赖，生成6042文件manifest3，未更换镜像或放宽闭包。首失败代的prep/source/work/容器与原错误保留，不重试。

全新 `persistent-update-20261107-runtime-5f056322a31e4974` 从已封存源码副本重新执行存储probe/reopen、完整freeze、真实fixture，主执行者独立审阅确切payload、原native输入、签名正负验证、仪表与预算后写review。bundle完整gzip/newc及必需字节校验通过；实际admission于UTC07:02:41至07:02:57退出0、非OOM。实际7个ELF依赖、Ed25519正负验签、tmpfs真实子进程锁/fsync/socket、暂停无kernel QMP退出、guest Python runtime copy、原checker24项及driver31项全部通过；pair边界真实拦截一次精确VM命令并证明首失败后第二轮阻止。运行时freeze SHA256=`ddf9e15f153b7b5c2eda9ebe2940f57191ef2f9b7552eee8c2efaeb6bbd6c881`。此时仍未启动guest或创建资格数据盘，guest UID namespace实际执行未证明；正常双启动与实际portable export待执行，`finalSystemAccepted=false`。

2026-11-07 Linux 首次实际pair失败保留：runtime5f056322a31e4974在已通过admission后创建唯一128MiB新ext4盘并实际启动第一轮。kernel6.12.113、virtio/DRM/input、cgroup、数据盘UUID挂载和strict smoke到达；guest uptime9.095秒因Weston socket未就绪失败，RawGuard立即停止并完整回收进程树，容器退出1、非OOM，第二轮未执行。原盘、容器、source/prep/work、raw/QMP/kernel/scanout/首失败及Windows副本保留；planned result中的bootAttempted=false不能抹去真实guest-start，pair-failure-summary明确记录实际guest已启动。没有正常关机或正常双启动通过证据，`finalSystemAccepted=false`。

隔离解析实际initramfs的userspace（未重启旧盘/guest）复现Weston读取132字节CRLF weston.ini时报malformed section header；仅同文件LF转换后进入DRM后端初始化，再因无设备诊断容器的seatd缺失而停止。该对照定位Windows换行问题，不证明新guest正常运行；修复须新完整runtime冻结、fixture/review/bundle/admission/pair。原失败代不续跑。

2026-11-07 公开运行协调与启动配置修复：新增new-linux-host-runtime.ps1及snapshot_linux_host_runtime.py，参数化冷代state、packages/assets/runtime inputs、工具manifest；顺序执行new/probe/reopen/prepare/fixture/bundle/admission/pair/export/verify/replay，每阶段首尝试锁存。review只消费独立提供且逐字段相同的批准；pair保持1950秒外限，export使用新独占输出卷，verify/replay只读挂密封导出与独立结果卷。共享实际mount/CID/image/nonce/权限检查，cleanup保留原错误、单列清理错误并复验停止。公共cold容量收据改目录挂载，保留可迁移路径映射；主执行者PowerShell25+10全部通过，均未实际启动guest。

weston.ini现仅132→122字节CRLF转LF，精确eol属性防止再次checkout污染；真实headless Weston/Pixman/kiosk解析控制通过。guest-error同一fatal记录新增固定weston/seatd三日志各最多4096字节尾部，fd/no-follow/nonblock、单次pread、常规单链接检查，读取失败不覆盖原错误/traceback；不推迟RawGuard或增加transport事件。runtime source extension只接受已审阅的配置/属性规范化与guest诊断精确before/after SHA+size，仍拒绝任意生产源码变化。主执行者Linux45项通过；初次合并控制因Docker默认noexec tmpfs导致两项无害shebang执行控制被拒绝，失败日志保留，随后仅诊断容器显式exec tmpfs后45项通过，正式runtime挂载合同未改。新runtime实际执行尚待重新冻结，旧失败代和冷构建字节保留，`finalSystemAccepted=false`。

2026-11-07 公共运行入口复制参数修复：`b47108f3b8834736` 代实际初始化退出0、非OOM，但单记录嵌套数组被PowerShell展开成字符串，收据复制误构造为 `docker cp <CID>:/ <evidence>/s`。180秒复制超时后该代锁存失败，清理inspect也超时；原始错误、部分复制、source/prep/work与容器全部保留，没有probe或guest。后续独立只读inspect确认原初始化容器已停止、退出0，Docker重新响应；不重试失败代。

全部11个阶段的复制记录统一改为具名source/destination对象，避免单元素与多元素形状不同。fake Docker现逐阶段核对精确三参数、完整CID和源路径、目标路径及复制次数；新增控制先在 `new-copy-0 ...:/ .../s` 真实RED，修复后21项全部通过。独立只读审查确认八个单复制阶段受同一缺陷影响，其他参数数组未发现同类阻断。日志见environment-rebuild-20261107/public-runtime-controls/root-copy-{red,green}.log；这些控制不代表实际pair或portable export通过，`finalSystemAccepted=false`。

2026-11-07 公共入口实际无 guest 准入通过：新代 `persistent-update-20261107-runtime-e8dded4355a14c16` 经公开CLI完成真实new、storage-probe、独立storage-reopen、prepare、fixture、bundle和admission，全部收据按精确文件复制成功。主执行者在只读prep/source输入下独立复算完整冻结、fixture products、原checker签名正负验证，并核对已审阅的LF配置和guest诊断字节，再显式提供精确review给bundle；review-base SHA256=`e98478e1e1cbffc184b4d12eee53a77153d52ce9a3a62c51479ec911d76beb3b`，runtime freeze SHA256=`bd91e1cbb00db573d97ef3bc350582f26985ab9771b2984c96e98a1234de0fc1`。实际admission于UTC07:28:57至07:29:12退出0、非OOM，7个ELF闭包、真实验签、tmpfs锁/fsync/socket、暂停无kernel QMP、runtime copy、原checker/driver和首失败阻止boot2边界全部通过。此时本代尚未创建数据盘或启动guest，guest UID namespace执行仍未证明，正常pair与实际导出继续待验，`finalSystemAccepted=false`。

2026-11-07 公共入口首次实际 pair 失败保留：`e8dded4355a14c16` 代新盘boot1已通过Weston DRM/Pixman、Host、原生更新确认及新版本实际Ready；观测到真实隔离UID/namespace/seccomp与受限cgroup。guest uptime143.097秒在Close后的startup-confirmed收据解析处报 `persistent update incomplete terminal worker lifecycle`，RawGuard即刻停止，boot2未执行。容器退出1、非OOM，supervisor cleanupComplete=true、未超时；原盘/卷/容器及完整失败work/stage和独立summary保留。raw流包含12条有效persistent receipt、8个原生输入边沿及新版本Ready；失败终态行尚未完成stderr导出，不能根据现有raw断言触发的唯一子字段。新诊断证明Weston配置修复已越过旧故障点，但无正常关机、双启动或portable export成功证据，`finalSystemAccepted=false`。

只读代码复核另发现两个可独立复现的终态问题：FrameClockStats.pending是累计已返回None的空等待次数，不能解释为仍悬挂请求；Host poll在结果接收前读取隔离状态，join后未刷新，可能把已清理worker的旧cleaned=false写入终态历史。将分别以计数语义和确定性rendezvous回归验证修复，不修改历史checker/driver或伪造统计。旧失败代不重试；Rust改动通过后须全新target完整冷构建、新探针/几何来源与新runtime/盘，再继续原合同。

2026-11-07 终态计数语义与隔离历史修复：FrameClockStats.pending实际累计已返回None的空轮询，旧pending=0门错误拒绝合法worker；parser现验证固定字段/source、严格u64计数及waits=frames+timers+inputs+closes+cancels+pending，保留Close=1、Cancel=0、Ready/Result/nativeExit/reap和真实隔离清理门。Host在worker join后重新读取原共享StrictLaunch快照，避免把poll早期cleaned=false留在终态历史；不设置cleaned、不修改SDK等待/取消语义。原失败raw缺少完整终态行，不能断言此次失败只由其中一个子条件造成。

计数回归RED已证明合法pending=7被拒和不平衡waits被接受；确定性rendezvous的Rust RED为1通过1失败，生产最小修复后2/2及完整close_grace16/16通过，失败清理仍保持false/failed。主执行者再次运行Windows24项及固定tools镜像内Linux92项parser/runtime/pair/export控制全部通过，容器退出0、非OOM；独立只读审阅无阻断。证据见environment-rebuild-20261107/terminal-diagnostic-1fb86e26869b4a28与runtime-controls/root-terminal-counts24-final.log。Rust诊断target仅是原成功冷target的可写副本，不能作为冷资格产物；历史checker/driver未改，旧失败代不重试。接着须全新空target完整十步冷构建、新probe/geometry/runtime/盘继续验收，finalSystemAccepted=false。

2026-11-07 终态修复后的全新冷构建通过：`persistent-update-20261107-linux-89161e5b1ad44f96` 使用新空target及1439文件源码冻结，于UTC07:49:54至08:11:05完成全部十条原合同命令，容器退出0、非OOM。all-targets为775 passed、0 failed、24 ignored、58组；两种feature check、Host/helper release build通过，三个真实controller及public integration各1/0/0/0，完整recovery父矩阵18/0/1/0。主执行者固定Rust镜像下独立重算prepare/build产品、六包、源码、存储、命令/日志hash与两时点ELF seal，全部通过。新freeze SHA256=bfef7a9d20a7774f51a53553d6aa79fc2c08ec6409ca6ae0cb56b8c7168c355b，build-result SHA256=25c769ca9260a0d452f2afc29337620bec1437f5d8cb7bfe22437076635e1903；Host为35880152字节/SHA64e5d4ae1a9737d29362538a8937c638c7907c63910d06ad4ec0593b6603ae49，helper为3861952字节/SHA caba310970e34d3fbc69dd61fbaf007d8e7082f66014380e3aa0f845762dbac9。

完整证据见本代final-prep、final-inspect与environment-rebuild-20261107/mingos-runtime-assets-20261107-3dcba6974274/cold-verify-*。原fd90与所有失败target保持原样；该新代开始独立新空target source-v2 probe重编，接着须新几何来源、tools观察及runtime/fixture/review/admission/新盘pair。显式恢复覆盖的独立源码审阅和精确case/kill枚举已准备，但尚未运行本代coverage/scope review；无新的guest或正常双启动成功声明，finalSystemAccepted=false。

2026-11-07 显式恢复覆盖审阅通过：在固定Rust镜像、禁网、UID65534及只读冷构建输入下，实际运行coverage与独立scope review，两个容器均退出0、非OOM。对cold8916十步构建重新核验后，精确匹配615个UPDATE_CASE身份、770个UPDATE_KILLED/reap身份、18个成功父测试身份及9条真实Host观察；唯一child_process保持显式ignored，不计为父测试通过。覆盖原namespace缺失、无files、空/非空files、Prepared回滚、CommitDecided完成、清理/恢复再次中断及普通open/Host无写拒绝。审阅依据为冻结Rust断言、实际案例身份与保留的Host观察；父进程内部捕获而未转发的child原始输出没有补造。

证据位于`artifacts/environment-rebuild-20261107/recovery-coverage-controls/recovery-scope-20261107-9d9cd537d4e4/`，`scope-review.json` SHA256为`05f085949b7b4317b5f47853f05d61ef7c4156e83d04bf2877b70c1b157d3196`；containment另绑定实际CID、挂载及冷state。该独立报告记录`persistentUpdateExplicitRecoveryVerified=true`，范围严格为Linux受控进程中断、真实SIGKILL/reap和显式恢复；原coverage生成文件的pending标志保持不变，不把它改成审阅结果。不包含guest崩溃、断电、介质损坏或物理输入资格，`formalQualification=false`、`finalSystemAccepted=false`。

2026-11-07 新runtime7432首次pair在boot1强raw复核时失败并保留：本代freeze为e64714c65d7a29fa37e28b8d6d9676dfdb05985f3d2d08572efd2780d99f23f3，独立root核验真实fixture签名/篡改拒绝、冷8916绑定和原预算后，公共bundle/admission通过。UTC08:22:37至08:25:51实际boot1完成新版本0.2.0试运行、Close/Result/nativeExit/reap、持久commit、同步及正常桌面关机；随后未改的原checker拒绝`native button cause not enclosed by reviewed raw input/state step`，boot2没有启动。CID133e112aa6363b245eeac1f151291723af3d76bec7c8788bea4f3d8333e9b43c退出1、非OOM；原128MiB盘、source/prep/work、全部原始guest/input/QMP/scanout、stage和失败摘要保留，不能续跑或改判该代成功。

精确诊断与独立审阅确认10条native边沿的revision和stderr offset范围都合法，失败来自QMP像素换算：原目标(1148,12)转换为ABS(29411,492)，实际Wayland为(1148.8671875,12.01171875)，native取整成(1149,12)。原公式pixel*32767/(extent-1)与本机实测ABS*extent/32768的连续extent映射不一致。现将发送端改为有界pixel*32768/extent，保留原5/2步脚本、精确checker坐标门、pointer-ready和所有时间/资源预算。真实五点投影回归RED出现四个预期坐标失败；修复后固定tools/Linux容器133项infra/parser/runtime/pair/export控制全部通过。Windows全infra另有两个Linux O_NOFOLLOW依赖错误，未计通过；正式控制以实际Linux133项为准。证据见environment-rebuild-20261107/pointer-extent-controls。Rust/Kotlin与冷ELF未改；新runtime只允许精确hash/字节绑定的boot脚本转变，随后须全新runtime/盘实测，`persistentUpdateNormalRebootVerified=false`、`finalSystemAccepted=false`。

2026-11-07 qualification匿名卷恢复工具完成：公开RestoreVolume入口仅接受固定helper镜像digest、严格raw USTAR及嵌入restore-manifest全量清单。归档在Docker操作前完整校验；只创建source省略的匿名volume，root初始化仅CAP_CHOWN，FD保留完成UID65534所有权移交，writer恢复payload后由另一个实际RO容器全量重开核对。路径/owner/mode/特殊对象/重复/隐藏增量、lease/fsync、外部完成receipt和CID挂载合同均为失败即止；不存在资源删除或现有命名卷写入入口。真实Docker产生CAP_CHOWN和Windows Desktop精确drive翻译两种等价DTO，分别RED后只允许精确等价表示。两次早期actual失败容器及日志保留。

actual live-e7e4dd0c3e64第三次全新恢复和restore-live-b144eca66993两次并发公开恢复全部经独立RO通过；后者产生不同匿名卷、nonce及六个不同creator/writer/verifier CID，调用者提供的同名sentinel卷dev/ino/UID/GID/mode/nlink/hash前后完全一致。固定Rust/Linux组合控制110项中108通过、2项非Linux平台跳过，12项真实worker控制全部执行。最终公开PowerShell控制8/8在Windows PowerShell通过；当前pwsh启动的旧harness复现子进程Get-FileHash模块加载失败，harness明确设置Windows PowerShell内置PSModulePath后8/8通过，生产入口未为此改动。最终harness证据为artifacts/qualification-restore-controls/ps-8cfa69bf231840279bb0c554e8b713a2。独立静态及实际DTO审查无阻断。以上均为synthetic-control，historicalAssetsRestored=false；原Mac缓存/镜像归档仍不可用，不把工具通过记为历史资产恢复，formalQualification=false、finalSystemAccepted=false。

2026-11-07 Linux持久更新正常双启动与实际portable回放通过：全新runtime b30e37e37a5a46c3绑定cold8916、冻结SHA256 a83c8f7ecc51717c4c1978cab792a61de295944db6524683672180bfd5032151及原5/2步native输入。独立主执行者实际复算fixture/签名/篡改拒绝/几何来源及review，公共bundle/admission通过后，于UTC08:39:18.760至08:44:47.512完成同bundle、同128MiB盘两次fresh QEMU启动。bundle identity e8048d990390d8ffd18abaac0afb62f33666ee27a80f61e29224bdaa73c96696；disk UUID dbb18a50-0533-42d1-b777-cf7f54bbe11f；boot ID依次0831a1ba-d825-4865-a1d0-494f914a6583与3457e07f-d401-42c2-a429-f52624e23c6a，marker1→2。boot1完整新版本0.2.0试运行、Ready/Close/Result/nativeExit/reap、commit和sync正常关机；boot2无重建/恢复重开相同content/private/catalog，再次正常关机。原checker精确UI因果与完整强raw链通过，pair容器27efcf6282f3ce3ec05047e030284427e212ec4eecae5ed05f77121b27601a92退出0、非OOM。

公开export→verify→replay均实际退出0。密封导出3502文件，manifest SHA256为8de918c038441e39630b7e88772031708e2c9f3cb4dbcae0cc3fd47b13540740；独立sealed driver重放报告linux-portable-raw-normal-reboot-verified并设置persistentUpdateNormalRebootVerified=true。可迁移PAX gzip位于environment-rebuild-20261107/portable-archive-a5b41592e057/persistent-update-portable.tar.gz，191417046字节，SHA256 ab0a551aa08f8abb6dad22fc2ab8cb15fa5e1d895962ccc9dadc90d22e4f56b5；固定Rust镜像禁网/UID65534只读源内核验3503常规文件bytes/hash/mode及898目录名称/类型/观察到的mode，封存源前后复核一致，Windows复制hash相同。该PAX证据包不属于严格USTAR缓存恢复格式。全部历史失败代继续保留，不重试或改判。本阶段仅Linux/TCG/虚拟输入、正常关机与明确的进程恢复范围；无guest-crash/断电/介质损坏/物理鼠标声明，formalQualification=false、finalSystemAccepted=false。下一阶段继续独立Linux持久卸载资格与系统交付。

2026-11-07 失败代/RED证据压缩完成：failure-evidence-archive-20261107-98ab2bdd71封存5个保留runtime失败代及pointer/public-runtime/runtime/terminal/Weston控制原始目录，含三张失败128MiB原盘和b471误复制的部分root内容。9293个常规文件/符号链接、原始常规文件3793063117字节；gzip归档1716306393字节，SHA256=2e7527696320fe2a92f5bddc751086d5b5cbd624c18faf2a8ba106b0faa49764。manifest SHA256=a2c81b3333476860e48abee51a6c7b6b6f14e05544759bdf00493ff55512d9fb；压缩后独立读取每个成员、常规文件bytes/hash及符号链接target，全量源清单重新计算一致，原证据未改变。目录mode/恢复资格不在此Windows证据包声明内。最初Windows MAX_PATH与拒绝符号链接两次诊断失败也保留，后续用全新输出目录及Win32长路径、符号链接元数据封存通过；未提取或运行归档链接，未删除任何卷/盘/容器。该包不代表旧macOS/APFS资产已恢复。

2026-11-07 Linux持久卸载独立实现与来源包阶段：新增封闭uninstall冷构建profile、七条命令、专用helper/geometry、独立runtime/pair/export及公开11阶段协调入口；默认update合同保持。runtime首次加载资产driver前逐字核对六个Python checker源，公共入口在Docker和状态创建前拒绝错误cold profile。专用checker从完整raw/QMP/Wayland和原生输入重建安装、Ready/Close/Result/reap、卸载与四阶段私有/removed状态；合成密封双启动、移离checkout后的独立CLI回放及manifest/成员/freeze/跨boot篡改拒绝均通过。对真实Rust emitter逐项复核后，ready严格绑定source=N；Windows导出改用Path.lstat取实际单链接元数据。

本阶段主执行者实际分组Linux控制197项全部通过（Rust63、Tools134），证据uninstall-controls-2b9ff96fe506；此前Rust镜像不含QEMU导致的189项188通过/1错误保留，另在固定Tools镜像真实补跑QMP通过。公共cold27、卸载runtime24、既有update runtime21控制通过（fake Docker）；Node18/18。独立审阅未发现cold/geometry明确阻断，未把静态审阅记为Rust执行。

Kotlin新代740082ff5d3d406da70e08ca4c60156d于UTC09:07:29至09:16:21完成四个真实离线强制重编和独立RO重放，固定镜像/UID65534/禁网/2CPU4GiB，七个容器退出0非OOM。专用uninstall身份dev.ming.install-demo/1.0.0/SDK6，packaged SHA892575d55834acce71f956f2002f8ea3d63e5857ca5e19e5dc7f23fdc2328f63；原cache2496文件完整前后核验未变。主执行者独立复算全部89个host导出文件hash一致；final-observation SHA96b5372f3f95ff8cadaf6c4d376c967b82ebce7ade51150284d2274ddb0d0b3f。前三个准备失败代全部保留，没有重试或删除。此阶段尚未执行新Rust cold、实际卸载guest、双启动或portable导出，persistentUninstallNormalRebootVerified未成立，formalQualification=false、finalSystemAccepted=false。

2026-11-07 卸载首个真实冷构建及部署诊断：cold59f0274d1b3b480d于UTC09:29:50至09:48:24完成全部七条命令，实际容器退出0、非OOM；all-targets776通过、0失败、24忽略、58组，两项显式控制各1/0/0/0。freeze SHA256为857bfe2c257ec8f0e613e8ff30ab1a21e7f6d27a8461c97cf05bafaca6694742，build-result为a018b36bc230598f5d21676c3ff1abfcdf3490a4dac9bd34a6824e160ebddb31；Host为97610e58bbb2bed48c69d3b43eb5c21cb829bc73f549f52d3f7707b819e3f1c7，卸载helper为ee3cee2af525d4b93c5809ae510d47e3250e6c01b523a8f5635b2f48cc69f8b8。assets9570a924e437另完成空target禁网source-v2探针、真实几何ELF执行、6042工具文件及23项动态库观察，独立只读复核通过；这不构成完整签名离线APT闭包。

真实runtime bfcc983e0151404e通过new、storage-probe与独立reopen，prepare首次从/assets/checker加载驱动时，导入期HERE.parents[2]因部署路径层数不足抛出IndexError。客体尚未启动，没有fixture、pair或卸载通过声明；该失败代的源码/卷/容器/日志全部保留，不续跑。修复必须覆盖资产目录、prep及源码布局的实际导入与输入验证，并重新完整冻结构建。

固定Tools镜像完整boot Python discovery在独立fixture卷及逐模块TMPDIR布局下实际执行546项：543通过、1失败、0错误、2项非Linux边界跳过，六项/work存储控制均通过。唯一失败为test_legacy_frozen_artifacts_match_hash_gate，缺少原legacy冻结probe。证据uninstall-all-boot-layout-1cc2da6e6e8f保留，先前布局失败也保留；仓库历史与现存归档清单仅找到不同hash的source-v2，未替换原字节或放宽历史门槛，因此不能称全套测试通过。formalQualification=false、finalSystemAccepted=false。

2026-11-07 卸载驱动部署路径修复：仅将已知/assets/checker与/prep布局显式绑定/repo，源码目录推断保留；不改变七步/一步输入、签名、冻结来源或执行预算。新增实际执行驱动源码并调用真实InputPlan的回归，Linux RED复现资产路径IndexError，GREEN20/20通过。主执行者独立组合控制199/199通过（Rust63、Tools136，uninstall-controls-69fd3a377e94）。另在真实/prep挂载完整复制的driver/checker/tests/input后执行20/20、无跳过，验证admission实际discover布局；新测试自身的源码深度假设一并修正。证据uninstall-driver-prep-8fb877ea900b，两个容器退出0、非OOM、inspection通过、cleanupErrors为空。原bfcc失败代不续用；后续须新冷构建、资产及runtime再验，finalSystemAccepted=false。

2026-11-07 卸载启动历史绑定修复：全新cold3a4b4a45012046db实际七条命令全部通过，all-targets776/0/24；新assets281e509dff5d完成source-v2空target构建和实际几何/工具检查。runtime5d98a41eb54d4b17的首轮guest真实完成安装、启动、Close、卸载和正常关机，但强raw checker拒绝三处启动中PID0历史，第二轮未启动。独立核对Rust先记录starting/PID0、再发布worker PID211的时序；没有把该症状归因于历史丢失。修复仅允许与本次唯一native launch及后续worker-started绑定的启动down/up锚点，其他非零身份和完整终态仍精确匹配。RED正例复现后，11组错误身份/状态/trace/终态反例继续拒绝；实际Linux15项checker控制全过。原失败首轮的只读诊断通过，原guest SHA256 a2e554a84a6add9736169038ca48751ec165b6d08781aac6f19fe734ecde7ff0保持，且qualificationAccepted=false、boot2Started=false；该代全部资源保留且不续用。证据见uninstall-pending-history-controls-eeadb42c7d2f、uninstall-boot1-diagnostic-dbcc93162f90及原root-failed-pair-readback。新四包编译、冷构建、双启动和导出回放仍待完成。

2026-11-07 x86_64 VM开发交付基础：用户确认可复建的VM开发系统、应用、持久数据、更新/卸载和恢复说明；固定已核验二进制工具链可作为构建依赖，不新增全Debian/编译器源码重建或逐字节可复现要求。新增独立Linux developer pack只读验证器，闭合manifest、完整文件/目录集合、no-follow/单链接、mode/大小/hash、资源上限及结束前完整重开；主执行者实际Linux40/40通过，含66MiB单文件、514MiB集合和读后替换反例。新增developer-vm-v1封闭15秒预览profile，固定Text Entry SDK6和Motion SDK5，绑定Host/应用/source-v2来源，禁止输入与资格fixture。实际desktop没有文件授权流程，未用需要file slot的viewer冒充可运行应用。profile仅做来源字节绑定，不重放编译器，不把预览当成交付。

另新增独立开发镜像归档迁移入口，固定Rust/Tools/Kotlin三份OCI index归档；验证外部大小/hash、全部blob/descriptor、唯一linux/amd64、层顺序/diffID、tagless Docker manifest及绑定attestation，后者不认证发布者。修复每次load前重验各自FD与普通失败部分收据留存；结果先独占创建并fsync，不宣称断电原子journal。真实三归档只读验证通过，尚未实际load或在新daemon验证index保持；历史恢复合同未变。综合独立Linux控制为checker15、profile13、migration16、boot21，共65/65、零跳过，固定Tools镜像、禁网、UID65534、只读根/源及前后inspect/hash通过；证据developer-combined-controls-0105b576aee2，result SHA256 64c56521ee104b06a5c677edd43837f5e55b3c4d970981e71ed12c87e3fdacee。pack证据developer-pack-root-controls-a7d71c2e55bb，迁移integration证据development-image-migration-integration-4b0e5c057745。未改变原legacy frozen probe缺失事实，formalQualification=false、finalSystemAccepted=false。

2026-11-07 Kotlin公开源码构建编排：新增固定镜像、独立源码/缓存/工作/输出四卷的公开kotlin_delivery入口，支持developer双包、uninstall四包和update六包；加入Motion的SDK5精确ABI及capability检查。编译要求offline、rerun、独立工作目录和实际目标任务，另起只读容器核对包身份、源码/收据hash与依赖前后链；每次create先重验已有卷，失败/超时保留inspect，仅停止所属运行CID，失败代不能原地续跑。集成时先保留缺失DTO fixture的14通过/2错误结果，再补入真实DTO回归输入（仅替换用户名）；主执行者和独立复核均16 Python与38 Node通过，pipeline语法通过。原型实际synthetic8d1a0b1fbe2e4ec2b5354d5cbdc0a8cc验证612源文件、2496缓存文件的复制、原cache保持及只读拒绝；exit7非零和超时Running=true后owned stop/reinspect均实际执行。新RO全量基线与旧基线不可变依赖一致，变化只在派生缓存；旧基线失配和三次DTO/prepare失败代保留。证据kotlin-synthetic-actual-e724e1cd及missing-volume-21eaab8f，compileExecuted=false；公开入口的新四包/双包实际编译另行记录，不能由synthetic替代。宿主docker-cp导出仍需外层独立封印。formalQualification=false、finalSystemAccepted=false。

2026-11-07 VM复建输入实际归档与恢复阶段：新增公开developer_cache.py/worker及source_archive.py数据层，历史qualification USTAR/plan/worker合同不变。真实缓存导出290da81f含936744960字节归档（SHA77b59c4e3a4aa96485fabf2c009ba7e7e6e05aadfd37d8f68cb22c863c5d84c6），外部descriptor5ee32079347e5bc107bc3196ed22ac8bd33d224f55c465e316a225fa5790be9d。新restore70a5d3e1完成42操作，匿名payload独立只读复核后投影两个新消费者卷，12495 Cargo/2496 Gradle文件全量清单通过；原缓存保持。仅Cargo原7个0640文件在归档/新消费者中显式规范化0644，旧模式与每项转换均被descriptor绑定。结果SHAdcb16934a97d6717eb9a4f937cd47425f7d15218dfaae77834fef111da4aa6ac。

源码strictUSTAR采用短别名与惰性JSON原路径映射，保留目录/空目录/模式及原owner记录，consumer owner规范化65534；原始source不变。实际exportedfb0b2d61f6从冷构建9436的RO源码导出1487文件/392目录，source.tar30914560字节，SHAcb2c58555bbbe5ac645c214ac9a29100ae6a329150e26d9f4c1b5e7bacf19144，外部descriptor8bb9ff1a0f3bfcd052997ff550cc8b1ed5cdc03bd6dc64d322c0bed400f79e43。restore9f97d47ab8f7全新Linux卷恢复及独立RO reopen核对全部内容/modes/owner；主执行者再核对宿主长路径与短路径artifacts/sr-9f97d47a全部文件字节、目录与原封印源码一致。长路径第一次stat遇MAX_PATH失败保留，随后用Win32长路径核对完整副本；为公开构建导出另一新短路径副本，未移动或覆盖原件。公共集成Linux控制52项51通过/1 Windows-only跳过；source集成控制需显式RO fixture，未提供时明确跳过。证据developer-archives-integrated-controls-cd9e72c4e923。

同阶段新uninstall冷构建9436c4cf1c0e4ade全部7命令通过，776/0/24；完整sealed verify通过。runtime f500a8a300fd4f82于UTC11:46:28至11:51:46在同128MiB盘完成两次正常启动，strong raw验收通过，bundle24f7b87e2dcc9baedff77fe8e95f7e3c4996b2b6e93f27fd22d2433c863b7ec6、disk1adf18a6-081b-4d69-957e-c514e57cb695。export3467files成功，manifest4f4e4df9777fa8790d4a867c2293ee3c2b7eb476619e350b99aef8ad4e5b2635；public verify失败于tools/rust-image.json缺失。独立诊断确认runtimeInputs漏带cold.environment引用的三项工具收据，不改变exporter契约、不补写失败封印。完整3项收据已在新输入副本逐hash闭合，并保留8项缺失/篡改/未知引用控制；需新runtime代再验。旧失败代保持失败，不续用。formalQualification=false、finalSystemAccepted=false，整体交付未验收。

2026-11-07 恢复输入独立重建完成：恢复源码 sr-9f97d47a 与 Cargo 消费者的新 cold f7e24f3a64814d49 执行七条固定命令，776 通过、0 失败、24 忽略。实际构建容器 21531f094f3aa7919a35e337e5ef40e5e8df2a2aef8733ec2365724fc3095d32 退出0、非OOM。主执行者另以只读输入在 a595863effae65cd1a7b1b5ce38a13d04e8f7dd21db8366fc0f895a90e32a010 重放全部构建合同，并逐项核验原构建最终配置、八个挂载、命令和资源。证据 restored-cold-review-a6b01df5bbc1；freeze 4f148da2821789f7bb39bb15855da3d1c13254edb267cef467c5a590458b9d57，result 8595432a37f81e013a97063578ddbbaeb53ae191baffe3546e8e16953300b8e2。Host 97610e58bbb2bed48c69d3b43eb5c21cb829bc73f549f52d3f7707b819e3f1c7 与 helper ee3cee2af525d4b93c5809ae510d47e3250e6c01b523a8f5635b2f48cc69f8b8 均与 cold9436 字节一致；这只证明本次两个构建的观察结果，不扩大为所有环境的逐位可复现保证。

恢复源码/Gradle 的 Kotlin 开发双包 kr-83b7a1e2 已通过独立只读回放。主执行者用当前公共 verifyPackage 与 verifyReplayContracts 再次核验封印前后全部46文件、7目录、13745862字节，报告 root-restored-kotlin-review-kr-83b7a1e2.json。原源码与缓存保持。卸载28aef8a1便携归档已完成：uninstall-portable-archive-d6d3597139be/persistent-uninstall-portable.tar.gz，187107454字节，SHA256 c1b9e6aaaf433d234102b42ada5130b30ca9a490f323f955b1257f5fa57fbd1b，3479常规文件/887目录的摘要和模式全部匹配。更新、卸载及恢复边界说明新增于 vm-update-uninstall-recovery.md。交互笔记、跨容器正常重开和离线数据备份恢复仍在实施；formalQualification=false、finalSystemAccepted=false。

2026-11-07 Notes应用与实际离线来源构建通过：样例10文件冻结到 nw-bd2efb18，1512源文件/399目录前后完全一致。NotesDocument通过14个真实Kotlin/Wasm场景，包括拒绝授权、缺失、有效空文件、超长/无效UTF-8/换行、写失败、写后不匹配与目录同步反馈；四个真实Compose输入场景验证无自动保存、锁定保存、失败保留编辑及显式重新读取，存储能力均为明确替身。公开 developer-workflow profile 在 kw-8814483f 实际离线强制重编 Notes/Motion，两次编译和独立只读重放成功，原Gradle缓存保持。seal SHA256 6bab8f3669a9cb648565b092427e4411d6b3ca399955b46f5651cd3f2ad85459，result 1a00b638f5de5fe2fc3d7d78dab5774cf8b98fc34dd25ed06b00c25eb83df830。主执行者另执行当前公共 verifyPackage/verifyReplayContracts、十份容器inspection、完整46导出文件和全部编排文件摘要，root-notes-kotlin-review-kw-8814483f.json通过。Notes main SHA256 9d8543e6f3c9716be3a555dded01a353db0a14c377accc8d6077dbe138b2dc7a。此阶段不声明真实guest授权、文件保存或重启通过；诊断demo显式记录有效便签明文字节，README提示仅使用非敏感测试文字，模型默认不记录。独立交互workflow及其生命周期继续，formalQualification=false、finalSystemAccepted=false。

2026-11-07 开发预览公共外层实际双启动通过：developer-preview-actual-6d8f2b79 使用经过独立只读复核的 runtime pack c47421fc765d0d017b3c200366ebd240732fcdcfa8edd44efda7a4581e910364，复建输入另放 sibling，无虚构工具链来源。finalizer-a718e95c与consumer-root-e6b50a42七实际阶段的固定命令/挂载/UID/资源/退出状态经独立审阅通过。公共 new→pair 的实际CID5659721381e69e867d4df399b2109fcff4d285873d5abe70d37025b377d46329退出0，两次fresh QEMU均正常关闭，盘UUID ce8ba91e-828e-42fa-8272-4469ad9991e1，marker1→2。原始结果只读导出43文件，manifest bdb1959a5ab05580254ee7c3e66b9efc580e402ca81d72d4cf79b94f6154fdb1；主执行者独立完整原始guest流回放、终态/会话/marker/同步/kernel powerdown核验通过，见root-preview-actual-6d8f2b79-review.json。两版审阅脚本的Windows atime比较和单次1MiB读取诊断错误保留；修正v3后全流通过，原运行与导出字节未变。仅验证15秒预览及正常marker连续性，applicationPersistenceVerified=false、deliveryAccepted=false。

Notes README纠正私有能力说明：实际Host在配置data-root后为可信catalog选择的SDK6包提供按包绑定的默认私有会话，无需用户权限对话框；public共享存储才走单独consent。未修改已封印nw源码、已编译Main或NotesDocument；当前README与编译时README的文字差异明确保留。真实保存/重启流程继续。

2026-11-07 独立developer-workflow-v1交互合同集成：新增workflow_profile/control/cli，bootlib/bundle/vm仅增加新profile分支，旧developer-vm-v1仍拒绝输入。原逐revision门经真实socket RED证实受Motion约10Hz刷新影响，改为同session/layout摘要/inputEpoch，revision只允许推进；目标geometry/generation/prompt变化和等待pointer期间变化均拒绝，唯一QMP owner保持。主执行者集成后Linux141/141通过；再发现actual main的旧split-input入场先于workflow分支，导致interactive必拒绝，新增main-path回归RED后仅新profile+control允许无input-script，Linux143/143通过，零跳过。证据workflow-public-integration-c42f7b1d；未运行新的交互guest，不把socket/QMP替身计为真实保存。文档vm-workflow.md说明命令、超时、输入/明文诊断和验收边界。
