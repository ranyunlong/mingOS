import ming.sdk.*

private fun take(frame:Boolean, deadline:Long?=null):MingFrameEvent {
    repeat(64) {
        val result=MingFrames.wait(frame,deadline)
        check(result.code==0)
        result.event?.let{return it}
    }
    error("FRAME_LIVE_NO_EVENT")
}
fun main() {
    val first=MingFrames.monotonicNanos()
    check(first.code==0)
    val start=checkNotNull(first.nanos)
    check(start>=0)
    val deadline=if(start>Long.MAX_VALUE-5_000_000)Long.MAX_VALUE else start+5_000_000
    val timer=take(false,deadline)
    check(timer.kind==MingFrameKind.TIMER && timer.nowNanos>=deadline && timer.frameSequence==0L)
    MingLog.event(901,1)
    val a=take(true)
    val b=take(true)
    check(a.kind==MingFrameKind.FRAME && b.kind==MingFrameKind.FRAME)
    check(a.frameSequence==1L && b.frameSequence==2L && b.nowNanos-a.nowNanos>=16_666_667L)
    MingLog.event(902,1)
    val pending=MingFrames.wait(false,Long.MAX_VALUE,true)
    check(pending.code==0 && pending.event==null)
    MingLog.event(903,1)
    val invalid=MingFrames.wait(false,-1L,true)
    check(invalid.code==MingFrames.INVALID && invalid.event==null)
    MingLog.event(904,1)
    val last=MingFrames.monotonicNanos()
    check(last.code==0 && checkNotNull(last.nanos)>=b.nowNanos)
    MingLog.event(905,1)
    MingLog.event(999,1)
}
