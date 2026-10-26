# 第6D项：受控HostFrame与真实Compose异步模型

日期2026-10-25。Kotlin/Wasm/Wasmtime、Ming-only外部能力边界不变。6C实现及自动验收通过，物理鼠标专项因用户Escape暂挂，不冒称通过。以下工作不需要控制用户鼠标，可独立推进；整体OS goal继续active。

## 已有资格与选择

probes/compose-frame的确定性sidecar真实执行了Recomposer.runRecomposeAndApplyChanges、LaunchedEffect、withFrameNanos、delay/timeout、取消和finally，共28项检查通过。它使用官方Runtime1.10 port、Compiler2.3.20和官方coroutines1.9源码资格变体；原published coroutines带入clock_time_get/poll_oneoff/fd_write/proc_exit，现有adapter正确拒绝。GREEN依赖两处显式拒绝缺省平台服务的actual与四份注解语法兼容副本，common算法不改。

sidecar时间由测试main推进，不证明宿主时钟、GUI帧率或Native呈现。后续只从已验证源码模式构建production候选，保留出处/许可/版本与单线程、weakref、internal identity限制。

选择两个窄接口：受控monotonic读取和有界frame/timer/input/close等待。禁止一般FD polling、任意clock ID、直接WASI映射或宽泛系统调用。服务使用app/session私有的Instant起点，纳秒单位不等于纳秒精度，不提供wall clock。frame opportunity是宿主单调计时机会，首版目标间隔16_666_667ns，不宣称硬件vsync或scanout。

## API与版本

新增sdkVersion5；旧1..4导入、MGF2命令和额度不变。SDK5明确继承SDK1..4，使用v4.frame_submit仍按v4解码；旧v2/v3提交入口继续拒绝op6/op7。新命名空间为ming_frame_v1，仅sdkVersion>=5可导入。SDK5 clock-only模块可在engine-only宿主执行；导入ming_v3/v4仍需text feature。旧SDK3/4的整体text门控保持不变。

1. clock_monotonic_ns(out_ptr:i32,capacity:i32)->i32。capacity必须8；成功写little-endian非负i64 nanos并返回8。
2. wait(request_ptr:i32,request_length:i32,event_ptr:i32,event_capacity:i32)->i32。request_length=16，event_capacity=48。成功返回48，NONBLOCK未就绪或有界wait slice未产生事件返回0，非法输入-2、服务/传输错误-3、额度或计数耗尽-4。0或负状态不得写event buffer。

无native pointer。先完整复制request，再写event，允许合法Wasm memory重叠，不依赖原生对齐。指针、长度、加法与memory范围在worker进入RPC前检查；parent再次校验内容和版本。沿用现有Rpc.id及单worker同步调用的一次未决请求，不另加可伪造的guest ticket。

Request16布局：u32 version=1（offset0）；u32 flags（4，FRAME_WANTED=1、NONBLOCK=2，其余bit非法）；i64 earliest_deadline_ns（8，-1表示无timer，其余必须>=0）。

Event48布局：u32 version=1（0）；u32 kind（4，FRAME=1、TIMER=2、INPUT=3、CLOSE=4、CANCEL=5）；i64 now_ns（8）；u64 frame_sequence（16）；i32 input[4]（24..39）；u64 reserved=0（40）。只有FRAME递增frame_sequence；其他事件携带最近的frame sequence，可为0。INPUT、CLOSE、CANCEL才使用input数组；纯frame/timer数组全0。CLOSE/CANCEL的input[0]=0。

## 等待与公平性

close/cancel优先，resize等控制输入及时交付；普通input burst有界，不能令已到期timer/frame永久饥饿。timer与frame同时到期时可交付真正已到期的FRAME，driver先运行到期timer；未到frame机会的timer不能被伪装成FRAME。

一次阻塞slice最多约16.7ms，并受剩余app deadline约束；不修改现有run/view时限、fuel、请求数或memory限额。FRAME按实际上次交付时间限速，迟到不补发一串过期frame。NONBLOCK不得sleep。单调时间不得倒退，frame sequence与u128到i64转换checked，普通错误不能破坏最后成功图形frame。

native input仍来自ViewBridge，payload与旧input编码一致。headless明确close事件仍优先；显式idle8可作为有界yield机会，不能伪造时间。若blocking等待没有frame/timer工作且headless输入已EOF，返回CLOSE；NONBLOCK无工作返回0。请求frame或timer时headless也可获得真实clock机会，供有限Wasm合同使用。该规则必须在unit与真实Wasm测试中核对，不以忙等消耗CPU。

新增frame-clock统计按实际调用/交付记录，与framesSubmitted、framesPresented和presentedFrame独立。不得把clock pulse计成真实呈现。

## Kotlin与Compose集成

先实现低层MingFrames读取/等待包装及真实Wasm ABI合同。后续将已验证的bounded dispatcher/Delay/parent clock转为正式候选：ready128、timer64、frame waiter32、error16、单次pump4096步；overflow/取消/迟到都显式处理。

真实parent clock必须进入运行Recomposer.runRecomposeAndApplyChanges的协程上下文；Recomposer自身在effect context安装内部BroadcastFrameClock。使用官方Recomposer loop处理Snapshot/apply，不能用手工ControlledComposition.recompose循环替代异步支持。两次apply阶段后仍检查32 committed节点，transient64，Renderer资源事务不变。

保留现有同步MingApplication/SDK4行为。初版增加独立framed入口和SDK5真实示例，以避免旧编译源引入新imports；不在coroutines缺省服务中直接引用SDK5，从而不让legacy同步应用隐式依赖新能力。源码coroutines变体的构建/ABI身份与依赖替换必须用真实编译核对，不能同时链接两个冲突模块。

## 验收

- 纯clock状态机与packet：version/flags/negative deadline/overflow、min frame interval、timer不得broadcast、NONBLOCK、公平性、close/cancel、0/error不写memory。
- 真Wasm：SDK4及更旧拒绝新导入，SDK5精确签名，坏ptr/容量/重叠/字节序、0与负返回、时间单调、实际timer/frame、请求/fuel/时限不放宽，旧SDK回归。
- 真实异步model：remember/state、LaunchedEffect、withFrameNanos、delay/timeout、取消/异常、onDispose、容量/clock重复和倒退；source/typedimports/原始日志绑定。
- 真实Kotlin动画示例CPU/GPU终态精确对照、普通native零读回、呈现身份与clock统计分开、Linux/Wayland；不将确定性虚拟时间当真实帧钟。
- 留存6C当前快照和Esc未完成项。帧时钟完成后继续输入/布局与桌面、平台隔离、可启动Linux镜像；完整OS未完成前不结束goal。
