# 第6C项：真实Compose与Ming组件闭环

日期2026-10-24。用户最终放弃JVM，继续Kotlin/Wasm/WASI、Wasmtime与Ming SDK-only。用户已授权连续推进，6B验收后实施本项，不另设审批仪式。按Superpowers设计/计划流程记录本阶段边界。

## 已有证据

官方Compose Compiler2.3.20、Runtime1.10.0原始源码及WASI平台适配已编译出counter-03；父线程独立冻结宿主/清单/Wasm并重放成功，见artifacts/compose-qualification/parent-replay/result.json。真实remember、mutableState、受控重组、ComposeNode移除、DisposableEffect和重复dispose通过。Wasm SHA256为453936b6926f59a2e394e8815cb6a0550a0c0c2e4a29ddc98f0ab61b5aebe09b。

这是无窗口编程模型资格，尚未形成正式组件SDK、异步调度或UI性能结论。当前port限制包括单线程、Kotlin内部identity字段版本耦合、上游web WeakReference的强引用fallback，以及缺少默认frame clock。所有限制必须继续显式记录。不能以自造注解/Runtime、JVM或浏览器替代真实移植，也不能放宽WASI白名单。

## 选择与范围

选择已有官方Runtime源码port作为Gradle included build，以显式dependencySubstitution引用runtime project；新增ming-compose库和真实compose-demo。这样可从源码复现，不要求用户事先手动复制KLIB，也不把实验探针包装成官方WASI发布物。保持根SDK和旧样例的构建/导入兼容。

首个闭环仅包含MingColumn、MingText、MingButton、一个Compose拥有的节点树和SDK4绘制适配。全部使用真正的@Composable/ComposeNode/remember/state。应用表达状态与onClick；桥接层自动接收Snapshot apply通知、记录变化并驱动ControlledComposition，应用不能被要求手工列出modified state。真实Recomposer参与受控组合；本项不宣称完整调度器。

先完成同步输入到重组和绘制。LaunchedEffect、delay、动画与平台帧时钟属于下一项，必须有独立实际验证，不能静默模拟它们已受支持。

## 节点、布局与生命周期

Applier只修改Ming节点树，不调用系统底层。垂直布局使用整数frame像素和SDK3 Q6文字测量，限定初始组件集，不实现泛化布局引擎。按钮拥有确定的半开命中矩形，点击来自MingInput；resize原样保留所有合法宿主viewport，缩减可选详情以满足P*N<=64M，不能固定夹画布使ViewportGate失配。

会话已提交树最多32节点（含root），一次更新最多64个暂态节点；最终额度在initial组合或两次apply阶段完成后检查，以支持Composer先插入再移除的合法keyed替换。16个已提交文字布局；原子替换最多暂存16个新布局，总活动布局<=32，低于宿主SDK3的64上限。实际实现须同时满足宿主SDK3和SDK4已有额度。文字变更先创建/测量新layout，成功后替换并关闭旧layout；销毁节点释放文字，销毁会话关闭Snapshot observer、组合、文字及样式。样式在会话启动时建立，点击/移动/大窗口resize复用既有阴影key。错误回滚和失败退出不得泄漏活动SDK句柄。

绘制沿用MGF2/layout1，AA op6、shadow op7和text op5不改ABI。普通应用仍只导入Ming SDK，禁止原生路径、文件、线程或GPU对象入口。所有预算保留，包括32M合计work、8 shadow/16 AA、256命令、4M像素、文本/图像资源上限。

## 验收

1. 真Compiler/Runtime构建，最终Wasm函数导入精确检查；保留源码/许可证/provenance。
2. 自动Snapshot通知驱动重组，验证remember身份、节点复用、更新/移除顺序、DisposableEffect与observer销毁；不使用应用手工changes集合。
3. 点击、重复点击和resize驱动真实Ming窗口；CPU/GPU截图精确一致，阴影/字体缓存可核对，普通native无像素读回。
4. 超限/资源失败保留上一帧、关闭仍可恢复；真实Linux/Wayland执行，记录单独Compose包路径。
5. 启动时间与状态到呈现的实际耗时分开记录。现有port首次JIT可能较慢；不得用宿主局部渲染时间冒充端到端性能。

完成本项后继续SDK帧时钟/协程调度和动画，再推进桌面合成、Linux隔离、可启动系统。整体OS goal保持active。
