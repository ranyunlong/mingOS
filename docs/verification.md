# 第一阶段验证记录

后续 Rust 迁移结果见 [原生宿主验证记录](native-verification.md)。本文保留第一阶段实际记录。

日期：2026-10-23。平台：Windows；Node.js v24.19.0；JDK 21.0.6；Gradle 8.14.5；Kotlin 2.3.20。

## 当前实际结果

| 检查 | 结果 |
| --- | --- |
| node --test tests/*.test.mjs tests/kotlin.integration.mjs | 25 项：22 通过、0 失败、3 跳过 |
| node scripts/check.mjs | 已通过，覆盖 src/scripts/tests 的 JavaScript 语法 |
| node scripts/build-kotlin.mjs | 实际编译及打包成功；修复脚本后，不依赖外部 JAVA_TOOL_OPTIONS 的重复构建成功 |
| 真实 Kotlin 集成 | 3 项全部通过：仅 Ming 导入、UTF-8 授权读取、未授权拒绝 |
| node scripts/demo.mjs | 授权内容匹配；资源 2 和无启动授权均返回 -1，正常退出 |
| Linux 内核隔离 | 未实现、未验证 |

## 测试先行与评审

能力服务/导入策略先观察到 7 项缺失行为失败，再实现并通过；进程宿主 9 项先失败，再实现并通过；CLI 2 项先失败，再实现并通过。真实 Kotlin 集成的 3 项在构建产物不存在时失败。

独立代码评审发现非普通文件可能在 openSync 中阻塞父进程。修复前回归测试捕获 BLOCKING_OPEN_REACHED；修复后通过。现在先 lstat 排除非普通文件，再以 O_NOFOLLOW/O_NONBLOCK（平台支持时）打开，保留 fstat 检查。

跳过项目：Windows 无权限创建符号链接的测试 1 项；依赖 mkfifo 的 POSIX FIFO 子进程测试 2 项。不能将这些跳过项写成已通过 Linux 验证。

## 环境诊断

JDK 的默认 Unix-domain socket 临时路径使最小 Selector.open() 复现 Invalid argument: connect。指定项目缓存目录后同一探针成功。构建脚本为 Windows 使用 JAVA_TOOL_OPTIONS 设置专用 socket 临时目录，确保 Gradle 客户端和 daemon 都在初始化前获得设置。已清除外部 JAVA_TOOL_OPTIONS 后重新执行构建脚本并成功，不需要手动修复环境。

首次工具链下载较慢；Kotlin 编译器通过官方 Maven Central 分段下载并核对其官方 SHA-1 摘要，随后放入项目缓存。WASI 标准库也与官方模块元数据的 SHA-256 核对一致。Gradle wrapper 固定 8.14.5 与官方 SHA-256。额外诊断下载任务已终止。

## 不包含的保证

- 没有启动镜像、真实硬件、图形界面或 UI 性能结论。
- Node/V8 是受信任原型运行时；没有声称抵御引擎漏洞。
- 没有 cgroup/RSS 硬限制、Linux 命名空间、系统调用完整策略或生产凭据隔离。
- 包和启动配置受信任；没有发布者签名认证。同名 AppID 不赋予其他运行实例的授权。
- 文件描述符固定引用已授权文件，但不保证文件内容在外部并发修改时保持快照。
- 父进程被强制结束后的操作系统级子进程托管/回收需在 Linux/生产宿主阶段单独验证。
- WSL 只有 docker-desktop 内部发行版；Docker 引擎未运行，因此本次没有可用的 Linux 执行环境。

## 真实产物

Kotlin 源码：samples/read-file/src/wasmWasiMain/kotlin/Main.kt；SDK：ming-sdk/src/wasmWasiMain/kotlin/ming/sdk/Files.kt。产物：artifacts/read-file/main.wasm，7632 字节。

SHA-256：83c17b2df1bf7872f3fd8fa5bf64450eaaf33425df08678219d1ebdaf6306c49。

模块导入实际检查只包含 ming_v1 下的 SDK 函数，没有 WASI 或 env 导入。

演示确认内容为 Hello from Kotlin on MingOS! 你好，加换行；ungrantedResource=-1；withoutAnyGrant=-1；exitCode=0。

## 最终执行

构建：PowerShell 设置 JAVA_HOME 为本机 JDK 21，清除外部 JAVA_TOOL_OPTIONS，执行 node scripts/build-kotlin.mjs。结果 BUILD SUCCESSFUL，并生成实际应用包。

最终命令：

~~~sh
node --test tests/*.test.mjs tests/kotlin.integration.mjs
node scripts/demo.mjs
node scripts/check.mjs
~~~

完整测试 25 项、22 通过、3 平台跳过；演示与语法检查成功。独立评审复核特殊文件修复后未发现该修改中的其他可操作问题。
