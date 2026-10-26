struct Size { logical:vec2<u32>,physical:vec2<u32>,srgb:u32,pad0:u32,pad1:u32,pad2:u32 }
@group(0) @binding(0) var<storage,read> pixels:array<u32>;
@group(0) @binding(1) var<uniform> size:Size;
@vertex fn vs(@builtin(vertex_index) i:u32)->@builtin(position) vec4<f32>{
    let p=array<vec2<f32>,3>(vec2<f32>(-1.,-1.),vec2<f32>(3.,-1.),vec2<f32>(-1.,3.));
    return vec4<f32>(p[i],0.,1.);
}
@fragment fn fs(@builtin(position) p:vec4<f32>)->@location(0) vec4<f32>{
    let xy=min(vec2<u32>(p.xy)*size.logical/size.physical,size.logical-vec2<u32>(1u));
    let pixel=pixels[xy.y*size.logical.x+xy.x];
    var rgb=vec3<f32>(f32((pixel>>16u)&255u),f32((pixel>>8u)&255u),f32(pixel&255u))/255.;
    if size.srgb!=0u {rgb=select(pow((rgb+vec3<f32>(0.055))/1.055,vec3<f32>(2.4)),rgb/12.92,rgb<=vec3<f32>(0.04045));}
    return vec4<f32>(rgb,1.);
}
