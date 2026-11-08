# P01 S2 WSL Environment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在正式公共入口创建全新 WSL2/VHD/daemon，严格加载三个固定 index，并通过有效资源限制 smoke 与独立观察。

**Architecture:** Windows Python controller 负责独占输出、冻结输入、WSL 创建和二进制 stdin transport；Linux guest helper 负责私有 namespace、cgroup、daemon 生命周期及观察。显式 Docker transport 复用现有严格 development loader，不修改其 index/config/rootfs 合同。

**Tech Stack:** Python3 标准库、Windows PowerShell、WSL2、Docker29.8.1 static binaries、Linux unshare/nsenter、cgroup2。

**Spec:** `docs/superpowers/specs/2026-11-08-p01-wsl-environment-design.md`（与 P01 总设计一起读）。

## Global Constraints

- 所有旧资源及失败代保留；不 delete/prune/reset/unregister，不原地重试。
- 不修改 Desktop、默认 distro、全局 WSL 配置、共享 cgroup 祖先控制位；不 global shutdown。
- S2 fixedTools 预存在是显式前提；S6 单入口仍须补齐 handoff 白名单提取和缺 Tools 的首次严格载入，不能手工步骤替代 P01 gate。
- Docker bundle29.8.1：86055881 bytes，SHA256 `d8db66739d2e28d4933786d73e918d9be643a67fbd835db1bf740d650a259e70`；不声明 publisher signature。
- smoke UID/GID65534、cap全部0、NNP1、只读根、network none、cpu.max quota=2*period、memory.max=4294967296、pids.max=256。
- `formalQualification=false`、`finalSystemAccepted=false`、`coldBuildExecuted=false`；S2 不关闭 S3–S6 或 P01。
- 实际新代验收是必要门槛；mock、历史实验或仅load成功不能替代。

## 文件和接口地图

创建 `scripts/boot/docker_transport.py`、`test_docker_transport.py`：显式 executable/endpoint/config 及 loader runner adapter。

创建 `scripts/boot/wsl_environment.py`、`test_wsl_environment.py`：Windows controller、rootfs构造、冻结/传输、CLI与证据；创建 `scripts/boot/wsl_environment_guest.py`、`test_wsl_environment_guest.py`：接收器、私有启动、nsenter admission、smoke、只读观察。

修改 `docs/p01-environment-audit.md`、`docs/superpowers/specs/2026-11-08-p01-clean-bootstrap-design.md`：实际 S2 结果/采用WSL选择及保留S3–S6。新增 `docs/p01-wsl-environment.md`：参数、前提、成功/失败证据及后续消费者用法。不修改 `qualification_image_archive.py`、`development_image_migration.py`、`cold-boot.py` 的历史合同。

所有 Python 测试使用 unittest；Windows controller 纯逻辑先在 Windows 测，Linux descriptor/namespace/cgroup 语义在新 distro 实测。后续任务使用前项精确命名：

```python
DockerTransport(executable: str, endpoint: str, config_directory: str)
DockerTransport.argv(args: list[str]) -> list[str]
DockerTransport.run(args: list[str], *, timeout: int, stdin=None)
DockerTransport.migration_runner(receipt, key, command, timeout, **kwargs)
freeze_inputs(paths: list[Path], destination: Path) -> dict
prepare_rootfs(export_path: Path, bundle_path: Path, output_path: Path) -> dict
create_environment(*, generation: str, distro: str, output_directory: Path,
                   wsl_executable: Path, outer_docker: DockerTransport,
                   docker_bundle: Path, archive_root: Path) -> dict
observe_environment(environment_receipt: Path, output_directory: Path) -> dict
exec_environment(environment_receipt: Path, argv: list[str], *,
                 output_directory: Path, timeout: int) -> dict
receive_file(path: str, sha256: str, size: int, mode: int, stream) -> dict
launch_environment(generation: str, *, ready_timeout: int = 30) -> dict
admit_environment(handle: dict, argv: list[str], *, timeout: int = 30, stdin=None) -> subprocess.CompletedProcess
run_smoke(handle: dict) -> dict
observe_guest(handle: dict, load: dict, smoke: dict) -> dict
```

