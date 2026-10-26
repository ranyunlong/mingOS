# 真实Compose与Ming组件验证

更新2026-10-25。用户选择Kotlin/Wasm，正式运行时为Rust/Wasmtime。当前已有真实Compose Compiler/Runtime驱动的MingColumn、MingText、MingButton和MingApplication同步输入循环。应用无需手工报告修改的state集合；桥接自动订阅Snapshot并重组。

本项实现与自动验收已通过，物理鼠标专项因用户Escape停止Computer Use而暂挂，不计作通过；完整交互门槛仍待恢复授权后执行。整个OS、异步帧时钟、桌面和启动镜像仍未完成。

## 编程模型与边界

使用Compiler2.3.20和官方Runtime1.10.0源码WASI port，通过Gradle includedBuild从源码消费，保留原版权、许可和修改通知。MingApplication没有依赖JVM或浏览器；JDK/Node仅用于编译和测试工具。外部函数必须通过Ming-only导入白名单，adapter仅改写random_get。

同步会话拥有32个committed节点（含root），一次更新最多64 transient节点；16个已提交文字layout加最多16个staged替换，总活动不超过32，低于宿主64上限。资源替换与hit targets只在frame成功提交后更新；失败关闭新资源、保留旧frame和输入区域，最终dispose释放observer、composition、recomposer、layout和style。

这是首批单行文字/垂直布局/鼠标按钮。现阶段不宣称Compose UI或Material全组件兼容，也没有完整键盘focus、IME、布局引擎、动画和异步调度。port的single-thread、内部identity版本耦合、strong weakref fallback与explicit clock限制继续成立，见compose-wasi-port.md。

## 实际执行

- 自动Snapshot/节点桥接：真实Wasm27项行为检查，91条原始事件精确匹配，最后299=1；27项包括remember身份、same-state、移除/move、effect/RememberObserver、幂等dispose及异常清理。优化宿主在60000ms/300M预算下重放约2.85s，消耗19260872 fuel。
- 容量P2 CBR-001已修复并独立关闭：Composer合法keyed替换会先插入后移除，不能把临时旧树计数当最终超限；现initial组合和normal+late apply后校验32最终节点，并保留64 transient guard。disjoint、子树替换、late change和真超限清理回归通过。
- Renderer合同11/11：执行真正Kotlin/Compose Wasm，Node只double SDK边界；覆盖文字事务、16+16替换、frame拒绝保留旧hit geometry、style/metrics失败与关闭错误。
- 应用循环合同10/10：初始绘制、记住计数、条件节点、重复/未知输入、1x1及4M像素viewport、失败退出和4096次idle预算。
- 这些Node合同不是原生像素或系统隔离验收。真实原生宿主和图形证据单独记录。

资料：../artifacts/compose-bridge/composition/task1-closure、composition-review/closure、renderer-package、renderer-review和application-review。已确认独立容量复核closed，组件绘制复核未发现缺陷；应用循环/呈现验收复核已完成；PRESENTATION-001发现两份摘要可能共同陈旧，现补frame.sequence==framesSubmitted锚点，原18份保存outcome按加强后的生产AST重验通过。该补强没有重执行GUI，旧执行/报告/harness哈希保持原样，见application-review/final-revalidation.json。

## Windows真实应用

最终当前结果为../artifacts/ui-compose-reviewed/acceptance.json：400份Runtime/bridge/SDK/Rust/shader/fixture/配置源码与宿主、Compiler raw Wasm、package、metadata、字体及harness在首case前冻结，调用前后和报告前复查。

8组真实CPU/GPU PNG精确一致：初始、计数增加、条件隐藏、恢复、四次大resize、窄窗口、1x1、320x240。计数和隐藏改变实际像素，恢复精确回到初始；按钮共享1个canonical阴影key，移动尺寸和状态更新不会重复生成阴影patch；文字复用/释放和warm glyph计数分别核对。

自动native无snapshot：1次Surface API接受，presentedFrame为seq1/1100x760，readback0，呈现参数32B。显式snapshot：1次API接受，readback3344000B，参数32B。两窗口自动关闭；没有进行物理鼠标操作。这是Surface API证据，不代表物理scanout时刻。

新增呈现身份后，整仓all-features213项通过、默认忽略12项独立硬件测试，Clippy -D warnings通过；真实Wasm host测试4/4及desktop状态测试3/3通过。6B硬件专项有独立历史结果，不把它们冒称为本轮重新执行。

初始PNG SHA256：

~~~text
1062cad5de0404d32d613ee0a81ed32893562b55e214e8dd3f33af078bc1897a
~~~

首轮ui-compose-20261025画面和宿主源码保存在../artifacts/compose-initial-source-snapshot，不替代新增遥测后的reviewed证据。

## Linux与呈现身份修正

首轮../artifacts/linux-compose-20261025的11条应用路径本体、154 Rust与154 fixture通过，但外层runner旧的“SDK2/3必须呈现两次”断言失败，整体记录仍为failed。实际两应用均处理resize并提交2帧，release快速启动将它们合并为1次呈现。

因此新增Outcome.presentedFrame：只在Surface/softbuffer present成功后记录source frame的sequence/width/height，headless明确null。修正后的验收保持resize和2次提交要求，并要求最后API接受的帧身份与最终frame一致；不会仅降低次数门槛或接受stale帧。162项verifier fixture已在Windows执行，其中161通过、1项既有Unix socket skip；Linux本轮必须162/162且无skip。

新结果../artifacts/linux-compose-20261025-reviewed/summary.json完整passed：11路径、release Rust155项、Linux162项fixture无skip、字体42项、release全功能/全targets编译和Windows二次重验通过，容器exit0/非OOM/已停止。SDK2/3实测2次提交、1次呈现，presentedFrame为seq2/1100x760，与最终frame完全一致。Weston为headless/Pixman，未暴露物理LinuxGPU；release/default debug选择、actual binary、target及命令均绑定，不改变宿主Wasm限额。

四条初始图像路径（Windows CPU/GPU、Linux headless/Wayland）PNG精确一致，见../artifacts/compose-bridge/cross-platform.json。400份来源及原始/最终Wasm、宿主和旧/新harness归档在../artifacts/compose-source-snapshot；后续源码正常演进时应按快照解读本阶段结果。

## 性能与后续

同一冻结Wasm在debug宿主出现60s超时，优化宿主通过；这支持debug宿主性能问题，尚无compile-only timing，不能把所有耗时归因于Module::new。应用启动、host局部prepare/render和端到端输入延迟应分别测量，不能用局部微秒数冒充FPS。

继续受控帧时钟、协程/取消和动画。确定性虚拟frame资格将与真实HostFrame接口分开验证；Computer Use保留用户停止状态，不以scripted事件冒称物理鼠标验收。
