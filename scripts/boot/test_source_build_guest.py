"""Real executable wrapper boundary; fake Docker is explicitly an external seam."""
import hashlib
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from docker_transport import DockerTransport
try:
    import source_build_guest as target
except ModuleNotFoundError:
    target = None


class EnvironmentTests(unittest.TestCase):
    def test_sanitizes_all_connection_environment_case_insensitively(self):
        self.assertIsNotNone(target, 'source_build_guest implementation missing')
        hostile = ['DOCKER_HOST', 'DOCKER_CONTEXT', 'docker_config', 'Tls_VERIFY',
                   'CONTAINERD_ADDRESS', 'CONTAINERD_NAMESPACE', 'CONTAINERD_SNAPSHOTTER',
                   'containerd_other', 'ConTaInErD_Custom', 'https_proxy', 'HTTP_PROXY',
                   'ALL_PROXY', 'NO_PROXY', 'FTP_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR']
        with mock.patch.dict(os.environ, {**dict.fromkeys(hostile, 'hostile'), 'CONTROL_KEEP': 'ok'}, clear=True):
            self.assertEqual(target.wrapper_environment(), {'CONTROL_KEEP': 'ok'})

    def test_fresh_admission_rejects_stale_handle_and_failed_child(self):
        self.assertIsNotNone(target, 'source_build_guest implementation missing')
        import wsl_environment_guest as s2
        handle = {'generation': 'control-generation', 'pid': 12, 'starttime': '34',
                  'exeSha256': 'a' * 64, 'namespaces': {'mnt': 'mnt:[1]', 'net': 'net:[2]', 'cgroup': 'cgroup:[3]'},
                  'socket': {'device': 4, 'inode': 5, 'uid': 0, 'gid': 0},
                  'daemonId': 'control-daemon',
                  'containerdNamespaces': {'containers': 'control-generation', 'plugins': 'control-generation-plugins'}}
        stale = {**handle, 'socket': {**handle['socket'], 'inode': 6}}
        with mock.patch.object(s2, 'observe_handle', return_value=stale):
            with self.assertRaisesRegex(ValueError, 'stale'):
                target._live_admission(handle, 30)
        with mock.patch.object(s2, 'observe_handle', return_value=handle), \
                mock.patch.object(s2, 'admit_environment', return_value=subprocess.CompletedProcess([], 7)):
            with self.assertRaises(ValueError): target._live_admission(handle, 30)


@unittest.skipUnless(os.name == 'posix', 'Linux executable/shebang controls')
class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(target, 'source_build_guest implementation missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.binary = self.base / 'docker-control'
        self.binary.write_text('#!' + sys.executable + '\n' +
            'import json,os,sys\n'
            'if os.environ.get("CONTROL_KOTLIN"):\n'
            ' os.write(2,b"external Docker seam\\n");sys.exit(23)\n'
            'if sys.argv[-1:] == ["binary"]:\n'
            ' os.write(1,sys.stdin.buffer.read());os.write(2,b"\\x00\\xffstderr");sys.exit(37)\n'
            'os.write(1,json.dumps({"argv":sys.argv,"env":dict(os.environ)}).encode())\n', encoding='utf8')
        self.binary.chmod(0o555)
        self.transport = DockerTransport(str(self.binary), 'unix:///run/control/docker.sock', str(self.base / 'client'))
        self.wrapper = self.base / 'docker-wrapper'

    def create(self):
        return target.create_wrapper(self.transport, self.wrapper)

    def invoke(self, args, **kwargs):
        return subprocess.run([str(self.wrapper), *args], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False, **kwargs)

    def test_actual_wrapper_argv_environment_and_no_extra_stdout(self):
        self.create()
        env = {**os.environ, 'DOCKER_HOST': 'bad', 'DoCkEr_Context': 'bad',
               'CONTAINERD_ADDRESS': 'bad', 'containerd_NAMESPACE': 'bad',
               'CONTAINERD_SNAPSHOTTER': 'bad', 'ConTaInErD_Extra': 'bad', 'https_proxy': 'bad'}
        result = self.invoke(['version', '--format', '{{json .}}'], env=env)
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        value = json.loads(result.stdout)
        self.assertEqual(value['argv'], [str(self.binary), '--host', 'unix:///run/control/docker.sock',
                         '--config', str(self.base / 'client'), 'version', '--format', '{{json .}}'])
        self.assertFalse(any(k.upper().startswith(('DOCKER_', 'TLS_', 'CONTAINERD_')) for k in value['env']))
        self.assertNotIn('https_proxy', value['env'])
        self.assertTrue(self.wrapper.is_absolute() and os.access(self.wrapper, os.X_OK))

    def test_actual_binary_stdio_and_exit_status(self):
        self.create()
        data = bytes(range(256)) * 4097
        result = self.invoke(['binary'], input=data)
        self.assertEqual(result.stdout, data)
        self.assertEqual(result.stderr, b'\x00\xffstderr')
        self.assertEqual(result.returncode, 37)

    def test_all_override_forms_rejected_before_external_executable(self):
        self.create()
        cases = []
        for flag in ['--host', '--context', '--config', '--tls', '--tlsverify',
                     '--tlscacert', '--tlscert', '--tlskey', '-H', '-c']:
            cases += [[flag, 'hostile'], [flag + '=hostile']]
        cases += [['-Hhostile'], ['-chostile'], ['-DHhostile'], ['-Dcother'],
                  ['-D', '-H', 'hostile'], ['--', '--host=hostile']]
        for args in cases:
            with self.subTest(args=args):
                result = self.invoke(args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')

    def test_frozen_module_binary_and_wrapper_pins_detect_drift(self):
        record = self.create()
        pins = target.wrapper_pins(record)
        for name in ['wrapper', 'transport', 'binary']:
            path = Path(pins[name]['path'])
            self.assertEqual(pins[name]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.wrapper.chmod(0o755)
        self.wrapper.write_text(self.wrapper.read_text() + '# mutation\n')
        with self.assertRaises(ValueError): target.wrapper_pins(record)

    def test_every_phase_repeats_live_admission_instead_of_cached_record(self):
        import wsl_environment_guest as s2
        record = self.create()
        record['admission'] = {'status': 'caller-supplied-cached-record-is-not-authority'}
        handle = {'generation': 'control-generation', 'pid': 12, 'starttime': '34',
                  'exeSha256': 'a' * 64, 'namespaces': {'mnt': 'mnt:[1]', 'net': 'net:[2]', 'cgroup': 'cgroup:[3]'},
                  'socket': {'device': 4, 'inode': 5, 'uid': 0, 'gid': 0},
                  'daemonId': 'control-daemon', 'containerdNamespaces': {}}
        observations = []
        def observe(generation, pid):
            self.assertEqual((generation, pid), ('control-generation', 12))
            value = {**handle, 'controlObservation': len(observations) + 1}
            observations.append(value)
            return value
        def admitted(supplied, argv, *, timeout):
            self.assertEqual((supplied, argv, timeout), (handle, ['/usr/bin/true'], 30))
            return subprocess.CompletedProcess(argv, 0)
        def readlink(path):
            return handle['namespaces'][path.rsplit('/', 1)[1]]
        # The privileged accepted S2 process boundary is external to this unit
        # control. All wrapper pins/traversal and repeated comparison stay real.
        with mock.patch.object(s2, 'inner_transport', return_value=self.transport), \
                mock.patch.object(s2, 'observe_handle', side_effect=observe), \
                mock.patch.object(s2, 'admit_environment', side_effect=admitted), \
                mock.patch.object(target.os, 'readlink', side_effect=readlink), \
                mock.patch.object(Path, 'read_text', return_value=str(os.getpid()) + '\n'):
            first = target.wrapper_pins(record, handle=handle)
            second = target.wrapper_pins(record, handle=handle)
        self.assertEqual(first['admission']['beforePins']['before']['controlObservation'], 1)
        self.assertEqual(first['admission']['afterPins']['after']['controlObservation'], 4)
        self.assertEqual(second['admission']['beforePins']['before']['controlObservation'], 5)
        self.assertEqual(second['admission']['afterPins']['after']['controlObservation'], 8)

    def test_exclusive_output_and_unsafe_executable(self):
        self.create()
        with self.assertRaises(FileExistsError): self.create()
        link = self.base / 'link-binary'
        link.symlink_to(self.binary)
        with self.assertRaises((ValueError, OSError)):
            target.create_wrapper(DockerTransport(str(link), self.transport.endpoint,
                                  self.transport.config_directory), self.base / 'unsafe-wrapper')

    def test_current_kotlin_runner_receives_absolute_executable_string(self):
        import kotlin_delivery
        self.create()
        repo = self.base / 'repo'; repo.mkdir()
        for name in kotlin_delivery.REQUIRED_SOURCE:
            p = repo / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'control')
        baseline = self.base / 'baseline.json'
        baseline.write_text(json.dumps({'schemaVersion': 1, 'files': {'module.jar': 'a' * 64}}))
        output = self.base / 'kotlin-result'
        # Real run -> CapturedDocker -> executable wrapper -> external seam.
        # The seam intentionally fails first image inspection; no fake success/compilation.
        with mock.patch.dict(os.environ, {'CONTROL_KOTLIN': '1'}):
            with self.assertRaisesRegex(RuntimeError, 'fixed-image'):
                kotlin_delivery.run({'repo': repo, 'baseline': baseline, 'output': output,
                    'original_cache': 'control-gradle', 'profile': 'developer-workflow',
                    'docker': str(self.wrapper)})
        command = json.loads((output / 'fixed-image.command.json').read_bytes())
        self.assertEqual(command['argv'][0], str(self.wrapper))
        self.assertEqual((output / 'fixed-image.stderr.log').read_bytes(), b'external Docker seam\n')
        self.assertEqual(json.loads((output / 'fixed-image.exit.json').read_bytes())['exitCode'], 23)
        result = json.loads((output / 'result.json').read_bytes())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['validationKind'], 'actual-docker')
        # Existing runner's actual-docker label alone cannot establish real acceptance.
        with self.assertRaises(ValueError): target.require_actual_runner_result(result)
        for bad in [{'status': 'pipeline-completed', 'validationKind': 'fake-control'},
                    {'status': 'synthetic-input-controls-passed', 'validationKind': 'actual-docker'}]:
            with self.assertRaises(ValueError): target.require_actual_runner_result(bad)

    def test_current_cache_restore_receives_absolute_executable_string(self):
        import developer_cache
        import developer_cache_worker as worker
        self.create()
        cargo, gradle = self.base / 'cargo', self.base / 'gradle'
        cargo.mkdir(); gradle.mkdir()
        (cargo / 'crate').write_bytes(b'cargo'); (cargo / 'crate').chmod(0o644)
        (gradle / 'module.jar').write_bytes(b'gradle'); (gradle / 'module.jar').chmod(0o644)
        baseline = self.base / 'baseline.json'
        baseline.write_text(json.dumps({'schemaVersion': 1, 'files': worker.file_map(worker.scan(gradle))}))
        inputs = self.base / 'inputs'; inputs.mkdir()
        worker.export_inputs(cargo, gradle, baseline, inputs)
        pin = hashlib.sha256((inputs / 'developer-inputs.json').read_bytes()).hexdigest()
        output = self.base / 'cache-result'
        with mock.patch.dict(os.environ, {'CONTROL_KOTLIN': '1'}):
            with self.assertRaisesRegex(ValueError, 'fixed-image-inspect'):
                developer_cache.restore(inputs, pin, output, docker=str(self.wrapper))
        result = json.loads((output / 'result.json').read_bytes())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['operations'][0]['command'][0], str(self.wrapper))
        self.assertEqual(result['operations'][0]['exitCode'], 23)
        self.assertEqual(result['validationKind'], 'actual-docker')
        # This exercises real restore input verification and real wrapper invocation;
        # fake external Docker fails before allocation and is never accepted.


