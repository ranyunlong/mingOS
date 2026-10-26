package qualification

import ming.sdk.*

private var assertions = 0
private fun expect(ok: Boolean, label: String) { assertions++; check(ok) { label } }
private fun done(code: Int) { MingLog.event(code, assertions) }
private fun packet(kind: Int = 1, text: ByteArray = "中".encodeToByteArray(), a: Int = 0, b: Int = 0, c: Int = 0): ByteArray {
    val bytes = ByteArray(56 + text.size)
    word(bytes, 0, 1); word(bytes, 4, kind); word(bytes, 8, 1); word(bytes, 16, 2); word(bytes, 24, 3)
    word(bytes, 32, a); word(bytes, 36, b); word(bytes, 40, c); word(bytes, 48, text.size)
    text.copyInto(bytes, 56)
    return bytes
}
private fun word(bytes: ByteArray, offset: Int, value: Int): ByteArray {
    repeat(4) { bytes[offset + it] = (value ushr (it * 8)).toByte() }; return bytes
}
private fun event(bytes: ByteArray): TextInputEvent {
    val r = MingTextInput.decode(bytes)
    expect(r.code == 0 && r.event != null, "expected valid event")
    return r.event!!
}
private fun invalid(bytes: ByteArray, length: Int = bytes.size) {
    val r = MingTextInput.decode(bytes, length)
    expect(r.code == -2 && r.event == null, "malformed packet accepted (length=$length)")
}
private fun codec() {
    val p = packet(); val e = event(p)
    expect(e.kind == TextInputKind.COMMIT && e.text == "中", "commit decode")
    expect(e.sequence == 1L && e.epoch == 2L && e.frameSequence == 3L, "header decode")
    p[56] = 65; expect(e.text == "中", "String must detach from buffer")
    val endian = packet(); word(endian,8,0xfedcba98.toInt()); word(endian,12,0x01234567)
    word(endian,16,-1); word(endian,20,0x7fffffff)
    val ee = event(endian)
    expect(ee.sequence == 0x01234567fedcba98L && ee.epoch == Long.MAX_VALUE, "LE unsigned low word")
    event(word(word(packet(),24,-1),28,0x7fffffff))
    for (s in listOf("\u0000", "\u007f", "\u0080", "\u07ff", "\u0800", "\ud7ff", "\ue000", "\uffff", "\ud800\udc00", "\udbff\udfff", "中文A😀")) {
        expect(event(packet(text=s.encodeToByteArray())).text == s, "valid scalar UTF8")
    }
    for (hex in listOf(listOf(0x80),listOf(0xc0,0x80),listOf(0xc1,0xbf),listOf(0xc2),listOf(0xc2,0x20),listOf(0xe0,0x80,0x80),listOf(0xe1,0x80),listOf(0xed,0xa0,0x80),listOf(0xed,0xbf,0xbf),listOf(0xf0,0x80,0x80,0x80),listOf(0xf0,0x90,0x80),listOf(0xf4,0x90,0x80,0x80),listOf(0xf5,0x80,0x80,0x80),listOf(0xff))) {
        invalid(packet(text=hex.map { it.toByte() }.toByteArray()))
    }
    for (n in listOf(0,1,55)) invalid(ByteArray(n))
    invalid(packet(), Int.MIN_VALUE); invalid(packet(), Int.MAX_VALUE); invalid(packet(), -1); invalid(packet(), 0); invalid(packet(), 58); invalid(packet(), 60)
    invalid(ByteArray(569)); invalid(packet(text=ByteArray(513){65}))
    event(packet(text=ByteArray(512){65})); invalid(packet(text=byteArrayOf()))
    for (offset in listOf(0,4,52,44,48,32,36,40)) {
        val value = when(offset) { 0 -> 2; 4 -> 6; 44 -> 16; 48 -> -1; else -> 1 }
        invalid(word(packet(),offset,value))
    }
    invalid(word(packet(),4,0)); invalid(word(packet(),4,-1));
    invalid(word(packet(),48,0)); invalid(word(packet(),48,4))
    for (offset in listOf(8,16,24)) {
        invalid(word(packet(),offset,0)); invalid(word(packet(),offset+4,Int.MIN_VALUE))
    }
    for (flags in 0..15) expect(event(word(packet(),44,flags)).modifiers == flags, "known modifier bits")
    for (flag in listOf(16,32,Int.MIN_VALUE,-1)) invalid(word(packet(),44,flag))
    val utf = "A中😀Z".encodeToByteArray() // byte boundaries 0,1,4,8,9; UTF16 0,1,2,4,5
    for ((byte,unit) in listOf(0 to 0,1 to 1,4 to 2,8 to 4,9 to 5)) {
        val v=event(packet(2,utf,byte,9)); expect(v.cursorStart == unit && v.cursorEnd == 5, "byte->UTF16 range")
    }
    val padded=packet().copyOf(568); expect(MingTextInput.decode(padded,59).event?.text == "中","explicit length excludes unused capacity")
    val hidden=event(packet(2,utf,-1,-1)); expect(hidden.cursorStart == -1 && hidden.cursorEnd == -1,"hidden cursor")
    event(packet(2,byteArrayOf(),0,0)); event(packet(2,byteArrayOf(),-1,-1))
    for ((a,b) in listOf(-1 to 0,0 to -1,-2 to -2,2 to 4,4 to 7,9 to 10,8 to 4)) invalid(packet(2,utf,a,b))
    invalid(packet(2,utf,0,9,1))
    for (key in 1..15) for (down in 0..1) for (repeat in 0..1) event(packet(3,byteArrayOf(),key,down,repeat))
    for ((a,b,c) in listOf(0 toTriple 1,16 toTriple 1,1 toTriple 2)) invalid(packet(3,byteArrayOf(),a,b,c))
    invalid(packet(3,byteArrayOf(),1,1,2)); invalid(packet(3,"a".encodeToByteArray(),1,1,0))
    for (phase in 0..3) { event(packet(4,byteArrayOf(),0,4095,phase)); event(packet(4,byteArrayOf(),-1,-1,phase)) }
    for ((a,b,c) in listOf(Triple(-1,0,0),Triple(0,-1,0),Triple(4096,0,0),Triple(0,4096,0),Triple(0,0,4),Triple(-2,-2,0))) invalid(packet(4,byteArrayOf(),a,b,c))
    invalid(packet(4,"x".encodeToByteArray(),0,0,0))
    for (reason in 1..4) { val v=event(word(packet(5,byteArrayOf(),reason),24,0)); expect(v.frameSequence == 0L,"cancel zero source") }
    for ((a,b,c) in listOf(Triple(0,0,0),Triple(5,0,0),Triple(1,1,0),Triple(1,0,1))) invalid(packet(5,byteArrayOf(),a,b,c))
    invalid(packet(5,"x".encodeToByteArray(),1)); invalid(word(packet(5,byteArrayOf(),1),28,Int.MIN_VALUE))
    done(901)
}
private infix fun Int.toTriple(b: Int) = Triple(this,b,0)
private fun editing() {
    val empty=TextEditState()
    val chinese=empty.commit("你好")
    expect(chinese.text == "你好" && chinese.caret == 2 && chinese.anchor == 2,"Chinese commit")
    val composing=chinese.select(1,2).preedit("中文😀",1,4)
    expect(composing.text == "你好" && composing.preedit?.text == "中文😀", "preedit preserves committed")
    expect(composing.display().text == "你中文😀" && composing.display().caret == 5,"composed replacement display")
    expect(composing.preedit("").preedit == null && composing.preedit("").text == "你好", "empty preedit clears only composition")
    val cd=composing.display(); expect(cd.preeditStart == 1 && cd.preeditEnd == 5 && cd.cursorStart == 2 && cd.cursorEnd == 5 && cd.startOffset == 0 && cd.fullLength == 5,"display range mapping")
    val cancelled=composing.cancel()
    expect(cancelled.text == "你好" && cancelled.preedit == null && cancelled.anchor == 1 && cancelled.caret == 2,"cancel keeps selection and text")
    val applied=composing.commit("世")
    expect(applied.text == "你世" && applied.caret == 2 && applied.preedit == null,"commit replaces selection")
    expect(applied.cancel() === applied && applied.select(2) === applied,"no change identity")
    expect(applied.select(0,99) === applied && applied.select(-1) === applied,"invalid selection")
    expect(applied.commit("\ud800") === applied && applied.commit("\udfff") === applied,"reject unpaired surrogate")
    expect(applied.preedit("😀",1,2) === applied && applied.preedit("中",-1,0) === applied,"invalid preedit scalar cursor")
    val emoji=TextEditState("A😀中B")
    var s=emoji.key(3); expect(s.caret == 4,"left B")
    s=s.key(3); expect(s.caret == 3,"left Chinese")
    s=s.key(3); expect(s.caret == 1,"left entire surrogate")
    s=s.key(4); expect(s.caret == 3,"right entire surrogate")
    expect(s.select(2) === s,"cannot select inside surrogate")
    expect(s.key(1).text == "A中B" && s.key(1).caret == 1,"backspace entire emoji")
    expect(s.select(1).key(2).text == "A中B", "delete entire emoji")
    expect(emoji.select(1,3).key(1).text == "A中B", "backspace selection")
    expect(emoji.select(3,1).key(2).text == "A中B", "delete reversed selection")
    val shift=emoji.key(3,modifiers=1).key(3,modifiers=1)
    expect(shift.anchor == 5 && shift.caret == 3 && shift.selectionStart == 3 && shift.selectionEnd == 5,"Shift expands selection")
    expect(shift.key(3).caret == 3 && shift.key(3).anchor == 3,"left collapses selection")
    expect(shift.key(4).caret == 5,"right collapses selection")
    val all=emoji.key(10,modifiers=2)
    expect(all.anchor == 0 && all.caret == 5 && all.selectedText == "A😀中B","Ctrl+A")
    expect(all.commit("中").text == "中","replace all")
    expect(emoji.key(5).caret == 0 && emoji.select(0).key(6).caret == 5,"Home End")
    expect(emoji.key(5,modifiers=1).anchor == 5 && emoji.key(5,modifiers=1).caret == 0,"Shift Home")
    expect(emoji.select(0).key(6,modifiers=1).selectedText == emoji.text,"Shift End")
    for(key in listOf(7,9,11,12,13,14,15,999)) expect(emoji.key(key) === emoji,"unsupported key has no edit")
    for(key in 1..15) expect(emoji.key(key,down=false) === emoji,"keyup has no edit")
    for(key in listOf(1,2,3,4,7,9,11,12,13)) expect(emoji.key(key,modifiers=2) === emoji,"no fake control/clipboard")
    for(m in listOf(4,8,16)) expect(emoji.key(1,modifiers=m) === emoji,"unsupported modifier")
    expect(empty.key(1) === empty && empty.key(2) === empty && empty.key(3) === empty && empty.key(4) === empty,"edge no-op identity")
    expect(emoji.key(8) === emoji && composing.key(8).preedit == null,"Escape clears only composition")
    val max=TextEditState("中".repeat(170)+"ab")
    expect(max.text.encodeToByteArray().size == 512,"512 byte exact fixture")
    expect(max.commit("x") === max && max.commit("中") === max,"overflow must preserve original")
    val maxPre=max.preedit("😀".repeat(128),0,256)
    expect(maxPre.preedit != null && maxPre.text == max.text,"independent preedit limit")
    expect(maxPre.preedit("x".repeat(513)) === maxPre,"preedit overflow preserves original")
    expect(maxPre.commit("x") === maxPre,"failed commit preserves composition too")
    expect(max.select(170,172).commit("cd").text == "中".repeat(170)+"cd","replacement budget subtracts selection")
    val selection=max.select(170,172); expect(selection.commit("中") === selection,"overflow replacement retains original")
    val start=maxPre.select(0).preedit("😀".repeat(128),0,0)
    for(v in listOf(maxPre,start,maxPre.preedit("😀".repeat(128),-1,-1))) {
        val d=v.display()
        expect(d.text.encodeToByteArray().size <= 512 && d.caret in 0..d.text.length,"bounded display caret")
        expect(d.text.encodeToByteArray().decodeToString(throwOnInvalidSequence=true)==d.text,"bounded display scalar safe")
        expect(d.selectionStart in 0..d.text.length && d.selectionEnd in 0..d.text.length,"bounded selection")
    }
    val asciiWindow=TextEditState("a".repeat(512)).preedit("b".repeat(512)).display()
    expect(asciiWindow.text == "b".repeat(512) && asciiWindow.caret == 512 && asciiWindow.startOffset == 512 && asciiWindow.fullLength == 1024,"overflow window retains caret context")
    val asciiStart=TextEditState("a".repeat(512),0,0).preedit("b".repeat(512),0,0).display()
    expect(asciiStart.text == "b".repeat(512) && asciiStart.caret == 0 && asciiStart.startOffset == 0,"start cursor window")
    var bad=0
    for (make in listOf<()->TextEditState>({TextEditState("\ud800")},{TextEditState("x".repeat(513))},{TextEditState("😀",1,1)})) try { make() } catch (_: IllegalArgumentException) { bad++ }
    expect(bad == 3,"constructor invariants")
    val pending=chinese.apply(event(packet(2,"中国".encodeToByteArray(),0,6)))
    expect(pending.text == "你好" && pending.preedit?.cursorEnd == 2,"apply preedit conversion")
    expect(pending.apply(event(packet(5,byteArrayOf(),1))).text == "你好","apply cancel")
    expect(pending.apply(event(packet(text="世".encodeToByteArray()))).text == "你好世","apply commit")
    expect(chinese.apply(event(packet(3,byteArrayOf(),1,1,1))).text == "你","apply repeat keydown")
    expect(chinese.apply(event(packet(4,byteArrayOf(),1,2,1))) === chinese,"pointer left to field wrapper")
    done(902)
}
private fun boundary() {
    val pending=MingTextInput.poll(); expect(pending.code == 0 && pending.event == null,"pending buffer not decoded")
    val error=MingTextInput.poll(); expect(error.code == -99 && error.event == null,"error buffer not decoded")
    val commit=MingTextInput.poll(); expect(commit.code == 0 && commit.event?.text == "中文😀","host commit")
    val pre=MingTextInput.poll(); expect(pre.code == 0 && pre.event?.cursorStart == 1 && pre.event?.cursorEnd == 4,"host preedit Chinese byte indices")
    expect(commit.event?.text == "中文😀","poll result detached after next call")
    repeat(3) { val v=MingTextInput.poll(); expect(v.code == -2 && v.event == null,"invalid Host return not decoded") }
    val recovered=MingTextInput.poll();expect(recovered.event?.kind == TextInputKind.CANCEL,"recovery after malformed packet")
    for(args in listOf(listOf(-1,0,1,1),listOf(0,-1,1,1),listOf(0,0,0,1),listOf(0,0,1,0),listOf(4096,0,1,1),listOf(0,4096,1,1),listOf(Int.MAX_VALUE,0,Int.MAX_VALUE,1),listOf(0,0,Int.MIN_VALUE,1))) {
        expect(MingTextInput.textFocus(true,args[0],args[1],args[2],args[3]) == -2,"local focus rejects")
    }
    done(903) // Node asserts zero focus host calls at this point.
    expect(MingTextInput.textFocus(true,0,1,4096,4095) == 0,"valid focus")
    expect(MingTextInput.textFocus(false,-1,Int.MAX_VALUE,-2,99) == 0,"inactive zero rectangle")
    expect(MingTextInput.textFocus(true,4095,4095,1,1) == -3,"host focus error preserved")
    done(904)
}
fun main() { codec(); editing(); boundary(); done(999) }
