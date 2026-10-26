# NEXT-stage typography research

Research snapshot: 2026-10-24. Design only; implementation starts after the parent GPU stage and Linux source provenance work finish. This document does not authorize changes to those stages. The continuing goal is usable MingOS, beginning with readable Chinese/Latin labels, not an entire UI framework or IME.

## 1. Existing contract and integration boundary

CodeGraph-first inspection: `ming-sdk/src/wasmWasiMain/kotlin/ming/sdk/Graphics.kt:32`, `crates/ming-app-host/src/graphics/{frame.rs:17,font.rs:1,raster.rs:69,gpu.rs:329,image_cache.rs,gpu_session.rs,presenter.rs:63}`; direct read of unindexed `graphics/scene.wgsl` after CodeGraph returned no match. These are a moving-parent snapshot, not a frozen implementation baseline.

- SDK 2 submits MGF2 magic `0x4d474632`, frame-layout version 1, 24-byte header, 32-byte commands; frame <=65,536 bytes, <=256 commands, UTF-8 pool <=4,096 bytes. Each op 3 payload is <=256 bytes, valid UTF-8; scale is 1..4. Frame dimensions <=4,096 each, <=4,194,304 pixels.
- Op 3 is `[x,y,scale,ARGB,pool_offset,byte_length,0]`. Preserve its integer top-left origin, uppercase ASCII conversion, 5x7 bitmap, `6*scale` advance per Unicode scalar, blank space, unknown-character box, clipping and blend rounding. Lowercase and unsupported Unicode already have observable fallback behavior. Do not reinterpret op 3 as high-quality text or change SDK 2 limits.
- GPU op 3 currently derives one fixed cell directly from each pixel's x coordinate; it does NOT scan every glyph. Variable advances/marks cannot reuse that addressing. The existing shader scans scene commands per pixel, with a 64,000,000 candidate-work guard.
- The host owns a serialized GPU session, a persistent image atlas capped at 32 MiB, and a same-device GPU output buffer consumed by native surface presentation. Image cache reuse avoids uploads on unchanged scenes; compaction may reupload live images. Preserve these contracts, image handle/count limits, error latching and native-buffer present.
- Future implementation changes only text preparation/drawing plus explicit SDK 3 plumbing. No replacement GPU device, image API, presentation architecture or CPU-frame upload path is required. Reconfirm the parent snapshot before coding.

## 2. Font: precise redistributable candidate

Recommend **unmodified `NotoSansSC-Regular.otf`**, upstream `notofonts/noto-cjk`, tag **`Sans2.004`**, path `Sans/SubsetOTF/SC/NotoSansSC-Regular.otf`. This is the upstream China region subset, not the Google Fonts variable TTF/WOFF2 with a similar family name. Start with Regular/400 only; no synthetic bold/italic, variable axes, collections or additional OS fonts.

- Tag resolves to commit **`523d033d6cb47f4a80c58a35753646f5c3608a78`** [F1]. GitHub metadata reports **8,331,336 bytes**, Git blob ID `fc0fda9394367c16c1af7555a2ae4d5e2e6a6f02` [F2]. This blob ID is NOT a downloaded-file SHA-256.
- Larger alternative: `Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf`, **16,437,364 bytes**, blob `dc15562470b4f842321894787a0d066879ccff8b` [F3]. Upstream describes language-specific OTFs as full coverage and SubsetOTFs as region-specific [F4]. Choose the larger file only if the required repertoire exceeds the SC subset; do not silently substitute fonts at runtime.
- The exact tag's root `LICENSE` says **SIL Open Font License 1.1**, dated 26 February 2007 [F5]; this selected release is not Apache-2.0. Bundling/embedding/redistribution with software is permitted subject to retaining copyright notices and OFL, no standalone sale, and the license's reserved-name/derivative conditions. MingOS's code license does not replace the font license.
- Later packaging must retain upstream filename, distribute the full OFL and the actual font copyright/notice metadata in accessible notices, and record commit, path, byte length and a freshly computed SHA-256 in broker-owned asset metadata. Avoid custom subsetting initially; it creates a modified-font naming/notice review.
- Verified here: upstream release format description, tag/commit, metadata size/blob identity and license text. **Not verified:** binary hash, embedded copyright/reserved names, actual name/cmap/CFF tables, precise Chinese/Latin repertoire, hinting or rendered quality. No font binary was fetched. Audit the requested Latin accents, Chinese punctuation, rare Han and replacement glyph against the actual cmap before promising coverage.

