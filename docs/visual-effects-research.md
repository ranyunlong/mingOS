# MingOS 下一阶段视觉效果与 UI 基础研究

日期：2026-10-24；结论为源码静态研究与官方资料核验，未执行 Cargo、Docker、GPU 或新实现。字体冻结与 Linux/Windows 验收继续独立进行。
目标是 Linux 底座、Kotlin/Wasm only-Ming-SDK，以及逐步建立原生质感；本次仅研究一个可交付小步，不代表完整桌面或 macOS 品质已达成。

## 推荐结论

**首个 bounded slice：SDK4 显式圆角抗锯齿命令，沿用整数 compute、现有文字 atlas 与 GPU buffer→Surface；暂不同时引入阴影、动画和组件系统。**
相较新建渲染后端，它不增加持久 GPU 资源，也最容易保留 CPU/GPU 精确 pixel oracle；4×4 固定采样只是质量起点，仍需人工检查角部台阶。
阴影缓存作为下一项独立候选；实例化后端留到命令扩展确实受 64M 限制、且整数合成方案有证据时再考虑。

## 真实代码边界

先通过 CodeGraph 查询 graphics、desktop、ui、SDK；下列路径均相对仓库根。CodeGraph 输出有截断，缺失细节用定点源码搜索核对；本机没有 codegraph CLI。
- graphics/gpu.rs:11、121、400：每像素遍历打包后的绘制命令，8×8 workgroup；候选准入却使用原始命令数（含 clip），上限 64,000,000；scene 上限 36MiB。[L1]
- graphics/scene.wgsl:7、32 与 raster.rs:4、64：op1 已有二值圆角，无 coverage AA；按 painter order 做整数 source-over，背景黑色、最终输出不透明。不能把“已有 radius”说成已有抗锯齿。[L1]
- graphics/mod.rs:281：SDK3 将基础光栅 work 与 prepared text.work 相加，再检查 32,000,000；op5 并非免预算。帧 64KiB/256 命令、单边≤4096、总像素≤4,194,304。[L2]
- text/frame.rs、bins.rs：每帧 glyph 实例≤2048、唯一布局输入≤4096B、tile 引用总数≤65536；16×16 tile、每个 text draw 的每 tile≤64 候选、文字 staging≤2MiB。[L2]
- glyph_cache.rs：coverage atlas 独立 4MiB、驻留元数据≤1MiB、新增 padded 上传≤1MiB/帧；CPU coverage≤4MiB、布局/CPU 元数据≤2MiB；图像 atlas 另占 32MiB。[L2]
- gpu_session.rs、presenter.rs:63、present.wgsl：同设备 GPU 帧持有输出 buffer，fullscreen render pass 呈现；普通 native 无像素读回，显式 snapshot/headless 才读回。已有 RenderPipeline 是呈现器，不是实例化场景后端。[L1,L3]
- gpu.rs:348：每次 compute 仍最多等待完成 2 秒；GpuTiming 的 executeReadbackMicros 在 native 中仅含执行等待，renderMicros 不含最终呈现/VSync，不能推导持续帧率。[L1,L3]
- desktop.rs:65、265 与 ui.rs:132：点击起点与拖动位移供查看器使用，hover 不上传、release 无独立事件；失焦取消宿主状态，但没有组件 cancel/focus 协议；普通队列≤128、空闲等待≤20ms。[L4]
- Frame3.kt:15 与 engine.rs:51、109：真实 SDK3 builder/不可变 CJK layout 已存在；worker、导入白名单、父进程 broker 均按版本校验。wasmWasi 编译目标不等于获准完整 WASI；现包仅 Ming 导入。[L2,L5,S6]

## 预算定量：先区分准入量与时间

令 P=逻辑帧像素数，N=原始帧命令数；当前 GPU 准入 C=P×max(N,1)≤64,000,000，且 N≤256。下表不等同于可实现帧率。[L1]