class SourceCacheBudgetTests(unittest.TestCase):
    """Pure accounting controls; these do not observe a Windows VHD."""
    def feature(self, name):
        self.assertTrue(hasattr(target, name), 'Task4 missing ' + name)
        return getattr(target, name)

    def test_budget_counts_retained_spools_restores_both_private_caches_and_export(self):
        budget = self.feature('_source_cache_budget')(
            archive=100, harness=20, delivery=300, source=40,
            cache_archive=50, cache_payload=60, gradle=30, entries=10,
            host_reserve=7, linux_reserve=11, export_max=80)
        self.assertEqual(budget['items'], {
            'inputArchiveAndHarness': 120, 'retainedInputSpools': 120,
            'restoredDelivery': 300, 'restoredSource': 40,
            'cachePrivateArchive': 50, 'cacheRestoredPayload': 60,
            'cacheConsumers': 60, 'kotlinPrivateSources': 80,
            'kotlinPrivateGradleCaches': 60, 'kotlinWorkAndOutputs': 17179869184,
            'guestCopyAndCanonicalExport': 160, 'metadataAndLogs':268476416, 'linuxReserve': 11})
        self.assertEqual(budget['linuxTotalBytes'], 17448346661)
        self.assertEqual(budget['alreadyRetainedBytes'], 240)
        self.assertEqual(budget['linuxAdditionalBytes'], 17448346421)
        self.assertEqual(budget['hostRequiredBytes'], 17448346828)
        self.assertEqual(budget['kotlinPrepareMinimumBytes'], 8 * 1024**3)

    def test_budget_rejects_bool_negative_and_missing_values(self):
        function = self.feature('_source_cache_budget')
        good = dict(archive=100, harness=20, delivery=300, source=40,
                    cache_archive=50, cache_payload=60, gradle=30, entries=10,
                    host_reserve=7, linux_reserve=11, export_max=80)
        for key in good:
            for value in (True, -1, '100'):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    function(**{**good, key: value})
        for key in ('host_reserve','linux_reserve','export_max'):
            with self.subTest(key=key),self.assertRaises(ValueError):function(**{**good,key:0})

    def test_export_ceiling_above_prepare_floor_is_never_reduced(self):
        budget=self.feature('_source_cache_budget')(archive=1,harness=1,delivery=1,source=1,
            cache_archive=1,cache_payload=1,gradle=1,entries=1,
            host_reserve=1,linux_reserve=1,export_max=10*1024**3)
        self.assertEqual(budget['items']['kotlinWorkAndOutputs'],20*1024**3)
        self.assertEqual(budget['items']['guestCopyAndCanonicalExport'],20*1024**3)
        self.assertEqual(budget['hostRequiredBytes'],budget['linuxTotalBytes']+20*1024**3+1)

    def test_host_budget_covers_full_linux_backing_growth_without_sparse_vhd_assumption(self):
        function=self.feature('_source_cache_budget')
        arguments=dict(archive=100,harness=20,delivery=300,source=40,cache_archive=50,
            cache_payload=60,gradle=30,entries=10,host_reserve=1024**3,
            linux_reserve=2*1024**3,export_max=1024**3)
        budget=function(**arguments)
        self.assertEqual(budget['hostRequiredBytes'],budget['linuxTotalBytes']+3*1024**3)
        # Every part of the Linux ledger may need new Windows backing, including
        # retained inputs and reserves; ext4 bavail proves no physical allocation.
        for key in ('archive','harness','delivery','source','cache_archive','cache_payload',
                    'gradle','entries','linux_reserve'):
            larger=function(**{**arguments,key:arguments[key]+12345})
            with self.subTest(key=key):
                self.assertEqual(larger['hostRequiredBytes']-budget['hostRequiredBytes'],
                                 larger['linuxTotalBytes']-budget['linuxTotalBytes'])
                self.assertGreater(larger['hostRequiredBytes'],budget['hostRequiredBytes'])

    def test_sufficient_ext4_but_export_only_host_free_space_is_rejected(self):
        budget=self.feature('_source_cache_budget')(archive=100,harness=20,delivery=300,source=40,
            cache_archive=50,cache_payload=60,gradle=30,entries=10,
            host_reserve=1024**3,linux_reserve=1024**3,export_max=1024**3)
        host={'specSha256':'a'*64,'environment':{'path':'C:/environment.json','sha256':'b'*64},
            's2Generation':'s2','generation':'s3','availableBytes':3*1024**3,
            'vhd':{'path':'C:/data.vhdx','bytes':200,'device':3,'inode':4}}
        linux={'filesystemType':0xef53,'device':7,'availableBytes':100*1024**3,
               'mountinfo':'7 / ext4 rw','path':'/var/lib/s2','inode':9}
        check=self.feature('_check_source_cache_capacity')
        context=dict(spec_sha256='a'*64,environment=host['environment'],s2_generation='s2',
                     generation='s3',expected_device=7)
        with self.assertRaises(ValueError):check(budget,host,linux,**context)
        complete=budget['linuxTotalBytes']+3*1024**3
        with self.assertRaises(ValueError):check(budget,{**host,'availableBytes':complete-1},linux,**context)
        self.assertEqual(check(budget,{**host,'availableBytes':complete},linux,**context),linux)

    def test_fixed_paths_derive_from_both_generations_reject_aliases(self):
        paths = self.feature('_phase_paths')('s2-control', 's3-control')
        self.assertEqual(str(paths['archive']), '/var/lib/s2-control/s3/s3-control/inputs/handoff/handoff.tar')
        self.assertEqual(str(paths['harness']), '/var/lib/s2-control/s3/s3-control/inputs/harness')
        self.assertEqual(str(paths['output']), '/var/lib/s2-control/s3/s3-control/source-cache')
        for s2, s3 in [('x/../y', 'ok'), ('ok', '../bad'), ('', 'ok')]:
            with self.assertRaises(ValueError): target._phase_paths(s2, s3)

    def test_filtered_snapshot_keeps_full_source_identity_separate(self):
        manifest = {'files': {'src/Main.kt': {'sha256': 'a'*64},
                              'build/stale': {'sha256': 'b'*64},
                              'src/build/generated': {'sha256': 'c'*64},
                              'LICENSE': {'sha256': 'd'*64}},
                    'directories': ['build', 'src', 'src/build']}
        mapping = self.feature('_filtered_source_mapping')(manifest, 'e'*64)
        self.assertEqual(mapping['sourceDescriptorSha256'], 'e'*64)
        self.assertEqual(mapping['snapshot'], {'files': {'LICENSE': 'd'*64, 'src/Main.kt': 'a'*64},
                                               'directories': ['', 'src']})
        self.feature('_check_filtered_snapshot')(mapping, mapping['snapshot'])
        for changed in ({'files': {'LICENSE':'d'*64}, 'directories':['']},
                        {'files':mapping['snapshot']['files'], 'directories':['','src','build']}):
            with self.assertRaises(ValueError): target._check_filtered_snapshot(mapping, changed)

    def test_filtered_snapshot_accepts_original_consumer_mixed_case_recursive_order(self):
        # Literal sourceTree/snapshotTree output format from unchanged
        # kotlin_delivery_pipeline.mjs:30-33 and build-linux-host-kotlin.mjs:63-66:
        # recursive pre-order, each sibling list sorted with JS localeCompare.
        root='probes/compose-frame/coroutines-port/upstream'
        directories=['','probes','probes/compose-frame','probes/compose-frame/coroutines-port',root,
            root+'/commonMain',root+'/commonMain/kotlin',root+'/jsAndWasmSharedMain',
            root+'/META-INF',root+'/wasmWasiMain']
        files={root+'/commonMain/kotlin/Main.kt':'a'*64,root+'/META-INF/module.kotlin_module':'b'*64}
        manifest={'files':{name:{'sha256':digest} for name,digest in files.items()},
                  'directories':directories[1:]}
        mapping=self.feature('_filtered_source_mapping')(manifest,'c'*64)
        self.assertNotEqual(mapping['snapshot']['directories'],directories)
        consumer={'files':files,'directories':directories}
        try:observed=self.feature('_check_filtered_snapshot')(mapping,consumer)
        except ValueError as error:self.fail('valid unchanged consumer mixed-case order rejected: '+str(error))
        self.assertIs(observed,consumer)

    def test_filtered_snapshot_accepts_original_consumer_nested_prefix_order(self):
        # Recursive traversal visits a/deep before the sibling a-z, whereas a
        # global Python path sort puts a-z before a/deep.
        consumer={'files':{'a/deep/Main.kt':'a'*64,'a-z/Other.kt':'b'*64},
                  'directories':['','a','a/deep','a-z']}
        manifest={'files':{name:{'sha256':digest} for name,digest in consumer['files'].items()},
                  'directories':['a','a/deep','a-z']}
        mapping=self.feature('_filtered_source_mapping')(manifest,'c'*64)
        self.assertNotEqual(mapping['snapshot']['directories'],consumer['directories'])
        try:observed=self.feature('_check_filtered_snapshot')(mapping,consumer)
        except ValueError as error:self.fail('valid unchanged consumer nested-prefix order rejected: '+str(error))
        self.assertIs(observed,consumer)

    def test_filtered_snapshot_rejects_duplicate_missing_extra_and_unsafe_directory_collections(self):
        import copy
        good={'files':{'a/deep/Main.kt':'a'*64},'directories':['','a','a/deep']}
        mapping={'sourceDescriptorSha256':'b'*64,'snapshot':copy.deepcopy(good)}
        check=self.feature('_check_filtered_snapshot')
        for directories in (['','a','a','a/deep'],['','a'],['','a','a/deep','extra'],
                ['a','a/deep'],['','a/deep'],['','a','../escape'],['','a','/absolute'],
                ['','a','a//deep'],['','a','a\\deep'],['','a','a/.'],['','a',1],
                ('','a','a/deep'),None):
            with self.subTest(directories=directories),self.assertRaises(ValueError):
                check(mapping,{**good,'directories':directories})
        # Both collections need independent validation: matching malformed input
        # cannot become acceptable merely because normalization compares equal.
        for directories in (['','a','a','a/deep'],['','a/deep'],['','a','../escape'],
                            ['','a','/absolute'],['','a','a\\deep'],['','a',1]):
            invalid={**good,'directories':directories}
            with self.subTest(identical_invalid=directories),self.assertRaises(ValueError):
                check({**mapping,'snapshot':copy.deepcopy(invalid)},invalid)

    def test_filtered_snapshot_retains_exact_file_hash_path_and_type_validation(self):
        import copy
        good={'files':{'a/Main.kt':'a'*64},'directories':['','a']}
        mapping={'sourceDescriptorSha256':'b'*64,'snapshot':copy.deepcopy(good)}
        check=self.feature('_check_filtered_snapshot')
        for files in ({},{'a/Main.kt':'c'*64},{'a/Main.kt':'a'*64,'a/extra.kt':'b'*64},
                      {'a/Main.kt':1},{'a/Main.kt':'INVALID'},{'../Main.kt':'a'*64},[],None):
            with self.subTest(files=files),self.assertRaises(ValueError):
                check(mapping,{**good,'files':files})
        for invalid in ({'files':{'a/Main.kt':1},'directories':['','a']},
                        {'files':{'a/Main.kt':'INVALID'},'directories':['','a']},
                        {'files':{'a/../Main.kt':'a'*64},'directories':['','a']},
                        {'files':{'missing/Main.kt':'a'*64},'directories':['']},
                        {'files':{'a':'a'*64},'directories':['','a']},
                        {'files':[],'directories':['']},
                        {**good,'unexpected':True}):
            with self.subTest(identical_invalid=invalid),self.assertRaises(ValueError):
                check({**mapping,'snapshot':copy.deepcopy(invalid)},invalid)