## 3. Two plausible Rust options; exact proposal pins

Versions below were resolved from primary docs on the snapshot date, not recalled as “latest.” Pins are research recommendations, not dependency/lockfile edits.

| Option | Proposed direct pins | Fit and costs |
| --- | --- | --- |
| A: explicit single-line pipeline | `fontdue = "=0.9.5"`, `rustybuzz = "=0.20.1"` | Rustybuzz shapes to glyph IDs/positions; fontdue supplies indexed grayscale masks. Small explicit API surface; broker must implement script-run segmentation, direction/cluster handling and layout policy. Fontdue eagerly preprocesses mapped/GSUB glyph outlines on font creation, so a CJK font's startup/heap cost needs measurement. |
| B: restricted existing text engine **(recommend)** | `cosmic-text = { version = "=0.19.0", default-features = false, features = ["std", "swash"] }` | Existing shaping, run layout and raster interfaces reduce Chinese/Latin positioning mistakes. Use only label layout and masks; do not expose its editor/widget abstractions. More transitive code and internal caches require accounting. |

A is viable for a deliberately narrower label engine, but do not advertise fontdue's character layout as OpenType shaping. Verified `Font::{from_bytes,metrics_indexed,rasterize_indexed,horizontal_line_metrics}`; masks are top-left row-major u8 coverage [R1]. Fontdue's source calls `outline_glyph` across collected mapped/substitution glyphs [R2]. Its ttf-parser backend documents glyf/CFF/CFF2 outline support [R3]; this does not replace a smoke test of the chosen OTF. Rustybuzz exposes `UnicodeBuffer::{push_str,set_direction,set_script,set_language}`, `shape`, glyph clusters and x/y advances/offsets [R4]. Use the same face/index in both libraries, scale design units by ppem/UPEM, and never add independent kerning after shaping.

B's **0.19.0 uses harfrust, not rustybuzz**, for shaping; its manifest specifies harfrust ^0.5.0, fontdb ^0.23, skrifa ^0.40.0 and swash ^0.2.6. Its declared Rust minimum is **1.89** [R5]; this workspace's `rust-toolchain.toml` requests 1.99.1, satisfying the declared minimum only. Transitive resolution, compilation and target support remain untested. Primary swash API inspected at **0.2.10**, within that requirement; the later implementation must lock the resolved graph and recheck those APIs.

Fontdue and cosmic-text publish MIT OR Apache-2.0 licensing; rustybuzz publishes MIT, with upstream HarfBuzz notices to retain [R6]. This statement does not audit the entire transitive license graph; font OFL and dependency notices must be packaged separately.

## 4. Broker ownership and restricted engine adapter

- Broker alone loads the immutable bundled font pack and owns font bytes, parsed fonts, layouts, masks and GPU residency. Guest input is UTF-8, built-in numeric style IDs and coordinates: **no font bytes, native font handles, family strings, host paths, URLs or native font loaders**. Layout handles are app-scoped, typed, generation checked and invalid across broker instances; closing an app releases its handles.
- For B, create `fontdb::Database::new()`, load broker bytes through `load_font_data`, then `FontSystem::new_with_locale_and_db("zh-CN", db)`. Validate the single installed face and map built-in style 1 to that exact family/Regular face. Do not call `FontSystem::new()` **or `new_with_fonts()`**: source shows the latter's std path calls `load_system_fonts()` [R7]. No file-backed `Source`, fontconfig, host discovery or runtime fallback outside this database.
- Restrict all attributes to that face, weight 400 and fixed features; no guest-controlled rich spans. Disable optional `shape-run-cache`, editor and syntax features. Use `Buffer`, `Metrics`, `Wrap::None`, `Shaping::Advanced`, `set_text(..., None)`, `shape_until_scroll`, `layout_runs`; copy resolved glyphs/clusters into broker records [R8]. Temporary buffers need not live as widgets.
- Fetch masks through `SwashCache::get_image_uncached` into the broker's bounded LRU; never retain its unbounded public image/outline maps or use `Buffer::draw`/`with_pixels` callbacks as the GPU renderer. Source selects standard outlines with `Format::Alpha`; accept only `SwashContent::Mask`, rejecting unexpected color/subpixel content explicitly [R9].

