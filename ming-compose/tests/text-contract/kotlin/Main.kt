package ming.compose

import androidx.compose.runtime.*
import ming.sdk.*

private var checks=0
private fun verify(ok:Boolean, message:String) { checks++; check(ok) { message } }
private fun packet(kind:Int, seq:Long, epoch:Long, source:Long=7, text:String="", a:Int=0,b:Int=0,c:Int=0,mods:Int=0):TextInputEvent {
    val t=text.encodeToByteArray(); val bytes=ByteArray(56+t.size)
    fun word(o:Int,v:Int) { repeat(4){ bytes[o+it]=(v ushr (it*8)).toByte() } }
    fun wide(o:Int,v:Long) { word(o,v.toInt());word(o+4,(v ushr 32).toInt()) }
    word(0,1);word(4,kind);wide(8,seq);wide(16,epoch);wide(24,source);word(32,a);word(36,b);word(40,c);word(44,mods);word(48,t.size);t.copyInto(bytes,56)
    return checkNotNull(MingTextInput.decode(bytes).event)
}
private fun fieldContract(mode:Int) {
    val first=TextFieldState(TextEditState("A中😀B"))
    val second=TextFieldState()
    val hidden=mutableStateOf(false)
    val pad=mutableStateOf(0)
    val c=MingComposition();val r=MingRenderer();val receiver=TextInputReceiver(r)
    fun paint(w:Int=160,h:Int=180):Int { c.recompose();return r.render(c.root,w,h) }
    try {
        c.setContent {
            MingColumn(padding=pad.value,gap=0) {
                if(!hidden.value) {
                    MingTextField(first,placeholder="第一个",emPx=16)
                    MingTextField(second,placeholder="第二个",emPx=16)
                }
            }
        }
        verify(r.publishedCaret==null,"caret before submit")
        val initial=paint()
        if(mode==1||mode==2||mode==3) {
            verify(initial<0,"expected staged failure")
            verify(r.textTargetCount==0&&r.publishedCaret==null,"failed frame published targets/caret")
            return
        }
        verify(initial==0&&r.textTargetCount==2,"real Compose field insertion")
        verify(receiver.afterDraw()==0,"initial inactive focus")
        verify(receiver.receive(packet(4,1,2,a=4,b=10,c=1))==0,"pointer press")
        verify(first.isFocused&&!second.isFocused,"first focus")
        verify(r.publishedCaret==null,"unsubmitted caret must stay absent")
        val accepted=paint()
        if(mode==4) {
            verify(accepted<0&&r.publishedCaret==null&&r.textTargetCount==2,"submit rollback")
            return
        }
        verify(accepted==0&&r.publishedCaret!=null,"caret only after accepted frame")
        verify(receiver.afterDraw()==0,"active focus hint")
        if(mode==11) {
            val old=checkNotNull(r.publishedCaret);pad.value=24
            verify(paint()<0,"reject changed geometry")
            verify(r.publishedCaret==old&&r.textTargetCount==2,"rejected frame retained old caret/targets")
            verify(r.pointer(4,10,1,0)==0&&first.isFocused,"old hit geometry remains live")
            return
        }
        receiver.receive(packet(1,2,1,text="WRONG"))
        verify(first.value.text=="A中😀B","genuinely older epoch IME commit ignored")
        receiver.receive(packet(2,3,2,text="中文😀",a=3,b=10))
        verify(first.value.preedit?.cursorStart==1&&first.value.preedit?.cursorEnd==4&&first.value.text=="A中😀B","preedit not committed")
        verify(paint()==0&&receiver.afterDraw()==0,"preedit render")
        receiver.receive(packet(5,4,2,source=0,a=3))
        verify(first.value.preedit==null&&first.value.text=="A中😀B","cancel preserves committed")
        receiver.receive(packet(1,5,2,text="你"))
        verify(first.value.text=="你A中😀B","Chinese commit inserts at measured caret")
        receiver.receive(packet(1,5,2,text="duplicate"));receiver.receive(packet(1,6,1,text="stale"))
        receiver.receive(packet(4,6,2,source=6,a=4,b=10,c=1))
        verify(first.value.text=="你A中😀B","stale/duplicate/source fence")
        receiver.receive(packet(3,6,2,a=10,b=1,mods=2))
        verify(first.value.selectedText==first.value.text,"control A")
        receiver.receive(packet(1,7,2,text="😀"))
        receiver.receive(packet(3,8,2,a=1,b=1))
        verify(first.value.text=="","backspace preserves full scalar")
        receiver.receive(packet(1,9,2,text="A中😀B"))
        receiver.receive(packet(3,10,2,a=5,b=1))
        receiver.receive(packet(3,11,2,a=4,b=1,mods=1))
        verify(first.value.selectedText=="A","shift selection")
        for(k in listOf(11,12,13))receiver.receive(packet(3,(12+k).toLong(),2,a=k,b=1,mods=2))
        verify(first.value.text=="A中😀B","no fake clipboard")
        receiver.receive(packet(1,26,2,text="旧"))
        receiver.receive(packet(5,27,3,source=0,a=1))
        receiver.receive(packet(3,30,3,a=9,b=1))
        verify(second.isFocused&&!first.isFocused,"Tab changes field")
        receiver.receive(packet(1,31,2,source=6,text="LATE OLD IME"))
        verify(second.value.text=="","late old Native epoch does not leak after Tab boundary")
        verify(paint()==0&&receiver.afterDraw()==0,"second focus committed")
        receiver.receive(packet(1,32,3,source=8,text="第二"))
        verify(second.value.text=="第二"&&first.value.text=="旧中😀B","field isolation and old committed FIFO")
        first.value=TextEditState("A中😀B")
        receiver.receive(packet(3,33,3,source=8,a=9,b=1,mods=1))
        verify(first.isFocused&&!second.isFocused,"Shift Tab")
        verify(paint()==0&&receiver.afterDraw()==0,"first focus again")
        receiver.receive(packet(4,34,4,source=9,a=4,b=10,c=1))
        receiver.receive(packet(4,35,4,source=9,a=35,b=10,c=2))
        receiver.receive(packet(4,36,4,source=9,a=35,b=10,c=3))
        verify(first.value.selectedText=="A中","drag uses Q6 prefix measurements, Chinese width differs")
        verify(paint()==0&&r.publishedCaret?.x==33&&r.focusRect==r.publishedCaret,"fractional Q6 prefix caret and IME rectangle")
        val before=first.value
        receiver.receive(packet(3,37,4,source=9,a=7,b=1));receiver.receive(packet(3,38,4,source=9,a=1,b=0))
        verify(first.value===before,"Enter and keyup no edit")
        first.value=TextEditState("A😀中B")
        receiver.receive(packet(3,39,4,source=9,a=5,b=1))
        receiver.receive(packet(3,40,4,source=9,a=4,b=1))
        verify(first.value.caret==1,"Right uses scalar boundary")
        receiver.receive(packet(3,41,4,source=9,a=2,b=1))
        verify(first.value.text=="A中B","Delete removes whole emoji")
        receiver.receive(packet(3,42,4,source=9,a=6,b=1))
        receiver.receive(packet(3,43,4,source=9,a=3,b=1,mods=1))
        verify(first.value.selectedText=="B","End and Shift Left")
        first.value=TextEditState("中".repeat(170)+"ab")
        receiver.receive(packet(1,60,4,source=9,text="x"))
        verify(first.value.text.encodeToByteArray().size==512,"overflow retains text")
        verify(paint(18,40)==0&&receiver.afterDraw()==0,"narrow viewport")
        val caret=checkNotNull(r.publishedCaret)
        verify(caret.x>=0&&caret.x+caret.width<=18&&caret.y>=0&&caret.y+caret.height<=40,"caret in source")
        verify(paint(1,1)==0&&receiver.afterDraw()==0,"minimum viewport")
        verify(r.publishedCaret==null&&r.textTargetCount==0,"invisible interior has no caret or targets")
        verify(paint()==0&&receiver.afterDraw()==0,"viewport recovery")
        hidden.value=true
        verify(paint()==0&&receiver.afterDraw()==0,"remove fields")
        verify(r.publishedCaret==null&&r.textTargetCount==0&&!first.isFocused&&!second.isFocused,"remove clears caret and focus")
    } finally { receiver.cleanup();c.dispose();verify(r.dispose()==0,"resource cleanup");verify(r.dispose()==0,"idempotent dispose") }
    verify(!first.isFocused&&!second.isFocused,"dispose clears state focus")
}
private fun appContract(mode:Int) {
    val first=TextFieldState();val second=TextFieldState()
    val result=MingTextApplication.run {
        MingColumn(padding=0,gap=0) { MingTextField(first,"first",16);MingTextField(second,"second",16) }
    }
    verify(result.code==0,"SDK6 app exits on close")
    verify(result.frames>=2,"async redraw")
    verify(first.value.text==(if(mode==12)"中文😀旧" else "中文😀")&&second.value.text=="B","instance FIFO fields")
    verify(!first.isFocused&&!second.isFocused&&first.value.preedit==null,"app cleanup")
}
private fun budgetContract(mode:Int) {
    val states=List(if(mode==21)5 else 4){i->TextFieldState(TextEditState("$i"+"a".repeat(100)).preedit("中".repeat(70)+"$i",10,20))}
    val revision=mutableStateOf(0);val c=MingComposition();val r=MingRenderer()
    try {
        c.setContent { MingColumn(padding=0,gap=0) {
            states.forEach { MingTextField(it,emPx=16) }
            if(mode!=21)repeat(12){MingText("leaf:$it:"+revision.value,emPx=16)}
        } }
        val initial=r.render(c.root,200,1000)
        if(mode==21)verify(initial==-4&&r.textTargetCount==0,"fifth field rejects before allocation")
        else {
            verify(initial==0&&r.textTargetCount==4,"4 fields and 12 other leaves")
            states.forEachIndexed{i,s->s.value=TextEditState("new$i"+"b".repeat(100)).preedit("文".repeat(70)+"new$i",11,21)}
            revision.value++;c.recompose()
            verify(r.render(c.root,200,1000)==0,"64-live staged replacement budget")
            verify(r.pointer(40,10,1,0)==0,"temporary exact prefix hit within live budget")
        }
    } finally {c.dispose();verify(r.dispose()==0,"budget cleanup")}
}
private fun persistenceContract(mode:Int) {
    val result=when(mode) {
        30->MingApplication.run {MingText("bounded")}
        31->MingApplication.runPersistent {MingText("persistent")}
        32->MingFramedApplication.run {MingText("bounded")}
        33->MingFramedApplication.runPersistent {MingText("persistent")}
        else->MingTextApplication.run {MingText("persistent SDK6")}
    }
    verify(result.code==if(mode==30||mode==32)-4 else 0,"old bound versus persistent close")
    verify(result.frames==1,"idle does not repaint")
}
private fun fastInputContract(mode:Int) {
    val first=TextFieldState(if(mode in 53..57)TextEditState("ABCD",0,0) else TextEditState())
    val second=TextFieldState(TextEditState("你好 MingOS"))
    val c=MingComposition();val r=MingRenderer();val receiver=TextInputReceiver(r)
    var seq=0L
    fun send(kind:Int,ep:Long=1,text:String="",a:Int=0,b:Int=0,phase:Int=0,source:Long=7) {
        verify(receiver.receive(packet(kind,++seq,ep,source=source,text=text,a=a,b=b,c=phase))==0,"fast receive")
    }
    fun paint(){c.recompose();verify(r.render(c.root,720,448)==0,"fast frame");verify(receiver.afterDraw()==0,"fast hint")}
    try {
        c.setContent {MingColumn(padding=36,gap=16){MingText("",emPx=8);MingTextField(first,emPx=16);MingTextField(second,emPx=16)}}
        paint()
        when(mode) {
            50,51 -> {
                if(mode==51){send(5,2,a=1);send(5,5,a=1)}
                send(4,if(mode==51)5 else 1,a=40,b=80,phase=1)
                send(1,if(mode==51)5 else 1,text="甲A")
                if(mode==51){send(5,7,a=1);send(5,11,a=1)}
                send(4,if(mode==51)11 else 1,a=40,b=128,phase=1)
                send(1,if(mode==51)11 else 1,text="乙B")
                verify(first.value.text=="甲A","same source fresh first commit lost: "+first.value.text)
                verify(second.value.text=="乙B你好 MingOS","same source fresh second commit lost/routed to wrong field: "+second.value.text)
            }
            52 -> {
                send(4,a=40,b=80,phase=1);paint()
                send(1,text="旧") // queued committed precedes the press boundary, so keep old owner.
                send(5,2,a=1);send(4,2,a=40,b=128,phase=1);send(1,2,text="乙B")
                send(1,1,text="LATE OLD IME")
                send(2,2,text="中文",a=0,b=6)
                paint()
                verify(first.value.text=="旧","old committed FIFO retained")
                verify(second.value.text=="乙B你好 MingOS","new epoch belongs to new logical field")
                verify(second.value.preedit?.text=="中文","fresh preedit survives already-fenced field announcement")
            }
            53,56 -> {
                send(4,a=40,b=80,phase=1);paint()
                if(mode==56){send(4,a=57,b=80,phase=2);paint()}
                verify(receiver.drain(InputEvent(1,720,448,0))==0,"same-size rich wake")
                send(4,a=70,b=80,phase=2);send(4,a=70,b=80,phase=3)
                verify(first.value.selectedText=="ABC","same-size wake cleared drag: "+first.value.selectedText)
            }
            54 -> {
                send(4,a=40,b=80,phase=1);paint()
                first.value=first.value.preedit("中文",0,2);paint()
                val prior=first.value
                verify(receiver.drain(InputEvent(1,720,448,0))==0,"preedit rich wake")
                verify(first.value===prior&&first.value.preedit?.text=="中文","same-size wake cleared preedit")
            }
            55 -> {
                send(4,a=40,b=80,phase=1);paint()
                first.value=first.value.preedit("中文",0,2)
                receiver.viewportChanged()
                verify(receiver.drain(InputEvent(1,700,440,0))==0,"actual resize callback")
                verify(first.value.preedit==null&&first.value.text=="ABCD","true resize cancels preedit, keeps committed")
                send(4,a=70,b=80,phase=2)
                verify(first.value.selectedText=="","true resize cancels drag")
            }
            58 -> {
                send(4,a=40,b=80,phase=1,source=101);paint()
                send(1,text="中文😀",source=102);paint()
                send(2,text="候选",a=0,b=6,source=103);paint()
                send(5,2,a=3,source=104)
                send(4,2,a=40,b=128,phase=1,source=105);send(1,2,text="B",source=105)
                verify(first.value.text=="中文😀","review: fresh Latin must not route to announced A")
                verify(second.value.text=="B你好 MingOS","review: B receives same-batch same-source Latin")
            }
            57 -> {
                first.value=TextEditState();paint()
                send(4,a=40,b=80,phase=1);send(1,text="ABCD")
                send(4,a=40,b=80,phase=1);send(1,text="X")
                verify(first.value.text=="XABCD","fresh text plus second click uses current scalar text/Q6 caret: "+first.value.text)
            }
        }
    } finally {receiver.cleanup();c.dispose();verify(r.dispose()==0,"fast resources close")}
}

