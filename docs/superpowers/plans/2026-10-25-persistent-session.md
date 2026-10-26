# 7D持续会话实施

- [ ] 先用实际协议/预算失败合同RED，保留现有bounded RPC字节与结果。
- [ ] 纯预算算法与cooperative wait授信、私有worker协议及Result校验。
- [ ] parked-aware watchdog和持续FrameClockService；CPU/内存原硬限制不删除。
- [ ] desktop显式duration0、host shutdown动作及受控回收。
- [ ] 有界旧SDK回归、持续模式注入测试与真实Kotlin证据，独立复核。

写范围由独立worker负责execution_budget.rs、engine.rs、supervisor.rs、protocol.rs、frame_clock_service.rs、desktop_catalog.rs、ming_desktop.rs及scoped tests/artifacts。主线程负责native显示/输入与SDK组件，避免重叠。


## 7D独立实现冻结记录（2026-10-25）

七个约定Rust生产文件及一个scoped集成测试已冻结。逐文件SHA-256和源码快照见 `artifacts/persistent-session/source-manifest.json`、`SHA256SUMS.txt`、`source-snapshot/`；实际结果见 `verification.json` 和 `HANDOFF.md`。未修改lib.rs、desktop_native.rs、ui.rs、graphics、Kotlin、默认JS或旧证据。主线程仍需在lib.rs注册 `#[cfg(feature = "engine")] mod execution_budget;`。

完成可选fuel wire、parent授信与worker累计核对、滚动请求预算和wall service-time背压、parked-aware watchdog、持续FrameClockService、duration0/可信shutdown以及Native所需input_bridge getter。持续worker新增私有Ready消息，避免Wasm start section通过cooperative wait逃逸startup watchdog。持续shutdown按host wall time优先于ready/首帧门控及积压UI动作；正duration保留旧调度。

最终scoped rustc执行281项测试（158 default CPU unit、11持续集成、7真实子进程探针、27 bounded回归、78 minimal unit；两个unit配置有重复覆盖），全部通过；minimal/engine-only library编译通过。复用现有Cargo deps，CARGO_INCREMENTAL=0，未建立新全量target/cache。Getter、startup及shutdown门控保存RED→GREEN；真实120ms watchdog下约750ms idle、busy loop fuel trap、请求超额单app隔离、关闭/cancel及OS PID回收均有证据。host service elapsed是wall service time，不是精确CPU计量。

真实持续Kotlin、多平台GUI、完整GPU矩阵、独立review、SDK6/FrameCredit接入与boot由主线程继续验收。本记录不将短时Wasm或假时钟单测称作真实长时间Kotlin运行。


7D终止授信边界追加：Close/Cancel或live.closed后锁存终止，后续wait的fuel_grant为Some(0)，不增加totalGranted。仅supervisor.rs修改；manager 200ms force-cancel及UI行为不变。真实RED显示忽略20次Close的guest原可消耗50241 fuel（初始10000）；修复后legacy/SDK5两路径均fuel trap。追加192项scoped测试通过，修订冻结及hash位于 artifacts/persistent-session/terminal-credit/，保留原冻结证据。


Native rich输入计数hook追加：Session::note_input_drop(WindowId, &'static str)仅增加对应实例的dropped_inputs并记录app/window/generation、input_dropped和静态reason，不记录text；unknown忽略。legacy send及manager poll/200ms cancel源码片段逐字不变。171项scoped测试通过，RED/GREEN及最新ming_desktop.rs hash保存在 artifacts/persistent-session/input-drop-hook/，旧冻结证据保留。
