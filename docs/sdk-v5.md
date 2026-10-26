# SDK5 HostFrame（实验）

更新2026-10-25。应用仍为Kotlin/Wasm，由Rust/Wasmtime执行。SDK5继承SDK1..4，v4.frame_submit仍按SDK4协议解码；旧v2/v3提交入口继续拒绝AA/shadow新命令。SDK1..4行为及既有包保持兼容。

## 时间与等待

ming_frame_v1仅对SDK5开放，所有参数和结果为i32。clock_monotonic_ns(out,capacity8)成功写8字节LE非负i64并返回8。wait(request,length16,event,capacity48)成功48、pending0、非法-2、服务错误-3、额度/时限-4。0/error不得写event；有效重叠或未对齐Wasm内存允许，request先完整复制。坏ptr/容量在worker进入RPC前拒绝；parent再次校验。

Request16：version u32=1，flags u32（FRAME_WANTED1、NONBLOCK2），earliest_deadline_ns i64（-1无timer，其余>=0）。Event48：version u32=1、kind u32（FRAME1/TIMER2/INPUT3/CLOSE4/CANCEL5）、now_ns i64、frame_sequence u64、input[4] i32、reserved u64=0。只有FRAME递增frame_sequence；其他类型可携0。纯frame/timer及close/cancel的input全0。

时间起点为app/session内的Instant，非wall clock。每次阻塞slice至多约16.7ms且受剩余app预算限制；NONBLOCK不sleep，过期不再交付事件。FRAME按实际交付限速，迟到不补发旧frame，不代表hardware vsync或scanout。timer不能被伪装为尚未到期的frame；同时到期时FRAME可唤醒driver处理timer。

input burst8后先处理到期时间事件，不提前取走下一条input，保证新wait与继承event_next共享FIFO。close/cancel优先。Headless无待处理输入且没有frame/timer工作时blocking wait可返回CLOSE EOF；idle8仅提供有界yield，不伪造时间。

~~~kotlin
val time = MingFrames.monotonicNanos() // ClockReadResult(code, nanos?)
val result = MingFrames.wait(wantFrame = true, deadlineNs = null)
if (result.code == 0 && result.event != null) {
    // kind: FRAME/TIMER/INPUT/CLOSE/CANCEL; nowNanos/frameSequence/input
}
~~~

## 真正Compose异步入口

MingFramedApplication.run使用官方Recomposer.runRecomposeAndApplyChanges、受控FrameDispatcher/Delay和真正MonotonicFrameClock。父clock进入runner协程上下文；Recomposer给effect context安装内部BroadcastFrameClock。旧MingApplication仍为SDK4同步入口，不隐式依赖新imports。

~~~kotlin
val result = MingFramedApplication.run {
    var visible by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { delay(80); withFrameNanos { }; visible = true }
    MingColumn { MingText(if (visible) "完成" else "等待") }
}
~~~

owned ready128/timer64/frame waiter32/error16，单次pump4096步。deadline从fresh host时间计算，event now只在交付事件时推进；取消和close不执行late body。32 committed/64 transient节点、16 committed+16 staged layout及旧图形预算不变。没有一般FDpolling、任意clock ID或WASI权限扩展。

官方Runtime1.10源码port与coroutines1.9源码资格变体均保留许可证、来源与最小修改。默认平台服务显式unsupported，正常协程必须继承owned dispatcher/Delay/error context。single-thread、strong weakref fallback和Kotlin内部identity版本耦合仍是实验限制。

证据、已执行命令和真实动画结果见animated-verification.md；物理鼠标专项按用户Escape停止状态未执行。SDK5clock-only可用engine-only宿主，ming_v3/v4导入仍要求text feature；旧SDK3/4整体text门控未改变。