@unittest.skipUnless(sys.platform == 'linux', 'Linux full-source/cache descriptor controls')
class SourceCacheInputTests(unittest.TestCase):
    """Real S1/cache bytes, with no Docker API or live admission substitution."""
    def setUp(self):
        from test_import_handoff import ImportControls
        import current_source as source
        import developer_cache_worker as cache
        import handoff_manifest as handoff
        import tarfile
        self.fixture = ImportControls(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        f = self.fixture; self.base = f.base
        inputs = f.root / 'rebuild-inputs/cache'
        (inputs / 'fixture.json').unlink()
        cargo, gradle = self.base/'cargo', self.base/'gradle'
        cargo.mkdir(); gradle.mkdir()
        (cargo/'crate').write_bytes(b'cargo'); (gradle/'module.jar').write_bytes(b'gradle')
        (cargo/'crate').chmod(0o644); (gradle/'module.jar').chmod(0o644)
        baseline = self.base/'baseline.json'
        baseline.write_bytes(source.encoded({'schemaVersion':1,'files':{'module.jar':source.sha(b'gradle')}}))
        # Export requires the recorded source ownership; explicit fixture adaptation
        # is confined to temporary roots and never represents a Docker restore.
        if os.getuid() == 0:
            for root in (cargo, gradle):
                os.chown(root,65534,65534)
                for p in root.iterdir(): os.chown(p,65534,65534)
        cache.export_inputs(cargo,gradle,baseline,inputs)
        self.cache_pin = source.sha((inputs/'developer-inputs.json').read_bytes())
        f.raw = source.encoded(handoff.make(f.root,f.runtime_pin,f.source_pin))
        (f.root/'handoff-manifest.json').write_bytes(f.raw)
        f.handoff_pin = source.sha(f.raw)
        with tarfile.open(f.archive,'w',format=tarfile.PAX_FORMAT) as archive:
            archive.add(f.root,arcname='delivery')
        f.archive_pin = source.sha(f.archive.read_bytes()); f.archive.chmod(0o444)
        self.spec = {'handoff': {'sha256':f.archive_pin,'bytes':f.archive.stat().st_size,
                    'manifestSha256':f.handoff_pin,'sourceDescriptorSha256':f.source_pin},
                    'cacheDescriptorSha256':self.cache_pin}

    def feature(self,name):
        self.assertTrue(hasattr(target,name), 'Task4 missing '+name)
        return getattr(target,name)

    def test_archive_preflight_checks_three_external_pins_and_exact_cache_siblings(self):
        result = self.feature('_source_cache_inputs')(self.spec,self.fixture.archive)
        self.assertGreater(result['deliveryBytes'],0)
        self.assertEqual(result['sourceBytes'],len(b'fixture license\n')+len(b'#!/bin/sh\nexit 0\n'))
        self.assertEqual(result['cacheDescriptorSha256'],self.cache_pin)
        self.assertEqual(result['cacheFiles'],['dependencies.tar','developer-inputs.json','gradle-baseline.json'])
        import copy
        for key in ('sha256','manifestSha256','sourceDescriptorSha256'):
            bad=copy.deepcopy(self.spec);bad['handoff'][key]='0'*64
            with self.subTest(key=key), self.assertRaises(ValueError):
                target._source_cache_inputs(bad,self.fixture.archive)
        bad=copy.deepcopy(self.spec);bad['cacheDescriptorSha256']='0'*64
        with self.assertRaises(ValueError): target._source_cache_inputs(bad,self.fixture.archive)

    def test_full_source_recheck_rejects_mutation_and_extra_directory(self):
        root,_=self.fixture.imported()
        observe=self.feature('_full_source_mapping')
        first=observe(root,self.fixture.source_pin)
        self.assertEqual(first['snapshot']['files']['bin/run'],hashlib.sha256(b'#!/bin/sh\nexit 0\n').hexdigest())
        (root/'source-repo/bin/run').write_bytes(b'changed')
        with self.assertRaises(ValueError): observe(root,self.fixture.source_pin)

    def test_cache_baseline_drift_and_extra_sibling_fail_before_restore(self):
        root,_=self.fixture.imported(); inputs=root/'delivery/rebuild-inputs/cache'
        verify=self.feature('_verified_cache_inputs')
        verify(inputs,self.cache_pin)
        (inputs/'gradle-baseline.json').write_bytes(b'{}')
        with self.assertRaises(ValueError): verify(inputs,self.cache_pin)
        (inputs/'surprise').write_bytes(b'extra')
        with self.assertRaises(ValueError): verify(inputs,self.cache_pin)

    def test_full_source_extra_directory_and_mode_change_rejected(self):
        root,_=self.fixture.imported();observe=self.feature('_full_source_mapping')
        observe(root,self.fixture.source_pin)
        (root/'source-repo/unlisted').mkdir()
        with self.assertRaises(ValueError):observe(root,self.fixture.source_pin)
        (root/'source-repo/unlisted').rmdir()
        (root/'source-repo/bin/run').chmod(0o644)
        with self.assertRaises(ValueError):observe(root,self.fixture.source_pin)

    def test_guest_harness_independently_checks_metadata_pin_modes_and_exact_members(self):
        verify=self.feature('_guest_harness')
        root=self.base/'frozen-harness';root.mkdir();root.chmod(0o555)
        # Root is temporarily writable only while constructing this explicit fixture.
        root.chmod(0o755);(root/'empty').mkdir();(root/'empty').chmod(0o555)
        helper=root/'helper.py';helper.write_bytes(b'control');helper.chmod(0o444)
        manifest={'schemaVersion':1,'kind':'p01-s3-harness-input',
                  'checkout':{'path':'C:/checkout','reviewedCommit':'a'*40,'dirtyFiles':{}},
                  'directories':{'empty':{'hostMode':511,'transferMode':365}},
                  'files':{'helper.py':{'bytes':7,'sha256':hashlib.sha256(b'control').hexdigest(),
                                        'hostMode':438,'transferMode':292}}}
        meta=root/'manifest.json';meta.write_text(json.dumps(manifest));meta.chmod(0o444);root.chmod(0o555)
        pin=hashlib.sha256(meta.read_bytes()).hexdigest()
        observed=verify(root,pin);self.assertGreater(observed['bytes'],7)
        for mutation in ('extra','mode','bytes','metadata'):
            with self.subTest(mutation=mutation):
                root.chmod(0o755)
                if mutation=='extra':(root/'extra').write_bytes(b'extra')
                if mutation=='mode':helper.chmod(0o644)
                if mutation=='bytes':helper.chmod(0o644);helper.write_bytes(b'changed');helper.chmod(0o444)
                if mutation=='metadata':meta.chmod(0o644);meta.write_bytes(b'{}');meta.chmod(0o444)
                root.chmod(0o555)
                with self.assertRaises(ValueError):verify(root,pin)
                root.chmod(0o755)
                if (root/'extra').exists():(root/'extra').unlink()
                helper.chmod(0o644);helper.write_bytes(b'control');helper.chmod(0o444)
                meta.chmod(0o644);meta.write_text(json.dumps(manifest));meta.chmod(0o444);root.chmod(0o555)
        root.chmod(0o755);(root/'empty').chmod(0o755)

    def test_initializer_rejects_nonempty_target_without_partial_chown_or_deletion(self):
        initialize=self.feature('_initialize_empty_targets')
        first,second=self.base/'first',self.base/'second';first.mkdir();second.mkdir()
        marker=second/'retain';marker.write_bytes(b'retain')
        before=first.stat()
        with self.assertRaises(ValueError):initialize([first,second])
        after=first.stat()
        self.assertEqual((before.st_uid,before.st_gid,before.st_mode),(after.st_uid,after.st_gid,after.st_mode))
        self.assertEqual(marker.read_bytes(),b'retain')
        if os.geteuid()==0:
            marker.unlink();initialize([first,second])
            for path in (first,second):
                self.assertEqual((path.stat().st_uid,path.stat().st_gid,path.stat().st_mode&0o777),(65534,65534,0o700))
        else:
            marker.unlink()
            with self.assertRaises(ValueError):initialize([first,second])

    def test_linux_capacity_observation_uses_actual_nofollow_directory_and_bavail(self):
        observe=self.feature('_linux_space')
        value=observe(self.base)
        self.assertEqual(value['device'],self.base.stat().st_dev)
        self.assertEqual(value['inode'],self.base.stat().st_ino)
        self.assertEqual(value['path'],str(self.base))
        self.assertIs(type(value['filesystemType']),int)
        self.assertGreater(value['availableBytes'],0)
        self.assertLessEqual(value['availableBytes'],os.statvfs(self.base).f_blocks*os.statvfs(self.base).f_frsize)
        self.assertIn(' - ',value['mountinfo'])
        link=self.base/'linked-capacity';link.symlink_to(self.base,target_is_directory=True)
        with self.assertRaises((ValueError,OSError)):observe(link)


class SourceTransferSignatureTests(unittest.TestCase):
    def test_phase_requires_original_task2_transfer_context(self):
        import inspect
        parameter=inspect.signature(target._source_cache_phase).parameters.get('transfer_context')
        self.assertIsNotNone(parameter,'private phase must require original Task2 transfer context')
        self.assertIs(parameter.default,inspect.Parameter.empty)

    def test_original_transfer_history_ownership_cannot_be_bypassed(self):
        self.assertTrue(hasattr(target,'_transfer_history_owner'),'original root-owned transfer histories required')
        target._transfer_history_owner((1,2,0o100600,1,0,0,8,1,1))
        for uid,gid in ((65534,0),(0,65534),(65534,65534)):
            with self.subTest(uid=uid,gid=gid),self.assertRaises(ValueError):
                target._transfer_history_owner((1,2,0o100600,1,uid,gid,8,1,1))


@unittest.skipUnless(sys.platform == 'linux', 'Linux actual transfer closure filesystem controls')
class SourceTransferClosureTests(unittest.TestCase):
    """Synthetic operation history; real nofollow trees, pins and accepted validator."""
    def setUp(self):
        import wsl_environment_transfer as transfer
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name)
        self.paths={'archive':self.base/'inputs/handoff/handoff.tar','harness':self.base/'inputs/harness',
                    'output':self.base/'source-cache'}
        for path in ('inputs/handoff','inputs/harness/scripts/boot','staging','operations/handoff','operations/harness'):
            (self.base/path).mkdir(parents=True,exist_ok=True)
        self.paths['archive'].write_bytes(b'archive');self.paths['archive'].chmod(0o444)
        adapter=self.paths['harness']/transfer.ADAPTER_ROLE
        adapter.write_bytes(b'isolated transfer adapter');adapter.chmod(0o444)
        row={'bytes':adapter.stat().st_size,'sha256':hashlib.sha256(adapter.read_bytes()).hexdigest(),
             'hostMode':0o644,'transferMode':0o444}
        manifest={'schemaVersion':1,'kind':'p01-s3-harness-input','checkout':{},
                  'directories':{name:{'hostMode':0o755,'transferMode':0o555} for name in ('scripts','scripts/boot')},
                  'files':{transfer.ADAPTER_ROLE:row}}
        meta=self.paths['harness']/'manifest.json';meta.write_text(json.dumps(manifest));meta.chmod(0o444)
        meta_pin={'bytes':meta.stat().st_size,'sha256':hashlib.sha256(meta.read_bytes()).hexdigest()}
        self.spec={'generation':'s3-control','environment':{'path':'C:/environment.json','sha256':'b'*64},
            's2Acceptance':{'path':'C:/acceptance.json','sha256':'c'*64},
            'harness':{'manifestSha256':meta_pin['sha256']},
            'handoff':{'bytes':7,'sha256':hashlib.sha256(b'archive').hexdigest()}}
        self.handle={'generation':'s2-control'}
        self.context={'specRef':{'path':'C:/spec.json','sha256':'a'*64},'environment':self.spec['environment'],
            'acceptance':self.spec['s2Acceptance'],'generation':'s3-control','s2Generation':'s2-control',
            'adapter':{key:row[key] for key in ('bytes','sha256')},'manifestPin':meta_pin}
        for name in ('context.json','staging/context.json'):self.write(name,self.context)
        self.members={'handoff':{'handoff.tar':{'bytes':7,'sha256':self.spec['handoff']['sha256'],'mode':0o444}},
            'harness':{transfer.ADAPTER_ROLE:{'bytes':row['bytes'],'sha256':row['sha256'],'mode':0o444},
                       'manifest.json':{**meta_pin,'mode':0o444}}}
        for role,nonce in (('handoff','d'*32),('harness','e'*32)):
            self.write('operations/'+role+'/owner.json',{'context':self.context,'nonce':nonce})
            self.write('operations/'+role+'/complete.json',{'status':'succeeded','context':self.context})
            for ordinal,(name,pin) in enumerate(sorted(self.members[role].items())):
                record={'schemaVersion':1,'operationNonce':nonce,'action':'receive','status':'succeeded',
                    'bytes':pin['bytes'],'sha256':pin['sha256'],'sealSha256':None,'error':None,
                    'possibleSurvivors':False,'observations':[],
                    'child':{'pid':12,'pgid':12,'sid':12,'starttime':'34'}}
                transfer.validate_operation_record(record)
                self.write('operations/'+role+'/member-'+str(ordinal)+'.json',record)
        for path in (self.paths['harness'],self.paths['harness']/'scripts',self.paths['harness']/'scripts/boot',
                     self.paths['archive'].parent):path.chmod(0o555)
        def writable():
            for path in (self.paths['harness'],self.paths['harness']/'scripts',self.paths['harness']/'scripts/boot',
                         self.paths['archive'].parent):path.chmod(0o755)
        self.addCleanup(writable)

    def write(self,name,value):
        (self.base/name).write_text(json.dumps(value))

    def observe(self):
        self.assertTrue(hasattr(target,'_completed_transfers'),'Task4 missing _completed_transfers')
        if os.geteuid()==0:
            return target._completed_transfers(self.spec,self.handle,'a'*64,self.context,self.paths)
        # Ordinary fixture workers cannot create root-owned temporary histories.
        # This ownership-only seam never stands in for actual source acceptance.
        with mock.patch.object(target,'_transfer_history_owner',return_value=None):
            return target._completed_transfers(self.spec,self.handle,'a'*64,self.context,self.paths)

    @unittest.skipUnless(hasattr(os,'geteuid') and os.geteuid()==0, 'actual root-owned history control')
    def test_actual_root_history_rejects_changed_owner_and_input_owner(self):
        self.observe()
        for path in (self.base/'context.json',self.base/'operations/handoff',self.paths['archive'],
                     self.paths['harness']/'scripts/boot/wsl_environment_transfer.py'):
            try:
                os.chown(path,65534,65534)
                with self.subTest(path=str(path)),self.assertRaises(ValueError):self.observe()
            finally:os.chown(path,0,0)

    def test_complete_original_context_and_both_role_histories_are_stable(self):
        self.assertEqual(self.observe(),self.observe())

    def test_every_original_context_binding_is_required(self):
        import copy
        self.observe()
        for field,value in (('specRef',{'path':'C:/spec.json','sha256':'f'*64}),
                ('environment',{'path':'C:/other.json','sha256':'b'*64}),
                ('acceptance',{'path':'C:/other.json','sha256':'c'*64}),
                ('generation','other'),('s2Generation','other'),('manifestPin',{'bytes':1,'sha256':'f'*64}),
                ('adapter',{'bytes':1,'sha256':'f'*64})):
            original=self.context;self.context=copy.deepcopy(original);self.context[field]=value
            try:
                with self.subTest(field=field),self.assertRaises(ValueError):self.observe()
            finally:self.context=original

    def test_root_and_staging_context_changes_are_rejected(self):
        self.observe()
        for name in ('context.json','staging/context.json'):
            self.write(name,{**self.context,'generation':'changed'})
            with self.subTest(name=name),self.assertRaises(ValueError):self.observe()
            self.write(name,self.context)

    def test_missing_or_failed_completion_and_duplicate_owner_nonce_rejected(self):
        self.observe()
        for role in ('handoff','harness'):
            path=self.base/'operations'/role/'complete.json';raw=path.read_bytes();path.unlink()
            with self.subTest(role=role),self.assertRaises((ValueError,OSError)):self.observe()
            path.write_text(json.dumps({'status':'failed','context':self.context}))
            with self.assertRaises(ValueError):self.observe()
            path.write_bytes(raw)
        self.write('operations/harness/owner.json',{'context':self.context,'nonce':'d'*32})
        with self.assertRaises(ValueError):self.observe()

    def test_member_record_pin_nonce_action_and_extra_history_rejected(self):
        self.observe();path=self.base/'operations/handoff/member-0.json';original=json.loads(path.read_bytes())
        for key,value in (('sha256','f'*64),('bytes',8),('operationNonce','f'*32),('action','export'),('possibleSurvivors',True)):
            path.write_text(json.dumps({**original,key:value}))
            with self.subTest(key=key),self.assertRaises(ValueError):self.observe()
            path.write_text(json.dumps(original))
        extra=self.base/'operations/handoff/member-00.json';extra.write_text(json.dumps(original))
        with self.assertRaises(ValueError):self.observe()

    def test_same_byte_member_replacement_changes_observation_and_symlink_rejected(self):
        first=self.observe();path=self.base/'operations/handoff/member-0.json';raw=path.read_bytes()
        saved=self.base/'saved-member';path.rename(saved);path.write_bytes(raw)
        self.assertNotEqual(first,self.observe())
        path.unlink();path.symlink_to(saved)
        with self.assertRaises((ValueError,OSError)):self.observe()


