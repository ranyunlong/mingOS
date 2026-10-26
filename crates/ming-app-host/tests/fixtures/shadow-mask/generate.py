"""Reproduce fixtures using the qualified independent full-image Python oracle."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
SOURCE = ROOT / 'probes/shadow-nine-slice/reference.py'
PIN = '3f4be92b5f86838f57e89354cc6d12cad3692a4bb8f7b915fd5544364d463883'
source = SOURCE.read_bytes()
assert hashlib.sha256(source).hexdigest() == PIN, 'qualified oracle changed'
(HERE / 'oracle_source.py').write_bytes(source)
spec = importlib.util.spec_from_file_location('shadow_full_oracle', HERE / 'oracle_source.py')
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
cases = [
    ('max-r64-b16.a8', 769, 513, 64, 16),
    ('r64-b0.a8', 513, 257, 64, 0),
    ('clamp-odd.a8', 12, 9, 64, 2),
    ('thin-b16.a8', 1, 9, 64, 16),
    ('odd-single-axis.a8', 41, 19, 8, 3),
    ('round-each-pass.a8', 1, 2, 0, 2),
]
entries = []
for name, w, h, requested, blur in cases:
    r = min(requested, w//2, h//2)
    cw, ch = min(w, 2*(r+blur)+1), min(h, 2*(r+blur)+1)
    # Direct full convolution of canonical geometry, never candidate/mapper output.
    data = b''.join(oracle.full_shadow(cw, ch, r, blur))
    assert len(data) == (cw+2*blur)*(ch+2*blur)
    (HERE / name).write_bytes(data)
    entries.append(dict(file=name, original_width=w, original_height=h,
        requested_radius=requested, effective_radius=r, blur=blur,
        core_width=cw, core_height=ch, patch_width=cw+2*blur, patch_height=ch+2*blur,
        bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
manifest = dict(format='row-major A8, no header',
    provenance=dict(source='probes/shadow-nine-slice/reference.py', source_sha256=PIN,
        snapshot='oracle_source.py', generator='generate.py',
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        expected='independent rational AA plus full-image direct per-tap H then V convolution',
        rounding='(sum + (blur+1)^2//2)//(blur+1)^2 after EACH pass',
        domain='canonical body plus zero halo of blur pixels; no mapper used to generate expected',
        python=sys.version), fixtures=entries)
(HERE / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
print(json.dumps(manifest, indent=2))
