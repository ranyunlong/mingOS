"""Pure verification of the separate offline source-v2 probe build."""
from pathlib import Path
import re
import stat

import linux_host_qualification as q
from linux_host_build import RUST_IMAGE

COMMAND = ['cargo', '+1.99.1', 'build', '--offline', '--locked', '--release',
           '-p', 'ming-app-host', '--example', 'isolation_probe_v2', '--no-default-features', '-j1']
SOURCES = ('probes/linux-isolation-source-v2/probe.rs',
           'crates/ming-app-host/examples/isolation_probe_v2.rs', 'Cargo.lock', 'rust-toolchain.toml')
ARTIFACTS = ('mingos-isolation-probe', 'vm-prepare.sh')
PRODUCTS = {'build-receipt.json', *ARTIFACTS,
            'command/command.json', 'command/result.json', 'command/stdout.log', 'command/stderr.log'}


def require(condition, message):
    if not condition:
        raise ValueError('source-v2 probe build: ' + message)


def verify_probe_build(probe_dir, repo, cold_freeze_sha):
    """Verify relocated bytes against original argv/cwd and return the receipt.

    This reads evidence only. It never runs Cargo, a probe, or a guest.
    """
    probe_dir, repo = Path(probe_dir), Path(repo)
    require(isinstance(cold_freeze_sha, str) and re.fullmatch('[0-9a-f]{64}', cold_freeze_sha),
            'cold source freeze digest required')
    before = q.tree_inventory(probe_dir)
    require(set(before) == PRODUCTS, 'exact receipt, artifacts and command logs required')
    # q.tree_inventory binds files; explicitly bind the only allowed directory too.
    require(sorted(path.name for path in probe_dir.iterdir()) ==
            ['build-receipt.json', 'command', 'mingos-isolation-probe', 'vm-prepare.sh']
            and sorted(path.name for path in (probe_dir / 'command').iterdir()) ==
            ['command.json', 'result.json', 'stderr.log', 'stdout.log'], 'unexpected directory member')
    receipt = q.read_json(probe_dir / 'build-receipt.json')
    require(type(receipt) is dict and set(receipt) == {'schemaVersion', 'profile', 'command', 'result',
            'sourceFreezeSha256', 'rustImage', 'source', 'artifacts', 'sourceBeforeAfterMatched',
            'cargoRegistryBeforeAfterMatched', 'formalQualification', 'finalSystemAccepted'}, 'receipt schema differs')
    require(type(receipt['schemaVersion']) is int and receipt['schemaVersion'] == 1
            and receipt['profile'] == 'source-v2' and receipt['command'] == COMMAND
            and receipt['sourceFreezeSha256'] == cold_freeze_sha and receipt['rustImage'] == RUST_IMAGE
            and receipt['sourceBeforeAfterMatched'] is True and receipt['cargoRegistryBeforeAfterMatched'] is True
            and receipt['formalQualification'] is False and receipt['finalSystemAccepted'] is False,
            'build source/image/argv/stability binding differs')
    source = {name: q.stable_hash(repo / name) for name in SOURCES}
    require(receipt['source'] == source, 'four frozen source inputs differ')
    artifacts = {name: before[name] for name in ARTIFACTS}
    require(receipt['artifacts'] == artifacts, 'actual probe artifacts differ')
    for name in ARTIFACTS:
        info = (probe_dir / name).lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_mode & 0o111 and not info.st_mode & 0o7000,
                'executable regular probe artifact required')
    result = receipt['result']
    require(type(result) is dict and set(result) == {'command', 'startedAt', 'finishedAt', 'exitCode',
            'leaderExitCode', 'timedOut', 'orphanedChildren', 'cleanupComplete', 'error', 'stdout', 'stderr'},
            'execution result schema differs')
    require(result['command'] == COMMAND and type(result['exitCode']) is int and result['exitCode'] == 0
            and type(result['leaderExitCode']) is int and result['leaderExitCode'] == 0
            and result['timedOut'] is False and result['orphanedChildren'] == []
            and result['cleanupComplete'] is True and result['error'] is None
            and isinstance(result['startedAt'], str) and result['startedAt']
            and isinstance(result['finishedAt'], str) and result['finishedAt'],
            'successful contained execution required')
    command = q.read_json(probe_dir / 'command/command.json')
    require(command == {'command': COMMAND, 'cwd': '/repo', 'timeoutSeconds': 1800, 'startedAt': result['startedAt']}
            and type(command['timeoutSeconds']) is int, 'original command admission differs')
    require(q.read_json(probe_dir / 'command/result.json') == result, 'actual execution result differs')
    for label in ('stdout', 'stderr'):
        require(result[label] == before['command/' + label + '.log'], 'raw compiler log differs: ' + label)
    require(q.tree_inventory(probe_dir) == before
            and {name: q.stable_hash(repo / name) for name in SOURCES} == source,
            'probe evidence or source changed during verification')
    return receipt
