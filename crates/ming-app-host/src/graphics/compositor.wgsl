// Exactly five bounded host-owned sources. Application pixels remain on the GPU.
@group(0) @binding(0) var<storage, read> params: array<u32>;
@group(0) @binding(1) var<storage, read_write> output_pixels: array<u32>;
@group(0) @binding(2) var<storage, read> source0: array<u32>;
@group(0) @binding(3) var<storage, read> source1: array<u32>;
@group(0) @binding(4) var<storage, read> source2: array<u32>;
@group(0) @binding(5) var<storage, read> source3: array<u32>;
@group(0) @binding(6) var<storage, read> source4: array<u32>;
fn sample_source(layer: u32, index: u32) -> u32 {
    switch layer {
        case 0u: { return source0[index]; }
        case 1u: { return source1[index]; }
        case 2u: { return source2[index]; }
        case 3u: { return source3[index]; }
        default: { return source4[index]; }
    }
}
@compute @workgroup_size(8,8,1)
fn main(@builtin(global_invocation_id) id: vec3<u32>) {
    if id.x>=params[0] || id.y>=params[1] {return;}
    let p=vec2<i32>(id.xy);
    var color=params[2];
    for(var layer=0u;layer<params[3];layer+=1u) {
        let b=4u+layer*12u;
        let origin=vec2<i32>(i32(params[b]),i32(params[b+1u]));
        let size=vec2<i32>(i32(params[b+2u]),i32(params[b+3u]));
        let lo=vec2<i32>(i32(params[b+6u]),i32(params[b+7u]));
        let hi=vec2<i32>(i32(params[b+8u]),i32(params[b+9u]));
        if any(p<origin) || any(p>=origin+size) || any(p<lo) || any(p>=hi) {continue;}
        let local=vec2<u32>(p-origin);
        let sx=local.x*params[b+4u]/u32(size.x);
        let sy=local.y*params[b+5u]/u32(size.y);
        let src=sample_source(layer,sy*params[b+4u]+sx);
        let alpha=((src>>24u)*params[b+10u]+127u)/255u;
        let rgb=(vec3<u32>(src&255u,(src>>8u)&255u,(src>>16u)&255u)*alpha
            + vec3<u32>(color&255u,(color>>8u)&255u,(color>>16u)&255u)*(255u-alpha)+vec3<u32>(127u))/255u;
        color=0xff000000u|rgb.x|(rgb.y<<8u)|(rgb.z<<16u);
    }
    output_pixels[id.y*params[0]+id.x]=color;
}
