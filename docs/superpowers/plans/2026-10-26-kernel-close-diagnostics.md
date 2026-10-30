# Kernel 关闭边界诊断实施计划

**Goal:** 为 v23 后续诊断补足 CPU/cgroup 证据，区分 worker 缺少调度时间与关闭协议延迟；不把诊断实现当作恢复验收通过。

**Architecture:** 复用 guest 每秒一次的 process-table。仅诊断 profile 增加资源字段，每次只读固定内核文件并限制读取大小；用 PID、starttime 和 cgroup 路径校验采样身份。完整进程枚举及关机前残留 worker 检查不裁剪。新模块随 boot bundle 打包并计入源码 SHA。

**Tech Stack:** Python 3.12 标准库、现有 Linux cgroup v2、unittest。

**Spec:** `docs/system-image-verification.md` 的 v21—v23 诊断续记，以及 `docs/superpowers/specs/2026-10-25-kernel-desktop-recovery-design.md`。

## 约束

- 不修改 Host/SDK/Wasm、Close 宽限、fuel、CPU、内存和 pids 限额。
- 不挂载或修改既有数据盘，不覆盖历史失败证据。
- 进程采样时间使用 guest monotonic；不得与 Rust 不同 Instant 原点直接相减。
- 采样错误、进程退出、身份变化显式记录，不能以缺少样本证明进程已经回收。

## 执行与验证

- [x] 先写失败测试：stat 的带空格/括号 comm、CPU 与限流计数、路径边界、文件大小上限、进程退出与 PID/cgroup 身份变化。
- [x] 最小实现采样模块，接入诊断 process-table，并同步 bundle 文件清单和源码摘要。
- [x] 运行定向测试、全部 Boot 回归及 JavaScript 基础检查；核对工作区 diff。
- [ ] 环境恢复后补父侧共用 Instant 的 Close 请求、SDK 回复写出、Result 接受及取消时间线，并以真实 kernel 恢复场景复验。

## 第一轮本机执行边界（后续状态见下文）

2026-10-26：本工作区缺少 `artifacts/`、项目 Rust 工具链和 MingOS Linux/QEMU 工具镜像。Docker 可访问，但只有其他项目镜像。已尝试访问项目固定 Rust 1.99.1 的官方发行清单，沙箱外连接仍超时。不能据本机单元测试宣告 v23 的 APP_CANCELLED 或重复输入问题已修复；父侧插桩及真实 kernel 复验仍待执行。


## 第一轮本地结果

新增 11 项资源采样和进程表回归，完整 Boot 回归 55/55，Node 回归 24/24，
JavaScript 与 Boot Python 语法检查通过。新增测试先因采样模块/接口缺失失败，随后实现后通过。
测试日志和当前源码 SHA 保存于 `artifacts/kernel-close-diagnostics-local-20261026/v1/`。
资源采样使用非缓冲读取，每个内核文件至多读取 4097 字节（含 1 字节溢出探测）。
父侧时间线和 kernel 复验仍未完成；本轮没有修改 Rust 生产代码或冻结应用字节。

## 后续推进：服务预算中的 Close/Cancel 分支

2026-10-26 本机已拉取项目锁定的 Rust 1.99.1 镜像（原 digest）和 Debian 基础镜像，
正在恢复依赖及 QEMU 工具镜像。静态检查发现 supervisor 的非 cooperative SDK 服务延迟
分支把 `bridge.closed()` 当作 `APP_CANCELLED`：正常 Close 本身也满足条件，可能在宽限期内
中断已经生成但尚未写出的 SDK 回复。这是可单独验证的候选缺陷，尚不能据此断言是 v23 的完整根因。

- [x] 保持原行为提取最小服务延迟函数，新增回归：正常 Close 保留延迟并仍能收到帧时钟 Close；显式 Cancel 及 Close 后 Cancel 仍中断。
- [x] 用锁定 Rust 容器运行并保留 RED；确认错误来自正常 Close 被误取消后，只调整取消判定。
- [ ] 运行 GREEN、既有 Result/宽限保护及持续会话回归；记录当前源码和运行边界。
- [ ] 环境具备后继续父侧时间线和真实 Kotlin/kernel 复验，新产物独立编号，不冒充原冻结产物。


## 第二轮实际结果：服务预算误取消修复

