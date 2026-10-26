# Pointer-ready transport API v1 — frozen handoff

This is Boot-owned instrumentation for the reviewed data-text profile. It does not modify Rust, Kotlin, SDK or worker budgets. Original logical input JSON, coordinates and keys remain SHA-bound by review.

## Protocol

1. Require current validated boot/session, actual desktop process PID, target app inputReady and presented root/source.
2. Send the original ABS(x,y) in a separate QMP transaction and record the current desktop stderr byte offset.
3. Await a structured receipt from the current MingHost client: same boot/session/DesktopPID, a newer logOffset, current pointer object and Root surface, coordinates within one logical pixel. Enter or motion followed by its frame is accepted. Leave/release clears readiness. Waiting is bounded to 5 seconds; no fixed sleep or replay.
4. Send original button-down and button-up as separate QMP transactions. Wait for original source/root/phase expectations. Keyboard events preserve order in separate transactions.
5. POWER uses motion on the same Root surface; it does not require another enter.

Guest forwards metadata only; raw Wayland stderr remains a separate complete byte stream below its hard 8MiB capture limit. Limit overflow is explicit and unaccepted. Pointer parser limits both incomplete and complete lines to 65536 bytes. Passive evdev observation is limited to the task guest, Virtio keyboard/tablet, nonblocking reads, no grab, 4096 events and 900 seconds.

## Entry points

Builder flags: --input-diagnostics --wayland-client-diagnostics --pointer-handshake-diagnostics.

VM flags: --diagnostic-split-input --diagnostic-pointer-handshake. A pointer-ready manifest cannot silently use the old transport. cold-boot.py propagates these exact flags for a pointer-ready bundle.

POINTER_SYNC_POLICY is defined in wayland_receipt.py and main's conditional review v4 copies that exact dictionary. Instrumented qualification additionally requires reviewed instrumentedProfile=data-text-pointer-ready-v1, instrumentedQualification=true and an exact bounded instrumentation dictionary. Short diagnostics have a 15-second capture bound after actual ready; approved qualification disables only this diagnostic capture window. Existing Wasm/CPU/memory/pids/fuel/watchdog bounds and the outer VM timeout remain.

## Actual evidence and qualification boundary

v6 established that initial client pointer capability/resource creation happened only after ABS and that the client received release without press despite guest evdev recording both. v7's pointer receipt synchronization delivered press/release at the original point, advanced Name source, displayed mingos, delivered POWER, exited the desktop code0 and completed all serial strict probes plus sync/unmount/kernel Power down.

v7 is NOT overall GREEN: both final app records contain APP_CANCELLED and null packageId/events/frameClock. closed/completed=true and cgroup cleanup do not prove natural Kotlin finally/dispose or exact SDK text hashes. Main owns the production Close grace/receipt fix. Conditional review-v4 is not executable until its prerequisites are met. No formal pair has been started.

The source frozen after v7 adds only the complete-line parser bound fix relative to its embedded version; current Windows/Linux file tests are 38/38. The incomplete formal-outcome guard RED test is preserved under artifacts as pending work and is not part of this frozen green source. No formal-outcome guard is claimed implemented.