| 逻辑帧 | P | 64M 下 N 最大值 | 再加一条时 C |
| --- | ---: | ---: | ---: |
| 1100×760 | 836,000 | 76 | 64,372,000 |
| 1280×720 | 921,600 | 69 | 64,512,000 |
| 1920×1080 | 2,073,600 | 30 | 64,281,600 |
| 2560×1440 | 3,686,400 | 17 | 66,355,200 |
| 2048×2048 | 4,194,304 | 15 | 67,108,864 |

3840×2160 已超过逻辑帧像素上限；物理 Surface 允许更大不代表逻辑帧可原生 4K。clip 在 pack 时折入 draw，但仍占 C 的 N；缩小矩形/裁剪不降低此准入量。[L1,L4]
1080p 的 24 条基础命令为 49,766,400；再加 8 个 shadow draw 达 66,355,200，直接失败。AA 替换已有 rect 可保持 N；用多层矩形模拟阴影则同时扩大 N 与实际覆盖工作。
基础 W=裁剪后矩形/图像面积之和+旧 op3 估算；SDK3 另加每个 mask 裁剪后的矩形面积，合计≤32M。透明像素/被遮挡像素并不免计费。[L2]
例如 2048 个 96×96 mask 的面积为 18,874,368，再加 1080p 背景 2,073,600，剩 11,052,032 work；这仅是假设 mask 尺寸的面积算例，单 mask≤256²，还需通过 atlas、上传、tile 等独立约束。
文字 tile 内层循环不计入 C；按全帧引用 R≤65536，每引用至多覆盖一个 16² tile，故最多再访问 16,777,216 个 glyph 候选（裁剪只会降低），不能把 C 当成 shader 总操作上限。[L2]
同一 label 重画仍增加 glyph 实例/面积；颜色和整数平移可复用 coverage，分数相位变化可能产生新 mask 与上传。新增 effects 必须保留文字预算余量，不得挤占字形 atlas。[L2]

## 三个实现方向

| 方向 | 与现状的衔接、收益 | 增量成本与取舍 | 判断 |
| --- | --- | --- | --- |
| A 延续 compute，局部整数 AA | 为新 op 计算角部 coverage，再复用现有整数 blend；旧 op/字体路径可保持字节回归 | 保留 O(PN)；每角像素最多 16 次圆内测试；无新增 atlas/离屏帧 | 首选，边界最清楚 |
| B 有界离屏 blur + 阴影 cache | 仅滤波圆角形状的 alpha mask，缓存后在 painter order 插入一个 shadow draw；GPU 全程驻留 | 冷缓存要建 mask+双 pass+暂存；命中仅省滤波，仍付 C 与合成面积；尺寸动画易失效 | AA 之后独立评估 |
| C 实例化 RenderPipeline 场景后端 | quad instance + scissor + atlas，片元工作趋向覆盖面积，适合以后更多 UI 元素 | 需新场景路径、文字取样与颜色约定；重叠仍有 overdraw；现有 64M 准入不会自动消失 | 暂缓，成本最大 |

A 的浮点 SDF+smoothstep 可表达边缘，但 fwidth/dpdx 仅允许 fragment 阶段，不能直接搬到现有 compute；跨后端浮点精度也不能替代现有整数 oracle。[S1]
B 建议以 u32 storage buffer ping-pong 保存 0..255 coverage，定权整数可分离核、每 pass 明确舍入与零边界；先避免依赖滤波 sampler 或可选 storage texture 格式。跨 workgroup 的阶段分离用顺序 dispatch，不靠 workgroup barrier 冒充全局同步。[S1,S4]
B 的讨论上限：滤波半宽 R≤16、含 halo patch≤512²、缓存≤8 项/8MiB、两块 512²×4B scratch 共 2MiB；键含尺寸/圆角/核版本/scale，整数位移和颜色不入键；固定分辨率后的 scale 变化须失效。
320×200 面板加两侧 16px halo 得 352×232=81,664 像素；33-tap 双 pass 最多读取 5,389,824 次，另计 mask 构建与合成。8 个不同冷阴影仅滤波就 43,118,592，超过 32M 合并工作目标；热缓存也不能消除 8P 的命令候选准入。
B 必须先预留冷帧全部 work/缓存/临时内存；全部 pinned 时返回额度错误，不能驱逐仍在用的资源。8MiB cache+2MiB scratch 是新增 GPU 上限，CPU oracle 存储也需独立上限，不代表驱动 VRAM/RSS 保证。
C 官方接口确认 draw(vertices, instances)、VertexStepMode::Instance、set_scissor_rect 可用；只合并 painter order 连续且状态相同的实例，不能按材质重排透明层。[S2,S3]
C 的硬件 alpha blend/UNORM/sRGB 舍入没有承诺复现当前每步 (…+127)/255。保留整数合成意味着还需有界分桶/顺序合成等另一个设计；仅把整数 mask 交给新 pipeline 不能宣称已解决 PN。禁止把旧 oracle 放宽成截图近似。[S1,S3；推论]
C 即使使用普通 instancing，也需记录 instance bytes、顶点/片元覆盖、draw/clip 批数及 overdraw；以后替换 64M 时须建立新后端独立的有界准入，不能只删限制。

