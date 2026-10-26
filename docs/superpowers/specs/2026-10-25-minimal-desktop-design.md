# 第7阶段：最小Ming桌面设计

日期2026-10-25。前置：真实Kotlin/Wasm、SDK5 HostFrame、真实Compose异步模型、GPU直接呈现与字体/AA/阴影自动验收通过。6C/6D物理鼠标专项仍因用户Escape暂挂，不以脚本或虚拟环境冒充实际物理输入。整体OS goal未完成。

## 分层选择

MingDesktop拥有Ming应用的窗口树、z-order、focus、chrome、Dock/launcher和多应用生命周期。普通应用仍为Wasm，只通过Ming SDK交换frame与输入，不暴露Wayland socket、GPU对象、Linux窗口协议或任意进程启动。

Linux初始显示/输入backend继续借用Weston/Wayland：MingDesktop是单一全屏client，内部合成自己的Ming app surfaces。Weston仅承担下层DRM/input/scanout生态；Ming窗口与应用管理在MingDesktop实现，不能把Weston测试client误称为已完成的Ming桌面。Windows winit/Surface保持开发及硬件验证后端。

官方Weston kiosk-shell支持将top-level窗口置为fullscreen，可用shell=kiosk-shell.so；见官方文档https://wayland.pages.freedesktop.org/weston/toc/kiosk-shell.html（2026-10-25查验）。后续启动基础候选Debian13 trixie，官方stable信息https://www.debian.org/releases/stable/；最终image/digest/包版本在启动阶段锁定，当前不把容器当可启动系统。

## 首个阻断点：设备共享与应用状态

独立RTX4070/DX12纯色probe实际发现：两个Graphics附着同一GpuSession.clone时，两个app都从imageHandle1开始，B的1x1蓝图可能复用A的红图；关闭A相同编号也会移除B共享cache条目。不同handle交替会重复上传。证据artifacts/desktop-gpu-context-probe。

GpuSession.clone表示同一逻辑render session的alias，不能当新app上下文。新增显式fork_render_context：共享wgpu Device/Queue/不可变pipeline与device失效状态，创建独立images/glyphs/shadows缓存、buffers、metadata与stats。Frame引用保留所属session；cache/close/stat不会跨app作用。

wgpu error scopes及device操作需用共享driver guard顺序执行，统一锁序driver→app GPU state，避免不同app mutex不能保护同device scope栈。Device identity由同一共享guard/token表示，presenter可以接受同device不同context的不可变frame buffer；不同device仍拒绝。旧单应用clone和已有preview语义保留。

## 窗口与合成

初版固定同时最多4个普通client，加可信system shell surface。GPU直接采样各client输出buffer，不CPU读回再上传。根frame仍受当前4M像素/旧预算限制；高DPI/更大frame与复杂tile优化另列实际测量后推进，不宣称Retina或无限窗口。

窗口状态使用host-owned WindowId，持有app instance、content rect、restore rect、visibility/minimize/maximize、focus和latest immutable frame。所有操作由纯状态机先决定，再执行GPU/worker副作用。frame合成按z-order，chrome/system UI优先命中；content input投影到app logical viewport。

每个window使用ViewportGate；resize后直到匹配尺寸frame被呈现才开放指针。pointer capture绑定windowId，close/minimize/focus loss清理capture/key状态。close、launch、restore和crash恢复必须有限且可观察；不能以窗口数量或截图替代多app执行证明。

## 应用生命周期和服务边界

初始catalog为可信系统配置列出的已验证Ming包，不接受guest路径/任意shell命令。parent为每个app创建独立Broker、worker、ViewBridge及forked GPU render context。启动/退出/错误状态回传MingDesktop，清理window/frame/资源；关闭一个app不影响另一个。

当前preview的总fuel/时限/事件预算适合有限开发验收，不是长期OS会话。后续独立session方案必须按受控yield补给预算、限制速率/CPU/memory/pids并保留失控worker终止能力；不能简单删除时限或无限补fuel。平台隔离阶段再落实Linux UID/namespaces/seccomp/cgroup。

Shell、launcher、Dock先采用host-owned系统UI，普通应用保持高级语言SDK。后续系统Shell能力必须来自parent授予的role，不能只凭manifest id或包名获得启动/关闭其他应用权限。中文输入/键盘focus/TextField另有完整输入模型任务，不把现有ASCII快捷键处理声称IME完成。

## 验收分步

7A：同device独立render contexts，红/蓝相同handle交替精确，close/stat/cache隔离，warm不重复上传，direct无隐式读回，scope/poison/device loss正确。
7B：纯窗口状态机、z-order/focus/capture/resize/minimize/restore/close；真实GPU多surface合成与CPU oracle、latest frame身份和资源预算。
7C：真实多worker Kotlin apps、launcher/Dock、关闭/崩溃/重启，自动Windows/Linux证据；保留物理输入暂挂状态。
7D：长期会话预算与输入/中文IME/基本恢复，随后平台服务/隔离及可启动Linux镜像。整个系统的完成必须覆盖原目标，不能用一个动画或桌面截图代替。
