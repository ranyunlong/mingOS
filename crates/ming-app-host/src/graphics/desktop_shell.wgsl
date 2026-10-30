// Trusted host postprocess. Every invocation chooses one topmost rectangular
// outer before touching chrome; root content remains unchanged in content areas.
@group(0) @binding(0) var<storage, read> p: array<u32>;
@group(0) @binding(1) var<storage, read> root: array<u32>;
@group(0) @binding(2) var<storage, read_write> output: array<u32>;

const BAR: u32 = 0xffe8ebefu;
const INK: u32 = 0xff26313fu;
const TITLE_BG: u32 = 0xffe2e5e9u;
const FOCUSED: u32 = 0xffe5edfbu;
const BLUE: u32 = 0xff367be8u;
const RED: u32 = 0xffdf625eu;
const YELLOW: u32 = 0xffe5b64bu;

fn ink(code:u32, x:i32, y:i32) -> bool {
    if (x<0 || x>=5 || y<0 || y>=7 || code<32u || code>126u) { return false; }
    return (p[212u+(code-32u)*7u+u32(y)] & (1u<<u32(4-x))) != 0u;
}
fn text_at(base:u32, count:u32, x:i32, y:i32, width:i32) -> bool {
    if (x<0 || y<0 || y>=7 || x>=max(width,0)/6*6 || u32(x/6)>=count) {return false;}
    return ink(p[base+u32(x/6)],x%6,y);
}
// Fixed 2x sampling; metadata and glyph rows retain their existing layout.
fn text_at_2x(base:u32, count:u32, x:i32, y:i32, width:i32) -> bool {
    if (x<0 || y<0) {return false;}
    return text_at(base,count,x/2,y/2,width/2);
}
fn topbar(x:i32,y:i32) -> u32 {
    if (y<5 || y>=19) {return BAR;}
    let left=array<u32,7>(77u,105u,110u,103u,32u,79u,83u);
    let right=array<u32,5>(80u,79u,87u,69u,82u);
    let lx=x-12;
    let rx=x-(i32(p[0])-72);
    if (lx>=0 && lx<84 && ink(left[u32(lx/12)],(lx%12)/2,(y-5)/2)) {return INK;}
    if (rx>=0 && rx<60 && ink(right[u32(rx/12)],(rx%12)/2,(y-5)/2)) {return INK;}
    return BAR;
}
fn dock(x:i32,y:i32) -> u32 {
    var color=BAR;
    for(var i=0u;i<p[3];i++) {
        let b=148u+i*16u;
        let cx=x-i32(p[b]);
        let cy=y-i32(p[b+1u]);
        if(cx<0 || cx>=64 || cy<0 || cy>=64) {continue;}
        if(cx>=8 && cx<56 && cy>=6 && cy<36) {color=select(0xfff8f9fbu,FOCUSED,p[b+3u]!=0u);}
        if(p[b+13u]!=0u && cx>=4 && cx<20 && cy>=4 && cy<20) {
            color=0xfff8f9fbu;
            if(ink(85u,cx-6,cy-1)) {color=INK;}
        }
        if(p[b+13u]!=0u && cx>=46 && cx<62 && cy>=4 && cy<20) {
            color=0xfff8f9fbu;
            if(ink(83u,cx-48,cy-1)) {color=INK;}
        }
        if(text_at_2x(b+5u,min(p[b+4u],1u),cx-27,cy-14,12) || text_at(b+5u,p[b+4u],cx-8,cy-40,48)) {color=INK;}
        if(cx>=29 && cx<35 && cy>=50 && cy<54) {
            switch p[b+2u] {
                case 1u,3u: {color=YELLOW;}
                case 2u: {color=BLUE;}
                case 4u: {color=RED;}
                default: {color=BAR;}
            }
        }
        break;
    }
    return color;
}
fn placeholder(failed:bool,cx:i32,cy:i32,width:i32) -> u32 {
    let starting=array<u32,8>(83u,116u,97u,114u,116u,105u,110u,103u);
    let failure=array<u32,6>(70u,97u,105u,108u,101u,100u);
    let tx=cx-12;
    let ty=cy-32-16;
    if(tx>=0 && tx<max(width-24,0)/6*6 && ty>=0 && ty<7) {
        if(failed) {
            if(tx/6<6 && ink(failure[u32(tx/6)],tx%6,ty)) {return INK;}
        } else {
            if(tx/6<8 && ink(starting[u32(tx/6)],tx%6,ty)) {return INK;}
        }
    }
    return select(0xfff0f2f5u,0xfff9e9e9u,failed);
}
@compute @workgroup_size(8,8)
fn main(@builtin(global_invocation_id) id:vec3<u32>) {
    if(id.x>=p[0] || id.y>=p[1]) {return;}
    let x=i32(id.x);
    let y=i32(id.y);
    let at=id.y*p[0]+id.x;
    var color=root[at];
    if(y<24) {color=topbar(x,y);}
    else if(y>=i32(p[1])-64) {color=dock(x,y);}
    else {
        for(var n=p[2];n>0u;n--) {
            let b=4u+(n-1u)*36u;
            if(p[b+4u]!=0u) {continue;}
            let cx=x-i32(p[b]);
            let cy=y-i32(p[b+1u]);
            if(cx<0 || cy<0 || cx>=i32(p[b+2u]) || cy>=i32(p[b+3u])) {continue;}
            if(cy<32) {
                color=select(TITLE_BG,FOCUSED,p[b+6u]!=0u);
                if(cy==31) {color=0xffc8ced6u;}
                let dy=cy-16;
                for(var k=0;k<3;k++) {
                    let dx=cx-(16+k*18);
                    if(dx*dx+dy*dy<=25) {
                        switch k {
                            case 0: {color=RED;}
                            case 1: {color=YELLOW;}
                            default: {color=0xff56ae79u;}
                        }
                    }
                }
                if(text_at_2x(b+9u,p[b+8u],cx-72,cy-9,i32(p[b+2u])-80)) {color=INK;}
            } else if(p[b+5u]==0u) {color=placeholder(p[b+7u]!=0u,cx,cy,i32(p[b+2u]));}
            break;
        }
    }
    if(arrayLength(&p)>877u && p[877u]!=0u) {
        let dw=min(i32(p[0])-16,600);
        let dx=(i32(p[0])-dw)/2;
        let dy=(i32(p[1])-208)/2;
        let cx=x-dx;
        let cy=y-dy;
        if(cx>=0 && cx<dw && cy>=0 && cy<208) {
            color=select(0xfffafbfcu,0xffc8ced6u,cx==0 || cy==0 || cx==dw-1 || cy==207);
            for(var n=0u;n<p[879u];n++) {
                let base=880u+n*97u;
                if(text_at(base+1u,p[base],cx-12,cy-12-i32(n)*14,dw-24)) {color=INK;}
            }
            if(cx>=dw-192 && cx<dw-112 && cy>=168 && cy<196) {
                color=0xffe2e5e9u;
                let deny=array<u32,6>(68u,69u,78u,89u,32u,32u);
                let close=array<u32,6>(67u,76u,79u,83u,69u,32u);
                let cancel=array<u32,6>(67u,65u,78u,67u,69u,76u);
                let tx=cx-(dw-192)-22;
                let ty=cy-178;
                if(tx>=0 && tx<36 && ty>=0 && ty<7) {
                    var code=deny[u32(tx/6)];
                    if(p[878u]==1u || p[878u]>=4u) {code=close[u32(tx/6)];}
                    if(p[878u]==2u || p[878u]==3u) {code=cancel[u32(tx/6)];}
                    if(ink(code,tx%6,ty)) {color=INK;}
                }
            }
            if(p[878u]!=1u && p[878u]<4u && p[878u]!=10u && cx>=dw-100 && cx<dw-20 && cy>=168 && cy<196) {
                color=BLUE;
                let allow=array<u32,6>(65u,76u,76u,79u,87u,32u);
                let revoke=array<u32,6>(82u,69u,86u,79u,75u,69u);
                let retry=array<u32,6>(82u,69u,84u,82u,89u,32u);
                let tx=cx-(dw-100)-22;
                let ty=cy-178;
                if(tx>=0 && tx<36 && ty>=0 && ty<7) {
                    var code=allow[u32(tx/6)];
                    if(p[878u]==2u || p[878u]==8u) {code=revoke[u32(tx/6)];}
                    if(p[878u]==3u || p[878u]==7u || p[878u]==9u || p[878u]==11u) {code=retry[u32(tx/6)];}
                    if(ink(code,tx%6,ty)) {color=0xffffffffu;}
                }
                if(p[878u]==10u && cx>=dw-100 && cx<dw-20 && cy>=168 && cy<196) {
                    let uninstall=array<u32,9>(85u,78u,73u,78u,83u,84u,65u,76u,76u);
                    let tx=cx-(dw-100)-13;
                    let ty=cy-178;
                    if(tx>=0 && tx<54 && ty>=0 && ty<7 && ink(uninstall[u32(tx/6)],tx%6,ty)) {color=0xffffffffu;}
                }
            }
        }
    }
    output[at]=color;
}

