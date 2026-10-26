# Ming SDK v0 与宿主协议

状态：实验 ABI，只覆盖第一阶段验证。SDK 版本 1 不是长期兼容承诺。

## 包清单

必填字段：schemaVersion=1、id、version、sdkVersion=1、entry。其他字段一律拒绝；文件授权不属于清单字段。entry 只接受同目录的 .wasm 文件名，拒绝绝对路径、目录跳转和最终符号链接。包目录和启动配置属于可信输入；未实现发布者签名与应用安装流程。

## 外部导入

模块只允许 ming_v1 的函数导入。WASI、env、未知模块、未知方法、内存/表/全局变量导入拒绝。函数参数和结果使用 i32；实际 SDK 与宿主进行逐项范围、参数数量和句柄验证。导出入口为 _start。

| 函数 | 参数 | 结果 |
| --- | --- | --- |
| file_open | resourceId | 正句柄或错误码 |
| file_length | handle | 字节数或错误码 |
| file_read_byte | handle, offset | 0..255 或错误码 |
| file_close | handle | 0 或错误码 |
| log | code, value | 0 或错误码 |

错误码：-1 DENIED；-2 INVALID；-3 IO_ERROR；-4 LIMIT。资源编号 1..65535，只由可信启动配置映射到本地路径。句柄绑定本次运行的 broker，与操作系统文件描述符不同。原型限制 16 个资源、32 个同时打开的应用句柄、每文件 256 KiB、4096 条日志。

按字节读取仅用于第一阶段正确性验证，不是最终高吞吐文件或图形接口。应用必须检查错误码；下一阶段设计有界批量传输、取消与异步错误。

## 进程协议

本节描述 Node 参考宿主的内部协议。Rust 原生宿主保持同一 SDK 函数 ABI，但初始化消息另外包含 fuel、memoryBytes，结果另外包含 fuelConsumed；内部 worker 协议不作为应用 SDK 的稳定接口。原生运行说明见 [Rust App Host](native-host.md)。

父进程启动固定 runner，以匿名 stdin/stdout 管道通信，清理继承环境。初始消息只有 Wasm 字节，不包含宿主文件路径、授权表或用户环境。

子进程请求：type=call、单调递增 id、method、整数 args。父进程从所持有的 broker 判定权限，回复相同 id 和整数 value。子进程不能通过消息选择另一个 AppID。

成功结束：type=result、exitCode=0，且子进程必须正常退出。失败：type=error，包含分类与有界消息。宿主限制请求数量、协议缓冲和运行时间，等待子进程 close 后才结束并回收资源。

默认运行期限 5000ms、模块 4 MiB、请求 32768 次。Node 堆参数不等于总内存硬上限。没有实现生产级 CPU/GPU 配额或 Linux 内核沙箱。

## 示例日志

1：授权文件长度；2：一个 UTF-8 原始字节；3：打开未授权资源 2 的结果；4：关闭句柄的结果；5：资源 1 不可用；6：读取错误。示例事件是诊断协议，不是稳定的产品 UI 或日志格式。
