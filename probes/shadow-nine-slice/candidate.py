"""Canonical patch experiment, independent of the direct full-image oracle."""


def coverage(x, y, w, h, requested):
    if x < 0 or y < 0 or x >= w or y >= h:
        return 0
    r = min(requested, w // 2, h // 2)
    x, y = min(x, w - 1 - x), min(y, h - 1 - y)
    if x >= r or y >= r:
        return 255
    hits = 0
    for sy in (1, 3, 5, 7):
        for sx in (1, 3, 5, 7):
            dx, dy = 8 * (r - x) - sx, 8 * (r - y) - sy
            hits += dx * dx + dy * dy <= 64 * r * r
    return (255 * hits + 8) // 16


def blur_line(line, radius):
    # Weighted prefix sums evaluate the exact tent, with a single rounding per pass.
    sums, moments = [0], [0]
    for i, value in enumerate(line):
        sums.append(sums[-1] + value)
        moments.append(moments[-1] + i * value)
    norm = (radius + 1) ** 2
    result = bytearray(len(line))
    for x in range(len(line)):
        lo, mid, hi = max(0, x - radius), x + 1, min(len(line), x + radius + 1)
        total = ((radius + 1 - x) * (sums[mid] - sums[lo])
                 + moments[mid] - moments[lo]
                 + (radius + 1 + x) * (sums[hi] - sums[mid])
                 - (moments[hi] - moments[mid]))
        result[x] = (total + norm // 2) // norm
    return result


def map_axis(u, size, core, margin):
    if size == core or u < margin:
        return u
    if u >= size - margin:
        return u - (size - core)
    return margin


def build_patch(w, h, requested, radius):
    r = min(requested, w // 2, h // 2)
    cw = min(w, 2 * (r + radius) + 1)
    ch = min(h, 2 * (r + radius) + 1)
    mask = [bytearray(coverage(x, y, cw, ch, r)
                     for x in range(-radius, cw + radius))
            for y in range(-radius, ch + radius)]
    horizontal = [blur_line(row, radius) for row in mask]
    columns = [blur_line(column, radius) for column in zip(*horizontal)]
    patch = [bytearray(row) for row in zip(*columns)]
    return cw, ch, patch