## 首切片的精确边界：建议 SDK4-AA1

本节为建议契约，尚未实现；首切片仅实心轴对齐圆角矩形 AA，没有 blur、描边、变换、圆角裁剪栈、动画时钟或按钮事件。
建议 manifest.sdkVersion=4，MingFrame4.rectAA，ming_v4.frame_submit(ptr,len)；沿用 MGF2/layout1 的 24B 头和 32B 命令，新 op6=[6,x,y,w,h,ARGB,radius,0]，不是更改 op1 的采样语义。
SDK4 复用 ming_v3 的布局创建/测量/关闭与 op5；父/worker 白名单和 text service 的“==3”判断届时必须显式纳入 4，并保留 text feature 门控；旧 ming_v3.frame_submit 仍按 SDK3 解码，新 op 只走 v4 提交。
SDK1/2/op3、SDK3/op1/op5 的字节语义与像素不变；SDK≤3 拒绝 op6，旧宿主拒绝 sdkVersion=4。无运行时静默降级；需要兼容旧宿主时发布独立 SDK3 构建，显式接受旧硬边圆角。[L2,L5]
所有 ABI 仍是 i32，应用仍只导入 Ming；不新增 WASI、原生绘图库、系统路径或宿主指针。新接口也必须走同一父进程验证和会话资源所有权。
几何沿用 frame 整数像素、坐标±1,000,000、正尺寸≤1,000,000、radius=0..64 并钳至 min(w/2,h/2)，clip 仍是替换式半开整数矩形；不借此承诺 Retina/DPI 全流程质量。
圆角定义为矩形内连续圆弧；只在四个 r×r 角区做 4×4 子像素测试，偏移为 {1,3,5,7}/8px，中心/直边直接全覆盖；圆内含边界。CPU 用独立标量参考核，GPU 逐条同序混色。
命中 n∈0..16 时 coverage=floor((255n+8)/16)，alpha=floor((coverage×colorAlpha+127)/255)，再用既有整数 blend；r=0 与普通直角矩形一致。保持现有编码 RGB 域合成，不在此切片引入线性光/透明桌面合成。
局部坐标先裁剪并确认角区，再放大 8 倍、钳定圆心差后平方：|d|≤512，平方和≤524,288；不得先对百万级输入距离平方。宿主面积/计数乘加需 checked arithmetic。
准入建议：AA≤16 条、角区计费 A=Σ4r²≤262,144；保守 Wnew=W基础（含 op6 整个裁剪面积）+W文字+16A≤32M，C≤64M 等原有限制继续生效，拒绝发生在分配/提交之前。
这让 AA 额外采样≤4,194,304 次/帧（32M 的 13.11%）；预算单位是保守工作估算，不是可交换的纳秒或完整 shader 指令数。glyph tile 内循环仍须单列观察。
算例：1100×760 背景、8 个 320×120/r16 面板、4 段各100 glyph 标签（假设每 mask16²且各段UTF8≤512B），13 命令；C=10,868,000，Wnew=836000+307200+102400+131072=1,376,672，尚须通过所有文字资源额度。
op6 复用现有 20-word GPU record 时，16 条最多占 1,280B 打包记录；若替换16个 op1 则记录字节不增加；新持久纹理/缓存/读回均为 0。帧输出仍≤16MiB/块，在途持有、分配计数仍需实测。