## 5. SDK evolution: new operation under SDK 3

**Choose SDK 3 imports + op 5 within the existing MGF2/layout-1 envelope.** SDK version and frame-layout version are separate contracts. A new frame layout adds migration work without helping this bounded label stage; reserve MGF3/layout-2 for variable-sized commands, large pools, paragraphs or future protocol requirements backed by measurements.

Proposed imports in `ming_v3` (names/signatures are new design, not existing APIs):

- `text_layout_create(ptr, byte_len, builtin_font_id, em_size_q6, flags) -> handle/error`; style 1 is the pinned Regular face. Copy and validate guest bytes immediately, then shape. Layout is immutable and independent of the frame pool. `flags=0` is strict; bit 0 `ALLOW_MISSING` explicitly permits .notdef and sets a metrics warning. Reject other flags.
- `text_layout_metrics(handle, out_ptr, capacity) -> 48/error`: write exactly 12 little-endian i32 words: advance, ascent, descent, gap, line_height, ink_left, ink_top, ink_right, ink_bottom (first nine are Q26.6 pixels), glyph_count, status_flags (bit 0 MISSING_GLYPH), font_pack_revision. Validate full guest output range first.
- `text_layout_close(handle) -> status`; app teardown closes all. `ming_v3.frame_submit` retains the existing packet layout and commands 1..4; new op **5** has arguments `[layout_handle,x_q6,baseline_y_q6,ARGB,0,0,0]`. The frame retains referenced layouts until GPU completion; a subsequent close cannot recycle an in-flight handle or mask slot.
- Decoder receives the negotiated SDK version: SDK 2 rejects op 5 before rendering; SDK 3 validates op 5's typed handle, coordinates, flags/reserved zeros and aggregate budgets. The old op 3 behavior and pool/command/frame limits remain identical under both SDKs. Never enable op 5 merely by relaxing the shared decoder's unknown-op match.
- SDK 3 support needs a deliberate import allowlist/package version/capability update and feature discovery before guest calls. Register existing resource/event operations under `ming_v3` with unchanged signatures, and audit broker `sdk_version == 2` guards explicitly so SDK 3 does not accidentally lose file/image/event access; keep SDK 2 negotiation unchanged. Proposed errors: existing invalid-input convention `-2`; allocate new documented codes for unsupported feature, missing glyph, text limit and stale handle. Do not reuse terminal GPU-device errors for ordinary text rejection.
- Later Kotlin API: `layout = textLayout(value, font=1, emPx=20)`, `layout.metrics()`, `frame.textLayout(layout,x,baselineY,color)`, explicit close. Keep `MingFrame.text(...,scale)` emitting op 3 without a semantic change. No SDK code is changed by this research.

## 6. Measurement and positioning contract

