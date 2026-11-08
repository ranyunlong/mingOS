# P01 independent WSL environment

The public entry point is `scripts/boot/wsl_environment.py`. It creates a new WSL2 distro, VHD and Docker 29.8.1 store. WSL shares its kernel and supporting infrastructure: this is not an independent kernel, physical host or security VM. S2 readiness does not qualify S3–S6, run a cold build, restore historical assets or accept the final system.

Use Windows Python 3, an explicit WSL executable and explicit outer Docker executable, named-pipe endpoint and private client config directory. The fixed Tools image must already be present in the outer daemon and must match the strictly verified Tools archive's index, platform, Config and RootFS. Missing Tools fails before export/import; there is no automatic pull. The static Docker bundle must have exactly the published input bytes and SHA256 pinned in the controller. `--archive-root` contains all three fixed Rust/Tools/Kotlin image archives produced by the S1 restore; S2 independently verifies them but does not replace S1 restore/reopen.

S6 still requires its host preparation layer: verify the external handoff pin, extract only the exact three approved archive members using held input handles, and use the strict existing loader to prepare fixed Tools when absent. Manually preparing Tools does not close that S6 gate or complete P01.

Run each operation with a new, absolute output directory. The examples use placeholder locations; provide actual executable and input locations. The generation must match `[a-z0-9][a-z0-9-]{0,47}` and distro `MingOS-P01-[A-Za-z0-9-]+`.

```powershell
$python = 'C:\Python313\python.exe'
& $python C:\mingOS\scripts\boot\wsl_environment.py create `
  --generation p01-fresh-20261108 `
  --distro MingOS-P01-fresh-20261108 `
  --output-directory C:\p01-evidence\fresh-20261108 `
  --wsl-executable C:\Windows\System32\wsl.exe `
  --outer-docker-executable 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' `
  --outer-docker-endpoint 'npipe:////./pipe/dockerDesktopLinuxEngine' `
  --outer-docker-config C:\p01-inputs\outer-client `
  --docker-bundle C:\p01-inputs\docker-29.8.1.tgz `
  --archive-root C:\p01-s1-restored\delivery\rebuild-inputs\images
```

The complete create lifecycle holds the original `environment-result.json` file handle throughout preflight, export/import, first-empty observation, helper/archive transfer, strict load, smoke, live observation and outer-resource preservation checks. The intermediate internal `create_empty_environment` API returns `creating/empty-daemon-observed`; it is not a ready environment and cannot be resumed through the public API. Only a complete public create can return `environment-ready`. Every public operation prints JSON; an ordinary failure exits nonzero and records its phase in the claimed output. An already occupied output is rejected and its files are retained.

```powershell
& $python C:\mingOS\scripts\boot\wsl_environment.py observe `
  --environment-receipt C:\p01-evidence\fresh-20261108\environment-result.json `
  --output-directory C:\p01-evidence\observe-20261108

& $python C:\mingOS\scripts\boot\wsl_environment.py exec `
  --environment-receipt C:\p01-evidence\fresh-20261108\environment-result.json `
  --output-directory C:\p01-evidence\exec-20261108 --timeout 30 `
  -- /usr/bin/uname -a
```

`exec` requires an absolute Linux executable and preserves the supplied argument strings. It does not translate Windows paths to `/mnt/c`, run a shell implicitly or call the current Windows-path-dependent cold-boot entry point. Later S1 consumption must explicitly establish guest source paths. Failed, intermediate or unknown receipt states are rejected. Observe checks live daemon and managed-containerd identities, private namespaces, socket, store, loaded image identities/config/rootfs, shared cgroup controls and the retained smoke proof. Observe only reads environment state and creates evidence logs; it does not load images, create containers or volumes, repair the daemon, restart smoke or rerun arbitrary code.

Five frozen modules form the execution closure: `wsl_environment.py`, `wsl_environment_guest.py`, `docker_transport.py`, `development_image_migration.py`, and `qualification_image_archive.py`. Host-pinned hashes and sizes are checked against actual guest bytes immediately before every helper execution. All helper, archive and large request transfers use binary file-descriptor stdin. The bootstrap opens parent directories without following links and creates destinations exclusively. Partial files remain on failure. Python bytecode caches are disabled for the guest closure.

Load enters the admitted daemon mount/net/cgroup namespaces after checking PID start time, executable hash, namespace IDs, daemon ID and socket identity. The unchanged strict loader validates all three archives before any image load and uses the real explicit Docker transport; evidence adds timeout and transmitted stdin hash/size without replacing the FD. Completion requires exactly Rust, Tools and Kotlin in order, including strict image inspections. Partial loads remain visible in the retained load receipt and block smoke.

Smoke creates and retains one fixed Tools container with pull disabled, no network, read-only root, UID/GID 65534, init, two CPUs, 4 GiB memory, 256 PIDs, all capabilities dropped and no-new-privileges. It has no mounts, automatic removal or restart. A frozen Python script runs via interactive binary stdin and observes UID/GID, capability sets, NoNewPrivs, root filesystem flags, cgroup CPU/memory/PID limits, interfaces, IPv4 routes and Desktop/socket visibility. Evidence retains inspect-before, start stdout/stderr and stdin pin, inspect-after and actual-effective JSON. Host validation independently checks these values. Attach exit zero cannot override nonzero container ExitCode, OOMKilled or a running container.

All failed and partial outputs, distro/VHDs, export containers, smoke containers, archives and logs are preserved. The API never deletes, prunes, resets, unregisters, performs global WSL shutdown, selects an existing distro, changes Desktop configuration, or writes shared ancestor cgroup controls. Do not retry a failed generation in place. Use a new generation, distro and output directory after inspecting its retained evidence. Unknown concurrent outer resource additions are reported as inconclusive and block readiness; old immutable resource identities/configurations/mounts/states must be preserved.

Task 5 must still demonstrate the public entry point with a fresh actual generation, complete Linux controls, an independent read-only observation and independent review before S2 can close.
