# 交付主线恢复检查点

日期：2026-10-27。当前继续kernel桌面关闭、Dock重启和后续整体验收，整体交付未完成。
完整状态与必要输入见 `artifacts/boot-delivery-20261027/DELIVERY-STATUS.md`。

## 构建链恢复

原工具镜像丢失，固定Debian基础仍存在。原容器HTTP下载失败、HTTPS证书环境不完整；本轮通过APT验证官方签名索引，按原锁定版本解析279包，再由宿主TLS验证的HTTPS取得完全相同字节。
每包大小和SHA256均核对，内核旧下载经同一索引校验后复用。一个HTTP/2失败包仅改传输为HTTP/1.1，未关闭TLS校验或更换版本。

安装上下文仅含已校验包，以`--network=none`构建。APT本地安装在`--no-download`下出现内部路径错误；相同小包禁网对照确认该选项退出100、正常本地获取退出0，随后保留禁网约束并移除该选项。
成功镜像ID为 `sha256:2440560d810a834637cfeeb1057f7f471c40caa8e532645e00e063cbc8ec639a`，默认UID65534。
279个实际安装版本与计划逐项一致。工具/kernel配置与模块检查、1085文件/80548468B运行库复制、暂停QEMU/QMP检查均通过。
暂停检查没有加载kernel，不能算作系统启动验收。单一镜像已导出为可恢复归档。

## 当前修复的系统Host

按既有可信系统镜像设计，在独立源码快照内嵌入原Counter/Text的raw和compiler输出，SDK与完整源字节都匹配才命中。
除生成表/数据外运行时Rust源码与开发树一致；staging Cargo只增加独立输出bin别名，避免共享cache顶层开发二进制被覆盖。最终payload仍为ming-app-host，开发注册表仍为空。

- 库回归203/203；独立源码/构建输入复核44项通过，146输入重读一致。
- 初版恢复 Host SHA256：`1c6181ddee9c7f6abaf2ac2e89c0ed77cd89a2e99bb511954b96897496ac1434`。后续 persistent-RPC 修复生成独立 current Host：`70f238adef5874cc6f8f7b445a998a8acfef4e594f1786318d5466168594d6af`，并使用隔离 Cargo target 重新构建。
- 真实两Kotlin包，CPU offscreen及原生Wayland各3实例均为system-image、自然关闭、Close1/Cancel0、无残留worker；各自新数据根Counter0→1。
- 两代Text精确mingos/again，默认中文note保持；Wayland实际呈现14帧。
- 两条路径49项独立审计通过；这是可信Host动作脚本，不是kernel/QMP/物理输入。

正式boot profile另发现Counter实验收据阶段不被接受。已用原脚本生成正式package收据，并确认原main.wasm/compiler.wasm/app.json字节一致；未编辑旧收据或放宽验证。
新kernel候选包与16步pointer握手/状态/代际输入已纯校验，但review保持未批准。几何来自当前冻结代码和原生验证，kernel实际位置仍须握手核对，不能用坐标重试掩盖失败。

## 仍缺的交付条件

原严格探针必须匹配既有两项SHA，当前找不到原件/可复现创建来源，未改哈希门槛。
优先需要旧Windows的完整 `final-bundle-v19` 或 `final-bundle-v9-gated`，其initramfs可取回原探针；原盘快照/身份也是后续持久化要求的输入。
用户批准 source-v2 新世代后，当前 persistent-RPC Host 已完成真实 kernel 的关闭/Dock重启/严格 worker SIGKILL 恢复/数据同步与 PowerDown，以及同 bundle、同新盘的两次 fresh QEMU 复验。该证据只覆盖 `strict-worker-sigkill-v1`，仍需为其余平台服务恢复模式和整体系统验收定义独立合同并实际执行。
原1800ms请求回归的先前RED不变。本轮没有用新数据根、暂停QEMU或原生通过代替上述验收；在随后第三轮同一缺件阻塞复核后，整体goal已标记blocked，交付未完成；取回原导出输入后继续。
