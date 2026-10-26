# 8阶段 Linux worker 内核隔离前置

2026-10-25。独立模块原型，尚不称作 production-secure。仅新增linux_isolation.rs、scoped probe/tests和artifacts/linux-isolation；主线程注册lib并串行接线，既有run/view/worker协议和已冻结生产文件不改。

## 接口和顺序

Parent: WorkerKey(app,generation) + 显式 Limits(memory,pids) → WorkerIsolation::prepare → spawn等待Init的child → attach(&mut Child) → child_policy(uid,gid,expected_parent) → 发送可信Init。可信Ready后mark_ready将cpu.max从100000/100000切为25000/100000。结束时kill owned cgroup、Child.wait/reap、cleanup_after_reap。PID1提前挂载cgroup2并只为/sys/fs/cgroup/mingos准备cpu/memory/pids controllers及0700 root所有权；manager留在该子树之外。

Worker: exec/动态链接和可信Init完成之后、任何Wasm解析/JIT之前，单线程调用unsafe apply_child(policy)。任一步失败立即退出，由parent回收，不能继续未隔离执行。验证PPid/worker PID/cgroup membership与startup limits；unshare mount/net/IPC/UTS；使mount propagation private，在owned /run/mingos-workers/key挂empty tmpfs，chroot且更换cwd，关闭>=3继承FD；clear groups、drop UID/GID和caps/ambient/bounding；drop之后设置PDEATHSIG(SIGKILL)、再次核对expected PPid；设置no_new_privs/nondumpable并安装seccomp TSYNC。

使用现有libc，不增新依赖。经典BPF校验x86_64 audit arch，杀死x32 ABI；拒绝host FS、network、exec、ptrace、signal-other-process和其他高危接口。clone3返回ENOSYS以触发pthread受限clone fallback；clone仅允许thread/VM/sighand及必要pthread flags，tgkill/kill仅允许当前TGID。保留匿名memory/mprotect、futex、signals、clock/getrandom、stdio IPC及线程能力。初期为denylist，未知syscall风险必须如实记载。

## 范围和资源

不做PID/user/cgroup namespace，不声称已做。mount/net/IPC/UTS严格模式需VM权限，缺权限明确fail/unsupported；Windows/non-x86_64 strict显式Unsupported。Docker仅运行无cap syscall baseline，它不是strict隔离成功，也不作为权限放宽理由。

TCG实证：7C两Kotlin应用约154205/187305ms ready，startup watchdog300s。startup用1core，可信Ready后steady25%core。主线程从boot-v4/guest.jsonl的process-table实际读到PID190 VmHWM峰234344KiB、PID191峰187856KiB。旧Kotlin资格候选采用512MiB/pids64，约2倍实测headroom；SDK6新包仍需另测。Limits要求调用方显式选择512MiB或1GiB、pids32或64，没有未量测的production默认。smoke用512MiB/64，资源压力probe用512MiB/32测试限额；VM仍需记录memory.peak与VmHWM。现有Wasm64MiB/fuel/RPC预算与parent host service预算继续保留。

## 实施及验证顺序

1. 纯身份/limits/cgroup所有权/过滤器合同真实RED，然后实现。
2. 无cap、无网络、只读root Docker用真实Linux syscall检查拒绝FS/network/exec/ptrace/进程clone/外部signal，同时保留stdio、anon JIT mapping、clock/random和thread/futex。
3. strict在同Docker必须明确失败。准备owned VM probe检查namespace/UID/caps/cgroup/cleanup/父死/资源限额；交boot/main串行运行，不访问正在工作的VM，不变更host系统。
4. 保存所有命令、原始失败/通过、source/hash和未实测边界；独立review及真实Kotlin严格隔离资格属于后续验收。

## 权威语义参考

- Linux man-pages PR_SET_PDEATHSIG: https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html （身份更改会清除此设置，亲线程死亡及PPid竞态）。
- Linux kernel cgroup v2: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html （cpu.max、memory.max、pids.max、cgroup.kill和no-internal-process规则）。
- Linux kernel seccomp: https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html （BPF arch、过滤继承及no_new_privs）。
- Linux man-pages capabilities、unshare、clone及seccomp（bounding/ambient、namespaces、pthread flags和TSYNC）。
