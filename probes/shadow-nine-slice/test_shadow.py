"""RED/GREEN contract tests; run with python -B test_shadow.py."""
import unittest

import candidate
import reference


class ShadowTests(unittest.TestCase):
    def test_explicit_aa_goldens(self):
        for r, expected in [(1, [[207]]), (2, [[96, 239], [239, 255]]),
                            (3, [[0, 143, 239], [143, 255, 255], [239, 255, 255]])]:
            actual = [[reference.coverage(x, y, 2*r+3, 2*r+5, r)
                       for x in range(r)] for y in range(r)]
            self.assertTrue(actual == expected, "full image differs")

    def test_tent_impulse_goldens(self):
        self.assertEqual(list(reference.blur_line([0, 255, 0], 1)), [64, 128, 64])
        self.assertEqual(list(candidate.blur_line([0, 255, 0], 1)), [64, 128, 64])

    def test_round_after_each_pass(self):
        # Delaying both roundings to a single 2D convolution would yield zero.
        mask = [bytearray(3), bytearray([0, 1, 0]), bytearray(3)]
        self.assertEqual(reference.blur(mask, 1), mask)
        horizontal = [candidate.blur_line(row, 1) for row in mask]
        columns = [candidate.blur_line(col, 1) for col in zip(*horizontal)]
        self.assertEqual([bytearray(row) for row in zip(*columns)], mask)

    def test_zero_boundary_and_blur_zero(self):
        for radius in range(17):
            for line in [[], [255], [1, 2], [255, 0, 0, 128, 1], [255]*39]:
                self.assertEqual(candidate.blur_line(line, radius),
                                 reference.blur_line(line, radius))
        self.assertEqual(candidate.blur_line([0, 1, 127, 255], 0), bytearray([0, 1, 127, 255]))

    def test_mapper_identity_when_not_stretched(self):
        for size in range(1, 18):
            for u in range(-16, size + 16):
                self.assertEqual(candidate.map_axis(u, size, size, 80), u)

    def test_mapper_seams_and_halo_goldens(self):
        # r=2, R=1 => margin=3, core=7, width=12, displacement=5.
        self.assertEqual([candidate.map_axis(u, 12, 7, 3) for u in range(-1, 13)],
                         [-1, 0, 1, 2, 3, 3, 3, 3, 3, 3, 4, 5, 6, 7])

    def test_patch_bound_and_large_exact(self):
        w, h, requested, radius = 181, 178, 64, 16
        cw, ch, patch = candidate.build_patch(w, h, requested, radius)
        self.assertEqual((cw, ch, len(patch[0]), len(patch)), (161, 161, 193, 193))
        expected = reference.full_shadow(w, h, requested, radius)
        margin = 80
        actual = [bytearray(patch[candidate.map_axis(y, h, ch, margin) + radius]
                           [candidate.map_axis(x, w, cw, margin) + radius]
                           for x in range(-radius, w + radius))
                  for y in range(-radius, h + radius)]
        self.assertTrue(actual == expected, "full image differs")

    def test_skinny_clamp_blur_zero_exact(self):
        for w, h in [(1, 1), (1, 9), (9, 1), (2, 7), (7, 2), (5, 7), (7, 5)]:
            for radius in [0, 1, 2, 16]:
                cw, ch, patch = candidate.build_patch(w, h, 64, radius)
                margin = min(64, w // 2, h // 2) + radius
                actual = [bytearray(patch[candidate.map_axis(y, h, ch, margin) + radius]
                                   [candidate.map_axis(x, w, cw, margin) + radius]
                                   for x in range(-radius, w + radius))
                          for y in range(-radius, h + radius)]
                self.assertTrue(actual == reference.full_shadow(w, h, 64, radius),
                                f"skinny mismatch: {w=} {h=} {radius=}")


if __name__ == '__main__':
    unittest.main(verbosity=2)