@unittest.skipUnless(sys.platform == 'linux', 'Linux phase controls; external daemon seams mocked')
class SourceCachePhaseTests(unittest.TestCase):
    """Real original import/cache inputs; privileged runtime is an explicit seam."""
    def setUp(self):
        self.data=SourceCacheInputTests();self.data.setUp();self.addCleanup(self.data.doCleanups)
        self.calls=[]
        d=self.data; self.base=d.base;self.imported,self.import_receipt=d.fixture.imported()
        self.spec={**d.spec,'generation':'s3-control','environment':{'path':'C:/environment.json','sha256':'b'*64},
                   'harness':{'path':'C:/harness','manifestSha256':'c'*64},
                   'limits':{'hostReserveBytes':1,'linuxReserveBytes':1,'exportMaxBytes':1024**3,
                             'phaseTimeoutSeconds':60,'transferTimeoutSeconds':60}}
        self.handle={'generation':'s2-control'}
        self.wrapper={'identity':{'endpoint':'unix:///run/s2-control/docker.sock'},
                      'pins':{'wrapper':{'path':'/control/wrapper'}}}
        self.host={'specSha256':'a'*64,'environment':self.spec['environment'],'s2Generation':'s2-control',
                   'generation':'s3-control','availableBytes':100*1024**3,
                   'vhd':{'path':'C:/data.vhdx','bytes':200,'device':3,'inode':4}}
        self.host_file=self.base/'host-capacity.json'; self.host_file.write_text(json.dumps(self.host))
        self.host_ref={'path':str(self.host_file),'sha256':hashlib.sha256(self.host_file.read_bytes()).hexdigest()}
        self.paths={'archive':d.fixture.archive,'harness':self.base/'harness','output':self.base/'phase'}
        self.paths['harness'].mkdir()
        self.cache={'status':'developer-cache-restored-consumers-readonly-verified','validationKind':'actual-docker',
                    'descriptorSha256':d.cache_pin,'volumeName':'e'*64,'cargoVolume':'owned-cargo',
                    'gradleVolume':'owned-gradle','restore':{'status':'payload-restored'},
                    'readonlyVerify':{'status':'payload-readonly-verified','statvfsReadOnly':True},
                    'projection':{'status':'consumer-projection-verified','sourceBeforeAfterMatched':True},
                    'consumerVerify':{'status':'consumer-readonly-content-verified','statvfsReadOnly':True},
                    'formalQualification':False,'finalSystemAccepted':False,'guestStarted':False,'resourcesPreserved':True}

    def invoke(self, *, corrupt=None, reopen=None, cache=None, space=None, closures=None):
        self.assertTrue(hasattr(target,'_source_cache_phase'),'Task4 source/cache phase missing')
        import contextlib, developer_cache, kotlin_delivery
        self.engine=mock.Mock();self.engine.volumes={'owned-import':{}};self.engine.containers=[]
        self.engine.docker='/control/wrapper'
        self.engine.register.side_effect=lambda name,*args,**kw:{'Name':name,'Driver':'local','Mountpoint':str(self.base)}
        self.calls=[]
        def restore(engine, paths, spec):
            self.calls.append(('import',str(paths['archive']),spec['handoff'].copy()))
            return self.imported,self.import_receipt
        def reopened(engine,paths,spec,root):
            self.calls.append(('reopen',str(root)))
            if corrupt:corrupt()
            return reopen or {'status':'handoff-and-source-readonly-reopened',
                'archiveSha256':self.spec['handoff']['sha256'],'handoffSha256':self.spec['handoff']['manifestSha256'],
                'sourceDescriptorSha256':self.spec['handoff']['sourceDescriptorSha256'],
                'formalQualification':False,'finalSystemAccepted':False,'coldBuildExecuted':False}
        def cache_restore(inputs,pin,output,docker):
            self.calls.append(('cache',str(inputs),pin,docker))
            return cache or self.cache
        with contextlib.ExitStack() as stack:
            if hasattr(target,'_completed_transfers'):
                stack.enter_context(mock.patch.object(target,'_completed_transfers',side_effect=closures,
                                                      return_value={'isolatedControl':True}))
            stack.enter_context(mock.patch.object(target,'_phase_paths',return_value=self.paths))
            stack.enter_context(mock.patch.object(target,'wrapper_pins',return_value={'admission':{'controlOnly':True}}))
            stack.enter_context(mock.patch.object(target,'_guest_harness',return_value={'bytes':10,'controlOnly':True}))
            stack.enter_context(mock.patch.object(target,'_linux_space',return_value=space or {'filesystemType':0xef53,
                'device':7,'availableBytes':100*1024**3,'mountinfo':'7 / ext4 rw','inode':9,'path':str(self.base)}))
            stack.enter_context(mock.patch.object(target,'_PhaseEngine',return_value=self.engine))
            stack.enter_context(mock.patch.object(target,'_restore_source',side_effect=restore))
            stack.enter_context(mock.patch.object(target,'_reopen_source',side_effect=reopened))
            stack.enter_context(mock.patch.object(target,'_reverify_consumers',return_value=self.cache['consumerVerify']))
            stack.enter_context(mock.patch.object(target,'_cache_manager_identity',side_effect=lambda record:contextlib.nullcontext()))
            stack.enter_context(mock.patch.object(developer_cache,'restore',side_effect=cache_restore))
            stack.enter_context(mock.patch.object(kotlin_delivery,'run',side_effect=AssertionError('Task4 must never compile')))
            arguments={'spec_sha256':'a'*64,'host_observation':self.host_ref,'output':self.paths['output']}
            import inspect
            if 'transfer_context' in inspect.signature(target._source_cache_phase).parameters:
                arguments['transfer_context']={'isolatedControl':True}
            return target._source_cache_phase(self.spec,self.handle,self.wrapper,**arguments)

    def test_transfer_completion_failure_precedes_output_and_docker(self):
        self.assertTrue(hasattr(target,'_completed_transfers'),'Task4 missing completed transfer closure check')
        with self.assertRaises(ValueError):self.invoke(closures=ValueError('incomplete accepted transfer'))
        self.assertEqual(self.calls,[]);self.assertFalse(self.paths['output'].exists())

    def test_changed_transfer_context_during_phase_retains_failure(self):
        self.assertTrue(hasattr(target,'_completed_transfers'),'Task4 missing completed transfer closure check')
        observations=iter([{'first':True},{'changed':True}])
        with self.assertRaises(ValueError):self.invoke(closures=lambda *a,**kw:next(observations))
        receipt=json.loads((self.paths['output']/'result.json').read_bytes())
        self.assertEqual(receipt['status'],'failed');self.assertTrue(receipt['resourcesPreserved'])

    def test_phase_orders_real_input_checks_import_independent_reopen_and_cache(self):
        result=self.invoke()
        self.assertEqual(result['status'],'source-cache-phase-completed')
        self.assertEqual([row[0] for row in self.calls],['import','reopen','cache'])
        self.assertEqual(self.calls[0][2],self.spec['handoff'])
        self.assertEqual(self.calls[-1][1],str(self.imported/'delivery/rebuild-inputs/cache'))
        self.assertEqual(self.calls[-1][3],'/control/wrapper')
        self.assertEqual(result['sourceRepo'],str(self.imported/'source-repo'))
        self.assertEqual(result['endpoint'],'unix:///run/s2-control/docker.sock')
        self.assertEqual(result['ownedVolumes'],['e'*64,'owned-cargo','owned-gradle','owned-import'])
        self.assertEqual(result['cargoReservedFor'],'S4')
        self.assertTrue(result['resourcesPreserved']);self.assertFalse(result['coldBuildExecuted'])

    def test_descriptor_mismatch_stops_before_first_docker_or_output_allocation(self):
        self.spec['cacheDescriptorSha256']='0'*64
        with self.assertRaises(ValueError): self.invoke()
        self.assertEqual(self.calls,[])
        self.assertFalse(self.paths['output'].exists())

    def test_source_mutation_after_reopen_fails_and_retains_resources(self):
        with self.assertRaises(ValueError):
            self.invoke(corrupt=lambda:(self.imported/'source-repo/LICENSE').write_bytes(b'changed'))
        receipt=json.loads((self.paths['output']/'result.json').read_bytes())
        self.assertEqual(receipt['status'],'failed');self.assertTrue(receipt['resourcesPreserved'])
        self.assertEqual(receipt['endpoint'],'unix:///run/s2-control/docker.sock')
        self.assertEqual(receipt['ownedVolumes'],['owned-import'])
        self.assertEqual([row[0] for row in self.calls],['import','reopen'])
        self.assertTrue((self.imported/'source-repo/LICENSE').exists())

    def test_missing_cache_ro_result_never_reaches_completed_phase(self):
        bad={**self.cache,'consumerVerify':{'status':'consumer-readonly-content-verified','statvfsReadOnly':False}}
        with self.assertRaises(ValueError):self.invoke(cache=bad)
        receipt=json.loads((self.paths['output']/'result.json').read_bytes())
        self.assertEqual(receipt['status'],'failed')
        self.assertEqual(receipt['ownedVolumes'],['e'*64,'owned-cargo','owned-gradle','owned-import'])
        self.assertTrue(receipt['resourcesPreserved'])

    def test_baseline_mutation_after_reopen_stops_before_cache_docker(self):
        with self.assertRaises(ValueError):
            self.invoke(corrupt=lambda:(self.imported/'delivery/rebuild-inputs/cache/gradle-baseline.json').write_bytes(b'{}'))
        self.assertEqual([row[0] for row in self.calls],['import','reopen'])
        self.assertEqual(json.loads((self.paths['output']/'result.json').read_bytes())['status'],'failed')

    def test_changed_host_capacity_pin_fails_before_docker(self):
        self.host_file.write_bytes(b'{}')
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(self.calls,[])
        self.assertFalse(self.paths['output'].exists())

    def test_non_ext4_or_exhausted_linux_stops_before_docker(self):
        for filesystem,available in [(0x9fa0,100*1024**3),(0xef53,1)]:
            with self.subTest(filesystem=filesystem,available=available),self.assertRaises(ValueError):
                self.invoke(space={'filesystemType':filesystem,'device':7,'availableBytes':available,
                                   'mountinfo':'retained raw','inode':9,'path':str(self.base)})
            self.assertEqual(self.calls,[])
            self.assertFalse(self.paths['output'].exists())


