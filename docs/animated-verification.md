# 第6D项：真实HostFrame与Compose动画验证

更新2026-10-25。已形成真实宿主时间→SDK5→owned dispatcher/frame clock→官方Recomposer/effects→Ming组件→CPU/GPU绘制闭环。当前自动验证通过；物理鼠标专项保持用户Escape停止状态，完整OS、长期会话、桌面和启动镜像未完成。

## 分层证据

- 低层Clock packet/core/service经过RED→GREEN；SDK5 realWasm验证版本/签名/坏memory/RPC前拒绝、pending/error无写、重叠/未对齐、旧图形decoder继承。
- 独立复核FRAME-CLOCK-001/002关闭：私有pending引起跨API输入倒序已取消；先交付到期时间机会、不取走原队列input。显式总budget限制sleep，过期read/wait或晚唤醒不交付数据。focus unit21及actualWasm7通过，原RED和closure保留。
- Root全features236项曾通过；两项fix后全features/Clippy再次通过，最终日志计数另见frame-clock-host。默认12hardware测试未在这些普通命令中执行，不混称。
- 低层Kotlin SDK边界double：16clock/58wait/20检查通过。随后真实Kotlin SDK调用原生host的clock/timer/两次frame/pending/local invalid全部通过，见../artifacts/frame-live/result.json。
- source coroutine异步资格28项、框架driver53项均真实编译并在优化Wasmtime执行。它们注入确定性时间，未冒充真实host clock。
- FramedComposition合同真实Ming节点、state/token身份、delay/frames/同值无重组/dispose通过；生产FramedApplication进一步使用真实SDK5时间，完成2次绘制并自动EOF清理，见../artifacts/framed-runtime及framed-app。

## Windows动画

samples/animated-demo使用真实LaunchedEffect(generation)、delay80ms、withFrameNanos约400ms渐显，alpha32→255，最后固定显示“动画完成”。重播触发新的effect key，取消模型由driver合同覆盖；脚本点击/终态收敛不单独证明取消实例计数。

scripts/build-animated.mjs在编译前后核对524项相关源码/配置/工具/来源，校验raw/package并生成build-receipt。样例verify再冻结源码、receipt、host、raw、package、imports和事件输入，调用前后验证。实际typedimports仅Ming namespace，adapter仅random_get映射。

8组CPU/GPU场景完成：complete、replay、earlyClose、narrow、tiny、maxSquare、wide、tall。完整场景终态PNG精确一致；早退只验证合法退出/状态。真实时间使中间frame数量可以不同，不将终态一致冒称逐帧时间轨迹一致。

默认complete两后端各提交25帧；GPU阴影仅1miss、后续24hit、参数32B，glyph/image/cache与读回独立记录。初始headlessGPU每提交会读回，因此其83600000B是25*1100*760*4；它不是native路径。

源码绑定native：无snapshot提交25/Surface API接受25、最终presentedFrame seq25/1100x760、readback0；显式snapshot同25次呈现、readback3344000B。两窗口自动关闭，未使用Computer Use。呈现参数为32B/acceptance；身份锚定frame.sequence==framesSubmitted，再核对presentedFrame，不能让两份共同陈旧摘要冒充最新。

结果目录../artifacts/animated-demo-prep/runs/source-bound-*，汇总../artifacts/animated-acceptance/checkpoint.json。初轮initial-complete只是未完整source binding的先期图形结果，保留为历史，不替代source-bound证据。

终态PNG SHA256：

~~~text
1790302e7ea60f4dcc0784dc47e7d43d440071572369a051ae160f457aa038f8
~~~

## Linux与回归

../artifacts/linux-animated-20261025/summary.json完整passed：release原生13路径（7headless/6Wayland）、Rust183、Linux177fixture无skip、字体42及release全features/alltargets编译。Windows二次fixture177中176通过/既有Socket skip1，保存证据复验通过；容器exit0、非OOM并停止。

真实headless动画提交25帧；Wayland提交25、API接受17（允许合并），最终presentedFrame仍seq25/1100x760。两路径与Windows CPU/GPU终态PNG完全一致。Weston headless/Pixman，不代表Linux物理GPU或显示器测试。

旧SDK1..4的六个样例已重编，在新优化host真实headless CPU执行全部通过，SDK4应用10边界合同通过。旧包保存在../artifacts/pre-framed-legacy-packages，避免root新依赖覆盖历史证据。

## 限制与后续

依旧是实验图形/应用平台，尚无生产级长期heap/真实硬件vsync/FPS或输入到scanout延迟结论。此前因工具中断留下的framed Kotlin独立审阅已补齐；发现的Host错误叠加清理异常丢失问题已最小修复，并经真实Kotlin/WASI故障合同、主线程重放及独立复核关闭，详见下节。历史自动验收保持原记录，物理鼠标专项仍暂挂。

当时发现的多应用cache混用问题已在7A通过fork_render_context隔离，clone仍保留同context别名语义。7B合成核心与内容scene已接通宿主库，见desktop-compositor-verification.md。后续继续原生桌面、真实多worker、Shell/Dock与生命周期，再推进Linux服务隔离和启动镜像；动画demo不等于整个OS完成。


## 2026-10-25：清理异常链修复与当前包回归

独立审阅发现FRAMED-KOTLIN-001（P2）：Host wait/clock失败形成FrameHostFailure后，若onDispose等清理也失败，末尾转返回码会丢失suppressed异常。FramedApplication仅收紧一处条件：无suppressed的纯Host错误仍保留原status，混合失败抛出原异常及清理链；无ABI/依赖/生产测试钩子变更。

同一份真实Kotlin/WASI故障合同RED为5通过/2预期失败，修复后7/7通过。主线程从冻结Wasm和相同harness在新目录再次重放7/7通过；检查原Throwable身份、异常链、纯Host返回码、effect/dispose独立失败以及finally/dispose各执行一次。故障组合使用deterministic Host capability double，未冒充Rust Host真实故障注入。另有真实Wasmtime headless CPU正常路径2帧/exit0。独立局部复核确认P2可关闭，闭环记录见../artifacts/framed-kotlin-fix-20261025/closure.json。

当前animated-demo已以529项输入前后指纹绑定重建，旧包及compiler Wasm保存于../artifacts/animated-framed-fix-20261025/prior-package。新包实跑complete两后端：CPU20帧、GPU25帧，终态PNG字节完全一致，SHA256仍为1790302e7ea60f4dcc0784dc47e7d43d440071572369a051ae160f457aa038f8；不同中间帧数量符合真实时钟机会，不代表时间轨迹一致。earlyClose两后端均提交1帧并正常退出。结果见../artifacts/animated-framed-fix-20261025/result.json及animated-demo-prep/runs/framed-fix-*。

本次修复仅重跑complete与earlyClose及7个故障合同；原8场景/native/Linux自动验收保留为历史结果，未宣称本次全部重跑。应用进程wall time包含JIT/启动，不用作动画时长、FPS或输入延迟结论。未执行物理输入或新的Surface/scanout验收。
