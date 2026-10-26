// GPU evaluates the validated SDK scene. No CPU frame upload.
@group(0) @binding(0) var<storage, read> scene: array<u32>;
@group(0) @binding(1) var<storage, read_write> pixels: array<u32>;
@group(0) @binding(2) var<storage, read> images: array<u32>;
@group(0) @binding(3) var<storage, read> coverage: array<u32>;
@group(0) @binding(4) var<storage, read> shadows: array<u32>;

fn blend(dst: u32, src: u32) -> u32 {
    let alpha = src >> 24u;
    if alpha == 255u { return src; }
    if alpha == 0u { return dst; }
    let rgb = (vec3<u32>(src & 255u, (src >> 8u) & 255u, (src >> 16u) & 255u) * alpha
        + vec3<u32>(dst & 255u, (dst >> 8u) & 255u, (dst >> 16u) & 255u) * (255u-alpha) + vec3<u32>(127u)) / 255u;
    return 0xff000000u | rgb.x | (rgb.y << 8u) | (rgb.z << 16u);
}
fn inside(p: vec2<i32>, xy: vec2<i32>, wh: vec2<i32>) -> bool {
    return all(p >= xy) && all(p < xy + wh);
}
// Only corner-local distances are squared; host radius <=64 bounds |d|<=512.
fn rounded_coverage(local: vec2<i32>, wh: vec2<i32>, radius: i32) -> u32 {
    let r = min(radius,min(wh.x/2,wh.y/2));
    let corner = min(local,wh-vec2<i32>(1)-local);
    if any(corner>=vec2<i32>(r)) { return 255u; }
    var hits = 0u;
    for(var sy=1;sy<8;sy+=2) {
        let dy=8*(r-corner.y)-sy;
        for(var sx=1;sx<8;sx+=2) {
            let dx=8*(r-corner.x)-sx;
            if dx*dx+dy*dy<=64*r*r { hits+=1u; }
        }
    }
    return (255u*hits+8u)/16u;
}
fn shadow_axis(u: i32, size: i32, core: i32, margin: i32) -> i32 {
    if size == core || u < margin { return u; }
    if u >= size - margin { return u - (size - core); }
    return margin;
}
@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) id: vec3<u32>) {
    if id.x >= scene[0] || id.y >= scene[1] { return; }
    let p = vec2<i32>(id.xy);
    var color = 0xff000000u;
    for (var i = 0u; i < scene[2]; i += 1u) {
        let b = 4u + i * 20u;
        let lo = vec2<i32>(i32(scene[b+8u]), i32(scene[b+9u]));
        let hi = vec2<i32>(i32(scene[b+10u]), i32(scene[b+11u]));
        if any(p < lo) || any(p >= hi) { continue; }
        let op = scene[b];
        let a0 = i32(scene[b+1u]); let a1 = i32(scene[b+2u]);
        let a2 = i32(scene[b+3u]); let a3 = i32(scene[b+4u]); let a4 = i32(scene[b+5u]);
        if op == 1u && inside(p, vec2<i32>(a0, a1), vec2<i32>(a2, a3)) {
            let r = min(i32(scene[b+6u]), min(a2 / 2, a3 / 2));
            let local = p - vec2<i32>(a0, a1);
            let d = max(max(vec2<i32>(r) - local - vec2<i32>(1), local - (vec2<i32>(a2,a3) - vec2<i32>(r))), vec2<i32>(0));
            if d.x*d.x + d.y*d.y <= r*r { color = blend(color, scene[b+5u]); }
        } else if op == 6u && inside(p,vec2<i32>(a0,a1),vec2<i32>(a2,a3)) {
            let cover=rounded_coverage(p-vec2<i32>(a0,a1),vec2<i32>(a2,a3),i32(scene[b+6u]));
            let ink=scene[b+5u];
            let alpha=(cover*(ink>>24u)+127u)/255u;
            color=blend(color,(ink&0x00ffffffu)|(alpha<<24u));
        } else if op == 7u {
            let local = p - vec2<i32>(a1, a2);
            let blur = i32(scene[b+16u]);
            if any(local < vec2<i32>(-blur)) || any(local >= vec2<i32>(a3,a4) + vec2<i32>(blur)) { continue; }
            let margin = i32(scene[b+15u]) + blur;
            let x = shadow_axis(local.x,a3,i32(scene[b+13u]),margin) + blur;
            let y = shadow_axis(local.y,a4,i32(scene[b+14u]),margin) + blur;
            let cover = shadows[scene[b+12u] + u32(y)*scene[b+17u] + u32(x)];
            let ink = scene[b+6u];
            let alpha = (cover*(ink>>24u)+127u)/255u;
            color = blend(color,(ink&0x00ffffffu)|(alpha<<24u));
        } else if op == 2u && inside(p, vec2<i32>(a1, a2), vec2<i32>(a3, a4)) {
            // Nonnegative relative coordinates < 1,000,000; image edge <=4096.
            // The products fit u32, avoiding signed 32-bit overflow for large destinations.
            let local = vec2<u32>(p - vec2<i32>(a1, a2));
            let sx = local.x * scene[b+13u] / u32(a3);
            let sy = local.y * scene[b+14u] / u32(a4);
            color = blend(color, images[scene[b+12u] + sy*scene[b+13u] + sx]);
        } else if op == 5u {
            let origin=vec2<i32>(a0,a1);let grid=vec2<u32>(scene[b+3u],scene[b+4u]);
            if any(p<origin)||any(grid==vec2<u32>(0u)){continue;}
            let tile=vec2<u32>(p-origin)/16u;if any(tile>=grid){continue;}
            let cell=scene[b+6u]+(tile.y*grid.x+tile.x)*2u;
            let first=scene[cell];let count=scene[cell+1u];let ink=scene[b+5u];
            for(var j=0u;j<count;j+=1u){
                let g=scene[b+12u]+scene[scene[b+7u]+first+j]*8u;
                let xy=vec2<i32>(i32(scene[g]),i32(scene[g+1u]));let wh=vec2<i32>(i32(scene[g+2u]),i32(scene[g+3u]));
                if !inside(p,xy,wh){continue;}
                let local=vec2<u32>(p-xy);let index=scene[g+4u]+local.y*scene[g+2u]+local.x;
                let alpha=(coverage[index/4u]>>((index%4u)*8u))&255u;
                let effective=(alpha*(ink>>24u)+127u)/255u;
                color=blend(color,(ink&0x00ffffffu)|(effective<<24u));
            }
        } else if op == 3u {
            let local = p - vec2<i32>(a0, a1);
            if any(local < vec2<i32>(0)) { continue; }
            let cell = vec2<u32>(local / a2);
            let character = cell.x / 6u;
            let column = cell.x % 6u;
            if character < scene[b+16u] && column < 5u && cell.y < 7u {
                let bits = scene[scene[b+15u] + character*7u + cell.y];
                if (bits & (1u << (4u-column))) != 0u { color = blend(color, scene[b+4u]); }
            }
        }
    }
    pixels[id.y * scene[0] + id.x] = color;
}