class SourceCacheReceiptTests(unittest.TestCase):
    def feature(self,name):
        self.assertTrue(hasattr(target,name),'Task4 missing '+name)
        return getattr(target,name)

    def test_cache_receipt_requires_all_independent_readonly_receipts(self):
        check=self.feature('_cache_result')
        good={'status':'developer-cache-restored-consumers-readonly-verified',
              'validationKind':'actual-docker','descriptorSha256':'a'*64,
              'volumeName':'e'*64,'cargoVolume':'owned-cargo','gradleVolume':'owned-gradle',
              'restore':{'status':'payload-restored'},'readonlyVerify':{'status':'payload-readonly-verified','statvfsReadOnly':True},
              'projection':{'status':'consumer-projection-verified','sourceBeforeAfterMatched':True},
              'consumerVerify':{'status':'consumer-readonly-content-verified','statvfsReadOnly':True},
              'formalQualification':False,'finalSystemAccepted':False,'guestStarted':False,'resourcesPreserved':True}
        self.assertEqual(check(good,'a'*64),good)
        import copy
        for field in ('restore','readonlyVerify','projection','consumerVerify'):
            bad=copy.deepcopy(good);del bad[field]
            with self.subTest(field=field), self.assertRaises(ValueError):check(bad,'a'*64)
        for key,value in [('statvfsReadOnly',False),('statvfsReadOnly',1)]:
            bad=copy.deepcopy(good);bad['consumerVerify'][key]=value
            with self.assertRaises(ValueError):check(bad,'a'*64)
        for key,value in [('validationKind','fake-control'),('cargoVolume','owned-gradle')]:
            with self.assertRaises(ValueError):check({**good,key:value},'a'*64)

    def test_effective_worker_policy_rejects_privilege_network_cgroup_and_rw_input(self):
        check=self.feature('_check_effective_worker')
        observed={'uid':65534,'gid':65534,'interfaces':['lo'],
                  'status':'CapEff:\t0000000000000000\nCapPrm:\t0000000000000000\nNoNewPrivs:\t1\nSeccomp:\t2\n',
                  'memoryMax':'4294967296','pidsMax':'256','cpuMax':'200000 100000',
                  'readOnly':{'/':True,'/harness':True,'/archive/handoff.tar':True,'/imported':True},
                  'mountinfo':'retained raw mountinfo'}
        self.assertEqual(check(observed,['/','/harness','/archive/handoff.tar','/imported']),observed)
        import copy
        for key,value in [('uid',0),('gid',0),('interfaces',['lo','eth0']),('pidsMax','128'),
                          ('memoryMax','max'),('cpuMax','max 100000'),
                          ('status',observed['status'].replace('Seccomp:\t2','Seccomp:\t0'))]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                check({**observed,key:value},['/','/harness','/archive/handoff.tar','/imported'])
        bad=copy.deepcopy(observed);bad['readOnly']['/imported']=False
        with self.assertRaises(ValueError):check(bad,['/','/harness','/archive/handoff.tar','/imported'])
        root={**observed,'uid':0,'gid':0,'status':observed['status'].replace('0000000000000000','0000000000000001')}
        check(root,['/','/harness'],root=True)
        with self.assertRaises(ValueError):check({**root,'status':root['status'].replace('0000000000000001','0000000000000003')},['/','/harness'],root=True)

    def test_independent_reopen_worker_mounts_archive_harness_and_import_readonly(self):
        function=self.feature('_reopen_source')
        import developer_cache
        engine=mock.Mock();engine.container.return_value=('d'*64,json.dumps({
            'result':{'status':'handoff-and-source-readonly-reopened'},'effective':{'control':True}}).encode())
        engine.evidence.receipt={}
        paths={'archive':Path('/fixed/handoff.tar'),'harness':Path('/fixed/harness')}
        spec={'handoff':{'sha256':'a'*64,'manifestSha256':'b'*64,'sourceDescriptorSha256':'c'*64}}
        with mock.patch.object(target,'_check_effective_worker',return_value=None):
            function(engine,paths,spec,Path('/fixed/imported'))
        args=engine.container.call_args.args
        self.assertEqual(args[0],'reopen-source')
        mounts=args[1]
        self.assertEqual({m['target'] for m in mounts},{'/archive/handoff.tar','/harness','/imported'})
        self.assertTrue(all(m['readonly'] for m in mounts))
        self.assertIn('reopen',args[2]);self.assertIn('a'*64,args[2]);self.assertIn('b'*64,args[2]);self.assertIn('c'*64,args[2])

    def test_cache_reverify_keeps_prior_source_container_ledger(self):
        function=self.feature('_reverify_consumers')
        engine=mock.Mock();engine.containers=[{'cid':'a'*64,'role':'import-source'}]
        engine.evidence.receipt={'containers':engine.containers}
        cache_engine=mock.Mock();cache_engine.containers=[]
        def run(*args):
            cache_engine.containers.append({'cid':'b'*64,'role':'reverify-cache-consumers'})
            engine.evidence.receipt['containers']=cache_engine.containers
            return 'b'*64,json.dumps({'effective':{},'result':{
                'status':'consumer-readonly-content-verified','statvfsReadOnly':True}}).encode()
        cache_engine.container.side_effect=run
        result={'volumeName':'c'*64,'cargoVolume':'cargo','gradleVolume':'gradle','descriptorSha256':'d'*64}
        with mock.patch.object(target._cache,'Engine',return_value=cache_engine), \
                mock.patch.object(target,'_check_effective_worker'):
            function(engine,{'harness':Path('/harness')},Path('/inputs'),result)
        self.assertEqual(engine.evidence.receipt['containers'],[
            {'cid':'a'*64,'role':'import-source'},{'cid':'b'*64,'role':'reverify-cache-consumers'}])

    def test_cancelled_cache_restore_reads_durable_snapshot_without_final_result(self):
        function=self.feature('_cache_failure_observation')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            snapshot={'volumeName':'a'*64,'containers':[{'cid':'b'*64,'role':'project'}]}
            (root/'receipt-0001-initial.json').write_text('{}')
            (root/'receipt-0002-progress.json').write_text(json.dumps(snapshot))
            self.assertEqual(function(root),snapshot)
            log=root/'operation-001.stdout.log';log.write_bytes(('c'*64+'\n').encode('ascii'))
            snapshot['operations']=[{'command':['/wrapper','create','--network','none'],
                'exitCode':None,'stdoutPath':str(log)}]
            (root/'receipt-0002-progress.json').write_text(json.dumps(snapshot))
            observed=function(root)
            self.assertEqual(observed['operations'][0]['retainedAllocationStdout'],'c'*64+'\n')
            self.assertIn('retainedAllocationLogIdentity',observed['operations'][0])
            (root/'result.json').write_bytes(b' '*(8*1024**2+1))
            with self.assertRaises(ValueError):function(root)

    def test_capacity_rejects_wrong_filesystem_device_low_space_and_host_context(self):
        check=self.feature('_check_source_cache_capacity')
        budget={'linuxTotalBytes':2000,'linuxAdditionalBytes':1000,'alreadyRetainedBytes':1000,
                'hostRequiredBytes':90,'kotlinPrepareMinimumBytes':8*1024**3}
        host={'specSha256':'a'*64,'environment':{'path':'C:/environment.json','sha256':'b'*64},
              's2Generation':'s2','generation':'s3','availableBytes':100,'vhd':{'path':'C:/data.vhdx','bytes':200,'device':3,'inode':4}}
        linux={'filesystemType':0xef53,'device':7,'availableBytes':1000,'mountinfo':'7 / ext4 rw',
               'path':'/var/lib/s2','inode':9}
        self.assertEqual(check(budget,host,linux,spec_sha256='a'*64,environment=host['environment'],
                               s2_generation='s2',generation='s3',expected_device=7),linux)
        import copy
        for field,value in [('filesystemType',0x9fa0),('device',8),('availableBytes',999)]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                check(budget,host,{**linux,field:value},spec_sha256='a'*64,environment=host['environment'],
                      s2_generation='s2',generation='s3',expected_device=7)
        for field,value in [('specSha256','c'*64),('generation','other'),('availableBytes',89),('vhd',None)]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                check(budget,{**host,field:value},linux,spec_sha256='a'*64,environment=host['environment'],
                      s2_generation='s2',generation='s3',expected_device=7)


class SourceHostCapacityTests(unittest.TestCase):
    """Actual Windows disk/file observations; fixture file is not an accepted VHD."""
    def test_native_host_observation_measures_free_space_and_preserves_growing_vhd_identity(self):
        self.assertTrue(hasattr(target,'_observe_host_capacity'),'Task4 host observer missing')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);vhd=root/'control.vhdx';vhd.write_bytes(b'abc')
            info=vhd.stat()
            accepted={'path':str(vhd),'bytes':2,'device':info.st_dev,'inode':info.st_ino}
            spec={'generation':'s3-control','environment':{'path':str(root/'environment.json'),'sha256':'b'*64}}
            if os.name!='nt':
                with self.assertRaises(ValueError):target._observe_host_capacity(spec,'a'*64,'s2-control',accepted)
                return
            first=target._observe_host_capacity(spec,'a'*64,'s2-control',accepted)
            self.assertEqual(first['specSha256'],'a'*64)
            self.assertEqual(first['environment'],spec['environment'])
            self.assertEqual(first['s2Generation'],'s2-control');self.assertEqual(first['generation'],'s3-control')
            self.assertEqual(first['vhd'],{**accepted,'bytes':3})
            self.assertGreater(first['availableBytes'],0)
            import shutil
            self.assertLessEqual(first['availableBytes'],shutil.disk_usage(root).total)
            vhd.write_bytes(b'abcde')
            second=target._observe_host_capacity(spec,'a'*64,'s2-control',accepted)
            self.assertEqual(second['vhd'],{**accepted,'bytes':5})
            for key,value in [('inode',info.st_ino+1),('device',info.st_dev+1),('bytes',6),('bytes',True),
                              ('path',str(root)+'/../'+root.name+'/control.vhdx')]:
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    target._observe_host_capacity(spec,'a'*64,'s2-control',{**accepted,key:value})
            directory=root/'target';directory.mkdir();(directory/'alias.vhdx').write_bytes(b'abc')
            junction=root/'junction'
            made=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(directory)],capture_output=True)
            self.assertEqual(made.returncode,0,made.stderr.decode(errors='replace'))
            try:
                alias=directory/'alias.vhdx';identity=alias.stat()
                linked={'path':str(junction/'alias.vhdx'),'bytes':3,'device':identity.st_dev,'inode':identity.st_ino}
                with self.assertRaises(ValueError):target._observe_host_capacity(spec,'a'*64,'s2-control',linked)
            finally:os.rmdir(junction)
            vhd.rename(root/'retained-original.vhdx');vhd.write_bytes(b'abcde')
            with self.assertRaises(ValueError):target._observe_host_capacity(spec,'a'*64,'s2-control',accepted)
            vhd.unlink();vhd.mkdir()
            with self.assertRaises(ValueError):target._observe_host_capacity(spec,'a'*64,'s2-control',accepted)


class CacheManagerIdentityTests(unittest.TestCase):
    """Root positive runs separately in a controller-owned management fixture.

    This is never a payload-worker confinement test or actual S2 admission.
    """
    def test_manager_identity_rejects_non_linux_or_nonroot(self):
        self.assertTrue(hasattr(target,'_cache_manager_identity'),'Task4 cache manager identity missing')
        record={}
        if sys.platform!='linux' or os.geteuid()!=0:
            with self.assertRaises(ValueError):
                with target._cache_manager_identity(record):self.fail('unqualified identity entered')
        else:
            # A root controller runs the real positive/exception controls below.
            with tempfile.TemporaryDirectory() as temp:
                with target._cache_manager_identity(record):
                    path=Path(temp)/'owned';path.mkdir(mode=0o700)
                self.assertEqual((path.stat().st_uid,path.stat().st_gid),(65534,65534))
            self.assertEqual(record['before'],record['after'])

    def test_root_manager_real_fs_creation_exception_restoration_and_no_cap_expansion(self):
        self.assertTrue(hasattr(target,'_cache_manager_identity'),'Task4 cache manager identity missing')
        if sys.platform!='linux' or os.geteuid()!=0:
            with self.assertRaises(ValueError):
                with target._cache_manager_identity({}):pass
            return
        import ctypes
        libc=ctypes.CDLL(None,use_errno=True)
        def actual():
            status=Path('/proc/self/status').read_text()
            caps={line.split(':',1)[0]:line.split(':',1)[1].strip()
                  for line in status.splitlines() if line.startswith(('CapInh:','CapPrm:','CapEff:','CapBnd:','CapAmb:'))}
            return {'resuid':list(os.getresuid()),'resgid':list(os.getresgid()),
                    'fsuid':libc.setfsuid(-1),'fsgid':libc.setfsgid(-1),'capabilities':caps}
        before=actual()
        with tempfile.TemporaryDirectory() as temp:
            for failure in (False,True):
                record={};directory=Path(temp)/('failure' if failure else 'success')
                try:
                    with target._cache_manager_identity(record):
                        during=actual()
                        self.assertEqual(during['resuid'],before['resuid'])
                        self.assertEqual(during['resgid'],before['resgid'])
                        self.assertEqual((during['fsuid'],during['fsgid']),(65534,65534))
                        self.assertEqual(during['capabilities'],before['capabilities'])
                        directory.mkdir(mode=0o700)
                        file=directory/'helper.py';fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                        os.write(fd,b'control');os.fsync(fd);os.close(fd)
                        self.assertEqual((directory.stat().st_uid,directory.stat().st_gid,directory.stat().st_mode&0o777),(65534,65534,0o700))
                        self.assertEqual((file.stat().st_uid,file.stat().st_gid,file.stat().st_mode&0o777),(65534,65534,0o600))
                        self.assertEqual(record['before'],before);self.assertEqual(record['during'],during)
                        if failure:raise RuntimeError('retained fixture failure')
                except RuntimeError as error:
                    self.assertTrue(failure);self.assertEqual(str(error),'retained fixture failure')
                self.assertEqual(actual(),before)
                self.assertEqual(record['after'],before)
                self.assertTrue(file.exists())

    def test_manager_identity_cannot_run_on_another_thread(self):
        self.assertTrue(hasattr(target,'_cache_manager_identity'),'Task4 cache manager identity missing')
        import threading
        results=[]
        def run():
            try:
                with target._cache_manager_identity({}):results.append('incorrectly-entered')
            except ValueError:results.append('rejected')
        thread=threading.Thread(target=run);thread.start();thread.join(timeout=5)
        self.assertFalse(thread.is_alive());self.assertEqual(results,['rejected'])


