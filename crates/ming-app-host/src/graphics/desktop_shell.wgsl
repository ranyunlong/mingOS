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
    output[at]=color;
}

