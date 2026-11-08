"""Local qualification controls; these never allocate Docker/WSL resources."""
import copy
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout


GATES = (
    'reviewed-committed-frozen-closure', 'source-and-affected-controls',
    's1-provenance-and-strict-input-pins', 'explicit-endpoint-and-complete-command-records',
    'new-distro-vhd-daemon-store', 'rootfs-bundle-tools-and-binaries',
    'historical-fsynced-first-empty-and-order',
    'three-fixed-index-config-ordered-rootfs-identities',
    'actual-daemon-environment-and-helper-trust', 'actual-containerd-namespace-pair-and-cli',
    'private-mount-propagation-visible-tmpfs-and-sockets',
    'private-cgroups-and-unchanged-shared-controls', 'managed-containerd-identity-and-paths',
    'configured-and-effective-retained-smoke', 'complete-old-resource-preservation',
    'dns-equivalence-set-and-schema', 'independent-standalone-before-after-witness',
    'public-observe-and-explicit-linux-exec', 'store-completeness-and-retained-state',
    'sealed-evidence-exact-membership-and-pins', 'final-receipt-and-scope-flags',
)
HELPERS = ('wsl_environment.py', 'wsl_environment_guest.py', 'docker_transport.py',
           'development_image_migration.py', 'qualification_image_archive.py')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


