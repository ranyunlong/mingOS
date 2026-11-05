"""Driver contracts: no Docker, VM, disk or paid calls are executed by these tests."""
import importlib.util
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent

def driver():
    spec=importlib.util.spec_from_file_location('update_stage',HERE/'run-stage.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


class DriverTests(unittest.TestCase):
    def host_test_packages(self, stage, repo):
        task='artifacts/persistent-update-regression-fixtures-20261105'
        sources=['settings.gradle.kts','build.gradle.kts','gradle.properties',
                 'gradle/wrapper/gradle-wrapper.jar','gradle/wrapper/gradle-wrapper.properties',
                 'scripts/wasm-sdk-adapter.mjs','probes/compose-wasi/port/wasm-imports.mjs',
                 'ming-sdk/src/Test.kt','ming-compose/src/Test.kt','probes/compose-frame/coroutines-port/Test.kt']
        for sample in ('image-viewer','read-file'):
            sources += ['samples/'+sample+'/app.json','samples/'+sample+'/src/Main.kt']
        for name in sources:
            path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
        for sample,sdk in [('image-viewer',2),('read-file',1)]:
            stage.write(repo/'samples'/sample/'app.json',{'schemaVersion':1,'id':'dev.ming.'+sample,
                        'version':'0.1.0','sdkVersion':sdk,'entry':'main.wasm'},exclusive=False)
        result={}
        for sample,sdk in [('image-viewer',2),('read-file',1)]:
            directory=repo/'artifacts'/sample;directory.mkdir(parents=True)
            manifest={'schemaVersion':1,'id':'dev.ming.'+sample,'version':'0.1.0','sdkVersion':sdk,'entry':'main.wasm'}
            stage.write(directory/'app.json',manifest)
            (repo/'samples'/sample/'app.json').write_bytes((directory/'app.json').read_bytes())
            for name in ('compiler.wasm','main.wasm'):(directory/name).write_bytes(b'\x00asm\x01\x00\x00\x00')
            info={'kotlin':'2.3.20','adapterVersion':1,'rawSha256':stage.sha(directory/'compiler.wasm'),
                  'packagedSha256':stage.sha(directory/'main.wasm'),'imports':[],'exports':[],
                  'rewrites':[{'from':'wasi_snapshot_preview1.random_get','to':'ming_v2.random_fill'}] if sdk==2 else []}
            stage.write(directory/'build-info.json',info)
            evidence={}
            for name in ('source-freeze.json','toolchain.json','network/profile.sb',sample+'/stdout.log',sample+'/stderr.log'):
                path=repo/task/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
                if name=='source-freeze.json':stage.write(path,{'sourceCommit':'a'*40,'files':{source:stage.sha(repo/source) for source in sources}},exclusive=False)
                evidence[str(path.relative_to(repo))]=stage.sha(path)
            receipt={'schemaVersion':1,'status':'passed','sample':sample,'offline':True,'compilerExitCode':0,
                     'sourceCommit':'a'*40,'rebuiltFromFrozenSource':True,'rebuiltThisQualification':False,
                     'rawToPackageReplayVerified':True,'abiValidatedAgainstCurrentEngine':True,
                     'sourceFreezePath':task+'/source-freeze.json','sourceFreezeSha256':evidence[task+'/source-freeze.json'],
                     'stdoutPath':task+'/'+sample+'/stdout.log','stdoutSha256':evidence[task+'/'+sample+'/stdout.log'],
                     'stderrPath':task+'/'+sample+'/stderr.log','stderrSha256':evidence[task+'/'+sample+'/stderr.log'],
                     'inputSha256':{name:stage.sha(repo/name) for name in sources},'evidenceSha256':evidence,
                     'manifestSha256':stage.sha(directory/'app.json'),'package':info,
                     'command':['java','--offline','--rerun-tasks','--no-build-cache',':'+sample+':compileProductionExecutableKotlinWasmWasi'],
                     'network':{'platform':'darwin','nonLoopbackDenied':True,'loopbackAllowed':True,
                                'profileSha256':evidence[task+'/network/profile.sb'],'pythonProbeExitCode':0,'javaProbeExitCode':0},
                     'toolchainEvidence':{'path':task+'/toolchain.json','sha256':evidence[task+'/toolchain.json']}}
            stage.write(directory/'build-receipt.json',receipt);result[sample]=receipt
        return result

    def test_host_test_packages_are_required_at_both_production_test_paths(self):
        with tempfile.TemporaryDirectory() as work:
            stage=driver();repo=Path(work).resolve();self.host_test_packages(stage,repo)
            self.assertTrue(hasattr(stage,'host_test_inputs'),'driver must preflight the real all-targets fixtures')
            value=stage.host_test_inputs(repo)
            self.assertEqual(set(value),{'image-viewer','read-file'})
            (repo/'artifacts/read-file/main.wasm').unlink()
            with self.assertRaisesRegex(ValueError,'host test fixture'):stage.host_test_inputs(repo)

    def test_host_test_package_bytes_sources_receipts_and_links_are_bound(self):
        with tempfile.TemporaryDirectory() as work:
            stage=driver();repo=Path(work).resolve();receipts=self.host_test_packages(stage,repo)
            self.assertTrue(hasattr(stage,'host_test_inputs'),'driver must validate all-targets fixture provenance')
            stage.host_test_inputs(repo)
            directory=repo/'artifacts/image-viewer'
            for name in ('main.wasm','app.json','build-info.json'):
                path=directory/name;original=path.read_bytes()
                if name=='build-info.json':stage.write(path,dict(stage.read(path),adapterVersion=99),exclusive=False)
                else:path.write_bytes(original+b' ')
                with self.assertRaises(ValueError,msg=name):stage.host_test_inputs(repo)
                path.write_bytes(original)
            for key,value in [('offline',False),('compilerExitCode',False),('rebuiltFromFrozenSource',False),
                              ('rawToPackageReplayVerified',False),('abiValidatedAgainstCurrentEngine',False),
                              ('inputSha256',{}),('evidenceSha256',{}),('sourceFreezeSha256','b'*64)]:
                receipt=copy.deepcopy(receipts['image-viewer']);receipt[key]=value
                stage.write(directory/'build-receipt.json',receipt,exclusive=False)
                with self.assertRaises(ValueError,msg=key):stage.host_test_inputs(repo)
            stage.write(directory/'build-receipt.json',receipts['image-viewer'],exclusive=False)
            source=repo/'ming-sdk/src/Test.kt';source.write_text('changed')
            with self.assertRaises(ValueError):stage.host_test_inputs(repo)
            source.write_text('ming-sdk/src/Test.kt')
            extra=directory/'extra.wasm';extra.write_bytes(b'extra')
            with self.assertRaises(ValueError):stage.host_test_inputs(repo)
            extra.unlink();path=directory/'main.wasm';raw=path.read_bytes();path.unlink()
            path.symlink_to(directory/'compiler.wasm')
            with self.assertRaises(ValueError):stage.host_test_inputs(repo)
            path.unlink();path.write_bytes(raw);os.link(path,directory/'linked')
            with self.assertRaises(ValueError):stage.host_test_inputs(repo)

    def test_source_freeze_includes_host_test_packages_and_external_build_evidence(self):
        with tempfile.TemporaryDirectory() as work:
            stage,repo,prep=self.preparation_repo(work);self.host_test_packages(stage,repo)
            captured=stage.capture_source(repo,prep)
            self.assertIn('artifacts/image-viewer/main.wasm',captured)
            self.assertIn('artifacts/read-file/main.wasm',captured)
            self.assertIn('artifacts/persistent-update-regression-fixtures-20261105/image-viewer/stdout.log',captured)
            path=repo/'artifacts/read-file/main.wasm';path.write_bytes(path.read_bytes()+b'changed')
            with self.assertRaisesRegex(ValueError,'source changed'):stage.verify_source_files({'files':captured},repo,prep)

    def test_build_preflight_rejects_missing_host_test_fixture_before_cache_copy(self):
        with tempfile.TemporaryDirectory() as work:
            stage=driver();root=Path(work).resolve();(root/'build').mkdir();(root/'inputs/payload').mkdir(parents=True)
            with mock.patch.object(stage,'copy_build_cache',side_effect=AssertionError('cache copy ran before fixture preflight')):
                with self.assertRaisesRegex(ValueError,'host test fixture'):
                    stage.inside_build(root,root,{'hostTestFixtures':{},'environment':{'qualificationId':'test'}})

    def run_build_explicit_tests(self, work, outcomes=None):
        stage=driver();repo=Path(work).resolve();root=repo/'prep';(root/'build').mkdir(parents=True)
        (root/'inputs/payload').mkdir(parents=True);self.host_test_packages(stage,repo)
        paths={'bootstrap':root/'external-inputs/bootstrap/main.wasm',
               'old':repo/'artifacts/persistent-update-demo/old/main.wasm',
               'new':repo/'artifacts/persistent-update-demo/new/main.wasm'}
        for path in paths.values():path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
        freeze={'hostTestFixtures':stage.host_test_inputs(repo),'environment':{'qualificationId':str(repo/'qualification')},
                'fixture':{name:{'packagedSha256':stage.sha(path)} for name,path in paths.items()}}
        calls=[]
        def cache_copy(source,cache,record):
            for name in ('ming-app-host','examples/prepare_persistent_update_fixture'):
                path=cache/'target/release'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'executable')
        def execute(directory,command,timeout,*,cwd=None):
            calls.append((command,dict(os.environ),directory,timeout,cwd));directory.mkdir()
            recovery='persistent_update::recovery_tests::' in command
            stdout='test result: ok. 18 passed; 0 failed; 1 ignored; 0 measured; 200 filtered out; finished in 0.01s\n' if recovery else \
                   'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 200 filtered out; finished in 0.01s\n'
            stdout,exit_code=(outcomes or {}).get(directory.name,(stdout,0))
            (directory/'stdout.log').write_text(stdout)
            return {'exitCode':exit_code}
        class PayloadReady(Exception):pass
        with mock.patch.dict(os.environ),mock.patch.object(stage,'copy_build_cache',side_effect=cache_copy), \
             mock.patch.object(stage,'execute',side_effect=execute), \
             mock.patch.object(stage,'review_from_payload',side_effect=PayloadReady):
            try:stage.inside_build(root,repo,freeze)
            except (PayloadReady,RuntimeError,ValueError) as error:return calls,root,error,PayloadReady
        self.fail('build must stop at the controlled payload boundary')

    def test_build_runs_exact_ignored_public_integration_with_ready_worker_and_fixtures(self):
        with tempfile.TemporaryDirectory() as work:
            calls,root,error,ready=self.run_build_explicit_tests(work)
            public=[call for call in calls if '--test' in call[0]]
            self.assertEqual(len(public),1,'ignored public integration must run explicitly')
            command,environment,directory,timeout,cwd=public[0]
            self.assertEqual(command,['cargo','+1.99.1','test','--offline','--locked','--release','-p','ming-app-host',
                '--test','persistent_update_runtime','separate_process_roles_and_real_stable_host_refuse_active_update_without_writes',
                '-j1','--','--exact','--nocapture','--ignored','--test-threads=1'])
            self.assertEqual(calls.index(public[0]),8,'all five Cargo prerequisites and three real controller checks run first')
            self.assertEqual(cwd,Path(work).resolve());self.assertEqual(timeout,900)
            for name in ('MING_TEST_PRIVATE_WORKER','MING_UPDATE_FIXTURE_HELPER','MING_UPDATE_BOOTSTRAP_WASM',
                         'MING_UPDATE_OLD_WASM','MING_UPDATE_NEW_WASM'):
                self.assertTrue(Path(environment[name]).is_file(),name)
            self.assertIsInstance(error,ready)
            self.assertTrue((root/'inputs/payload/ming-app-host').is_file())

    def test_build_stops_before_payload_for_failed_or_unexecuted_public_integration(self):
        passed='test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n'
        cases=[('nonzero',passed,101,RuntimeError),
               ('zero matches',passed.replace('1 passed','0 passed'),0,ValueError),
               ('ignored',passed.replace('1 passed','0 passed').replace('0 ignored','1 ignored'),0,ValueError),
               ('failed',passed.replace('ok. 1 passed; 0 failed','FAILED. 0 passed; 1 failed'),0,ValueError),
               ('eleven passed',passed.replace('1 passed','11 passed'),0,ValueError),
               ('duplicate summaries',passed+passed,0,ValueError),('missing summary','running 0 tests\n',0,ValueError)]
        for name,stdout,exit_code,expected in cases:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as work:
                calls,root,error,_=self.run_build_explicit_tests(work,{'explicit-update-public':(stdout,exit_code)})
                self.assertIsInstance(error,expected)
                self.assertEqual(len(calls),9)
                self.assertEqual(list((root/'inputs/payload').iterdir()),[])

    def test_build_runs_complete_recovery_parent_matrix_once_serially(self):
        with tempfile.TemporaryDirectory() as work:
            calls,root,error,ready=self.run_build_explicit_tests(work)
            recovery=[call for call in calls if any('persistent_update::recovery_tests' in part for part in call[0])]
            self.assertEqual(len(recovery),1,'complete matrix replaces duplicate exact recovery parents')
            command,_,directory,timeout,cwd=recovery[0]
            self.assertEqual(command,['cargo','+1.99.1','test','--offline','--locked','--release','-p','ming-app-host',
                '--lib','persistent_update::recovery_tests::','-j1','--','--nocapture','--test-threads=1'])
            self.assertEqual(calls.index(recovery[0]),9);self.assertEqual(len(calls),10)
            self.assertEqual(cwd,Path(work).resolve());self.assertEqual(timeout,900)
            controllers=[call for call in calls if any(part.startswith('ming_desktop::persistent_update_runtime::') for part in call[0])]
            self.assertEqual([call[0][9] for call in controllers],[
                'ming_desktop::persistent_update_runtime::migrated_and_absent_write_commit_only_after_real_ready_close_reap',
                'ming_desktop::persistent_update_runtime::absent_nonempty_runtime_failure_rolls_back_original_missing_namespace',
                'ming_desktop::persistent_update_runtime::killed_host_recovers_in_independent_process'])
            for command,_,_,timeout,_ in controllers:
                self.assertIn('--exact',command);self.assertIn('--ignored',command);self.assertEqual(timeout,900)
            for command,_,_,timeout,_ in calls[:5]:
                self.assertTrue({'--offline','--locked','--release','-j1'}<=set(command));self.assertEqual(timeout,1800)
            self.assertIsInstance(error,ready)

    def test_build_stops_for_incomplete_or_changed_recovery_matrix(self):
        passed='test result: ok. 18 passed; 0 failed; 1 ignored; 0 measured; 200 filtered out; finished in 0.01s\n'
        cases=[('nonzero',passed,101,RuntimeError),('missing summary','',0,ValueError),
               ('zero matches',passed.replace('18 passed','0 passed').replace('1 ignored','0 ignored'),0,ValueError),
               ('missing parent',passed.replace('18 passed','17 passed'),0,ValueError),
               ('additional parent',passed.replace('18 passed','19 passed'),0,ValueError),
               ('missing child',passed.replace('1 ignored','0 ignored'),0,ValueError),
               ('extra ignored',passed.replace('1 ignored','2 ignored'),0,ValueError),
               ('failed',passed.replace('ok. 18 passed; 0 failed','FAILED. 17 passed; 1 failed'),0,ValueError),
               ('measured',passed.replace('0 measured','1 measured'),0,ValueError),
               ('duplicate summaries',passed+passed,0,ValueError)]
        for name,stdout,exit_code,expected in cases:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as work:
                calls,root,error,_=self.run_build_explicit_tests(work,{'explicit-update-recovery':(stdout,exit_code)})
                self.assertIsInstance(error,expected);self.assertEqual(len(calls),10)
                self.assertEqual(list((root/'inputs/payload').iterdir()),[])

    def test_build_rejects_misleading_controller_test_summaries(self):
        passed='test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 200 filtered out; finished in 0.01s\n'
        for stdout in (passed.replace('1 passed','11 passed'),passed+passed,
                       passed.replace('0 measured','1 measured')):
            with self.subTest(stdout=stdout),tempfile.TemporaryDirectory() as work:
                calls,root,error,_=self.run_build_explicit_tests(work,{'explicit-update-1':(stdout,0)})
                self.assertIsInstance(error,ValueError);self.assertEqual(len(calls),6)
                self.assertEqual(list((root/'inputs/payload').iterdir()),[])

    def test_host_test_export_preserves_exact_frozen_packages_and_build_provenance(self):
        with tempfile.TemporaryDirectory() as work:
            stage=driver();repo=Path(work).resolve();self.host_test_packages(stage,repo)
            self.assertTrue(hasattr(stage,'export_host_test_inputs'),'offline export must retain real Kotlin build evidence')
            names={str(path.relative_to(repo)) for path in (repo/'artifacts').rglob('*') if path.is_file()}
            freeze={'hostTestFixtures':stage.host_test_inputs(repo),'files':{name:stage.sha(repo/name) for name in names}}
            output=repo/'export';output.mkdir();stage.export_host_test_inputs(repo,freeze,output,[])
            copied=stage.file_hashes(output/'host-test-inputs')
            self.assertEqual(set(copied),names|{'manifest.json'})
            self.assertEqual(stage.read(output/'host-test-inputs/manifest.json')['files'],freeze['files'])
            self.assertEqual((output/'host-test-inputs/artifacts/read-file/main.wasm').read_bytes(),b'\x00asm\x01\x00\x00\x00')
            missing=repo/'missing';missing.mkdir();del freeze['files']['artifacts/read-file/main.wasm']
            with self.assertRaisesRegex(ValueError,'frozen host test input'):stage.export_host_test_inputs(repo,freeze,missing,[])
            self.assertFalse((missing/'host-test-inputs').exists())

    def test_host_test_export_rejects_source_change_during_copy_before_writing_manifest(self):
        with tempfile.TemporaryDirectory() as work:
            stage=driver();repo=Path(work).resolve();self.host_test_packages(stage,repo)
            names={str(path.relative_to(repo)) for path in (repo/'artifacts').rglob('*') if path.is_file()}
            freeze={'hostTestFixtures':stage.host_test_inputs(repo),'files':{name:stage.sha(repo/name) for name in names}}
            output=repo/'export';output.mkdir();copy_regular=stage.copy_regular
            def replace_then_copy(source,target,excluded):
                if source==repo/'artifacts/read-file/main.wasm':source.write_bytes(b'changed between validation and copy')
                copy_regular(source,target,excluded)
            with mock.patch.object(stage,'copy_regular',side_effect=replace_then_copy):
                with self.assertRaisesRegex(ValueError,'host test input changed during export'):
                    stage.export_host_test_inputs(repo,freeze,output,[])
            self.assertFalse((output/'host-test-inputs/manifest.json').exists())

    def preparation_repo(self, work):
        stage=driver();repo=Path(work).resolve()/'repo';repo.mkdir()
        (repo/'.gitignore').write_text('artifacts/\n')
        # The tools image has no Git; only the host's source-list boundary is
        # synthetic. File enumeration, mount identity and hashes remain real.
        def host_source_listing(argv,**kwargs):
            self.assertEqual(argv,['git','ls-files','-z','--cached','--others','--exclude-standard'])
            self.assertEqual(kwargs,{'cwd':repo,'capture_output':True,'check':True})
            return mock.Mock(stdout=b'.gitignore\0')
        listing=mock.patch.object(stage.subprocess,'run',side_effect=host_source_listing)
        listing.start();self.addCleanup(listing.stop)
        prep=repo/'artifacts/prep2';prep.mkdir(parents=True)
        (prep/'run-stage.py').write_text('pass\n')
        (prep/'verify-persistent-update-evidence.py').write_text('pass\n')
        for name in ('mingos-isolation-probe','vm-prepare.sh'):
            path=repo/'probes/linux-isolation-source-v2/build-current'/name
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
        return stage,repo,prep

    def test_new_preparation_generation_freeze_uses_its_own_host_root(self):
        with tempfile.TemporaryDirectory() as work:
            stage,repo,prep=self.preparation_repo(work)
            (prep/'inputs').mkdir()
            inputs={}
            for number,plan in enumerate(stage.input_contracts(),1):
                path=prep/'inputs'/('input-boot%d.json'%number)
                stage.write(path,plan);inputs['boot%dSha256'%number]=stage.sha(path)
            files=stage.capture_source(repo,prep)
            stage.write(prep/'source-inventory.json',sorted(files))
            freeze={'schemaVersion':1,'profile':stage.PROFILE,'repo':str(repo),'preparedAt':'test',
                    'sourceCommit':'test','files':files,'sourceInventorySha256':stage.sha(prep/'source-inventory.json'),
                    'environment':stage.environment_contract('persistent-update-20261104-qual1-test',prep),
                    'runtime':{},'budget':stage.execution_budget(*stage.input_contracts()),'inputs':inputs,
                    'checker':{'path':str((prep/'verify-persistent-update-evidence.py').relative_to(repo)),
                               'sha256':stage.sha(prep/'verify-persistent-update-evidence.py')},
                    'fixture':{},'hostTestFixtures':{},'finalSystemAccepted':False}
            stage.write(prep/'freeze.json',freeze)
            with mock.patch.object(stage,'fixture_inputs',return_value={}),mock.patch.object(stage,'host_test_inputs',return_value={}),mock.patch.object(stage,'validate_runtime_inputs'):
                self.assertEqual(stage.verify_freeze(prep,repo),freeze)
                mapped=Path(work).resolve()/'mapped-prep';mapped.symlink_to(prep,target_is_directory=True)
                self.assertEqual(stage.verify_freeze(mapped,repo),freeze)
                freeze['environment']['hostBackingRoot']=str(repo/'artifacts/other/apfs-backing'/freeze['environment']['qualificationId'])
                stage.write(prep/'freeze.json',freeze,exclusive=False)
                with self.assertRaisesRegex(ValueError,'frozen limits'):
                    stage.verify_freeze(prep,repo)

    def test_mapped_new_generation_detects_prep_additions_and_byte_changes(self):
        with tempfile.TemporaryDirectory() as work:
            stage,repo,prep=self.preparation_repo(work)
            frozen=stage.capture_source(repo,prep)
            stage.write(prep/'source-inventory.json',sorted(frozen))
            mapped=Path(work).resolve()/'mapped-prep';mapped.symlink_to(prep,target_is_directory=True)
            self.assertEqual(stage.capture_source(repo,mapped),frozen)
            (prep/'added.py').write_text('new\n')
            with self.assertRaisesRegex(ValueError,'file set'):
                stage.verify_source_files({'files':frozen},repo,mapped)
            (prep/'added.py').unlink();(prep/'run-stage.py').write_text('changed\n')
            with self.assertRaisesRegex(ValueError,'source changed'):
                stage.verify_source_files({'files':frozen},repo,mapped)

    def test_mapped_preparation_must_be_same_directory_as_inventory_source(self):
        with tempfile.TemporaryDirectory() as work:
            stage,repo,prep=self.preparation_repo(work)
            frozen=stage.capture_source(repo,prep)
            other=Path(work).resolve()/'other-prep';other.mkdir()
            stage.write(other/'source-inventory.json',sorted(frozen))
            (other/'run-stage.py').write_bytes((prep/'run-stage.py').read_bytes())
            with self.assertRaisesRegex(ValueError,'preparation source mount'):
                stage.capture_source(repo,other)
            mapped=Path(work).resolve()/'mapped-prep';mapped.symlink_to(prep,target_is_directory=True)
            stage.write(prep/'source-inventory.json',[name for name in frozen if not name.endswith('/run-stage.py')])
            with self.assertRaisesRegex(ValueError,'preparation source mount'):
                stage.capture_source(repo,mapped)

    def test_fixed_inputs_and_retained_budget(self):
        stage=driver();first,second=stage.input_contracts()
        self.assertEqual([len(first['steps']),len(second['steps'])],[5,2])
        close=first['steps'][3]
        self.assertEqual(close['expect']['persistentUpdate'],{'transactionId':'$trial','app':2,
            'packageId':'dev.ming.persistent-update-demo','newVersion':'0.2.0','status':'committed'})
        for plan in (first,second):
            for step in plan['steps']:
                self.assertEqual([event['kind'] for event in step['events']],['pointer','button','button'])
                self.assertEqual([event['down'] for event in step['events'][1:]],[True,False])
        budget=stage.execution_budget(first,second)
        self.assertEqual(budget['minimumBootSeconds'],[670,520])
        self.assertEqual([budget[key] for key in ('timeoutSecondsEach','vmSupervisorSlackSeconds','pairTimeoutSeconds','containerTimeoutSeconds')],[900,45,1920,1950])

    def test_source_freeze_checks_complete_set_and_new_untracked_sources(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            repo=Path(work).resolve();prep=repo/'artifacts/prep';prep.mkdir(parents=True)
            for name in ('tracked.txt','ming-sdk/src/New.kt','third_party/coroutines/New.kt','.cargo/config.toml','scripts/new.py',
                         'probes/linux-isolation-source-v2/build-current/mingos-isolation-probe',
                         'probes/linux-isolation-source-v2/build-current/vm-prepare.sh'):
                path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
            (prep/'run-stage.py').write_text('pass\n')
            with mock.patch.object(stage.subprocess,'run',return_value=mock.Mock(stdout=b'tracked.txt\0')):
                captured=stage.capture_source(repo,prep)
                self.assertIn('ming-sdk/src/New.kt',captured)
                self.assertIn('third_party/coroutines/New.kt',captured)
                self.assertIn('.cargo/config.toml',captured)
                freeze={'files':captured}
                stage.verify_source_files(freeze,repo,prep)
                (repo/'ming-sdk/src/Added.kt').write_text('new')
                with self.assertRaisesRegex(ValueError,'file set'):
                    stage.verify_source_files(freeze,repo,prep)

    def test_first_failure_never_launches_second_and_latch_cannot_retry(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            pair=Path(work);calls=[]
            def run(number):calls.append(number);raise RuntimeError('first failed')
            result=stage.run_two_boots(pair,run,lambda number,report: {})
            self.assertEqual(calls,[1]);self.assertEqual(result['status'],'failed')
            with self.assertRaises(FileExistsError):stage.run_two_boots(pair,run,lambda number,report: {})
            self.assertEqual(calls,[1])

    def test_container_limits_readonly_source_runtime_and_openssl(self):
        stage=driver();environment=stage.environment_contract('persistent-update-20261104-qual1-test')
        argv=stage.docker_command('pair',Path('/repo-source'),Path('/prep-source'),environment)
        for value in ('none','--read-only','ALL','no-new-privileges','65534:65534','4g','2'):
            self.assertIn(value,argv)
        self.assertIn('type=bind,source=/repo-source,target=/repo,readonly',argv)
        self.assertIn('/run/mingos-runtime:rw,size=16m,mode=0700,uid=65534,gid=65534',argv)
        self.assertFalse(any('privileged' in value for value in argv))

    def test_pair_requires_concrete_authorization_before_docker(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            root=Path(work)
            stage.write(root/'authorization.json',{'formalRunAuthorized':False})
            with mock.patch.object(stage.subprocess,'run') as run:
                with self.assertRaisesRegex(ValueError,'authorization'):
                    stage.require_authorization(root,'pair',{'environment':{'qualificationId':'one'}})
                run.assert_not_called()

    def test_formal_admission_precedes_pair_and_uses_separate_backing_without_disk_volume(self):
        stage=driver();environment=stage.environment_contract('persistent-update-20261104-qual1-test',Path('/prep-source'))
        self.assertEqual(stage.STAGES[stage.STAGES.index('pair')-1],'admission')
        argv=stage.docker_command('admission',Path('/repo-source'),Path('/prep-source'),environment)
        self.assertIn('type=bind,source=/prep-source/admission-backing,target=/var/tmp',argv)
        self.assertFalse(any('type=volume' in value for value in argv))
        self.assertNotIn(environment['hostBackingRoot'],str(argv))
        self.assertIn('/prep/admission',str(argv))

    def test_authorization_rejects_bool_int_and_float_aliases(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            root=Path(work).resolve();stage.write(root/'freeze.json',{})
            freeze={'environment':{'qualificationId':'one','hostBackingRoot':'/private/tmp/one'}}
            valid={'formalRunAuthorized':True,'qualificationId':'one','freezeSha256':stage.sha(root/'freeze.json'),
                   'newDiskSizeMiB':128,'hostBackingRoot':'/private/tmp/one','bootCount':2,'retryAllowed':False}
            with mock.patch.object(stage,'verify_admission') as admission:
                stage.write(root/'authorization.json',valid);stage.require_authorization(root,'pair',freeze)
                admission.assert_called_once();admission.reset_mock()
                for key,alias in [('retryAllowed',0),('bootCount',2.0),('newDiskSizeMiB',128.0)]:
                    stage.write(root/'authorization.json',dict(valid,**{key:alias}),exclusive=False)
                    with self.assertRaisesRegex(ValueError,'authorization'):stage.require_authorization(root,'pair',freeze)
                admission.assert_not_called()

    def test_frozen_admission_requires_strict_integer_observations(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            root=Path(work).resolve();stage.write(root/'freeze.json',{});(root/'admission').mkdir()
            freeze={'files':{'x':'a'*64},'runtime':{'source':'actual'}}
            value={'schemaVersion':1,'status':'passed','guestStarted':False,'dataDiskCreated':False,
                   'finalSystemAccepted':False,'uid':65534,'freezeSha256':stage.sha(root/'freeze.json'),
                   'sourceSha256':freeze['files'],'runtimeInputs':freeze['runtime'],
                   'runtimeRoot':'/run/mingos-runtime','runtimeFileSystem':'tmpfs','runtimeMode':'0700','availableBytes':2147483648}
            for key in ('ed25519PositiveVerified','ed25519NegativeRejected','exclusiveLeaseVerified','leaseReleasedVerified',
                        'qmpUnixSocketBound','vmLaunchIntercepted','secondBootAfterFailureBlocked',
                        'runtimeCopyAndGuestImportPassed','checkerPositiveAndNegativeControlsPassed','driverPositiveAndNegativeControlsPassed'):value[key]=True
            stage.write(root/'admission/result.json',value);stage.verify_admission(root,freeze)
            for key,alias in [('schemaVersion',True),('schemaVersion',1.0),('uid',65534.0),('availableBytes',2147483648.0)]:
                stage.write(root/'admission/result.json',dict(value,**{key:alias}),exclusive=False)
                with self.assertRaises(ValueError):stage.verify_admission(root,freeze)

    def test_export_maps_checker_roots_and_preserves_only_completed_stage_evidence(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            root=Path(work).resolve();backing=root/'backing'
            directories=['inputs','bundle','build/fixture','external-inputs','admission','export-stage',
                         'backing/cold-pair','backing/disks']+['stages/'+name for name in stage.STAGES[:-1]]
            for name in directories:(root/name).mkdir(parents=True,exist_ok=True)
            for name in ('review.json','freeze.json','fixture-verification.json','execution-budget.json',
                         'run-stage.py','verify-persistent-update-evidence.py','raw_boot_audit.py','source-inventory.json',
                         'runtime-inputs.json','input-geometry.json','geometry.stdout.log','bootstrap-external-input.json',
                         'admission-host-storage.json','authorization.json','host-storage.json','environment-inspection.json'):(root/name).write_text(name)
            original=b'{"argv":["/prep/inputs/payload/helper"],"commandRoot":"/prep/build/fixture"}'
            (root/'build/fixture/command.json').write_bytes(original)
            (root/'stages/export/active.txt').write_text('incomplete')
            frozen={}
            for name in stage.REPLAY_MODULES:
                path=root/'scripts/boot'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
                frozen['scripts/boot/'+name]=stage.sha(path)
            self.host_test_packages(stage,root)
            for path in (root/'artifacts').rglob('*'):
                if path.is_file():frozen[str(path.relative_to(root))]=stage.sha(path)
            inputs={}
            for number in (1,2):
                path=root/'inputs'/('input-boot%d.json'%number);path.write_text('{}')
                inputs['boot%dSha256'%number]=stage.sha(path)
            stage.inside_export(root,root,{'environment':{'workRoot':str(backing)},'files':frozen,'inputs':inputs,
                                         'hostTestFixtures':stage.host_test_inputs(root)})
            output=root/'export-stage/export'
            for name in ('inputs','bundle','fixture','pair','build-evidence','external-inputs','admission','stage-evidence','host-test-inputs'):
                self.assertTrue((output/name).is_dir(),name)
            self.assertEqual((output/'fixture/command.json').read_bytes(),original)
            self.assertEqual((output/'inputs/session-review.json').read_bytes(),(root/'review.json').read_bytes())
            self.assertFalse((output/'stage-evidence/export').exists())
            for name in stage.REPLAY_MODULES:self.assertEqual(stage.sha(output/'replay/scripts/boot'/name),frozen['scripts/boot/'+name])
            for number in (1,2):self.assertEqual(stage.sha(output/('input-boot%d.json'%number)),inputs['boot%dSha256'%number])

    def test_export_replay_rejects_dependency_drift_or_unfrozen_module(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            root=Path(work).resolve();output=root/'out';output.mkdir()
            frozen={}
            for name in stage.REPLAY_MODULES:
                path=root/'scripts/boot'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
                frozen['scripts/boot/'+name]=stage.sha(path)
            (root/'scripts/boot'/stage.REPLAY_MODULES[0]).write_text('changed')
            with self.assertRaisesRegex(ValueError,'frozen replay dependency'):
                stage.export_replay_dependencies(root,root,{'files':frozen},output,[])
            self.assertFalse(list(output.rglob('*.py')))
            (root/'scripts/boot'/stage.REPLAY_MODULES[0]).write_text(stage.REPLAY_MODULES[0])
            del frozen['scripts/boot/'+stage.REPLAY_MODULES[0]]
            with self.assertRaisesRegex(ValueError,'frozen replay dependency'):
                stage.export_replay_dependencies(root,root,{'files':frozen},output,[])
            self.assertFalse(list(output.rglob('*.py')))

    def test_verify_executes_exported_checker_in_isolated_subprocess_from_other_cwd(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            root=Path(work).resolve();(root/'export').mkdir();(root/'stages').mkdir();observed=[]
            def execute(directory,argv,timeout,**kwargs):
                observed.append((directory,argv,timeout,kwargs))
                stage.write(root/'stages/verify/checker-result.json',{'persistentUpdateNormalRebootVerified':True,'finalSystemAccepted':False})
                return {'exitCode':0}
            with mock.patch.object(stage,'verify_freeze',return_value={}),mock.patch.object(stage,'verify_previous'), \
                 mock.patch.object(stage,'execute',side_effect=execute),mock.patch.object(stage,'seal_stage') as seal, \
                 mock.patch.object(stage,'load_checker') as checkout_checker:
                stage._host_stage(root,root,'verify')
                checkout_checker.assert_not_called();seal.assert_called_once()
            _,argv,timeout,options=observed[0]
            self.assertEqual(argv[1:3],['-I','-B'])
            self.assertEqual(argv[3],str(root/'export/verify-persistent-update-evidence.py'))
            self.assertEqual(options,{'cwd':Path('/tmp')})
            self.assertEqual(timeout,300)

    def test_pair_authorization_is_checked_before_any_docker_read_or_write(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            root=Path(work).resolve();stage.write(root/'authorization.json',{'formalRunAuthorized':False})
            with mock.patch.object(stage,'verify_freeze',return_value={'environment':{}}), \
                 mock.patch.object(stage,'verify_previous'),mock.patch.object(stage,'verify_review'), \
                 mock.patch.object(stage.subprocess,'run') as run:
                with self.assertRaisesRegex(ValueError,'authorization'):stage.host_stage(root,root,'pair')
                run.assert_not_called()

    def test_inside_cli_pair_rejects_false_authorization_before_disk_or_vm(self):
        stage=driver();stage.boot_modules(stage.REPO)
        import data_disk
        original=Path.read_text
        def contents(path,*args,**kwargs):
            limits={'cpu.max':'200000 100000','memory.max':'4294967296','pids.max':'256'}
            if path.parent==Path('/sys/fs/cgroup'):return limits[path.name]
            return original(path,*args,**kwargs)
        with mock.patch.object(stage.sys,'platform','linux'),mock.patch.object(stage.os,'geteuid',return_value=65534), \
             mock.patch.object(Path,'read_text',contents),mock.patch.object(stage,'verify_freeze',return_value={'runtime':{}}), \
             mock.patch.object(stage,'validate_runtime_inputs'),mock.patch.object(stage,'read',return_value={'formalRunAuthorized':False}), \
             mock.patch.object(data_disk,'create_disk') as disk,mock.patch.object(stage.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(ValueError,'authorization'):stage.inside(Path('/prep'),'pair')
            disk.assert_not_called();launch.assert_not_called()

    def test_pair_boundary_requires_admission_chain_before_disk_or_vm(self):
        stage=driver();stage.boot_modules(stage.REPO)
        import data_disk
        with mock.patch.object(stage,'require_authorization') as authorization, \
             mock.patch.object(stage,'verify_previous_chain',side_effect=ValueError('previous products changed')), \
             mock.patch.object(data_disk,'create_disk') as disk,mock.patch.object(stage.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(ValueError,'previous products changed'):stage.inside_pair(Path('/prep'),stage.REPO,{})
            authorization.assert_called_once_with(Path('/prep'),'pair',{})
            disk.assert_not_called();launch.assert_not_called()

    def test_inside_chain_allows_active_stage_but_rejects_changed_ancestor_product(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            root=Path(work).resolve();stage.write(root/'freeze.json',{})
            for name in stage.STAGES[:stage.STAGES.index('pair')]:
                (root/(name+'.txt')).write_text(name)
                stage.seal_stage(root,name,[name+'.txt'])
            (root/'stages/pair').mkdir()
            stage.verify_previous_chain(root,'pair')
            with self.assertRaises(FileExistsError):stage.verify_previous(root,'pair')
            (root/'build.txt').write_text('changed')
            with self.assertRaisesRegex(ValueError,'products changed'):stage.verify_previous_chain(root,'pair')

    def test_running_overlap_and_unknown_stopped_state_are_rejected(self):
        stage=driver()
        with self.assertRaisesRegex(ValueError,'overlapping'):
            stage.reject_overlapping_writers([{'State':{'Running':True},'Mounts':[
                {'Type':'bind','RW':True,'Source':'/private/tmp/source'}]}],[Path('/private/tmp/source/output')])
        self.assertFalse(stage.container_stopped({'State':{'Running':False,'Pid':12,'Restarting':False}}))
        self.assertFalse(stage.container_stopped({'State':{'Running':False,'Pid':0,'Restarting':True}}))
        self.assertTrue(stage.container_stopped({'State':{'Running':False,'Pid':0,'Restarting':False}}))

    def test_geometry_receipt_must_match_actual_source_hash_and_test_count(self):
        stage=driver()
        with tempfile.TemporaryDirectory() as work:
            root=Path(work).resolve()
            paths=['crates/ming-app-host/src/graphics/desktop_shell.rs','crates/ming-app-host/src/desktop_model.rs','crates/ming-app-host/src/desktop_native.rs']
            for name in paths:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
            receipt={'schemaVersion':1,'geometry':{'schemaVersion':1,'width':1280,'height':800,
                     'app2Dock':{'index':1,'count':2},
                     'boot1Points':[[1148,12],[880,478],[676,766],[96,104],[1238,12]],
                     'boot2Points':[[676,766],[1238,12]]},
                     'sourceSha256':{name:stage.sha(root/name) for name in paths},
                     'test':{'matched':1,'passed':1,'failed':0,'ignored':0},'stdoutSha256':'a'*64}
            stage.validate_geometry(receipt,root)
            receipt['test']['matched']=0
            with self.assertRaises(ValueError):stage.validate_geometry(receipt,root)

if __name__=='__main__':unittest.main()