`freeze_inputs` 返回 `{files: {basename: {sha256, bytes, mode}}, root: str}`；`prepare_rootfs` 返回来源hash及 binary pins；`launch_environment` 返回 PID/starttime/ns/socket/daemon identities，传给 admit/smoke/observe。所有函数错误抛 ValueError/OSError，controller 最外层保存 phase/exception 后非零退出；不得吞异常返回成功。

Task3接口裁定：`launch_environment`只接受generation，由它派生全部私有路径；不接受可指向旧data-root的任意config。`admit_environment`在验证身份、进入management及再次验证后，显式执行Linux argv并返回真实进程结果。内部`create_empty_environment`使用完整create同组关键字参数，返回`status=creating, phase=empty-daemon-observed`；Task4须在完整load/smoke/保全验证后才生成最终ready状态。这些接口只替换原计划的推测性内部签名，不改变环境验收条件。

Task4接口对齐：`run_smoke`不接收调用者name；新generation和固定generation label关联本代新容器。`observe_guest`显式接收既有load/smoke证明供只读核对。`admit_environment`的可选`stdin`仅转发同一binary文件FD供既有smoke使用，不映射Windows路径、不放宽Linux argv或Docker transport连接策略。

### Task 1: 显式 Docker transport

**Files:** Create `scripts/boot/docker_transport.py`, `scripts/boot/test_docker_transport.py`.

**Consumes:** `qualification_image_archive._docker_run` 的 command capture 与 `(result, operation)`；**Produces:** 上述 DockerTransport API。

- [x] 写 RED 测试，环境中同时设置坏 DOCKER_HOST、DOCKER_CONTEXT，断言 argv 仍由显式 endpoint 组成；空/相对executable、缺endpoint、tcp endpoint失败。

```python
def test_explicit_endpoint(self):
    client = DockerTransport('/usr/local/bin/docker', 'unix:///run/p01/docker.sock', '/opt/p01/client')
    self.assertEqual(client.argv(['info']), ['/usr/local/bin/docker', '--host',
        'unix:///run/p01/docker.sock', '--config', '/opt/p01/client', 'info'])
def test_loader_executable_cannot_change(self):
    client = DockerTransport('/usr/local/bin/docker', 'unix:///run/p01/docker.sock', '/opt/p01/client')
    with self.assertRaises(ValueError):
        client.migration_runner({'operations': []}, 'load', ['/bin/other','image','load'], 30)
```

- [x] `python -B -m unittest discover -s scripts/boot -p test_docker_transport.py -v`，保存实际失败输出。
- [x] 实现 argv validation；executable/config接受POSIX或Windows绝对路径，拒绝traversal/control字符；endpoint只接受明确unix/npipe。拒绝args中的连接覆盖选项`--host/-H`、`--context/-c`、`--config`及TLS连接覆盖（含`=value`或短选项附值）。`run`复制并清理Docker/context/TLS/proxy环境，显式`--host --config`，stdin保持二进制文件对象、shell=False。adapter校验executable，只允许stdin kwarg，调用原capture helper时显式传清理环境/shell=False并保持timeout，禁止wrapper shell拼命令。
- [x] 追加 fake-runner 测试断言 load 的 stdin 对象身份不变、超时/非零保留 operation、不会修改 os.environ/context；跑同一命令应全PASS。
- [x] 单独提交该交付，controller 复核。

### Task 2: 独占输入、rootfs 与冻结传输

**Files:** Create controller/guest 两模块和对应测试文件的本任务部分。

**Consumes:** 固定 loader `FIXED`/`verify_all`；**Produces:** freeze_inputs/prepare_rootfs/receive_file，控制器参数验证及独占命令记录器。

- [x] 写实际临时 tar/stream 测试：bundle错hash/bytes、重复名、symlink、超大成员；rootfs替换路径经链接、绝对/traversal/重复成员拒绝，合法 `/bin -> usr/bin` rootfs links 保留；输出已存在拒绝。
- [x] 接收器 Linux 测试使用真实临时目录，核对短传/超长/hash错误、已有文件、symlink父目录拒绝，错误后部分文件留存且同目标再次接收失败。

```python
def test_failed_transfer_is_not_reused(self):
    path = str(self.root / 'partial')
    with self.assertRaises(ValueError):
        receive_file(path, hashlib.sha256(b'abc').hexdigest(), 3, 0o444, io.BytesIO(b'ab'))
    self.assertTrue(Path(path).exists())
    with self.assertRaises(FileExistsError):
        receive_file(path, hashlib.sha256(b'abc').hexdigest(), 3, 0o444, io.BytesIO(b'abc'))
```

