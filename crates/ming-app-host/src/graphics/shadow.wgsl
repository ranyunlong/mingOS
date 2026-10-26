// Bounded A8-as-u32 generation. Each key owns one immutable 32-byte uniform.
struct Params {
    patch_size: vec2<u32>,
    core: vec2<u32>,
    radius: i32,
    blur: i32,
    offset: u32,
    padding: u32,
}
@group(0) @binding(0) var<uniform> params: Params;
@group(0) @binding(1) var<storage, read_write> mask_pixels: array<u32>;
@group(0) @binding(2) var<storage, read_write> horizontal_pixels: array<u32>;
@group(0) @binding(3) var<storage, read_write> cache_pixels: array<u32>;

// The SDK4 integer 4x4 AA lattice; distances are corner-local and <=512.
fn rounded_coverage(local: vec2<i32>, wh: vec2<i32>, radius: i32) -> u32 {
    if any(local < vec2<i32>(0)) || any(local >= wh) { return 0u; }
    let r = min(radius, min(wh.x / 2, wh.y / 2));
    let corner = min(local, wh - vec2<i32>(1) - local);
    if any(corner >= vec2<i32>(r)) { return 255u; }
    var hits = 0u;
    for (var sy = 1; sy < 8; sy += 2) {
        let dy = 8 * (r - corner.y) - sy;
        for (var sx = 1; sx < 8; sx += 2) {
            let dx = 8 * (r - corner.x) - sx;
            if dx * dx + dy * dy <= 64 * r * r { hits += 1u; }
        }
    }
    return (255u * hits + 8u) / 16u;
}
@compute @workgroup_size(8, 8, 1)
fn mask(@builtin(global_invocation_id) id: vec3<u32>) {
    if any(id.xy >= params.patch_size) { return; }
    mask_pixels[id.y * params.patch_size.x + id.x] = rounded_coverage(
        vec2<i32>(id.xy) - vec2<i32>(params.blur), vec2<i32>(params.core), params.radius);
}
@compute @workgroup_size(8, 8, 1)
fn horizontal(@builtin(global_invocation_id) id: vec3<u32>) {
    if any(id.xy >= params.patch_size) { return; }
    var sum = 0u;
    let b = params.blur;
    for (var tap = -b; tap <= b; tap += 1) {
        let x = i32(id.x) + tap;
        if x >= 0 && x < i32(params.patch_size.x) {
            sum += mask_pixels[id.y * params.patch_size.x + u32(x)] * u32(b + 1 - abs(tap));
        }
    }
    let norm = u32((b + 1) * (b + 1));
    horizontal_pixels[id.y * params.patch_size.x + id.x] = (sum + norm / 2u) / norm;
}
@compute @workgroup_size(8, 8, 1)
fn vertical(@builtin(global_invocation_id) id: vec3<u32>) {
    if any(id.xy >= params.patch_size) { return; }
    var sum = 0u;
    let b = params.blur;
    for (var tap = -b; tap <= b; tap += 1) {
        let y = i32(id.y) + tap;
        if y >= 0 && y < i32(params.patch_size.y) {
            sum += horizontal_pixels[u32(y) * params.patch_size.x + id.x] * u32(b + 1 - abs(tap));
        }
    }
    let norm = u32((b + 1) * (b + 1));
    cache_pixels[params.offset + id.y * params.patch_size.x + id.x] = (sum + norm / 2u) / norm;
}