- Phase scope: single horizontal Chinese/Latin label, no wrapping/ellipsis/normalization, no newline/tab or C0/C1 controls, no bidi formatting controls or RTL runs. Validate and reject unsupported input explicitly rather than guessing a paragraph policy. Empty string is valid with zero advance/ink. Preserve UTF-8 cluster byte ranges; never measure by Kotlin UTF-16 length, Unicode-scalar count, or summed bitmap widths.
- Frame pixels are the coordinate space, +x right and +y down. Origin is **left pen position on baseline**, unlike legacy text's top-left. New coordinates/metrics use signed Q26.6; position magnitude <=1,000,000 px, em size **8..96 px in 1/4 px steps**. No hidden DPI multiplier; resize/display scaling requires explicit layout recreation at the intended physical em size.
- Measure and draw consume the same immutable shaped records. Advance is the shaped pen delta (including spaces/kerning); ink conservatively encloses positioned mask rectangles across supported subpixel phases and may have negative bearings/overhang. Baseline-relative ink bounds are half-open. Spaces may advance without ink; combining marks may have zero advance. Draw clipping never changes reported metrics.
- For B, obtain face metrics with `font.as_swash().metrics(&[]).scale(em_px)` [R10]; expose nonnegative ascent/descent and `gap=max(0,leading)`, line_height=ascent+descent+gap, independent of string contents. Verify sign conventions against this engine, not a copied FreeType formula. Suggested top alignment is `baselineY = topY + ascent`; there is no implicit baseline snapping or automatic line stacking.
- Upstream buffer rendering calls `glyph.physical((0,run.line_y),1.0)`: glyph data is already relative to the run baseline. For our baseline API, replace that offset with `LayoutGlyph::physical((x,baselineY),1.0)` on the original glyph; neither subtract nor add `run.line_y`. Bitmap left=`physical.x+placement.left`, top=`physical.y-placement.top` [R8,R9]. Preserve library shaping/subpixel bins; its physical conversion quantizes positions, including Y hinting.
- Return advance rounded to nearest 1/64 px; expand ink minima down and maxima up to 1/64. Canonical ink metrics at origin `(0,0)` must conservatively cover the engine's finite subpixel phase variants: compute their union rather than assume a universal one-pixel padding. Report these as conservative ink bounds, not a phase-exact hitbox. CPU and GPU use identical quantization/coverage masks. For option A, bitmap top is `baselineY-(shaped_y_offset+ymin+height)` with Y-up offsets; xmin/ymin are not advance widths.
- Unsupported printable clusters return MISSING_GLYPH by default; optional ALLOW_MISSING returns .notdef plus the visible status flag. Never fall back to legacy ASCII boxes automatically. Font-pack change invalidates old metrics/layout/cache generations; apps recreate layouts explicitly.

## 7. Initial limits and cache accounting (proposals, not measured capacities)

| Resource | Enforced policy for the first implementation |
| --- | --- |
| Font pack | One Regular face; <=16 MiB source bytes per broker; immutable asset shared within that broker. The full CJKsc file also fits this byte cap, but must repeat the parser/heap acceptance gate. |
| Layout input/output | <=512 UTF-8 bytes/layout, <=512 shaped glyphs/layout, <=64 live layout handles/app; retained original strings <=32 KiB. Reject before publishing a handle if shaping expands past the cap. |
| Frame text work | Referenced unique input bytes <=4,096, <=2,048 positioned glyph instances/frame (count repeat draws), <=256 total scene commands; existing frame/pixel/pool limits remain. |
| CPU caches | <=4 MiB grayscale mask bytes plus <=4,096 mask entries; <=2 MiB owned layout/cache metadata including retained strings, glyph records and allocator bookkeeping. LRU; reserve/check before insertion. |
| GPU text cache | **Separate <=4 MiB persistent packed-u8 coverage buffer**, including padding; <=4,096 slots; <=256x256 bitmap including border. Does not consume guest image IDs or shrink/grow the existing **32 MiB image atlas**. |
| Frame staging | <=2 MiB text records/bin metadata; <=65,536 tile references; <=64 glyph candidates per tile per text command; <=1 MiB newly uploaded mask bytes/frame. Count actual allocated buffers/padding. |
| Hidden engine memory | 64 MiB peak font/parser/shaping heap acceptance target per broker, separate from the accounted caches. This is **not a proven upstream hard bound**; profile the selected font and bounded requests before shipping. No claim that a 8 MiB font requires only 8 MiB RAM. |

Validate lengths/ranges with checked arithmetic before shaping, allocating or queueing work. A post-shape glyph-count check alone does not bound the library's temporary allocation: qualify worst-case bounded input/combining sequences, then add allocation accounting or isolation if that peak cannot be bounded acceptably. Fix family/weight/features so matching caches cannot grow with arbitrary attributes; leave optional shape-run caching disabled. Validate GPU storage-binding/allocation limits on the existing device before enabling the capability.