## 资源与性能验证契约（后续实现验收用）

- oracle：独立 CPU 子像素参考逐像素对比 GPU 输出 u32，覆盖 radius0/1/64、奇偶/极小尺寸、半透明交叠、负坐标、全部裁剪方向、op1/op5 前后顺序；旧 SDK1/2/3 golden 精确不变。
- 额度恢复：AA第17条、W/C边界±1、超大几何、无效reserved、失效 layout；返回 -4（额度）/-2（协议或句柄），last-frame/sequence 不变，紧接合法提交成功，backend 不锁死。额度错误不能杀会话。[L6]
- 真实 GPU OOM/DeviceLost/驱动内部错误在现实现是锁存终态，不能伪装 -4 或承诺可恢复；新 effects 必须预检并避免为普通输入触发该路径。error scope/callback 只提供错误通道，不保证驱动恢复。[L1,L3,S4]
- 零读回：分别记录 native 无 snapshot 的 readbackBytes 增量=0、显式 snapshot 的增量=P×4；PNG/oracle 与 GPU timestamp 查询只能在单列测量会话读回，不能混入普通原生通路。
- 资源：核对 scene/输出 buffer 分配及峰值持有、上传、glyph 驻留/淘汰/压缩；冷启、稳定重画、整数移动/换色、反复 resize、拒绝后恢复、关闭后释放各一组，旧布局/旧帧引用继续计费。预期新 effect 持久驻留=0，不据此声称零分配。
- 性能：固定字体/应用包/源码摘要、release 配置、适配器/驱动/显示刷新；1100×760、1080p、近4MP，N到各自门限，稀疏/重叠AA及稀疏/密集文字矩阵；冷帧单列，热身后各≥1000帧，报告p50/p95/p99及拒绝率。
- 分开报告 Kotlin/RPC、text_prepare、pack、GPU执行、present/等待与输入到呈现；TIMESTAMP_QUERY 仅在支持并显式启用时使用，不支持则标明缺 GPU 纯执行时间；宿主等待时间不可冒充 GPU timestamp。[S3,S4]
- 建议放行门槛：指定已测硬件上无新增像素差/读回/常驻资源泄漏；无效果基线 p95 回归≤5%，上述8面板热帧 AA 增量 p95≤1ms。60Hz 的16.67ms仅为整帧目标，CPU提交到呈现也应计入；超标先降低切片上限，不宣称全平台60/120Hz。
- 平台：Windows DX12、Linux Vulkan 分别跑真实硬件 headless oracle 与 native Surface；Linux 至少记录 Wayland 路径，X11 支持结论须有独立运行。Weston/Pixman CPU/软件 Vulkan/编译成功都不等于 Linux 硬件 GPU 验证。[L3]

## 最小 desktop 衔接

AA 验收后可在一个 Kotlin 样例窗口画“面板+标签+单按钮”，复用不可变 TextLayout；所有布局、命中与组件状态在 SDK/Kotlin，宿主只提供有界事件/时钟与验证后的绘制。
下一独立 UI 切片先补版本化 pointer move/down/up/cancel、focus及button/action key；明确 frame 坐标、capture、失焦/resize取消与队列满策略，保留关闭优先，up/cancel 不可因 motion 洪泛丢失。[L4]
组件只做 normal/hover/pressed/focused/disabled 与一次激活，采用有界状态/事件序列回归；现有 [4,x,y,1] 点击不能伪装成完整按钮状态，IME/编辑/无障碍另立边界。
再考虑一条有界颜色/透明度过渡：宿主单调时间和一次待发 tick、≤1个活跃过渡/≤200ms，按需重绘，结束即停止；用固定时间输入做可重复 oracle。WaitUntil 可做唤醒原语，但不是 VSync/帧时钟保证，现有20ms idle也不是60Hz调度。[S5,L4]
这些只是查看器到单窗口 UI 基础的衔接，不包含窗口管理器、跨应用合成、模糊桌面、系统服务或可启动 Linux 镜像。