class SourceWorkerConfinementTests(unittest.TestCase):
    """Synthetic Docker inspections, independently checked against requested flags."""
    def engine(self, *, drift=False):
        self.assertTrue(hasattr(target,'_PhaseEngine'),'Task4 worker engine missing')
        from test_developer_cache import FakeEvidence
        class Evidence(FakeEvidence):
            def run(self,key,command,timeout,**kwargs):
                raw=super().run(key,command,timeout,**kwargs)
                args=command[1:]
                if args[0]=='create':
                    self.item['HostConfig']['PidsLimit']=int(args[args.index('--pids-limit')+1])
                    value=args[args.index('--tmpfs')+1];mount,options=value.split(':',1)
                    self.item['HostConfig']['Tmpfs']={mount:options}
                return raw
        evidence=Evidence(drift=drift)
        engine=target._PhaseEngine(evidence,'b'*32,'/control/wrapper',timeout=60)
        engine.volumes['owned-output']={}
        return engine,evidence

    def test_import_worker_uses_256_pids_bounded_tmpfs_retained_and_no_caps(self):
        import developer_cache as cache
        engine,evidence=self.engine()
        engine.container('import-source',[cache.mount('volume','owned-output','/output')],
                         ['-I','-B','/harness/scripts/boot/source_build_guest.py','import'])
        created=next(command for key,command in evidence.calls if key=='import-source-create')
        for flag,value in [('--network','none'),('--user','65534:65534'),('--cpus','2'),
                           ('--memory','4g'),('--pids-limit','256'),('--cap-drop','ALL'),
                           ('--security-opt','no-new-privileges'),('--tmpfs','/tmp:rw,nosuid,nodev,size=64m,mode=1777')]:
            self.assertEqual(created[created.index(flag)+1],value)
        self.assertIn('--read-only',created);self.assertNotIn('--cap-add',created)
        self.assertNotIn('--rm',created)
        self.assertEqual(evidence.calls[-1][0],'import-source-inspect-after')
        self.assertFalse(any(command[1] in ('rm','rmi','pull','build') for _,command in evidence.calls))

    def test_initializer_capability_is_chown_only_and_drift_prevents_execution(self):
        import developer_cache as cache
        engine,evidence=self.engine()
        engine.container('initialize-source',[cache.mount('volume','owned-output','/output')],
                         ['-I','-B','/harness/scripts/boot/source_build_guest.py','initialize'],root=True)
        created=next(command for key,command in evidence.calls if key=='initialize-source-create')
        self.assertEqual(created[created.index('--user')+1],'0:0')
        self.assertEqual(created[created.index('--cap-add')+1],'CHOWN')
        self.assertEqual(created.count('--cap-add'),1)
        engine,evidence=self.engine(drift=True)
        with self.assertRaises(ValueError):
            engine.container('import-source',[],['-I','-B','/harness/scripts/boot/source_build_guest.py','import'])
        self.assertFalse(any(command[1]=='start' for _,command in evidence.calls))


class KotlinTask5ContractTests(unittest.TestCase):
    """Synthetic boundary records exercise rejection, never compiler integration."""
    profiles = [('developer-workflow', ['notes-demo', 'animated-demo']),
                ('uninstall', ['read-file', 'image-viewer', 'text-entry-demo', 'uninstall'])]

    def feature(self, name):
        self.assertTrue(callable(getattr(target, name, None)), 'Task5 missing ' + name)
        return getattr(target, name)

    def runner_result(self, profile='developer-workflow'):
        import kotlin_delivery
        labels = dict(self.profiles)[profile]
        return {'schemaVersion': 1, 'status': 'pipeline-completed', 'profile': profile,
            'labels': labels.copy(), 'generation': 'synthetic-boundary-' + profile,
            'nonce': 'a' * 32, 'image': kotlin_delivery.IMAGE,
            'originalCacheVolume': 'original-gradle',
            'volumes': {role: profile + '-' + role for role in ('source', 'cache', 'work', 'output')},
            'validationKind': 'actual-docker', 'resourcesPreserved': True,
            'formalQualification': False, 'finalSystemAccepted': False, 'guestStarted': False,
            'replay': {'status': 'independent-readonly-replay-verified', 'originalCacheUnchanged': True,
                'results': [{'sample': 'persistent-uninstall-demo' if label == 'uninstall' else label,
                    'fixture': None, 'status': 'package-replay-verified', 'rawSha256': 'b' * 64,
                    'packagedSha256': 'c' * 64, 'formalQualification': False} for label in labels]}}

    def test_fixed_profile_order_new_output_and_original_gradle(self):
        function = self.feature('_kotlin_profiles')
        options = {'repo': Path('/fixed/source-repo'), 'output': Path('/fixed/kotlin'),
            'baseline': Path('/fixed/cache/gradle-baseline.json'),
            'original_cache': 'original-gradle', 'docker': '/fixed/docker-wrapper'}
        runs = function(options)
        self.assertEqual([row['profile'] for row in runs], [row[0] for row in self.profiles])
        self.assertEqual([row['output'] for row in runs],
                         [options['output'] / row[0] for row in self.profiles])
        self.assertEqual(len({str(row['output']) for row in runs}), 2)
        for row in runs:
            for key in ('repo', 'baseline', 'original_cache', 'docker'): self.assertEqual(row[key], options[key])
            self.assertNotIn('synthetic_controls', row)
        for key, value in [('synthetic_controls', True), ('docker', lambda *args: 'fake'),
                           ('docker', 'docker'), ('profile', 'update')]:
            with self.subTest(key=key), self.assertRaises(ValueError): function({**options, key: value})

    def test_pipeline_label_is_insufficient_and_profile_package_identity_is_exact(self):
        import copy
        check = self.feature('_kotlin_result')
        for profile, labels in self.profiles:
            good = self.runner_result(profile)
            self.assertEqual(check(good, profile), good)
            mutations = [('validationKind', 'fake-control'), ('synthetic_controls', True),
                ('compileExecuted', False), ('status', 'synthetic-input-controls-passed'),
                ('labels', list(reversed(labels))), ('image', 'sha256:' + '0' * 64)]
            for key, value in mutations:
                with self.subTest(profile=profile, field=key), self.assertRaises(ValueError): check({**good, key: value}, profile)
            bad = copy.deepcopy(good); bad['volumes']['cache'] = bad['volumes']['source']
            with self.assertRaises(ValueError): check(bad, profile)
            for key, value in [('sample', 'wrong-package'), ('fixture', 'old'), ('status', 'failed')]:
                bad = copy.deepcopy(good); bad['replay']['results'][0][key] = value
                with self.subTest(package_field=key), self.assertRaises(ValueError): check(bad, profile)
            for key, value in [('originalCacheUnchanged', False), ('results', good['replay']['results'][:-1])]:
                bad = copy.deepcopy(good); bad['replay'][key] = value
                with self.assertRaises(ValueError): check(bad, profile)

    def test_actual_private_and_copied_snapshots_and_original_prepare_8gib(self):
        import copy
        check = self.feature('_check_kotlin_prepare')
        snapshot = {'files': {'LICENSE': 'a' * 64}, 'directories': ['']}
        mapping = {'sourceDescriptorSha256': 'b' * 64, 'snapshot': snapshot}
        observation = {'privateSnapshot': copy.deepcopy(snapshot), 'privateRepoSnapshot': copy.deepcopy(snapshot),
            'originalCacheVolume': 'original-gradle', 'baselineManifestSha256': 'c' * 64,
            'cacheCopy': {'schemaVersion': 1, 'status': 'readonly-original-cache-copied-and-verified',
                'capacityBytes': 8 * 1024 ** 3, 'originalCacheVolume': 'original-gradle',
                'baselineManifestSha256': 'c' * 64, 'original': {'files': {'compiler.jar': 'd' * 64}, 'directories': ['']},
                'formalQualification': False, 'finalSystemAccepted': False}}
        self.assertEqual(check(mapping, snapshot, observation), observation)
        for key in ('privateSnapshot', 'privateRepoSnapshot'):
            bad = copy.deepcopy(observation); bad[key]['files']['LICENSE'] = 'e' * 64
            with self.subTest(snapshot=key), self.assertRaises(ValueError): check(mapping, snapshot, bad)
        changed = {'files': {**snapshot['files'], 'extra': 'a' * 64}, 'directories': ['']}
        with self.assertRaises(ValueError): check(mapping, changed, observation)
        for key, value in [('capacityBytes', 8 * 1024 ** 3 - 1), ('capacityBytes', True),
                ('status', 'passed'), ('originalCacheVolume', 'other'), ('baselineManifestSha256', 'f' * 64)]:
            bad = copy.deepcopy(observation); bad['cacheCopy'][key] = value
            with self.subTest(field=key), self.assertRaises(ValueError): check(mapping, snapshot, bad)

    def test_copy_replay_requires_exact_files_directories_modes_and_all_six_packages(self):
        import copy
        check = self.feature('_check_copy_replay')
        inventory = {'rootMode': 0o700, 'directories': {'packages': {'mode': 0o700}},
            'files': {'packages/evidence.json': {'mode': 0o600, 'bytes': 17, 'sha256': 'a' * 64}}}
        replay = {'status': 'copied-output-readonly-replay-verified', 'statvfsReadOnly': True,
            'compileExecuted': False, 'profiles': [row[0] for row in self.profiles],
            'results': [result for profile, _ in self.profiles for result in self.runner_result(profile)['replay']['results']],
            'formalQualification': False, 'finalSystemAccepted': False}
        self.assertEqual(check(inventory, copy.deepcopy(inventory), replay), replay)
        changes = []
        bad = copy.deepcopy(inventory); bad['files']['extra'] = bad['files']['packages/evidence.json'].copy(); changes.append(bad)
        bad = copy.deepcopy(inventory); bad['directories']['empty'] = {'mode': 0o700}; changes.append(bad)
        bad = copy.deepcopy(inventory); bad['files']['packages/evidence.json']['mode'] = 0o644; changes.append(bad)
        bad = copy.deepcopy(inventory); bad['files']['packages/evidence.json']['sha256'] = 'b' * 64; changes.append(bad)
        bad = copy.deepcopy(inventory); bad['rootMode'] = 0o755; changes.append(bad)
        for bad in changes:
            with self.subTest(inventory=bad), self.assertRaises(ValueError): check(inventory, bad, replay)
        for key, value in [('statvfsReadOnly', False), ('statvfsReadOnly', 1), ('compileExecuted', True),
                ('results', replay['results'][:-1]), ('profiles', ['uninstall', 'developer-workflow'])]:
            with self.subTest(replay=key), self.assertRaises(ValueError): check(inventory, inventory, {**replay, key: value})