- RED：旧判定下新增 3 项回归中，正常 Close 用例报 `APP_CANCELLED`，另外 2 项显式取消用例通过。
- 最小修复：`supervisor.rs` 的服务延迟只因 `cancel` 标志中断；正常 Close 保留原服务延迟，随后仍可送达关闭事件。未调整任何生产预算、关闭期限或 Result 接受条件。
- Linux 库测试 196/196、帧时钟宿主测试 7/7 通过，包括新增 3 项和既有 6 项关闭/Result 保护。
- 持续会话集成测试 10/11；固定 1800ms shutdown 的故障隔离用例仍失败。对原 HEAD 源码强制重新编译并核对二进制哈希后，同项也失败。保留 RED，不将整组报为通过，不以延长 shutdown 或放宽断言掩盖。两项 AppData 测试夹具显式创建 Unix 0700 目录，以满足既有私有目录前提；生产权限校验未改。
- 真实 Kotlin Text 已使用现有源码与固定 Kotlin 2.3.20 重建，并生成 559 个输入文件绑定收据；这是独立新产物，非原 v19 字节恢复。
- Linux CPU offscreen 的真实 Text 快速输入、精确文本、自然 Close1/Cancel0、worker 回收通过。
- 三实例关闭重启诊断独立核对 23/23：Text 第一代 PID10/window2/generation1 → 第二代 PID14/window3/generation2；兄弟保持 PID9/window1/generation1，直到最终 shutdown；三实例 Close1/Cancel0、error=null；`mingos`/`again` 和默认中文备注精确一致。

上述使用合成 Host action，不包含严格 guest、QMP/Dock pointer、Counter/ext4 或物理键盘资格。
原 v23 的完整恢复验收和 Wayland 按键重复仍待真实 kernel 复验，父侧同源时间线也仍未补齐。
证据：`artifacts/close-service-budget-20261026/`；三实例独立审计为 `native-relaunch/independent-audit.json`。

## 工具环境恢复

固定 Rust 镜像与 Debian 基础已恢复，Gradle/Kotlin 工具链已实际编译 Text。原 HTTP 工具镜像构建出现大内核包传输失败；HTTPS 派生镜像因容器内证书校验失败停止，未禁用校验。宿主 HTTPS 下载同版本 kernel 后，镜像内已签名 APT 索引的 SHA-256 核对通过。第一次预加载构建因 apt update 后缺失 archives 目录停止，已在独立 v2 Dockerfile 补建该目录；同版本锁定安装继续，构建结果单独记录。


## 后续环境与冻结输入检查

工具镜像预加载 v2 已经验证内核包 SHA-256，但随后 QEMU/Weston 等多个包 HTTP 连接失败，镜像构建仍未成功。
完整失败日志保留；HTTPS 尝试的证书校验失败也保留，未关闭证书或 APT 签名校验。原构建器要求的
`mingos-isolation-probe`（SHA-256 `d213382d37e1496c5d5eb8ba31081d712395eb7b4e7ae19c7a4d7fd8aa028ccd`）
与 `vm-prepare.sh`（`511336a8661fc3eb991e6c8b168c5615c7420f22baf3134eba72f3a111e15ac4`）不在当前仓库或 Git 历史中；
也未找到生成源码。不能以重新生成不同字节或修改硬编码哈希冒充原冻结资格。缺失项清单见本轮证据目录的 `missing-frozen-probes.json`。

Counter 构建脚本原先在 macOS 的 JAVA_HOME 下拼接 `bin/java.exe`，已先实际复现 ENOENT，再按平台选择 Java 名称，
保留 Windows 的默认 JBR 路径和专用 JVM 选项。新的 `local-package-20261026` 构建成功，SDK/Compose 保护哈希不变，
真实 Kotlin/Wasm 的 9 项合成数据合同通过；真实文件目录的进程重启读回另行记录，不与 ext4/kernel 冷启动混淆。


## Counter 真实文件读回闭环

使用独立新建的任务专用 Linux named volume，数据根由 UID501 建立为 0700；首个 macOS bind mount
因容器内 UID0 映射被生产校验正确拒绝，失败证据保留。未放宽 AppData 的所有者/私有目录检查。
两个独立 Linux CPU offscreen 进程会话以同一包和同一新卷完成 Counter 0→1→2，读回匹配、每轮仅一次
4B 写入并确认目录同步；两轮 Close1/Cancel0、error=null、finally/dispose 各1、全部 worker 回收。
独立审计 19/19，另只读核对最终文件为 UID501、0600、4 字节整数2，SHA-256 匹配预期。
这是本机新数据的进程重启验证，不是原 v19 数据盘、ext4、kernel 冷启动或崩溃耐久性资格。
证据：`artifacts/close-service-budget-20261026/native-counter/independent-audit.json`。

本轮最终本地基础检查：Boot55/55、Node24/24、JS 语法与 `git diff --check` 通过。
严格 guest/QMP/Dock 资格与父侧同源时间线仍未完成；缺失冻结探针和工具镜像下载问题保持可追踪。


## 2026-10-27 父侧时间线续进

父侧共用 Instant 的 Close 请求、SDK5+ 终态回复成功写出、Result 接受、期限决策及原生 Child 回收已实现并验证。
release库203/203、桌面1/1、帧时钟7/7、真实取消130/130、冻结 Kotlin Text 关闭/重启/输入审计35/35。
[完整记录](../../close-lifecycle-verification.md)区分 pipe flush、应用处理、Result 与回收；Strict2s/Off200ms 和原资源预算保持。
本计划上面的最终复选项仍未勾选：真实 kernel 恢复复验尚缺原冻结探针与可用工具镜像，不能用 offscreen 结果替代。
