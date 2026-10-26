"""Independent, deliberately direct integer geometry and full-image convolution."""


def coverage(x, y, w, h, requested):
    if not (0 <= x < w and 0 <= y < h):
        return 0
    r = min(requested, w // 2, h // 2)
    if r <= x < w - r or r <= y < h - r:
        return 255
    hits = 0
    for index in range(16):
        px = 8 * x + 2 * (index % 4) + 1
        py = 8 * y + 2 * (index // 4) + 1
        nearest_x = min(max(px, 8 * r), 8 * (w - r))
        nearest_y = min(max(py, 8 * r), 8 * (h - r))
        hits += (px - nearest_x) ** 2 + (py - nearest_y) ** 2 <= (8 * r) ** 2
    return (255 * hits + 8) // 16


def blur_line(line, radius):
    norm = (radius + 1) ** 2
    result = bytearray(len(line))
    for out in range(len(line)):
        total = 0
        for tap in range(-radius, radius + 1):
            source = out + tap
            if 0 <= source < len(line):
                total += line[source] * (radius + 1 - abs(tap))
        result[out] = (total + norm // 2) // norm
    return result


def blur(mask, radius):
    horizontal = [blur_line(row, radius) for row in mask]
    columns = [blur_line(column, radius) for column in zip(*horizontal)]
    return [bytearray(row) for row in zip(*columns)]


def full_shadow(w, h, requested, radius):
    mask = [bytearray(coverage(x, y, w, h, requested)
                     for x in range(-radius, w + radius))
            for y in range(-radius, h + radius)]
    return blur(mask, radius)
