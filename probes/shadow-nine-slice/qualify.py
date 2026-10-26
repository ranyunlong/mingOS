"""Whole-output exact qualification, stdlib only; each suite is a bounded batch."""
import argparse
import hashlib
import itertools
import json
import platform
from pathlib import Path
import sys
import time

import candidate
import reference

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts' / 'shadow-nine-slice'


def cases(suite):
    if suite == 'small':
        yield from itertools.product(range(1, 8), range(1, 8),
                                     [0, 1, 2, 3, 4, 8, 64], [0, 1, 2, 3, 4, 8, 16])
    elif suite == 'seams':
        for r, blur in [(0, 0), (0, 3), (1, 1), (3, 2), (8, 4), (64, 16)]:
            m = r + blur
            sizes = sorted({max(1, 2*m + d) for d in [-1, 0, 1, 2, 3]})
            yield from ((w, h, r, blur) for w, h in itertools.product(sizes, repeat=2))
    elif suite == 'radii':
        for r in range(65):
            for blur in [0, 1, 3, 8, 16]:
                yield 2*(r+blur)+4, 2*(r+blur)+7, r, blur
    elif suite == 'blurs':
        for blur in range(17):
            for r in [0, 1, 3, 8, 16, 32, 64]:
                m = r + blur
                yield 2*m+5, max(1, 2*r-1), r, blur  # x-only stretch; clamp matters
                yield max(1, 2*r-1), 2*m+4, r, blur  # y-only stretch
    elif suite == 'large':
        yield from [(769, 513, 64, 16), (512, 384, 64, 16),
                    (1024, 1, 64, 16), (1, 1023, 64, 16),
                    (513, 257, 64, 0), (257, 514, 0, 16)]
    else:
        raise ValueError(suite)


def digest(image):
    return hashlib.sha256(b''.join(image)).hexdigest()


def run(suite):
    started = time.perf_counter()
    totals = dict(cases=0, full_convolutions=0, compared_pixels=0,
                  mismatched_pixels=0, reference_seconds=0.0, patch_seconds=0.0,
                  comparison_seconds=0.0, max_patch_bytes=0,
                  x_only_stretch=0, y_only_stretch=0, both_stretch=0, no_stretch=0)
    cached = {}
    examples = []
    for w, h, requested, blur in cases(suite):
        effective = min(requested, w//2, h//2)
        key = w, h, effective, blur
        begin = time.perf_counter()
        if key not in cached:
            cached[key] = reference.full_shadow(w, h, requested, blur)
            totals['full_convolutions'] += 1
        expected = cached[key]
        ref_seconds = time.perf_counter() - begin
        totals['reference_seconds'] += ref_seconds
        begin = time.perf_counter()
        cw, ch, patch = candidate.build_patch(w, h, requested, blur)
        patch_seconds = time.perf_counter() - begin
        totals['patch_seconds'] += patch_seconds
        pw, ph = cw+2*blur, ch+2*blur
        assert pw <= 193 and ph <= 193
        assert len(patch) == ph and all(len(row) == pw for row in patch)
        totals['max_patch_bytes'] = max(totals['max_patch_bytes'], pw*ph)
        category = ('both_stretch' if w > cw and h > ch else
                    'x_only_stretch' if w > cw else 'y_only_stretch' if h > ch else 'no_stretch')
        totals[category] += 1
        begin = time.perf_counter()
        xm = [candidate.map_axis(x, w, cw, effective+blur)+blur for x in range(-blur, w+blur)]
        ym = [candidate.map_axis(y, h, ch, effective+blur)+blur for y in range(-blur, h+blur)]
        assert all(0 <= x < pw for x in xm) and all(0 <= y < ph for y in ym)
        actual = [bytearray(patch[y][x] for x in xm) for y in ym]
        # Expected is the independently sampled full-sized mask, naively blurred.
        # The candidate mapper is never used to construct expected.
        if actual != expected:
            first = None
            maximum = count = 0
            for y, (arow, erow) in enumerate(zip(actual, expected)):
                for x, (a, e) in enumerate(zip(arow, erow)):
                    if a != e:
                        count += 1
                        maximum = max(maximum, abs(a-e))
                        if first is None:
                            first = dict(u=x-blur, v=y-blur, actual=a, expected=e)
            failure = dict(w=w, h=h, requested=requested, effective=effective, blur=blur,
                           mismatched_pixels=count, max_abs_error=maximum, first=first)
            (OUT / f'{suite}-failure.json').write_text(json.dumps(failure, indent=2)+'\n')
            raise AssertionError(failure)
        comparison_seconds = time.perf_counter() - begin
        totals['comparison_seconds'] += comparison_seconds
        totals['cases'] += 1
        totals['compared_pixels'] += (w+2*blur)*(h+2*blur)
        if suite == 'large':
            examples.append(dict(w=w, h=h, requested_radius=requested, effective_radius=effective,
                blur_radius=blur, core=[cw, ch], patch_extent=[pw, ph],
                patch_bytes_a8=pw*ph, full_bytes_a8=(w+2*blur)*(h+2*blur),
                full_sha256=digest(expected), reconstructed_sha256=digest(actual),
                patch_sha256=digest(patch), reference_seconds=ref_seconds,
                patch_seconds=patch_seconds, comparison_seconds=comparison_seconds))
        if totals['cases'] % 250 == 0:
            print(f'{suite}: {totals["cases"]} cases, {time.perf_counter()-started:.3f}s', flush=True)
    totals['elapsed_seconds'] = time.perf_counter()-started
    result = dict(suite=suite, status='exact', python=sys.version,
                  executable=sys.executable, platform=platform.platform(), totals=totals,
                  examples=examples, source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in Path(__file__).parent.glob('*.py')},
                  notes=['A8 payload bytes only; Python object/temporary memory excluded.',
                         'CPU Python qualification timings are not GPU performance measurements.',
                         'reference cache key includes effective radius; each requested radius still builds a fresh candidate.'])
    path = OUT / f'{suite}.json'
    path.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(suite=suite, **totals)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=['small', 'seams', 'radii', 'blurs', 'large'])
    run(parser.parse_args().suite)