Pin all current-frame and in-flight slots; evict only completed-use LRU entries. Fragmentation never permits exceeding the physical buffer budget. If placement, input, mask dimensions, residency or upload/work budgets cannot be met, reject the frame/layout atomically with TEXT_LIMIT before any submitted draw; retain last successful frame. No silent truncation, incomplete Chinese glyphs, image reuploads or terminal GPU-error latch on ordinary quota failure. Admit/warm visible UI layouts in bounded batches before presenting a dense new screen.

## 8. GPU glyph path without a presentation rewrite

1. Resolve each op 5 to broker-owned positioned glyphs, ordered exactly as shaping produced them. Cache key: font digest/face index/pack revision, glyph ID, weight, em-size Q6, raster flags and quantized subpixel phase; exclude color, clip and absolute position. Color changes and integer-pixel movement reuse coverage.
2. On a cache miss, rasterize that glyph only; pack four alpha bytes per u32 in the separate persistent storage buffer with a transparent border. Upload dirty ranges only and update bounded offsets/generation metadata. Never open an image handle for a glyph, regenerate a whole text image, or upload resident glyph pixels on every frame. Atlas eviction/device loss/phase changes can cause misses; counters must distinguish them from steady-state reuse.
3. Build **16x16 pixel tile bins per op 5** from clipped glyph bounds; append glyph indices in original order. Empty bins are O(1). At an op 5 in the current command loop, a GPU invocation reads only its tile's candidate list, bounds-checks each referenced glyph and samples its atlas coverage. No loop over the entire run, entire font or all cached glyphs at every pixel.
4. Preserve painter order across rectangles/images/legacy/new text by leaving the existing scene-command traversal intact. Within a bin, preserve glyph order for overlapping marks. Scissor every mask to the active clip/frame. Effective alpha is `(coverage*ARGB_alpha+127)/255`; composite once with the existing integer RGB blend rule. No RGB subpixel rendering or color emoji this stage.
5. The 65,536-reference cap bounds additional glyph candidate checks to at most **16,777,216** (256 pixels/tile); preflight this separately from the existing 64,000,000 scene-command check. Cap per-bin count as above. Per-frame uploads are positioned records/bin indices plus genuinely missing masks, not all glyph images.
6. Add the persistent read-only glyph storage binding to this session's text shader path; keep the same output storage buffer and existing native presenter. Queue uploads/draws and residency mutation under the existing serialized session. Reuse slots only after their recorded queue submission completes; increment generations on relocation/recreation. CPU screenshots are explicit readback, not part of normal present.

## 9. Next-stage gates and explicit non-verification

Proceed in continuous, bounded stages after the parent handoff: acquire the exact font with notices/hash -> host-only bounded shape/measure/mask adapter -> SDK 3 capability/import/op integration -> persistent GPU glyph bins -> a small real label screen. Do not expand this into paragraph editing, IME, font browsing, arbitrary user fonts or a new widget framework.

Required evidence at those stages:

- Freeze the completed parent baseline; regression-check legacy op 3 lowercase, unknown Unicode, `6*scale` spacing, clipping, alpha and byte/scale limits; test SDK 2 rejection of op 5 and mixed op ordering.
- Validate real font tables and required cmap; render `MingOS 中文，标点。AV fi é Á` at 12/16/24/48 px with spaces, descenders, combining marks and negative bearings. Verify measure/draw agreement, .notdef reporting, clip edges and CPU/GPU mask parity; retain reference captures.
- Exercise invalid UTF-8, oversized/expanding shaping input, stale/cross-app handles, close while in flight, budget boundaries, tile overflow, atlas eviction, device failure and missing capability. Rejected frames must leave successful pixels intact.
- Prove counters: repeated unchanged frame -> zero glyph-mask upload bytes and zero unchanged-image uploads; integer movement/color change -> no mask upload; cold misses -> only dirty ranges. Verify serialized device/session identity and direct native-buffer present remain intact.
- Measure cold font parsing, shape/raster latency, CPU private allocations/RSS, GPU cache occupancy, worst-case glyph work and target Linux/Windows builds. Fontdue CJK eager cost and cosmic/swash CFF/hinting quality are unbenchmarked; static primary API support is not runtime qualification.