## 证据范围与主来源

源码证据是当前磁盘快照；工作目录没有可用 Git 元数据，不能提供可靠 commit 身份。历史验收记录与当前源码/二进制未在本研究中重新绑定；所有性能上限、AA/阴影预算与门槛都是推导或建议，未实现/实测。
研究完成后父任务已关闭SDK3验收，[L7]现记录Windows RTX4070 Laptop/DX12、CPU/GPU PNG对照、原生零像素读回及Linux五路径通过；本研究没有亲自重跑这些命令。headless读回样本、字体冷排版毫秒数不能外推native帧预算。Linux仍为Weston/Pixman CPU；物理Linux GPU/驱动保持“未验证”。
主来源于2026-10-24联网核验；锁定本项目 wgpu27.0.1/winit0.30.13，不用 latest 文档代替。docs.rs 的27版页面本次无法访问，改读官方标签源码；WGSL 当前草案的新扩展不自动代表 Naga27 支持，本切片只依赖已有整数/存储buffer能力。
- [L1] [GPU与shader](../crates/ming-app-host/src/graphics/gpu.rs)、[场景shader](../crates/ming-app-host/src/graphics/scene.wgsl)、[CPU参考](../crates/ming-app-host/src/graphics/raster.rs)。
- [L2] [帧准入](../crates/ming-app-host/src/graphics/mod.rs)、[文字帧](../crates/ming-app-host/src/text/frame.rs)、[分桶](../crates/ming-app-host/src/text/bins.rs)、[glyph cache](../crates/ming-app-host/src/graphics/glyph_cache.rs)、[SDK3契约](sdk-v3.md)。
- [L3] [呈现器](../crates/ming-app-host/src/graphics/presenter.rs)、[Surface验收](gpu-surface-verification.md)；[L4] [desktop](../crates/ming-app-host/src/desktop.rs)、[ui](../crates/ming-app-host/src/ui.rs)、[输入ABI](sdk-v2.md)。
- [L5] [SDK3 builder](../ming-sdk/src/wasmWasiMain/kotlin/ming/sdk/Frame3.kt)、[导入与worker门控](../crates/ming-app-host/src/engine.rs)、[broker](../crates/ming-app-host/src/broker.rs)；[L6] [额度拒绝保会话回归](../crates/ming-app-host/src/broker_frame_tests.rs)。
- [L7] [字体验收状态](typography-verification.md)、[已有SDK3对照产物](../artifacts/typography/acceptance.json)。
- [S1] [W3C WGSL 2026-10-21 草案](https://www.w3.org/TR/2026/CRD-WGSL-20261021/) §15.6.1、§15.7、§17.6：barrier作用域、浮点规则、derivative阶段/一致控制流限制。
- [S2] [wgpu v27.0.1 RenderPass官方源码](https://raw.githubusercontent.com/gfx-rs/wgpu/v27.0.1/wgpu/src/api/render_pass.rs)：draw的instances范围、set_scissor_rect。
- [S3] [wgpu v27.0.1 types官方源码](https://raw.githubusercontent.com/gfx-rs/wgpu/v27.0.1/wgpu-types/src/lib.rs)：VertexStepMode::Instance、BlendState、QueryType::Timestamp及feature要求。
- [S4] [wgpu v27.0.1 Device官方源码](https://raw.githubusercontent.com/gfx-rs/wgpu/v27.0.1/wgpu/src/api/device.rs)：create_compute_pipeline、limits、poll、push/pop_error_scope、设备丢失回调。
- [S5] [winit v0.30.13 event_loop官方源码](https://raw.githubusercontent.com/rust-windowing/winit/v0.30.13/src/event_loop.rs)：ControlFlow::WaitUntil与Poll/VSync说明。
- [S6] [JetBrains Kotlin/Wasm WASI官方文档](https://kotlinlang.org/docs/wasm-wasi.html)：wasmWasi目标；本项目only-Ming导入政策由本地L5证明，非该文档保证。