- [x] Windows 跑 `python -B -m unittest discover -s scripts/boot -p test_wsl_environment.py -v`，Linux 跑 `python3 -B -m unittest discover -s <frozen-tests> -p test_wsl_environment_guest.py -v`，确认新API缺失/故意输入失败而非平台语义跳过。
- [x] 实现freeze exclusive目录/文件、fsync、hash清单，构造wsl.conf与bundle替换tar；bundle固定bytes/hash前后核验，精确成员集合从可信固定tar实际观察后固化常量。禁止extractall；输入保留同fd前后stamp/hash复核。接收器用dirfd逐级O_NOFOLLOW，写循环处理partial write，hash/size通过才fchmod。
- [x] 实现 capture 每操作独立 `*.command.json/stdout.log/stderr.log/result.json`，全部exclusive；finally写超时/OSError结果；stdin记录sha/bytes而非内容。S2 helper清单覆盖transport、guest/controller、development loader及qualification helper，依赖闭包无隐式/public。
- [x] 加冻结后宿主源文件变化、guest helper改1byte、日志路径已存在、Windows reparse父目录的负控；运行本任务测试通过后提交。

### Task 3: 新 distro 与私有 daemon 生命周期

**Files:** Modify controller/guest及测试。

**Consumes:** Task1 transport、Task2输入/记录/接收；**Produces:** create_environment初始阶段、launch_environment和handle。

- [x] fake subprocess 对调用序列写RED：前置Tools缺失/旧distro/已有output时禁止 create/export/import；rootfs只导出已inspect的stopped container ID；禁止所有delete/prune/reset/unregister/shutdown/context use命令。
- [x] 为 config builder 写确定性断言：data/exec/pid/socket/containerd namespace仅本代；bridge/iptables等false；传入XDG_RUNTIME_DIR/DOCKER_HOST/代理环境后daemon实际env只含allowlist。
- [x] 实现spec创建步骤1–6；outer必须显式 npipe endpoint，固定Tools与verify_one结果逐项对照；创建stopped export container、export、prepare_rootfs、新WSL import。先记录外层info/ID/context/旧IDs，后续独立旧资源检查不能依赖列表行次序。
- [x] Linux launcher先generation cgroup再unshare，进入management后启用本代cpu/memory/pids；private mount遮蔽WSL共享路径，env -i与显式0700 TMPDIR。保存启动前后共享控制位、namespace、mountinfo、daemon/containerd cmdline与root/state/socket归属。
- [x] 30秒ready后空清单必须fsync且先于load/create；daemon退出、超时、非空store、缺controller、默认socket可见、共享控制位变化全部阻断。handle含/proc PID starttime、exe hash、ns ID、socket inode/owner、info.ID。
- [x] 实现admit_environment每次验证handle并进入management/nsenter；模拟PID复用、starttime不同、daemon ID不同和socket替换均失败，不在失败后重启。
- [x] 跑两模块unittest并保存结果；controller审查后提交。

### Task 4: 严格载入、有效限制 smoke 与观察 API

**Files:** Modify controller/guest、三个测试文件；Create `docs/p01-wsl-environment.md`。

**Consumes:** Tasks1–3 APIs；**Produces:** 完整create/observe/exec CLI与environment-result。

- [x] 写RED：三个archives未全部验证时无load；partial loader失败保留completedLoads且不smoke；inspect字段正确但容器内cpu/memory/pids不符时失败；start attach退出0但State.ExitCode非0/OOMKilled=true仍失败。
- [x] 把load_all接至migration_runner，guest调用前校验冻结代码hash和handle。load必须在daemon namespaces中运行，独立result_path，完成列表精确rust/tools/kotlin。禁止修改loader放宽index检查。
- [x] run_smoke用固定Tools和spec完整create argv；命令中直接内嵌标准库Python读取/proc/self/status、statvfs、cgroup三个max、/sys/class/net、route。分别记录inspect-before/start/inspect-after/actual-effective；actual JSON的断言在controller再次执行，不只相信容器exit0。
- [x] observe_guest只读检查live handle/ns/store/加载image与smoke，不创建容器/卷；exec_environment接收Linux argv、timeout和独立输出目录，进入私有环境并使用冻结helper，禁止自动Windows路径映射。
- [x] 完成CLI解析与JSON失败phase，receipt字段对齐spec；新输出目录占用、失败代重用、任一未知receipt状态拒绝。文档给具体create、observe、exec示例，说明S6缺Tools准备层gate、失败保留及独立内核限制。
- [x] 运行新三个模块全部unittest，以及现有 `test_development_image_migration.py`、`test_qualification_image_archive.py`、`test_import_handoff.py`适用平台回归；Linux恢复与descriptor检查必须在Linux执行。通过后提交。

