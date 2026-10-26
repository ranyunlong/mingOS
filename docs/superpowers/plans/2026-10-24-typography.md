# SDK3 typography implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development with disjoint write scopes and review; test first.

**Goal:** Render real Chinese/Latin labels through broker-owned fonts and immutable SDK3 layout handles without changing legacy text.
**Architecture:** Restricted cosmic-text adapter owns layouts/masks; SDK3 RPC validates memory and quotas; CPU and GPU use identical coverage; persistent glyph atlas with tile-indexed candidates preserves painter order and direct surface.
**Tech Stack:** Rust1.99.1, cosmic-text=0.19.0(std/swash only), pinned NotoSansSC Sans2.004, existingwgpu27/Kotlin2.3.20.
**Spec:** docs/superpowers/specs/2026-10-24-typography-design.md

## Global constraints

Keep SDK1/2/op3, no WASI or system-font scan, builtinfont1 only. Font<=16MiB; layout512bytes/512glyphs/64live; em8..96px Q6quartersteps; masks4MiB/4096; metadata2MiB. GPUglyph4MiB, image32MiB; frame2048glyphs/65536tile refs/64candidates each/2MiBstaging/1MiBnewuploads. No Git/worktree/commit. Qualification targets and limits distinguished honestly.

## Task1: Asset and text engine qualification

Files assets/fonts/{font-pack.json,NOTICE.txt,OFL.txt,NotoSansSC-Regular.otf}, scripts/verify-font-pack.py, tests/test_font_pack.py; src/text/{mod.rs,engine.rs}; Cargo files.
- [x] Verify immutable font bytes, licensing/embeddednames/cmap and metadata with negative fixtures.
- [x] Add failing host tests; integrate restricted cosmic FontSystem without system discovery; shape/measure/grayscale masks.
- [x] Test real corpus, phases, missing glyphs, bounds and empty/invalid strings; measure first-load private memory before enabling.

## Task2: Layout service and SDK3

Files text/service.rs; package,engine,broker,supervisor; SDK3 integrationtests.
- [x] Typed monotonic app-scoped handles, immutable records, close/quota/metrics serialization.
- [x] Precise imports, bounded guest copies, metrics output range checks; negotiate SDK3 without promoting SDK2 commands.
- [x] Frameop5 preflight with painter order, frame retained resources; CPUreference masks.

## Task3: GPUglyphcache and tile preparation

Files text/{cache.rs,bins.rs}; graphics/gpu.rs,scene.wgsl.
- [x] Pure bounded cache/bin tests first; define masks/residency generation and current-frame pinning.
- [x] Separate coverage binding, upload only dirty ranges; integer blend and glyph order preserved.
- [x] Hardware differential, reuse/move/color/close/eviction/error regressions and counters.

## Task4: Kotlin API and actual application

Files ming-sdk newText.kt/Frame3.kt, samples/text-demo, build adapter/tasks asneeded.
- [x] New classes avoid contaminating SDK2 imports; immutable textLayout and explicit close.
- [x] Real Chinese/Latin sample at multiple sizes with metrics and bounded inputs.
- [x] Compile actualKotlin and runCPU/GPU/native; preservelegacyexamples.

## Task5: Complete and continue

- [x] Full tests/Clippy/fmt, rawasset/font-memory evidence, scopedreviews/fixes.
- [x] Freeze source, LinuxCPU/Wayland+allfeatures and finalrevalidation.
- [x] Record constraints/perf/captures and advance tovisualeffects stage automatically.

最终验收见docs/typography-verification.md；Windows130+6显式硬件测试，默认93/no-default24；Linux93、85验收fixtures/42字体fixtures、5应用路径与全功能编译通过。整个系统goal仍active，下一阶段为视觉效果/UI基础。