class KotlinTask5ExecutionTests(unittest.TestCase):
    """External Docker/RO replay seams are synthetic; no actual compile is claimed."""
    def feature(self, name):
        self.assertTrue(callable(getattr(target, name, None)), 'Task5 missing ' + name)
        return getattr(target, name)

    def test_unchanged_runner_called_twice_under_manager_with_guards_and_independent_replay(self):
        import contextlib, kotlin_delivery
        execute = self.feature('_execute_kotlin_profiles')
        for name in ('_kotlin_checkpoint', '_inspect_kotlin_output', '_observe_kotlin_tools', '_replay_kotlin_copy'):
            self.feature(name)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); calls = []; inside = [False]
            cache = {'sourceRepo': str(root / 'imported/source-repo'), 'cache': {'gradleVolume': 'original-gradle',
                'cargoVolume': 'reserved-cargo'}, 'sourceImport': {'importedDirectory': str(root / 'imported')}}
            paths = {'output': root / 'phase', 'harness': root / 'harness', 'archive': root / 'handoff.tar'}
            spec = {'handoff': {'sourceDescriptorSha256': 'b' * 64}, 'limits': {'phaseTimeoutSeconds': 60}}
            mapping = {'sourceDescriptorSha256': 'b' * 64, 'snapshot': {'files': {}, 'directories': ['']}}
            wrapper = {'pins': {'wrapper': {'path': '/fixed/docker-wrapper'}}}
            engine = mock.Mock(); engine.evidence.receipt = {}; engine.containers = []; engine.volumes = {}
            engine.kotlin_context = {'outputInventories': {}}
            engine.evidence.root = root / 'kotlin'; engine.evidence.root.mkdir()
            def registered(name, label):
                profile = 'uninstall' if name.startswith('uninstall-') else 'developer-workflow'
                return {'Labels': {'org.mingos.generation': 'synthetic-boundary-' + profile, 'org.mingos.nonce': 'a' * 32}}
            engine.register.side_effect = registered
            @contextlib.contextmanager
            def manager(record):
                self.assertFalse(inside[0]); inside[0] = True; calls.append(('manager-enter',))
                try: yield
                finally: inside[0] = False; calls.append(('manager-exit',))
            def runner(options, *args, **kwargs):
                self.assertTrue(inside[0], 'unchanged helper creation needs fsuid/fsgid context')
                self.assertEqual(args, ()); self.assertEqual(kwargs, {}, 'never inject callable Docker')
                self.assertEqual(options['docker'], '/fixed/docker-wrapper')
                self.assertEqual(options['original_cache'], 'original-gradle')
                self.assertNotIn('synthetic_controls', options)
                self.assertEqual(Path(options['repo']), Path(cache['sourceRepo']))
                self.assertEqual(Path(options['baseline']), root / 'imported/delivery/rebuild-inputs/cache/gradle-baseline.json')
                calls.append(('run', options['profile'], str(options['output'])))
                return KotlinTask5ContractTests().runner_result(options['profile'])
            def boundary(name):
                def invoke(*args, **kwargs):
                    calls.append((name,))
                    return {'syntheticExternalBoundary': True, 'inventory': {'rootMode': 0o700, 'directories': {}, 'files': {}}}
                return invoke
            with mock.patch.object(kotlin_delivery, 'run', side_effect=runner), \
                    mock.patch.object(target, '_cache_manager_identity', side_effect=manager), \
                    mock.patch.object(target, '_kotlin_checkpoint', side_effect=boundary('guard')), \
                    mock.patch.object(target, '_reverify_consumers', side_effect=boundary('cache-ro')), \
                    mock.patch.object(target, '_inspect_kotlin_output', side_effect=boundary('private-snapshot-prepare')), \
                    mock.patch.object(target, '_initialize_profile_copy', side_effect=boundary('new-copy-owner')), \
                    mock.patch.object(target, '_observe_kotlin_tools', side_effect=boundary('independent-tool-bytes')), \
                    mock.patch.object(target, '_replay_kotlin_copy', side_effect=boundary('copied-output-ro')):
                execute(engine, paths, spec, cache, mapping, wrapper, {'generation': 's2-control'})
            runs = [item for item in calls if item[0] == 'run']
            self.assertEqual([row[1] for row in runs], ['developer-workflow', 'uninstall'])
            self.assertEqual(len({row[2] for row in runs}), 2)
            for boundary in ('private-snapshot-prepare', 'independent-tool-bytes', 'copied-output-ro'):
                self.assertEqual(sum(row[0] == boundary for row in calls), 2)
            self.assertGreaterEqual(sum(row[0] == 'cache-ro' for row in calls), 4)
            # A fresh source/cache/pin/live guard surrounds each runner and
            # independent observation/replay, rather than a once-only admission.
            external = {'run', 'private-snapshot-prepare', 'independent-tool-bytes', 'copied-output-ro'}
            substantive = [row[0] for row in calls if row[0] not in ('manager-enter', 'manager-exit', 'cache-ro')]
            for index, name in enumerate(substantive):
                if name in external:
                    self.assertEqual(substantive[index - 1], 'guard', name)
                    self.assertEqual(substantive[index + 1], 'guard', name)
            self.assertFalse(inside[0])

    def test_private_phase_rejects_wrong_ref_path_before_any_runner_or_allocation(self):
        import kotlin_delivery
        phase = self.feature('_kotlin_phase')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); original = root / 'phase'; original.mkdir()
            wrong = root / 'wrong-result.json'; wrong.write_text('{}')
            ref = {'path': str(wrong), 'bytes': 2, 'sha256': hashlib.sha256(b'{}').hexdigest()}
            with mock.patch.object(target, '_phase_paths', return_value={'output': original}), \
                    mock.patch.object(kotlin_delivery, 'run') as runner:
                with self.assertRaises(ValueError):
                    phase({'generation': 's3'}, {'generation': 's2'}, {}, spec_sha256='a' * 64,
                          transfer_context={}, source_cache_ref=ref, output=root / 'kotlin')
                runner.assert_not_called()
            self.assertFalse((root / 'kotlin').exists())
            self.assertEqual(wrong.read_bytes(), b'{}')

    def test_compiler_chain_rejects_missing_logs_skipped_task_predecessor_and_package_mismatch(self):
        import copy
        check = self.feature('_check_kotlin_compiler')
        sample = 'notes-demo'; label = sample
        command = ['java', '--offline', '--rerun-tasks', '--no-build-cache', '--no-configuration-cache',
                   ':notes-demo:compileProductionExecutableKotlinWasmWasi']
        stdout = b'> Task :notes-demo:compileProductionExecutableKotlinWasmWasi\nBUILD SUCCESSFUL\n'
        stderr = b''
        package = {'schemaVersion': 1, 'status': 'passed', 'sample': sample, 'fixture': None,
            'offline': True, 'compilerExitCode': 0, 'compilerSignal': None, 'command': command,
            'stdoutSha256': hashlib.sha256(stdout).hexdigest(), 'stderrSha256': hashlib.sha256(stderr).hexdigest(),
            'formalQualification': False, 'finalSystemAccepted': False, 'package': {'sdkVersion': 6}}
        chain = {'schemaVersion': 1, 'label': label, 'sample': sample, 'fixture': None,
            'status': 'passed', 'exitCode': 0, 'signal': None, 'previous': None}
        self.assertEqual(check(package, chain, stdout, stderr, label=label, previous=None), package)
        for changed in (None, b'', stdout.replace(b'\nBUILD', b' UP-TO-DATE\nBUILD'),
                        stdout.replace(b'\nBUILD', b' FROM-CACHE\nBUILD')):
            # Keep the candidate's digest internally consistent: a skipped task
            # must fail for not executing, not merely for a stale log hash.
            candidate = {**package, 'stdoutSha256': hashlib.sha256(changed or b'').hexdigest()}
            with self.subTest(log=changed), self.assertRaises(ValueError):
                check(candidate, chain, changed, stderr, label=label, previous=None)
        with self.assertRaises(ValueError): check(package, chain, stdout, None, label=label, previous=None)
        for key, value in [('sample', 'animated-demo'), ('compilerExitCode', 7), ('offline', False),
                ('command', command[1:3]), ('package', {'sdkVersion': 5})]:
            with self.subTest(package=key), self.assertRaises(ValueError):
                check({**package, key: value}, chain, stdout, stderr, label=label, previous=None)
        bad = copy.deepcopy(chain); bad['previous'] = {'label': 'foreign', 'sha256': 'f' * 64}
        with self.assertRaises(ValueError): check(package, bad, stdout, stderr, label=label, previous=None)
        with self.assertRaises(ValueError): check(package, {**chain, 'status': 'failed'}, stdout, stderr, label=label, previous=None)

    def test_motion_sdk5_and_uninstall_actual_sample_are_required(self):
        check = self.feature('_check_kotlin_compiler')
        for label, sample, sdk in [('animated-demo', 'animated-demo', 5),
                                   ('uninstall', 'persistent-uninstall-demo', 6)]:
            stdout = ('> Task :' + sample + ':compileProductionExecutableKotlinWasmWasi\n').encode()
            package = {'schemaVersion': 1, 'status': 'passed', 'sample': sample, 'fixture': None,
                'offline': True, 'compilerExitCode': 0, 'compilerSignal': None,
                'command': ['java', '--offline', '--rerun-tasks', '--no-build-cache', '--no-configuration-cache',
                            ':' + sample + ':compileProductionExecutableKotlinWasmWasi'],
                'stdoutSha256': hashlib.sha256(stdout).hexdigest(), 'stderrSha256': hashlib.sha256(b'').hexdigest(),
                'formalQualification': False, 'finalSystemAccepted': False, 'package': {'sdkVersion': sdk}}
            previous = {'label': 'text-entry-demo', 'sha256': 'd' * 64} if label == 'uninstall' else None
            chain = {'schemaVersion': 1, 'label': label, 'sample': sample, 'fixture': None,
                'status': 'passed', 'exitCode': 0, 'signal': None, 'previous': previous}
            self.assertEqual(check(package, chain, stdout, b'', label=label, previous=previous), package)
            with self.assertRaises(ValueError):
                check({**package, 'package': {'sdkVersion': 6 if sdk == 5 else 5}}, chain, stdout, b'', label=label, previous=previous)

    def test_exported_copy_worker_invokes_frozen_glue_with_original_source_cache_and_copy_readonly(self):
        function = self.feature('_replay_kotlin_copy')
        self.feature('_node_worker')
        engine = mock.Mock(); engine.evidence.root = Path('/fixed/kotlin')
        result = KotlinTask5ContractTests().runner_result()
        result['outputDirectory'] = '/fixed/kotlin/payload/developer-workflow'
        inventory = {'rootMode': 0o700, 'directories': {}, 'files': {}}
        engine.kotlin_context = {'outputInventories': {result['profile']: inventory}}
        observation = {'inventory': inventory, 'copiedInventory': inventory, 'results': result['replay']['results'],
            'status': 'profile-copy-readonly-replay-verified', 'statvfsReadOnly': True, 'compileExecuted': False}
        with mock.patch.object(target, '_node_worker', return_value=observation) as worker:
            function(engine, {'harness': Path('/fixed/frozen-harness')}, result,
                {'sourceDescriptorSha256': 'b' * 64, 'snapshot': {'files': {}, 'directories': ['']}},
                {'executables': {}, 'lockedFiles': {}})
        args = worker.call_args.args
        self.assertEqual(args[3], 'replay-copy')
        mounts = args[2]
        self.assertTrue(mounts and all(row['readonly'] for row in mounts))
        mounted = {row['target']: row for row in mounts}
        # Host Path separators vary; preserve exact logical fixed-tree spelling
        # and normalize separators only, without case-folding or resolving '..'.
        self.assertEqual(mounted['/out']['source'].replace('\\', '/'), result['outputDirectory'] + '/packages-and-evidence')
        self.assertEqual(mounted['/repo']['source'], result['volumes']['source'])
        self.assertEqual(mounted['/repo']['subpath'], 'repo')
        self.assertEqual(mounted['/deps/gradle']['source'], result['volumes']['cache'])
        self.assertEqual(mounted['/original-cache']['source'], result['originalCacheVolume'])
        self.assertEqual(mounted['/harness']['source'].replace('\\', '/'), '/fixed/frozen-harness')
        self.assertFalse(any('work' == row['target'].strip('/') or not row['readonly'] for row in mounts))

    @unittest.skipUnless(os.name == 'posix', 'actual Linux statvfs writable-copy rejection')
    def test_peer_inventory_rejects_real_writable_copy_even_with_equal_bytes(self):
        function = self.feature('_readonly_inventory_pair')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); original = root / 'original'; copied = root / 'copy'
            original.mkdir(); copied.mkdir()
            for directory in (original, copied): (directory / 'same').write_bytes(b'synthetic identical bytes')
            self.assertFalse(os.statvfs(copied).f_flag & os.ST_RDONLY)
            with self.assertRaisesRegex(ValueError, 'read-only'): function(original, copied)