This task fetched only primary text/API/license pages and GitHub JSON metadata: no font binary, dependency installation, build/test, Docker action, SDK/app artifact or Cargo.lock edit. No child agents. No Git metadata was present in this workspace, so a Git diff could not independently audit concurrent parent edits; this task's only filesystem write is this new document.

## Primary-source ledger (accessed 2026-10-24)

- [F1] Fixed tag identity: https://api.github.com/repos/notofonts/noto-cjk/git/ref/tags/Sans2.004
- [F2] Recommended font metadata only: https://api.github.com/repos/notofonts/noto-cjk/contents/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf?ref=Sans2.004
- [F3] Full SC-oriented CJK font metadata only: https://api.github.com/repos/notofonts/noto-cjk/contents/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf?ref=Sans2.004
- [F4] Deployment formats: https://github.com/notofonts/noto-cjk/blob/523d033d6cb47f4a80c58a35753646f5c3608a78/Sans/README.md
- [F5] Exact release license: https://raw.githubusercontent.com/notofonts/noto-cjk/523d033d6cb47f4a80c58a35753646f5c3608a78/LICENSE
- [R1] Fontdue indexed metrics/masks: https://docs.rs/fontdue/0.9.5/fontdue/struct.Font.html ; https://docs.rs/fontdue/0.9.5/fontdue/struct.Metrics.html
- [R2] Fontdue eager outline preparation: https://docs.rs/fontdue/0.9.5/src/fontdue/font.rs.html
- [R3] Parser outline support: https://docs.rs/ttf-parser/0.25.1/ttf_parser/struct.Face.html#method.outline_glyph
- [R4] Rustybuzz shaping/positions: https://docs.rs/rustybuzz/0.20.1/rustybuzz/ ; https://docs.rs/rustybuzz/0.20.1/rustybuzz/struct.UnicodeBuffer.html ; https://docs.rs/rustybuzz/0.20.1/rustybuzz/struct.GlyphPosition.html
- [R5] Cosmic version, engine, manifest/features: https://docs.rs/cosmic-text/0.19.0/cosmic_text/ ; https://docs.rs/crate/cosmic-text/0.19.0/source/Cargo.toml ; https://docs.rs/crate/cosmic-text/0.19.0/features
- [R6] Published crate licensing/notices: https://docs.rs/crate/fontdue/0.9.5 ; https://docs.rs/crate/rustybuzz/0.20.1 ; https://docs.rs/crate/cosmic-text/0.19.0
- [R7] Ownership/load behavior: https://docs.rs/cosmic-text/0.19.0/src/cosmic_text/font/system.rs.html ; https://docs.rs/fontdb/0.23.0/fontdb/struct.Database.html#method.load_font_data
- [R8] Layout/placement contracts: https://docs.rs/cosmic-text/0.19.0/cosmic_text/struct.Buffer.html ; https://docs.rs/cosmic-text/0.19.0/cosmic_text/struct.LayoutRun.html ; https://docs.rs/cosmic-text/0.19.0/src/cosmic_text/layout.rs.html ; https://docs.rs/cosmic-text/0.19.0/src/cosmic_text/buffer.rs.html
- [R9] Mask caching/raster source: https://docs.rs/cosmic-text/0.19.0/cosmic_text/struct.SwashCache.html ; https://docs.rs/cosmic-text/0.19.0/src/cosmic_text/swash.rs.html
- [R10] Face/line metrics: https://docs.rs/cosmic-text/0.19.0/cosmic_text/struct.Font.html ; https://docs.rs/swash/0.2.10/swash/struct.FontRef.html ; https://docs.rs/swash/0.2.10/swash/struct.Metrics.html ; https://docs.rs/swash/0.2.10/src/swash/metrics.rs.html
