# 持久安装 Task 4：raw checker 已实现，真实资格尚未运行

本目录包含两轮输入、未批准 review/budget 草稿、只读 verifier 和有界 Linux stage driver。本轮仅编辑这个准备目录；没有运行 QEMU、helper、bundle build、创建数据盘、正式 review 或提交。`reviewed=false`、`reviewer=null`、`finalSystemAccepted=false`。合成正例只验证 checker 接受合法结构，不能作为真实启动或物理鼠标资格证据。

`verify-persistent-install-evidence.py` 的 pair 入口已接通各门控，不再使用永久失败占位。缺少正式 review、独立签名文件或任何完整 raw stream 时继续拒绝；不接受 success-only summary。当前最终52项测试和10项静态/CLI检查见 `validation-task4.json` 与 `task4-final-checker-tests-v5/`，历史准备测试仍原样保留。

## 实现范围

- 两份输入保持原 SHA256：boot1 INSTALL / ALLOW / app2 Dock / POWER 四步；boot2 Dock / POWER 两步。Dock 为 `(680,770)` 实际 pointer + left down/up，没有 script/action/timer 启动；第一轮只 autostart app1，第二轮不安装、不播种。
- `raw-audit-base.py` 保持历史字节 SHA256 `fc005850bac0f4985e1ab0708f7ff3981cc389658170ee2233f68a4bdfe28bb2`。pair 复用其完整 guest/desktop stderr、raw state、worker 身份、QMP 唯一成功 ACK、pointer replay、自然退出/回收/sync/unmount/PowerDown 门控。5秒只按同一 host monotonic 从 ABS 发出起算，不比较 host/guest 时间。
- helper pre/post snapshots 按 raw argv、phase、role、boot/session、磁盘 UUID、marker 分别校验 envelope；只比较 boot1 final 与 boot2 pre 的完整稳定 content。content 有精确 schema、根文件交叉哈希、活动包受控路径、排序唯一且至多256文件、合法历史 package 完整 manifest/Wasm 对；未知/pending/journal/锁/逃逸路径拒绝。boot2 pre/post 必须相同。
- 从完整 stderr 原始 bytes 独立解析 `MING_PERSISTENT_INSTALL `；wrapper 精确绑定 boot/session/PID，单行至多262144 bytes、all receipts≤128、native edges≤64。逐条重放 guest `desktop-persistent-receipt` 的原 record 和 `[logStartOffset,logEndOffset)`，含 prefix 和 LF；遗漏、漂移、截断拒绝。
- 第一轮恰一 commit，无失败 receipt；commit 精确身份、signed source fingerprint/manifest/Wasm hash、原生 trace 内容/顺序、final content 一致。installed-complete 复用父侧持锁 commit content，要求 ALLOW down 收据 → 原始 Wayland ALLOW up 按钮 → commit → installed → ALLOW up 收据 → Dock 顺序，commit/installed trace 必须确实位于 ALLOW up handler trace 内，其 revision 被 ALLOW up 的真实 before/after 包围。第二轮不得有 commit、failed、installed-complete 或安装 trace。
- ALLOW up 内可先发布 installed state，再发布完整 handler 的 after state。仅首轮 seq4 允许 `before < installed <= confirmed < after` 的中间采样；下一 Dock 的 armed/baseline state 必须达到 after revision，且下一 pointer-ready 原始偏移必须晚于完整 up 收据。其余边沿仍要求 after 不晚于 expectation。对应 RED/GREEN 和缺 up、错序、过早下一步等反例见 `allow-mid-handler-tests/`、`allow-wayland-up-tests/` 与最终 v5 日志，不比较跨钟域时间。
- 所有真实左键 down/up 连续 sequence/pressSequence 与完整 client Wayland button 顺序、pointer/surface、实际坐标、已审 QMP 输入步骤一一绑定；不只挑 Dock 行。before/after 包含真实 state 和完整 AppRecord history，revision、traceCount/trace slices、无 droppedTrace/droppedInputs，并与同 revision 的轮询 raw state 一致。真实 live AppRecord.packageId 在最终 Result 前可为 null；错误非 null、已完成仍 null、最终错误身份均拒绝，每个持久快照同时绑定独立已验签的 initial app、trust/bootstrap 和 source 三 hash。
- 两轮 app2 Dock down 前必须没有 app2 state、完整历史或 trace。真实 down 的 action=launch、targetApp=2 后首次出现 gen1；worker-started、ready 必须绑定同 down、window、PID，先后在完整 raw bytes 中可定位；ready 具有实际接受的 presentation/inputReady。最终 outcome/reap 的 app2 身份必须相同，并排除复用 app1 PID。普通 Dock down 语义未改变。
- fixture 验证重新读真实 helper command、receipt、stdout、文件枚举、模板及 reviewed Wasm，独立 Ed25519 验签、篡改消息反例和 storage fingerprint 重算。pair 每次从原 fixture 目录重新验证后才接受验证 JSON，不信任其中声称成功的标记。

## 使用方式（只读校验）

```sh
python3 -B verify-persistent-install-evidence.py fixture \
  --repo /repo --inputs /inputs --review /review.json \
  --fixture /evidence/fixture-stage --output /evidence/fixture-verified.json

python3 -B verify-persistent-install-evidence.py pair \
  --repo /repo --inputs /inputs --review /review.json \
  --bundle /evidence/bundle --pair /evidence/cold-pair \
  --fixture-verification /evidence/fixture-verified.json \
  --output /evidence/pair-verified.json
```

输出必须是新文件。checker 不会执行 helper、签名、构建或启动 VM；签名验证所用 development fixture public key 与现有 helper `[7;32]` seed 一致，并要求实际 trust store 精确匹配。

stage driver 的 fixture 模式使用 `initialize-first <root> <initial-wasm> <source-wasm> <source-manifest> <catalog-template> <catalog-sha256> <receipt>`；guest 模板固定 `/opt/mingos/catalog.json`。`open-existing <root> <receipt>` 不读模板或种子。fixture/bundle/cold-pair stage 上限60/300/2000秒，每 VM900秒；本轮均未执行。driver 在创建输出前拒绝草稿 review。

## 尚待真实证据

见 `UNFINISHED.md`。当前 source/helper/profile/producer 合同已有实现和合成审计，但实际新 Linux 二进制、正式主代理 review、只读预算证据和两次新 VM 的真实 raw streams 尚未取得。只有这些输入全部就绪、独立复核和 pair 验证实际通过后，才可以报告持久安装正常重启与真实 Dock 启动资格；物理输入继续暂停。