@unittest.skipUnless(sys.platform == 'linux', 'Linux original Task4 phase-ref controls; external admission mocked')
class KotlinTask5PhaseRefTests(unittest.TestCase):
    """Consume real retained Task4 result bytes with synthetic external admission.

    These controls do not qualify S2 or demonstrate a compiler/image integration.
    """
    def setUp(self):
        self.assertTrue(callable(getattr(target, '_kotlin_phase', None)), 'Task5 missing _kotlin_phase')
        self.fixture = SourceCachePhaseTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.invoke()
        self.result = self.fixture.paths['output'] / 'result.json'
        self.original = self.result.read_bytes()

    def invoke(self, ref=None):
        import kotlin_delivery
        data = self.result.read_bytes()
        ref = ref or {'path': str(self.result), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        fixture = self.fixture
        with mock.patch.object(target, '_phase_paths', return_value=fixture.paths), \
                mock.patch.object(target, 'wrapper_pins', return_value={'admission': {'controlOnly': True}}), \
                mock.patch.object(target, '_completed_transfers', return_value={'isolatedControl': True}), \
                mock.patch.object(kotlin_delivery, 'run', side_effect=AssertionError('invalid Ref must never compile')):
            return target._kotlin_phase(fixture.spec, fixture.handle, fixture.wrapper, spec_sha256='a' * 64,
                transfer_context={'isolatedControl': True}, source_cache_ref=ref,
                output=fixture.paths['output'].parent / 'kotlin')

    def test_changed_raw_pin_and_failed_or_context_mismatched_result_fail_closed(self):
        original_ref = {'path': str(self.result), 'bytes': len(self.original),
                        'sha256': hashlib.sha256(self.original).hexdigest()}
        self.result.write_bytes(self.original + b' ')
        with self.assertRaises(ValueError): self.invoke(original_ref)
        for key, value in [('status', 'failed'), ('specSha256', 'f' * 64),
                ('generation', 'other-s3'), ('s2Generation', 'other-s2'),
                ('endpoint', 'unix:///foreign.sock'), ('formalQualification', True),
                ('transferClosure', {'foreign': True})]:
            record = json.loads(self.original); record[key] = value
            self.result.write_text(json.dumps(record))
            with self.subTest(field=key), self.assertRaises(ValueError): self.invoke()
            self.assertFalse((self.fixture.base / 'kotlin').exists())
        self.result.write_bytes(self.original)
        for key, value in [('bytes', True), ('bytes', len(self.original) - 1), ('sha256', 'e' * 64)]:
            with self.subTest(ref=key), self.assertRaises(ValueError): self.invoke({**original_ref, key: value})
        self.assertEqual(self.result.read_bytes(), self.original)




class KotlinNewCopyOwnershipTests(unittest.TestCase):
    """Synthetic copy provenance controls; actual Docker cp remains Task7."""
    def feature(self, name):
        value = getattr(target, name, None)
        self.assertTrue(callable(value), 'Task5 missing ' + name)
        return value

    def fixture(self):
        inventory = {'root': {'uid': 0, 'gid': 0, 'mode': 0o700},
            'directories': {'chain': {'uid': 0, 'gid': 0, 'mode': 0o755}},
            'files': {'chain/receipt.json': {'uid': 0, 'gid': 0, 'mode': 0o444,
                'bytes': 2, 'sha256': hashlib.sha256(b'{}').hexdigest(), 'nlink': 1}}}
        proof = {'path': '/fixed/kotlin/payload/uninstall/packages-and-evidence',
            'endpoint': 'unix:///var/run/docker.sock', 'copierCID': 'a' * 64,
            'command': ['cp', 'a' * 64 + ':/out', '/fixed/kotlin/payload/uninstall/packages-and-evidence'],
            'exitCode': 0, 'freshCopy': True, 'exclusive': True, 'writersStopped': True}
        return proof, inventory

    def test_exact_successful_exclusive_copy_and_uniform_owner_are_required(self):
        check = self.feature('_check_new_copy')
        proof, inventory = self.fixture()
        self.assertEqual(check(proof, inventory, path=proof['path'], endpoint=proof['endpoint']), 0)
        for key, value in [('path', '/foreign'), ('endpoint', 'unix:///foreign.sock'),
                           ('copierCID', 'b' * 64), ('exitCode', 1), ('freshCopy', False),
                           ('exclusive', False), ('writersStopped', False)]:
            with self.subTest(field=key), self.assertRaises(ValueError):
                check({**proof, key: value}, inventory, path=proof['path'], endpoint=proof['endpoint'])
        for owner in [(123, 123), (0, 65534), (65534, 0)]:
            changed = copy.deepcopy(inventory)
            changed['files']['chain/receipt.json'].update(uid=owner[0], gid=owner[1])
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                check(proof, changed, path=proof['path'], endpoint=proof['endpoint'])
        owned = copy.deepcopy(inventory)
        for row in [owned['root'], *owned['directories'].values(), *owned['files'].values()]:
            row.update(uid=65534, gid=65534)
        self.assertEqual(check(proof, owned, path=proof['path'], endpoint=proof['endpoint']), 65534)

    def test_links_and_unbounded_or_escaping_members_are_rejected(self):
        check = self.feature('_check_new_copy')
        proof, inventory = self.fixture()
        for key, value in [('nlink', 2), ('kind', 'symlink'), ('mode', 0o120777)]:
            changed = copy.deepcopy(inventory); changed['files']['chain/receipt.json'][key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                check(proof, changed, path=proof['path'], endpoint=proof['endpoint'])
        changed = copy.deepcopy(inventory)
        changed['files']['../foreign'] = changed['files'].pop('chain/receipt.json')
        with self.assertRaises(ValueError): check(proof, changed, path=proof['path'], endpoint=proof['endpoint'])

    @unittest.skipUnless(sys.platform == 'linux', 'actual Linux UID65534 nofollow copied-tree dispatch control')
    def test_actual_worker_owned_mode0700_copy_selects_unprivileged_readonly_observation(self):
        """Real filesystem input; Docker/inspection/effective policy is a synthetic seam."""
        function = self.feature('_initialize_profile_copy')
        self.assertEqual((os.getuid(), os.getgid()), (65534, 65534), 'ordinary Linux controller must run this real ownership control as UID/GID65534')
        with tempfile.TemporaryDirectory() as temp:
            phase = Path(temp) / 'kotlin'; phase.mkdir()
            output = phase / 'payload' / 'uninstall'; output.mkdir(parents=True)
            copied = output / 'packages-and-evidence'; copied.mkdir(mode=0o700)
            nested = copied / 'chain'; nested.mkdir(mode=0o700)
            member = nested / 'receipt.json'; member.write_bytes(b'{}'); member.chmod(0o444)
            before_root = os.lstat(copied)
            self.assertEqual((before_root.st_uid, before_root.st_gid, before_root.st_mode & 0o7777), (65534, 65534, 0o700))
            self.assertFalse(copied.is_symlink())
            cid = 'a' * 64; wrapper = '/fixed/synthetic-docker-boundary'
            def write(name, value): (output / name).write_text(json.dumps(value))
            write('output-copy.command.json', {'argv': [wrapper, 'cp', cid + ':/out', str(copied)]})
            write('output-copy.exit.json', {'exitCode': 0})
            write('verify-all-create.command.json', {'argv': [wrapper, 'create', 'synthetic-inspect-boundary']})
            write('result.json', {'syntheticExternalBoundary': True})
            result = {'profile': 'uninstall', 'outputDirectory': str(output),
                'containers': [{'label': 'verify-all', 'cid': cid, 'mounts': []}]}
            engine = mock.Mock(); engine.evidence.root = phase; engine.docker = wrapper
            engine.kotlin_context = {'endpoint': 'unix:///synthetic-boundary.sock'}
            actual_inspection = {'Id': cid, 'syntheticExternalBoundary': True, 'State': {'Running': False, 'ExitCode': 0}}
            engine.inspect.return_value = actual_inspection
            expected_inventory = {'root': {'uid': 65534, 'gid': 65534, 'mode': 0o700},
                'directories': {'chain': {'uid': 65534, 'gid': 65534, 'mode': 0o700}},
                'files': {'chain/receipt.json': {'uid': 65534, 'gid': 65534, 'mode': 0o444,
                    'bytes': 2, 'sha256': hashlib.sha256(b'{}').hexdigest(), 'nlink': 1}}}
            observed_requests = []
            def worker(label, mounts, command, **kwargs):
                self.assertIs(kwargs.get('root', False), False,
                    'already UID65534-owned mode0700 copy must select unprivileged observer; CHOWN-only root cannot reopen it')
                mapped = {row['target']: row for row in mounts}
                self.assertEqual(mapped['/new-copy']['source'], str(copied))
                self.assertTrue(all(row['readonly'] is True for row in mounts), 'already-owned observation must mount every input RO')
                request = json.loads(Path(mapped['/request/request.json']['source']).read_text())
                observed_requests.append(request)
                # The external worker seam actually reopens this real tree with
                # O_NOFOLLOW and reads all owners/modes/bytes, then runs the
                # production complete-owner policy. No caller owner flag suffices.
                rootfd = os.open(copied, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                childfd = os.open('chain', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
                filefd = os.open('receipt.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=childfd)
                try:
                    def owner(fd):
                        st = os.fstat(fd); return {'uid': st.st_uid, 'gid': st.st_gid, 'mode': st.st_mode & 0o7777}
                    st = os.fstat(filefd); raw = os.pread(filefd, st.st_size + 1, 0)
                    inventory = {'root': owner(rootfd), 'directories': {'chain': owner(childfd)},
                        'files': {'chain/receipt.json': {**owner(filefd), 'bytes': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest(), 'nlink': st.st_nlink}}}
                    self.assertEqual(inventory, expected_inventory)
                    self.assertEqual(target._check_new_copy(request['proof'], inventory,
                        path=str(copied), endpoint=engine.kotlin_context['endpoint']), 65534)
                finally:
                    os.close(filefd); os.close(childfd); os.close(rootfd)
                observed = {'syntheticExternalBoundary': True}
                receipt = {'result': {'status': 'new-copy-ownership-already-owned', 'before': inventory,
                    'after': copy.deepcopy(inventory), 'proof': request['proof'], 'effective': observed},
                    'effective': observed, 'effectiveAfter': observed}
                return 'b' * 64, json.dumps(receipt).encode()
            engine.container.side_effect = worker
            with mock.patch.object(target._kotlin, 'expected_inspection', return_value={'syntheticExternalBoundary': True}), \
                    mock.patch.object(target._kotlin, 'check_inspection') as inspection, \
                    mock.patch.object(target, '_check_effective_worker', side_effect=lambda observed, *args, **kwargs: observed):
                value = function(engine, {'harness': Path('/fixed/synthetic-harness')}, result)
            inspection.assert_called_once_with(actual_inspection, {'syntheticExternalBoundary': True}, finished=True)
            self.assertEqual(len(observed_requests), 1)
            self.assertEqual(value['result']['status'], 'new-copy-ownership-already-owned')
            self.assertEqual(value['result']['before'], expected_inventory)
            self.assertEqual(value['result']['after'], expected_inventory)
            self.assertEqual((os.lstat(copied).st_uid, os.lstat(copied).st_gid,
                              os.lstat(copied).st_mode & 0o7777), (65534, 65534, 0o700))
            self.assertEqual(member.read_bytes(), b'{}')
            member.chmod(0o600)

    @unittest.skipUnless(sys.platform == 'linux', 'actual Linux owned-copy descriptor observation control')
    def test_owned_copy_observer_checks_real_full_inventory_and_rejects_links(self):
        """Real UID/files/descriptor inventory; only RO mount flags are a synthetic boundary."""
        observe = self.feature('_observe_owned_copy')
        self.assertEqual((os.getuid(), os.getgid()), (65534, 65534))
        actual_inventory = target._seal._inventory
        ro_calls = []
        def synthetic_ro_mount(root, *, read_only=False):
            self.assertIs(read_only, True, 'production observer must request actual statvfs RO verification')
            ro_calls.append(str(root))
            # No runtime mount is created by this unit test. Every traversal,
            # nofollow open, full byte hash, mode and owner comes from real files.
            return actual_inventory(root, read_only=False)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'packages-and-evidence'; root.mkdir(mode=0o700)
            child = root / 'nested'; child.mkdir(mode=0o700)
            member = child / 'receipt.json'; member.write_bytes(b'{}'); member.chmod(0o444)
            proof, _ = self.fixture(); proof['path'] = str(root); proof['command'][-1] = str(root)
            initial = target._seal._stamp(os.lstat(root)); proof['copyRootIdentity'] = list(initial)
            with mock.patch.object(target._seal, '_inventory', side_effect=synthetic_ro_mount):
                value = observe(root, proof=proof, endpoint=proof['endpoint'])
                self.assertEqual(value['status'], 'new-copy-ownership-already-owned')
                self.assertIs(value['statvfsReadOnly'], True)
                self.assertEqual(value['before'], value['after'])
                self.assertEqual(value['before']['root'], {'uid': 65534, 'gid': 65534, 'mode': 0o700})
                self.assertEqual(value['before']['directories'], {'nested': {'uid': 65534, 'gid': 65534, 'mode': 0o700}})
                self.assertEqual(value['before']['files'], {'nested/receipt.json': {'uid': 65534, 'gid': 65534,
                    'mode': 0o444, 'bytes': 2, 'sha256': hashlib.sha256(b'{}').hexdigest(), 'nlink': 1}})
                self.assertEqual(value['rootIdentityBefore'], list(initial)); self.assertEqual(value['rootIdentityAfter'], list(initial))
                self.assertEqual(ro_calls, [str(root), str(root)])
                foreign_root = {**proof, 'copyRootIdentity': [*initial[:1], initial[1] + 1, *initial[2:]]}
                with self.assertRaises(ValueError): observe(root, proof=foreign_root, endpoint=proof['endpoint'])
                alias = child / 'alias.json'; os.link(member, alias)
                try:
                    with self.assertRaises(ValueError): observe(root, proof=proof, endpoint=proof['endpoint'])
                finally: alias.unlink()
                alias.symlink_to('receipt.json')
                try:
                    with self.assertRaises((ValueError, OSError)): observe(root, proof=proof, endpoint=proof['endpoint'])
                finally: alias.unlink()
            self.assertEqual(member.read_bytes(), b'{}'); self.assertEqual(os.lstat(member).st_mode & 0o7777, 0o444)
            self.assertEqual((os.lstat(root).st_uid, os.lstat(root).st_gid), (65534, 65534))
            member.chmod(0o600)

    @unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0, 'actual new-copy descriptor fchown requires Linux CHOWN-only root controller')
    def test_actual_owned_copy_skips_chown_and_preserves_modes_bytes(self):
        initialize = self.feature('_initialize_new_copy')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'packages-and-evidence'; root.mkdir(mode=0o700)
            item = root / 'receipt.json'; item.write_bytes(b'{}'); item.chmod(0o444)
            proof, _ = self.fixture(); proof['path'] = str(root); proof['command'][-1] = str(root)
            effective = {'uid': 0, 'gid': 0, 'capEff': 1, 'capBnd': 1, 'noNewPrivs': 1}
            before = item.read_bytes(); mode = item.stat().st_mode & 0o7777
            rootfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            itemfd = os.open(item, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                result = initialize(root, proof=proof, endpoint=proof['endpoint'], effective=effective)
                self.assertEqual(result['status'], 'new-copy-ownership-initialized')
                self.assertEqual(os.pread(itemfd, len(before), 0), before); self.assertEqual(os.fstat(itemfd).st_mode & 0o7777, mode)
                self.assertEqual((os.fstat(rootfd).st_uid, os.fstat(rootfd).st_gid, os.fstat(itemfd).st_uid, os.fstat(itemfd).st_gid), (65534,) * 4)
                for key, value in [('uid', 65534), ('capEff', 3), ('capBnd', 3), ('noNewPrivs', 0)]:
                    with self.subTest(policy=key), self.assertRaises(ValueError):
                        initialize(root, proof=proof, endpoint=proof['endpoint'], effective={**effective, key: value})
            finally:
                os.fchown(itemfd, 0, 0); os.fchown(rootfd, 0, 0)
                os.close(itemfd); os.close(rootfd)
                item.chmod(0o600)

if __name__ == '__main__': unittest.main()
