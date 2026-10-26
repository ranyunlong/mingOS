# CLAUDE.md

MingOS：基于 Linux 的独立桌面与应用平台。应用用 Kotlin/Wasm Compose 编写，经 Rust/Wasmtime
宿主调用 Ming SDK；父进程持有授权与系统资源。Node 只用于构建工具和早期参考宿主，应用执行不依赖它。

## 常用命令

```powershell
# JS 语法检查与单元测试：纯 JS + 生成的 wasm 夹具，秒级，不需要先构建
node scripts/check.mjs
npm test                      # node --test tests/*.test.mjs

# Rust 宿主。脚本把 CARGO_HOME/RUSTUP_HOME/CARGO_TARGET_DIR 都指向仓库内 .cache/，
# 不污染全局环境；首次需先跑 bootstrap-rust.ps1
.\scripts\bootstrap-rust.ps1
.\scripts\rust.ps1 build --locked
.\scripts\rust.ps1 test

# Kotlin 样例。产物写到 artifacts/（未跟踪）
node scripts/build-kotlin.mjs all
npm run test:kotlin           # 依赖上一步的 artifacts/read-file/main.wasm
```

## 仓库结构

- `crates/ming-app-host/` —— Rust 宿主。`broker.rs` 负责能力授权，`graphics/` 是 GPU 与呈现，
  `frame_clock*.rs` 是帧时钟，`desktop*.rs` 是多窗口桌面，`text*/ui.rs` 是文本与 UI。
- `src/` —— Node 侧早期参考宿主（`host/`、`runtime/`）与 CLI。
- `ming-sdk/`、`ming-compose/` —— Kotlin SDK 与 Compose 适配层。
- `samples/` —— 真实 Kotlin/Wasm 样例应用，由根 `settings.gradle.kts` 逐个 include。
- `probes/` —— 各阶段的限定性验证探针。**它们也在 Gradle 构建图里**：根 `settings.gradle.kts`
  用 `includeBuild` 引入 `probes/compose-wasi/port`（替代 `dev.ming.compose:runtime`）和
  `probes/compose-frame/coroutines-port`（替代 `kotlinx-coroutines-core`）。改动探针源码会影响根构建。
- `docs/` —— 从 `docs/README.md` 索引进；`adr/` 是决策记录，`superpowers/specs|plans/` 是设计与
  实施计划，其余 `*-verification.md` 是逐阶段验收记录。

## 约定

- 文档用中文。新阶段的证据、状态和验收产物放 `artifacts/` 下（已 gitignore）。
- `.gitattributes` 统一仓库内为 LF；`*.bat` / `*.cmd` / `*.ps1` 保留 CRLF。
- Rust 与 Gradle 的缓存都在仓库内（`.cache/`、`.gradle/`、`.kotlin/`），均已忽略。
- 不要提交 `**/build/`，也不要提交 `**/builds/`（后者是探针用 `include.init.gradle` 把
  Gradle 输出重定向到的目录）。
- 写文档时区分「已验收」「建议」「待验证」，不要把一个阶段的边界说成完整系统已交付；各阶段
  README 和验收记录都刻意写明了限制范围，保持这个习惯。

## 已知问题

`artifacts/` 未被跟踪，但 `README.md` 与 `docs/` 下有多处链接指向它。克隆仓库后这些链接是断的，
需要在 GitHub 上可见时得单独挑出要公开的证据文件。