Task4实现/控制及独立复核完成于`f00331c`；随后真实Task5发现实际plugins namespace缺陷，不能据此接受运行代。

- [ ] 真实缺陷修复门槛：修正单数配置键，补实际两namespace的launch/live/retained/host验证，真实RED/GREEN及独立修复复核；另用全新代执行Task5，原554bde65保持不合格。

### Task 5: 正式新代实测与独立审查

**Files:** Modify `docs/p01-environment-audit.md`, P01总设计环境选择段、操作文档；新增本次 `artifacts/p01-wsl-environment-20261108-<unique>/` 证据，不覆盖历史目录。

**Consumes:** 完整CLI；**Produces:** 独立新代实际成功/失败证据与审查结果。执行前controller指定全新generation/distro，不能沿用8a1c51f2。

- [ ] 记录 `git status --short` 与实现代码hash；核对外层fixedTools前提、三archive来源为S1完整导入，确认bundle固定SHA/bytes。新代名称示例 `mingos-p01-s2-20261108-<随机hex>`，随机值在实际执行时一次生成并写request。
- [ ] 用PowerShell参数数组/直接Python CLI运行正式create，完整参数模板如下；变量均由该次前置检查实际路径赋值，不使用字符串拼shell：

```powershell
python -B scripts/boot/wsl_environment.py create --generation $generation --distro $distro --output-directory $output --wsl-executable C:/Windows/System32/wsl.exe --outer-docker-executable 'C:/Program Files/Docker/Docker/resources/bin/docker.exe' --outer-docker-endpoint npipe:////./pipe/dockerDesktopLinuxEngine --outer-docker-config $outerConfig --docker-bundle $bundle --archive-root $verifiedArchiveRoot
```

- [ ] 验证新VHD、新daemon/containerd store首次空，三index身份保留，smoke有效限制和exit0。每项必须有实际日志，不能用历史实验值填receipt。失败留存，修实现后换全新代，绝不原地重试。
- [ ] 用另一个独占输出目录执行 `python -B scripts/boot/wsl_environment.py observe --environment-receipt <本次绝对receipt路径> --output-directory <新绝对观察目录>`，核对真实环境及所有冻结来源。调用exec运行 `/usr/bin/python3 -B -c "import os; print(os.getuid())"` 验证Linux argv传输，结果不作为冷构建证据。
- [ ] 读取外层before/after按旧ID保全核对；本入口新增stopped export container有明确label/ID。并发新增资源必须附任务归属证据；未知差异标记inconclusive并停止S2成功声明。确认shared cgroup control位未变。
- [ ] fresh reviewer独立审查生产代码、负控、真实证据和环境observations，特别检查daemon实际environ、containerd root/state、PID复用admission、无旧socket、三image Config/RootFS、cpu/memory/pids真实读取。修复后按影响重新跑必要测试；运行环境相关改动需新代实测。
- [ ] 更新审计文档明确 c62467b2 实验已成功（cpu.max 200000 100000、memory4294967296、pids256，诊断不是正式入口），正式入口结果单列。只将S2标记通过，P01及全部11项目总交付保持未完成。记录本地提交；向用户已授权的 `codex/persistent-update` 推送由controller统一执行，本计划worker不自行改远程。

## 自审覆盖与后续边界

Task1覆盖endpoint/环境污染；Task2覆盖输入/路径/冻结/二进制传输；Task3覆盖新distro/VHD/cgroups/daemon/空store；Task4覆盖严格image load、有效限制、失败收据及Linux transport；Task5覆盖实际全新代与独立审查。无任务执行Rust/Kotlin编译、runtime pack重装或保存重开；这些是S3–S6继续独立设计实施的必要门槛。S6另须实现handoff固定三archive提取和外层Tools首次载入，不能因S2通过而省略。
