"""Deliberately incorrect variants must disagree with independent full convolution."""
import itertools
import json
from pathlib import Path
import time

import candidate
import reference


def first_difference(actual, expected, blur):
    for y, (a, e) in enumerate(zip(actual, expected)):
        for x, (av, ev) in enumerate(zip(a, e)):
            if av != ev:
                return dict(u=x-blur, v=y-blur, actual=av, expected=ev)
    return None


def delayed_rounding(w, h, r, blur):
    # Intentionally WRONG: combine both passes before rounding.
    norm2 = (blur+1)**4
    out = []
    for y in range(-blur, h+blur):
        row = bytearray()
        for x in range(-blur, w+blur):
            total = sum(reference.coverage(x+dx, y+dy, w, h, r)
                        * (blur+1-abs(dx)) * (blur+1-abs(dy))
                        for dy in range(-blur, blur+1)
                        for dx in range(-blur, blur+1))
            row.append((total+norm2//2)//norm2)
        out.append(row)
    return out


def wrong_patch(w, h, requested, blur, variant):
    r = min(requested, w//2, h//2)
    cw, ch, patch = candidate.build_patch(w, h, requested, blur)
    if variant == 'omit_center_pixel':
        cw, ch = min(w, 2*(r+blur)), min(h, 2*(r+blur))
        mask = [bytearray(candidate.coverage(x, y, cw, ch, r)
                         for x in range(-blur, cw+blur))
                for y in range(-blur, ch+blur)]
        horizontal = [candidate.blur_line(row, blur) for row in mask]
        columns = [candidate.blur_line(col, blur) for col in zip(*horizontal)]
        patch = [bytearray(row) for row in zip(*columns)]
    margin = r if variant == 'omit_blur_from_margin' else r+blur
    return [bytearray(patch[candidate.map_axis(y, h, ch, margin)+blur]
                     [candidate.map_axis(x, w, cw, margin)+blur]
                     for x in range(-blur, w+blur))
            for y in range(-blur, h+blur)]


def main():
    begin = time.perf_counter()
    inputs = sorted(itertools.product(range(1, 9), range(1, 9), range(4), [1, 2]),
                    key=lambda p: (p[0]*p[1], *p))
    witnesses = []
    for variant in ['delay_rounding_until_after_both_passes',
                    'omit_center_pixel', 'omit_blur_from_margin']:
        for w, h, r, blur in inputs:
            expected = reference.full_shadow(w, h, r, blur)
            actual = (delayed_rounding(w, h, r, blur)
                      if variant == 'delay_rounding_until_after_both_passes'
                      else wrong_patch(w, h, r, blur, variant))
            difference = first_difference(actual, expected, blur)
            if difference:
                witnesses.append(dict(variant=variant, w=w, h=h, requested=r,
                                      effective=min(r,w//2,h//2), blur=blur,
                                      first_difference=difference))
                break
        else:
            raise AssertionError(f'Negative control escaped: {variant}')
    report = dict(status='all deliberate errors detected', witnesses=witnesses,
                  elapsed_seconds=time.perf_counter()-begin,
                  order='first witness sorted by (area,w,h,requested,blur), '
                        'search w,h=1..8 requested=0..3 blur=1,2; not a failure of the supplied formula')
    output = Path(__file__).resolve().parents[2]/'artifacts/shadow-nine-slice/negative-controls.json'
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
