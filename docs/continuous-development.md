# 连续开发路线与阶段验收

更新：2026-10-27。用户授权每个阶段完成后直接进入下一阶段，直到整体任务结束。阶段验收通过只表示该阶段结束，不表示整个操作系统已经完成。

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