private fun richWakeContract(mode:Int) {
    val first=TextFieldState(TextEditState("中文😀"))
    val r=MingRenderer();val receiver=TextInputReceiver(r)
    var prepared=false;var wakes=0;var resizes=0;var seq=0L
    fun pointer(x:Int,phase:Int){verify(receiver.receive(packet(4,++seq,1,source=101,a=x,b=10,c=phase))==0,"review rich pointer")}
    val result=runFramed(32,r,
        onInput={input->
            if(!prepared){prepared=true;pointer(4,1);if(mode==59)first.value=TextEditState("中文😀").preedit("候选",0,2)}
            val code=receiver.drain(input)
            if(input?.type==1){
                wakes++
                if(mode==59){
                    when(wakes){
                        1,2->verify(first.value.preedit?.text=="候选","review: same-size rich wake removed preedit")
                        3->{verify(first.value.preedit==null,"real kernel resize clears preedit");first.value=first.value.preedit("新",0,1)}
                        4->verify(first.value.preedit?.text=="新","repeat adopted size cancels only once")
                    }
                }else{
                    if(wakes==2){pointer(50,2);verify(first.value.selectedText=="中文","review: cross-rich-wake drag lost capture")}
                    if(wakes==3){pointer(70,2);verify(first.value.selectedText=="中文","real resize releases drag capture")}
                }
            }
            code
        },afterDraw=receiver::afterDraw,cleanup=receiver::cleanup,
        onViewportChanged={resizes++;receiver.viewportChanged()},
        content={MingColumn(padding=0,gap=0){MingTextField(first,emPx=16)}})
    verify(result.code==0&&wakes==4&&resizes==1,"kernel adopts exactly one normalized viewport change")
}
private fun fastEntryContract() {
    val first=TextFieldState();val second=TextFieldState(TextEditState("你好 MingOS"))
    val result=MingTextApplication.run {
        MingColumn(padding=36,gap=16){MingText("",emPx=8);MingTextField(first,emPx=16);MingTextField(second,emPx=16)}
    }
    verify(result.code==0,"bulk fast FIFO app close")
    verify(first.value.text=="甲A","bulk fast FIFO name: "+first.value.text)
    verify(second.value.text=="乙B你好 MingOS","bulk fast FIFO note: "+second.value.text)
    verify(!first.isFocused&&!second.isFocused,"bulk FIFO cleanup")
}

fun main() {
    val mode=MingInput.next().a
    try {
        when {mode==61->fastEntryContract();mode==59||mode==60->richWakeContract(mode);mode in 50..58->fastInputContract(mode);mode>=30->persistenceContract(mode);mode==20||mode==21->budgetContract(mode);mode==10||mode==12->appContract(mode);else->fieldContract(mode)}
        MingLog.event(960,checks)
        MingLog.event(999,1)
    } catch(failure:Throwable) {
        MingLog.event(980,mode)
        (failure.message?:"unknown").forEach{MingLog.event(981,it.code)}
        MingLog.event(999,0)
    }
}
