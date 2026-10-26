import ming.sdk.*
import kotlin.math.abs
import kotlin.math.min

fun main() {
 val file = MingFiles.open(1)
 if (file < 0) { MingLog.event(5,file); return }
 val header = MingBuffers.read(file,0,32)
 MingLog.event(21,header.count)
 val image = MingImages.open(file)
 MingFiles.close(file)
 if (image < 0) { MingLog.event(6,image); return }
 val imageWidth = MingImages.width(image); val imageHeight = MingImages.height(image)
 if (imageWidth < 1 || imageHeight < 1) { MingImages.close(image); return }
 var width = 1100; var height = 760; var zoom = 1.0; var panX = 0; var panY = 0; var canPan = false; var frames = 0
 fun fit(): Double = min((width - 48).toDouble()/imageWidth,(height - 136).toDouble()/imageHeight)
 fun draw(): Int {
  val f = MingFrame(width,height)
  val bg = 0xff11151b.toInt(); val surface = 0xff1a2029.toInt(); val ink = 0xffe6edf5.toInt(); val muted = 0xff91a0b4.toInt(); val accent = 0xffa8d9bd.toInt()
  f.rect(0,0,width,height,bg)
  f.rect(0,0,width,64,0xff171d25.toInt())
  f.text(24,24,if(width < 500) "PHOTO" else "MING PHOTO",ink)
  f.rect(width-208,16,72,32,if(abs(zoom-1.0)<0.001) 0xff304c42.toInt() else surface,8)
  f.text(width-190,25,"FIT",if(abs(zoom-1.0)<0.001) accent else muted)
  f.rect(width-124,16,100,32,surface,8); f.text(width-98,25,"100%",muted)
  val scale = fit() * zoom
  val dw = (imageWidth * scale).toInt().coerceAtLeast(1); val dh = (imageHeight * scale).toInt().coerceAtLeast(1)
  val x = (width-dw)/2+panX; val y = 80+(height-136-dh)/2+panY
  f.rect(24,80,width-48,height-136,0xff0c1016.toInt(),12)
  f.clip(24,80,width-48,height-136)
  f.rect(x+5,y+8,dw,dh,0x55000000,0)
  f.image(image,x,y,dw,dh)
  f.clip(0,0,width,height)
  f.text(24,height-32,"COASTAL LIGHT",ink)
  val percent = (scale*100).toInt().toString()+"%"
  f.text(width-24-percent.length*12,height-32,percent,muted)
  if(width > 900) f.text(width/2-132,height-32,"SCROLL . DRAG . F TO FIT",muted,2)
  val result = f.submit(); if(result==0) frames++
  return result
 }
 if (draw() < 0) { MingLog.event(9,-2); MingImages.close(image); return }
 loop@ while(true) {
  val event = MingInput.next()
  when(event.type) {
   0 -> break@loop
   1 -> { width=event.a.coerceIn(320,4096);height=event.b.coerceIn(240,4096);if(width.toLong()*height>4194304)height=4194304/width }
   2 -> { val steps=(abs(event.a)/100).coerceIn(1,8); repeat(steps) { zoom=if(event.a>0) zoom*1.12 else zoom/1.12 };zoom=zoom.coerceIn(0.15,8.0);MingLog.event(31,(zoom*100).toInt()) }
   3 -> { if(!canPan) continue@loop;panX=(panX+event.a).coerceIn(-50000,50000);panY=(panY+event.b).coerceIn(-50000,50000);MingLog.event(32,panX) }
   4 -> { canPan=event.b>=80&&event.b<height-56;if(event.b<64&&event.a>=width-208){zoom=if(event.a<width-136)1.0 else 1.0/fit();panX=0;panY=0}else continue@loop }
   6 -> { zoom=1.0/fit();panX=0;panY=0 }
   7 -> { zoom=1.0;panX=0;panY=0 }
   else -> continue@loop
  }
  if(draw()<0){MingLog.event(9,-2);break@loop}
 }
 MingImages.close(image);MingLog.event(40,frames)
}