class ProofFixture:
    """Synthetic, externally pinned local proof, not actual S2 acceptance."""
    def __init__(self, root):
        self.root = root
        self.base = root / 'evidence'
        self.base.mkdir()
        self.observer = 'observer'
        self.dirs = ['environment', 'environment/frozen', 'public', 'reviewed', 'outer-client',
                     self.observer, self.observer + '/public-observe', self.observer + '/public-exec']
        for name in self.dirs:
            (self.base / name).mkdir()
        (self.base / 'qualification-evidence').mkdir()
        (self.base / 'environment/distro').mkdir()
        (self.base / 'environment/distro/ext4.vhdx').write_bytes(b'live excluded bytes')
        self.gen = 'mingos-p01-s2-fixture'
        self.distro = 'MingOS-P01-fixture'
        self.commit = 'a' * 40
        self.pinmap = {}
        self.files = {}
        for name in HELPERS:
            data = ('# fixture ' + name + '\n').encode()
            self.write('public/' + name, data)
            self.write('environment/frozen/' + name, data)
            self.pinmap[name] = {'bytes': len(data), 'sha256': digest(data)}
            self.files[name] = {**self.pinmap[name], 'hostMode': 438,
                                'public': self.ref('public/' + name),
                                'frozen': self.ref('environment/frozen/' + name)}
        self.manifest = {'root': str(self.base / 'environment/frozen'),
                         'files': {n: {**p, 'mode': 438} for n, p in self.pinmap.items()}}
        self.handle = {'pid': 13, 'starttime': '1046319', 'exeSha256': 'b' * 64,
                       'namespaces': {'mnt': 'mnt:[11]', 'net': 'net:[12]', 'cgroup': 'cgroup:[13]'},
                       'socket': {'device': 236, 'inode': 13, 'uid': 0, 'gid': 0},
                       'containerdNamespaces': {'Containers': self.gen, 'Plugins': self.gen + '-plugins'},
                       'daemonId': 'fixture-daemon', 'generation': self.gen}
        self.env = {'schemaVersion': 1, 'generation': self.gen, 'distro': self.distro,
                    'status': 'environment-ready', 'phase': 'environment-ready',
                    'formalQualification': False, 'finalSystemAccepted': False,
                    'coldBuildExecuted': False, 'resourcesPreserved': True,
                    'helpers': copy.deepcopy(self.manifest), 'handle': self.handle,
                    'guestCodeRoot': '/root/' + self.gen + '-code',
                    'oldResources': {'status': 'preserved', 'equivalences': []},
                    'load': {'completedLoads': ['rust', 'tools', 'kotlin']}}
        self.request = {'generation': self.gen, 'distro': self.distro, 'reviewedHead': self.commit,
                        'archivePreparationResult': str(root / 'preparation.json'),
                        'archivePreparationPin': {'bytes': 3, 'sha256': digest(b'{}\n')},
                        'inputAdmission': str(self.base / 'input-admission.json'),
                        'argv': ['/fixture/python', 'create'], 'codePins': self.pinmap,
                        'createdAt': 1.25, 'formalQualification': False,
                        'finalSystemAccepted': False, 'coldBuildExecuted': False}
        (root / 'preparation.json').write_bytes(b'{}\n')
        self.write('input-admission.json', b'{}\n')
        self.repo = {'reviewedCommit': self.commit, 'head': self.commit, 'gitStatusShort': '',
                     'trackedWorktreeAndIndexStatus': '', 'observedAt': 1.5}
        self.controls = {'status': 'passed', 'container': 'c' * 64,
                         'interpreter': {'python': '3.13 fixture', 'tomllib': '/usr/lib/tomllib/__init__.py'},
                         'testsRun': 146, 'passed': 145, 'skips': [['fixture', 'Windows junction control']],
                         'failures': 0, 'errors': 0, 'codePins': {**self.pinmap, 'test_fixture.py': self.pinmap[HELPERS[0]]},
                         'actualWSLCreated': False, 'formalQualification': False,
                         'finalSystemAccepted': False, 'resourcesPreserved': True}
        self.collector = {'status': 'independent-public-followups-and-readonly-evidence-collected',
                          's2Accepted': False, 'p01Completed': False, 'formal': False,
                          'formalQualification': False, 'finalSystemAccepted': False,
                          'coldBuildExecuted': False, 'resourcesPreserved': True,
                          'independentReviewRequired': True}
        self.observer_bytes = b'fixture observer source\n'
        self.witness_bytes = b'fixture witness source\n'
        self.write(self.observer + '/controller-harness.py', self.observer_bytes)
        self.write(self.observer + '/readonly-witness.py', self.witness_bytes)
        self.write(self.observer + '/controller-pin.json', encode(self.pin(self.observer + '/controller-harness.py')))
        self.write(self.observer + '/witness-pin.json', encode(self.pin(self.observer + '/readonly-witness.py')))
        identity = {k: self.handle[k] for k in ('pid', 'starttime', 'exeSha256', 'namespaces')}
        self.witness = {'identity': identity, 'identityAfter': identity, 'socket': self.handle['socket'],
                        'socketIsSocket': True,
                        'info': {'ID': self.handle['daemonId'], 'Containerd': {'Namespaces': self.handle['containerdNamespaces']}},
                        'status': 'readonly-witness-collected'}
        self.gates = [{'name': n, 'result': 'passed'} for n in GATES]
        self.gates[15]['result'] = 'passed-empty-no-equivalence-applied'
        self.verdict_extra = {}
        self.closure_extra = {}
        self.inventory_extra = {}
        self.checkout = root / 'checkout'
        self.checkout.mkdir()
        (self.checkout / 'changed.txt').write_bytes(b'dirty source byte identity')
        self.harness = root / 'harness-input'
        self.harness.mkdir()
        (self.harness / 'scripts').mkdir()
        (self.harness / 'scripts/boot').mkdir()
        (self.harness / 'scripts/boot/helper.py').write_bytes(b'print("fixture")\n')
        mode = lambda p: stat.S_IMODE(p.stat().st_mode)
        hp = self.harness / 'scripts/boot/helper.py'
        self.harness_manifest = {'schemaVersion': 1, 'kind': 'p01-s3-harness-input',
            'checkout': {'path': str(self.checkout), 'reviewedCommit': self.commit,
                         'dirtyFiles': {'changed.txt': self.pin_path(self.checkout / 'changed.txt'), 'deleted.txt': None}},
            'directories': {n: {'hostMode': mode(self.harness / n), 'transferMode': 0o555}
                            for n in ('scripts', 'scripts/boot')},
            'files': {'scripts/boot/helper.py': {**self.pin_path(hp), 'hostMode': mode(hp), 'transferMode': 0o444}}}
        self.archive = root / 'handoff.tar'
        self.archive.write_bytes(b'fixture handoff bytes')
        self.spec_path = root / 'spec.json'
        self.spec_extra = {}
        self.refresh()

    def write(self, name, data):
        (self.base / name).write_bytes(data)

    @staticmethod
    def pin_path(path):
        data = path.read_bytes()
        return {'bytes': len(data), 'sha256': digest(data)}

    def pin(self, name):
        return self.pin_path(self.base / name)

    def ref(self, name):
        return {'path': str(self.base / name), **self.pin(name)}

    def external(self, name, data):
        p = self.root / name
        p.write_bytes(data)
        return {'path': str(p), **self.pin_path(p)}

    def refresh(self):
        """Re-sign fixture authority so semantic controls aren't mere stale-hash tests."""
        self.write('code-pins.json', encode(self.pinmap))
        self.write('request.json', encode(self.request))
        self.write('repository-before-launch.json', encode(self.repo))
        self.write('environment/frozen/manifest.json', encode(self.manifest))
        self.write('environment/environment-result.json', encode(self.env))
        self.write(self.observer + '/result.json', encode(self.collector))
        for when in ('before', 'after'):
            self.write(self.observer + '/readonly-' + when + '.stdout.log', encode(self.witness))
        for action in ('observe', 'exec'):
            follow = copy.deepcopy(self.env)
            follow['operationStatus'] = 'observed' if action == 'observe' else 'executed'
            follow['result'] = ({'handle': self.handle} if action == 'observe' else
                                {'argv': ['/usr/bin/python3', '-B', '-c', 'import os; print(os.getuid())'],
                                 'exitCode': 0, 'stdout': '0\n', 'stderr': ''})
            self.write(self.observer + '/public-' + action + '/environment-result.json', encode(follow))
        controls = self.external('controls.json', encode(self.controls))
        review = self.external('source-review.md', b'fixture source review\n')
        markdown = self.external('actual-review.md', b'fixture independent actual review\n')
        self.closure = {'schemaVersion': 1, 'kind': 'p01-s2-reviewed-source-closure',
            'generation': self.gen, 'distro': self.distro, 'reviewedCommit': self.commit,
            'environmentReceipt': self.ref('environment/environment-result.json'),
            'codePins': self.ref('code-pins.json'), 'request': self.ref('request.json'),
            'repositoryBeforeLaunch': self.ref('repository-before-launch.json'),
            'frozenManifest': self.ref('environment/frozen/manifest.json'),
            'sourceReview': review, 'frozenControls': controls, 'files': self.files,
            'hostModeSemantics': 'Windows host-stat provenance only; not POSIX authority', **self.closure_extra}
        existing = [p for p in self.base.rglob('*') if p.is_file()
                    and 'qualification-evidence' not in p.relative_to(self.base).parts
                    and 'distro' not in p.relative_to(self.base).parts]
        for index in range(365 - len(existing)):
            self.write('filler-%03d.raw' % index, b'fixture immutable bytes\n')
        inventory = {'schemaVersion': 1, 'kind': 'p01-s2-retained-evidence-inventory',
            'status': 'sealed-awaiting-independent-review', 'generation': self.gen, 'distro': self.distro,
            'pathBase': str(self.base), 'directories': sorted(self.dirs),
            'exclusions': [{'path': 'environment/distro', 'reason': 'live VHD identity/size scope'},
                           {'path': 'qualification-evidence', 'reason': 'self-reference excluded'}],
            'files': {p.relative_to(self.base).as_posix(): self.pin_path(p)
                      for p in self.base.rglob('*') if p.is_file()
                      and 'qualification-evidence' not in p.relative_to(self.base).parts
                      and 'distro' not in p.relative_to(self.base).parts},
            'modeSemantics': 'host mode provenance, not POSIX identity',
            'formalQualification': False, 'finalSystemAccepted': False,
            'coldBuildExecuted': False, 'p01Completed': False, **self.inventory_extra}
        self.write('qualification-evidence/manifest.json', encode(inventory))
        refs = {n: self.closure[n] for n in ('environmentReceipt', 'codePins', 'request', 'sourceReview', 'frozenControls')}
        refs.update({'observerResult': self.ref(self.observer + '/result.json'),
            'observerController': self.ref(self.observer + '/controller-harness.py'),
            'standaloneWitness': self.ref(self.observer + '/readonly-witness.py'),
            'standaloneBefore': self.ref(self.observer + '/readonly-before.stdout.log'),
            'standaloneAfter': self.ref(self.observer + '/readonly-after.stdout.log'),
            'publicObserveReceipt': self.ref(self.observer + '/public-observe/environment-result.json'),
            'publicExecReceipt': self.ref(self.observer + '/public-exec/environment-result.json'),
            'evidenceManifest': self.ref('qualification-evidence/manifest.json'), 'markdownReport': markdown})
        self.verdict = {'schemaVersion': 1, 'stage': 'P01-S2', 'status': 'accepted',
            'generation': self.gen, 'distro': self.distro, 'reviewedCommit': self.commit,
            'refs': refs, 'checkedGates': self.gates,
            'dnsEquivalenceRecordsExpected': 0, 'dnsEquivalenceRecordsObserved': 0,
            'unresolvedItems': [], 'scopeLimitations': ['point-in-time only'],
            'formalQualification': False, 'finalSystemAccepted': False, 'coldBuildExecuted': False,
            'p01Completed': False, 'resourcesPreserved': True, **self.verdict_extra}
        c = self.external('closure.json', encode(self.closure))
        v = self.external('verdict.json', encode(self.verdict))
        acceptance = {'schemaVersion': 1, 'stage': 'P01-S2', 'status': 'accepted',
                      'environmentReceiptSha256': self.closure['environmentReceipt']['sha256'],
                      'reviewedSourceClosure': {k: c[k] for k in ('path', 'sha256')},
                      'actualVerifierRecord': {k: v[k] for k in ('path', 'sha256')}}
        a = self.external('acceptance.json', encode(acceptance))
        hm = encode(self.harness_manifest)
        (self.harness / 'manifest.json').write_bytes(hm)
        self.spec = {'schemaVersion': 1, 's2Acceptance': {k: a[k] for k in ('path', 'sha256')},
            'environment': {k: self.closure['environmentReceipt'][k] for k in ('path', 'sha256')},
            'handoff': {'path': str(self.archive), **self.pin_path(self.archive),
                        'manifestSha256': 'd' * 64, 'sourceDescriptorSha256': 'e' * 64},
            'cacheDescriptorSha256': 'f' * 64,
            'harness': {'path': str(self.harness), 'manifestSha256': digest(hm)},
            'generation': 'mingos-p01-s3-fixture',
            'limits': {'transferTimeoutSeconds': 30, 'phaseTimeoutSeconds': 300,
                       'hostReserveBytes': 1024, 'linuxReserveBytes': 2048, 'exportMaxBytes': 4096},
            **self.spec_extra}
        self.spec_path.write_bytes(encode(self.spec))
        self.spec_sha = digest(self.spec_path.read_bytes())


class SourceBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        try:
            self.api = importlib.import_module('source_build')
        except ModuleNotFoundError:
            self.api = None

    def require_api(self):
        self.assertIsNotNone(self.api, 'Task1 local qualification implementation is missing')

    def fixture(self):
        self.require_api()
        f = ProofFixture(self.root)
        self.enterContext(patch.object(self.api, 'OBSERVER_CODE_SHA256', digest(f.observer_bytes)))
        self.enterContext(patch.object(self.api, 'WITNESS_CODE_SHA256', digest(f.witness_bytes)))
        return f

    def reject(self, f):
        with self.assertRaises((ValueError, OSError)):
            self.api.qualify_source_build(f.spec_path, f.spec_sha)
        self.assertFalse((self.root / 'output').exists())

    def test_local_gate_has_no_runtime_or_future_api(self):
        f = self.fixture()
        with (patch.object(subprocess, 'Popen', side_effect=AssertionError('runtime forbidden')),
              patch.object(subprocess, 'run', side_effect=AssertionError('runtime forbidden'))):
            q = self.api.qualify_source_build(f.spec_path, f.spec_sha)
        self.assertEqual(q['status'], 'source-build-qualified-local')
        self.assertFalse(q['formalQualification'])
        self.assertFalse(q['finalSystemAccepted'])
        self.assertFalse(q['coldBuildExecuted'])
        self.assertFalse(hasattr(self.api, 'run_source_build'))
        self.assertFalse(hasattr(self.api, 'reopen_source_build'))

    def test_frozen_gate_names_and_observer_policy_are_literal(self):
        self.require_api()
        self.assertEqual(set(self.api.GATE_NAMES), set(GATES))
        self.assertEqual(self.api.OBSERVER_CODE_SHA256, '53ee6b3c0848d0c42b961fbcf668948e8546377123f22feeedf7e20174ec7d95')
        self.assertEqual(self.api.WITNESS_CODE_SHA256, 'ca827d6d84cf3e34fd1c5cc8b74bff5af2923b633fc2724606be276d263edb1f')

    def test_wrong_spec_and_acceptance_pin_reject(self):
        f = self.fixture()
        with self.assertRaises(ValueError):
            self.api.qualify_source_build(f.spec_path, '0' * 64)
        f.spec['s2Acceptance']['sha256'] = '0' * 64
        f.spec_path.write_bytes(encode(f.spec)); f.spec_sha = digest(f.spec_path.read_bytes())
        self.reject(f)

    def test_gate_set_mutations_reject_after_resigning(self):
        f = self.fixture()
        original = copy.deepcopy(f.gates)
        for index in range(len(GATES)):
            for change in ('remove', 'replace', 'duplicate'):
                with self.subTest(index=index, change=change):
                    f.gates = copy.deepcopy(original)
                    if change == 'remove': f.gates.pop(index)
                    elif change == 'replace': f.gates[index]['name'] = 'candidate-selected-gate'
                    else: f.gates[index] = copy.deepcopy(f.gates[(index + 1) % len(GATES)])
                    f.refresh(); self.reject(f)

    def test_verdict_flags_results_counts_and_types_reject(self):
        f = self.fixture()
        mutations = [('schemaVersion', True), ('dnsEquivalenceRecordsExpected', True),
                     ('dnsEquivalenceRecordsObserved', '0'), ('formalQualification', 0),
                     ('finalSystemAccepted', True), ('coldBuildExecuted', True),
                     ('p01Completed', True), ('resourcesPreserved', 1),
                     ('unresolvedItems', ['missing evidence']), ('unknown', None)]
        for key, value in mutations:
            with self.subTest(key=key):
                f.verdict_extra = {key: value}; f.refresh(); self.reject(f)
        f.verdict_extra = {}
        for value in ('failed', 'inconclusive', True):
            f.gates[0]['result'] = value; f.refresh(); self.reject(f)

    def test_spec_nested_types_unknown_fields_and_traversal_reject(self):
        f = self.fixture()
        original = copy.deepcopy(f.spec)
        variants = []
        for key in original:
            v = copy.deepcopy(original); v.pop(key); variants.append(v)
        for key in original['limits']:
            for bad in (True, 0, -1, '30', 1.5):
                v = copy.deepcopy(original); v['limits'][key] = bad; variants.append(v)
        for bad in ('relative', str(self.root / 'x/../spec.json')):
            v = copy.deepcopy(original); v['environment']['path'] = bad; variants.append(v)
        v = copy.deepcopy(original); v['environment']['extra'] = 1; variants.append(v)
        v = copy.deepcopy(original); v['handoff']['bytes'] = True; variants.append(v)
        v = copy.deepcopy(original); v['schemaVersion'] = True; variants.append(v)
        v = copy.deepcopy(original); v['profiles'] = ['synthetic']; variants.append(v)
        for index, value in enumerate(variants):
            with self.subTest(index=index):
                f.spec_path.write_bytes(encode(value)); f.spec_sha = digest(f.spec_path.read_bytes()); self.reject(f)

    def test_duplicate_keys_at_each_nested_json_level_reject(self):
        f = self.fixture()
        for text in ('{"schemaVersion":1,"schemaVersion":1}',
                     '{"outer":{"path":"x","path":"x"}}',
                     '{"outer":[{"bytes":1,"bytes":1}]}', '{"x":NaN}'):
            with self.subTest(text=text):
                f.spec_path.write_bytes(text.encode()); f.spec_sha = digest(text.encode()); self.reject(f)

    def test_ready_only_or_collector_renaming_never_qualifies(self):
        f = self.fixture()
        for candidate in ({'status': 'accepted'}, f.env, f.collector):
            p = self.root / 'acceptance.json'; p.write_bytes(encode(candidate))
            f.spec['s2Acceptance']['sha256'] = digest(p.read_bytes())
            f.spec_path.write_bytes(encode(f.spec)); f.spec_sha = digest(f.spec_path.read_bytes()); self.reject(f)

    def test_cross_generation_and_dirty_reviewed_helpers_reject(self):
        f = self.fixture()
        f.request['generation'] = 'mingos-p01-s2-other'; f.refresh(); self.reject(f)
        f.request['generation'] = f.gen
        f.repo['trackedWorktreeAndIndexStatus'] = ' M source.py'; f.refresh(); self.reject(f)
        f.repo['trackedWorktreeAndIndexStatus'] = ''
        f.env['helpers']['files'][HELPERS[0]]['bytes'] = True; f.refresh(); self.reject(f)

    def test_stale_reference_and_swapped_source_control_reject(self):
        f = self.fixture()
        (self.root / 'actual-review.md').write_bytes(b'changed after verdict')
        self.reject(f)
        f.refresh()
        c = self.root / 'closure.json'; value = json.loads(c.read_bytes())
        value['sourceReview'] = value['frozenControls']; c.write_bytes(encode(value))
        a = self.root / 'acceptance.json'; av = json.loads(a.read_bytes())
        av['reviewedSourceClosure']['sha256'] = digest(c.read_bytes()); a.write_bytes(encode(av))
        f.spec['s2Acceptance']['sha256'] = digest(a.read_bytes())
        f.spec_path.write_bytes(encode(f.spec)); f.spec_sha = digest(f.spec_path.read_bytes()); self.reject(f)

    def test_inventory_exact_membership_and_exclusions(self):
        f = self.fixture()
        (f.base / 'extra').write_bytes(b'unlisted'); self.reject(f)
        f.refresh()
        f.inventory_extra = {'exclusions': [{'path': 'environment', 'reason': 'candidate hides failures'},
                                           {'path': 'qualification-evidence', 'reason': 'self'}]}
        f.refresh(); self.reject(f)

    def test_inventory_file_drift_and_false_outside_member_reject(self):
        f = self.fixture()
        (f.base / 'filler-000.raw').write_bytes(b'drift'); self.reject(f)
        f.refresh()
        inv = json.loads((f.base / 'qualification-evidence/manifest.json').read_bytes())
        inv['files']['../source-review.md'] = self.pin_path(self.root / 'source-review.md')
        f.inventory_extra = {'files': inv['files']}; f.refresh(); self.reject(f)

    pin_path = staticmethod(ProofFixture.pin_path)

    def test_dns_empty_exception_requires_actual_empty_list(self):
        f = self.fixture()
        for value in (None, {}, [None]):
            f.env['oldResources']['equivalences'] = value; f.refresh(); self.reject(f)

    def test_harness_freeze_is_exclusive_requalified_and_byte_exact(self):
        f = self.fixture()
        q = self.api.qualify_source_build(f.spec_path, f.spec_sha)
        out = self.root / 'output'
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('runtime forbidden')):
            result = self.api.freeze_harness(f.spec, q, output_directory=out)
        self.assertEqual(result['status'], 'source-build-harness-frozen-local')
        self.assertEqual((out / 'harness/scripts/boot/helper.py').read_bytes(), b'print("fixture")\n')
        self.assertEqual((out / 'harness/manifest.json').read_bytes(), (f.harness / 'manifest.json').read_bytes())
        record = result['files']['scripts/boot/helper.py']
        self.assertEqual(record['transferMode'], 0o444)
        self.assertEqual(record['hostMode'], stat.S_IMODE((out / 'harness/scripts/boot/helper.py').stat().st_mode))
        with self.assertRaises(FileExistsError):
            self.api.freeze_harness(f.spec, q, output_directory=out)

    def test_freeze_rejects_forged_result_or_drift_before_allocating(self):
        f = self.fixture()
        q = self.api.qualify_source_build(f.spec_path, f.spec_sha)
        fake = copy.deepcopy(q); fake['status'] = 'accepted'
        with self.assertRaises(ValueError):
            self.api.freeze_harness(f.spec, fake, output_directory=self.root / 'output')
        self.assertFalse((self.root / 'output').exists())
        (f.base / 'public' / HELPERS[0]).write_bytes(b'late drift')
        with self.assertRaises(ValueError):
            self.api.freeze_harness(f.spec, q, output_directory=self.root / 'output')
        self.assertFalse((self.root / 'output').exists())

    def test_harness_unknown_extra_mode_and_dirty_provenance_reject(self):
        f = self.fixture()
        original = copy.deepcopy(f.harness_manifest)
        for edit in ('bad-mode', 'bool-mode', 'unknown', 'dirty', 'undeclared-parent'):
            f.harness_manifest = copy.deepcopy(original)
            if edit == 'bad-mode': f.harness_manifest['files']['scripts/boot/helper.py']['transferMode'] = 0o666
            elif edit == 'bool-mode': f.harness_manifest['directories']['scripts']['hostMode'] = True
            elif edit == 'unknown': f.harness_manifest['extra'] = 'candidate policy'
            elif edit == 'dirty': f.harness_manifest['checkout']['dirtyFiles']['changed.txt']['sha256'] = '0' * 64
            else: f.harness_manifest['directories'].pop('scripts')
            f.refresh(); self.reject(f)
        f.harness_manifest = original; f.refresh()
        (f.harness / 'extra.py').write_bytes(b'extra'); self.reject(f)

    def test_link_and_hardlink_inputs_reject(self):
        f = self.fixture()
        link = self.root / 'handoff-link'
        try:
            os.link(f.archive, link)
        except OSError as error:
            self.skipTest('hardlink unavailable: ' + str(error))
        self.reject(f)

    def test_output_overlap_rejects_before_allocation(self):
        f = self.fixture()
        q = self.api.qualify_source_build(f.spec_path, f.spec_sha)
        with self.assertRaises(ValueError):
            self.api.freeze_harness(f.spec, q, output_directory=f.harness / 'new-output')
        self.assertFalse((f.harness / 'new-output').exists())

    def test_nested_witness_integer_boolean_alias_rejects(self):
        f = self.fixture()
        f.witness = copy.deepcopy(f.witness)
        f.witness['socket']['uid'] = False
        f.refresh()
        self.reject(f)

    def test_fix1_freeze_preserves_inventory_and_deleted_path(self):
        f = self.fixture()
        q = self.api.qualify_source_build(f.spec_path, f.spec_sha)
        before = {p.relative_to(f.base).as_posix(): f.pin_path(p) for p in f.base.rglob('*') if p.is_file()}
        for output in (f.base / 'new-output', f.base / 'environment/new-output',
                       f.base / 'environment/frozen/new-output', f.harness / 'new-output',
                       f.checkout / 'deleted.txt'):
            with self.subTest(output=str(output)):
                with self.assertRaises(ValueError):
                    self.api.freeze_harness(f.spec, q, output_directory=output)
                self.assertFalse(output.exists())
                self.assertEqual(before, {p.relative_to(f.base).as_posix(): f.pin_path(p)
                                          for p in f.base.rglob('*') if p.is_file()})
                self.assertFalse((f.checkout / 'deleted.txt').exists())
                self.assertEqual(q, self.api.qualify_source_build(f.spec_path, f.spec_sha))

    def test_fix1_all_pinned_external_files_and_ancestors_protected(self):
        f = self.fixture()
        protected = self.root / 'external-proofs'; protected.mkdir()
        original_external = f.external
        def external(name, data):
            if name in ('source-review.md', 'controls.json', 'actual-review.md', 'verdict.json', 'closure.json'):
                path = protected / name; path.write_bytes(data)
                return {'path': str(path), **f.pin_path(path)}
            return original_external(name, data)
        f.external = external; f.refresh()
        q = self.api.qualify_source_build(f.spec_path, f.spec_sha)
        before = {p.name: f.pin_path(p) for p in protected.iterdir()}
        for output in (protected, *(protected / name for name in before), f.checkout / 'changed.txt'):
            with self.subTest(output=str(output)), self.assertRaises(ValueError):
                self.api.freeze_harness(f.spec, q, output_directory=output)
        self.assertEqual(before, {p.name: f.pin_path(p) for p in protected.iterdir()})
        self.assertEqual(q, self.api.qualify_source_build(f.spec_path, f.spec_sha))

    def test_fix1_exclusions_allow_siblings_but_protect_pinned_files(self):
        f = self.fixture()
        pinned_root = f.base / 'qualification-evidence/pinned'; pinned_root.mkdir()
        original_external = f.external
        def external(name, data):
            if name == 'actual-review.md':
                path = pinned_root / name; path.write_bytes(data)
                return {'path': str(path), **f.pin_path(path)}
            return original_external(name, data)
        f.external = external; f.refresh()
        q = self.api.qualify_source_build(f.spec_path, f.spec_sha)
        before = f.pin_path(pinned_root / 'actual-review.md')
        for output in (pinned_root, pinned_root / 'actual-review.md'):
            with self.subTest(output=str(output)), self.assertRaises(ValueError):
                self.api.freeze_harness(f.spec, q, output_directory=output)
        self.assertEqual(before, f.pin_path(pinned_root / 'actual-review.md'))
        for output in (f.base / 'qualification-evidence/sibling-output',
                       f.base / 'environment/distro/sibling-output', f.checkout / 'unrelated-output'):
            result = self.api.freeze_harness(f.spec, q, output_directory=output)
            self.assertEqual(result['status'], 'source-build-harness-frozen-local')
            self.assertEqual(q, self.api.qualify_source_build(f.spec_path, f.spec_sha))

    def test_fix1_guest_path_rejects_resigned_host_and_aliases(self):
        f = self.fixture()
        values = ['C:\\guest\\tomllib.py', 'tomllib.py', '/usr//lib/tomllib.py',
                  '/usr/./lib/tomllib.py', '/usr/../lib/tomllib.py', '/usr/lib/tomllib/', '/']
        if os.name == 'nt':
            values.append(str(f.spec_path))
        for value in values:
            with self.subTest(value=value):
                f.controls['interpreter']['tomllib'] = value; f.refresh(); self.reject(f)

    def test_fix1_guest_path_validation_never_reads_host_filesystem(self):
        self.require_api()
        with (patch.object(Path, 'lstat', side_effect=AssertionError('host stat forbidden')),
              patch.object(Path, 'stat', side_effect=AssertionError('host stat forbidden')),
              patch.object(os, 'stat', side_effect=AssertionError('host stat forbidden')),
              patch.object(os, 'lstat', side_effect=AssertionError('host stat forbidden')),
              patch.object(Path, 'open', side_effect=AssertionError('host open forbidden')),
              patch.object(os, 'open', side_effect=AssertionError('host descriptor forbidden'))):
            self.api._path_text('/usr/lib/python3.13/tomllib/__init__.py')
            for value in ('C:\\guest\\tomllib.py', '/usr/lib/tomllib/', 'relative.py'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.api._path_text(value)

    def test_forged_qualification_boolean_alias_rejects(self):
        f = self.fixture()
        q = self.api.qualify_source_build(f.spec_path, f.spec_sha)
        q['formalQualification'] = 0
        with self.assertRaises(ValueError):
            self.api.freeze_harness(f.spec, q, output_directory=self.root / 'output')
        self.assertFalse((self.root / 'output').exists())

    def test_import_and_help_do_not_execute_processes(self):
        self.require_api()
        with (patch.object(subprocess, 'Popen', side_effect=AssertionError('runtime forbidden')),
              patch.object(subprocess, 'run', side_effect=AssertionError('runtime forbidden'))):
            importlib.reload(self.api)
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                self.api.main(['--help'])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn('Local, externally pinned S2 proof', output.getvalue())

    def test_input_mutation_is_rejected_by_same_api_stamps(self):
        f = self.fixture()
        with self.assertRaises(ValueError):
            with self.api._open(f.archive):
                f.archive.write_bytes(b'changed while held open')

    def test_freeze_failure_retains_partial_files_and_failed_receipt(self):
        f = self.fixture()
        q = self.api.qualify_source_build(f.spec_path, f.spec_sha)
        sync = self.api._sync_directory
        def fail_at_file(path):
            if Path(path).name == 'boot':
                raise OSError('injected directory fsync failure after copy')
            return sync(path)
        out = self.root / 'output'
        with patch.object(self.api, '_sync_directory', side_effect=fail_at_file), self.assertRaises(OSError):
            self.api.freeze_harness(f.spec, q, output_directory=out)
        self.assertEqual((out / 'harness/scripts/boot/helper.py').read_bytes(), b'print("fixture")\n')
        self.assertEqual(json.loads((out / 'freeze-result.json').read_bytes())['status'], 'failed')
        with self.assertRaises(FileExistsError):
            self.api.freeze_harness(f.spec, q, output_directory=out)

    @unittest.skipUnless(os.name == 'posix', 'Linux nofollow descriptor control')
    def test_real_linked_parent_is_rejected(self):
        f = self.fixture()
        linked = self.root / 'linked'
        linked.symlink_to(f.harness, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.api._path(linked / 'scripts/boot/helper.py')


if __name__ == '__main__':
    unittest.main()
