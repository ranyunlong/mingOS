# 真实运行之前仍未完成

1. 主代理冻结 Rust producer 与 guest transport 源码后，独立复核本目录 checker 及其三方 schema/argv 合同。当前原生收据与 pair gate 已实现，并有两轮 native 合成正例、事务/installed/内容/签名正反例；这些不是真实 VM 资格。
2. 新 Linux release host/helper 构建与最终 payload/template/Wasm/trust hash、source fingerprint 尚未取得。本目录 draft 的未知 hash、`reviewed=false`/`reviewer=null` 保持不变；正式 review 必须由主代理另存，不能用修改草稿绕过。
3. 显式父侧资格开关为 `--qualification-receipts persistent-install-pointer-ready-v1`，只对预审资格会话启用；普通持久会话不受64 native edge预算限制。正式 source/binary/profile/budget 必须绑定同一实现，不能复用旧RAM测试证据。
4. 未执行新 fixture stage、bundle build、QEMU、新卷或新128MiB数据盘。Task5 只有取得具体动作授权和正式 review 后才能按一次首轮、一次同盘冷重启执行；每VM900秒、外层2000秒，失败不重试、不追加输入、不格式化。之后对真实文件运行 fixture 与 pair verifier。
5. 物理显示/物理鼠标资格没有进行，继续暂停；即便 raw pair 将来通过，`finalSystemAccepted=false`。

历史 raw base 和旧测试证据未修改。本轮编辑仅在这个新准备目录，未改项目 Rust/Python 源码、正式 review 或执行真实运行。

最新 freeze 已修正 ALLOW handler 中间 installed revision 的合法采样，并绑定原始 Wayland up 按钮必须先于 commit。52 项合成测试和 10 项静态/CLI 检查通过；这些仍不是完整真实 pair 的成功证据。
