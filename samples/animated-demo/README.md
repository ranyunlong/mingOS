# 有限 HostFrame 动画示例

当前处于准备阶段：只写了真实 Kotlin/Compose 源码与验证工具，尚未编译或执行此样例。父线程的真正 MingFramedApplication entry 已通过独立真实CPU验证（artifacts/framed-app/result.json）；本样例仍需完成 root :animated-demo 注册、打包白名单及 adapter/build-info 分支后，才能运行下列命令。没有 stub、fake clock、JS 产品动画或替代 Runtime。

应用只调用 MingFramedApplication.run(content: @Composable () -> Unit): MingAppResult。LaunchedEffect(generation) 使用真实 kotlinx.coroutines.delay(80)，然后在真实 withFrameNanos 上按 Long 时间差推进约400ms。alpha 从32到255，RGB固定#245e55；插值先转Long再乘，最多64次推进，另有1次起始frame等待。不使用Math、JS时间、wall clock或新增WASI。

页面按顺序显示标题、“重新播放”按钮、“渐显文字”和状态行。完成后文字固定为“动画完成”，颜色完全不透明，不显示时间、帧数或generation。因此相同viewport的终态可CPU/GPU精确比较；中间帧随实际调度变化，不要求相同。重新播放只修改remember generation，由真实LaunchedEffect取消旧任务并启动新任务；取消路径不在finally写入完成状态。

## 预算与resize

固定6节点（含root和column）、4个文字布局、1个按钮。当前Renderer对应7命令：背景1、文字4、按钮shadow/AA2；低于15命令。合法viewport原样交给框架，不在应用中夹取或固定尺寸。使用SDK现有1..4096及P<=4,194,304像素边界；最坏P*7=29,360,128，低于64M工作预算。常见动画帧只变文字color，文本与em不变，可复用layout；状态切换只替换一个label。

## 真实编译门槛

父线程准备好entry、官方Runtime/coroutines源码composite及root登记后，在项目根运行：

```powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
node scripts/build-animated.mjs
```

也可由父线程在空闲的root Gradle窗口离线编译：

```powershell
.\gradlew.bat --offline --no-daemon :animated-demo:compileProductionExecutableKotlinWasmWasi
```

本任务未运行这些命令，且不自行修改root/scripts。父线程打包时还需把animated-demo纳入现有random_get-only adapter分支，并输出build-info.json；只加样例白名单而未加该分支会缺少适配或build-info。保持Kotlin/Compose Compiler2.3.20；不接受clock_time_get、poll_oneoff、fd_*、proc_exit等WASI补洞。

## 最小真实宿主验证

只在真实编译和打包成功后使用SDK5 full release宿主。verify.mjs是工具，不提供clock或动画；所有模式固定60000ms/300M fuel，每次使用新run label，产物仅写artifacts/animated-demo-prep/runs/。它冻结host/package、原始compiler Wasm、build-info、当前sample源码与自身哈希，检查typed imports与唯一random_get适配，并保存全部命令、stdout、stderr、截图和结果。编译时源码绑定仍以父线程build receipt为准。

```powershell
$hostExe = (Resolve-Path '.cache/rust-target/release/ming-app-host.exe').Path
$package = (Resolve-Path 'artifacts/animated-demo/app.json').Path
node samples/animated-demo/verify.mjs compare $hostExe $package complete-01 complete
node samples/animated-demo/verify.mjs compare $hostExe $package replay-01 replay
node samples/animated-demo/verify.mjs compare $hostExe $package close-01 earlyClose
node samples/animated-demo/verify.mjs compare $hostExe $package narrow-01 narrow
node samples/animated-demo/verify.mjs compare $hostExe $package tiny-01 tiny
node samples/animated-demo/verify.mjs compare $hostExe $package max-01 maxSquare
node samples/animated-demo/verify.mjs compare $hostExe $package wide-01 wide
node samples/animated-demo/verify.mjs compare $hostExe $package tall-01 tall
```

compare分别跑CPU/GPU。完整动画用无显式close的headless队列：没有frame/timer工作且输入EOF后才关闭；动画进行中仍由真实clock机会推进。earlyClose允许主动提前取消，甚至在第一帧前退出；不要求最终“动画完成”或截图，也不强行比较时间相关画面。idle8只是有限yield，不代表经过某个虚拟时间。

正常只允许一个code40=framesSubmitted；失败遵循旧样例code60=result.code。帧数受真实调度影响，不固定，CPU/GPU帧数不必相等。完整案例检查真实frame opportunities及重绘，再逐字节比较终态PNG；本地host写PNG路径相同，字节相等蕴含像素相等。最后还需查看截图确认“动画完成”和不透明文字；没有OCR或已接受golden之前不把字节相等单独称为语义终态验收。到期timer可以随同一个FRAME被driver处理，frameClock.timers合法为0，不要求timers>=1。

replay脚本点(40,90)是按本样例固定BuiltinFont、按钮为第二个叶节点准备的候选点，必须在真实渲染后核对命中位置。脚本证明输入送达和终态收敛，不能单靠正常退出日志计数被取消的协程；精确cancel/finally语义还应与父线程真实runtime合同交叉核对。未加入额外产品logger以伪造可观测性。

可单独执行自动native窗口检查（不会操作鼠标）：

```powershell
node samples/animated-demo/verify.mjs native $hostExe $package native-01
node samples/animated-demo/verify.mjs native-snapshot $hostExe $package native-snapshot-01
```

view有Live输入，默认15s自动关闭，仍用60000ms/300M预算。普通native要求wgpu-surface、framesPresented>0、readbackBytes=0及presentedFrame与最后frame身份一致；显式snapshot允许并要求读回。Native因Live/OS resize可改变viewport，不夹取尺寸。ComputerUse保持停止，既不执行也不冒称物理鼠标验证。

## 当前状态与限制

只有准备源码、JSON及JS工具的静态检查；没有执行Kotlin RED/GREEN、SDK double、CPU/GPU或native样例。未来若增加SDK边界double，必须实例化真实编译的Wasm，不能用JS实现产品协程/时钟。父线程已完成的FrameDispatcher/HostFrameClock资格及frame-live结果属于独立证据，不冒称本示例已运行。

frame opportunity不是硬件vsync/scanout，框架源码port的单线程、弱引用fallback和版本耦合限制仍适用。helper总耗时包含JIT/初始化，不作为状态到呈现性能结论。当前帧步数上限用于避免异常clock下无界推进；如果64步仍未到400ms，效果失败而不是静默冒充完成。
