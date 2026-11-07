# VM 复建输入归档

当前公开工具分别处理源码和 Cargo/Gradle 缓存。固定 Rust、Tools、Kotlin 镜像由 `development_image_migration.py` 验证。三类输入都须保留包外取得的精确 SHA256；归档完整性校验不认证发布者，也不代表编译或 VM 已运行。

## Cargo 与 Gradle

从已有缓存导出时，仅只读挂载原 Cargo registry 和完整 Gradle home。输出目录必须不存在，父目录必须已存在：

```sh
python -I -B scripts/boot/developer_cache.py export \
  --cargo-volume <含cargo/registry的现有卷> \
  --gradle-volume <完整Gradle-home卷> \
  --baseline <完整Gradle文件基线.json> --output <全新证据目录>
```

`exported/` 包含 `developer-inputs.json`、`dependencies.tar` 和原始 `gradle-baseline.json`。另行记录 descriptor 的 SHA256，保持这三个文件不变，再显式恢复：

```sh
python -I -B scripts/boot/developer_cache.py restore \
  --inputs <exported目录> --descriptor-sha256 <包外精确SHA256> \
  --output <另一个全新证据目录>
```

恢复工具先核对整个严格 USTAR，再使用原 qualification worker 恢复到新的匿名卷，独立只读复核，投影到两个新消费者卷后再次只读复核。结果中的 `cargoVolume` 含 `cargo/registry/`；`gradleVolume` 的根就是完整 Gradle home，可交给 `kotlin_delivery.py --original-cache`。保留匿名卷、消费者卷、所有容器及每步 argv/日志。

原缓存不写入。Cargo 的 7 个原 0640 常规文件在归档/新副本中规范化为 0644，descriptor 明确记录每项转换；原始清单和模式仍被绑定。Gradle 完整文件/空目录保持，基线支持现有六字段格式。新 Cargo home 不携带旧全局锁或派生数据库。工具固定现有 Rust helper 镜像，禁网、只读根、UID/GID65534；初始化新卷时仅增加 CHOWN。工具不下载、加载镜像、编译、启动 guest 或删除资源。

## 源码

`source_archive.py` 是 Linux 数据层入口，要求 UID/GID65534、可信 helper 安装于 `/repo/scripts/boot`。源码必须只读挂载，冷构建 freeze 必须由包外摘要固定。以下命令在已配置的受控 Linux 容器中执行，不单独设置 Docker 隔离：

```sh
python3 -I -B /repo/scripts/boot/source_archive.py export \
  --source /source/repo --freeze /cold/freeze.json \
  --freeze-sha256 <精确冷构建freeze摘要> \
  --archive /out/source.tar --descriptor /out/source-descriptor.json
python3 -I -B /repo/scripts/boot/source_archive.py restore \
  --archive /incoming/source.tar --descriptor /incoming/source-descriptor.json \
  --descriptor-sha256 <包外精确descriptor摘要> \
  --destination /consumer/repo
```

归档用短别名存储文件，完整原路径、目录、模式、owner 和文件摘要保存在严格 JSON 映射中；长源码路径不需要 PAX。导出清单必须等于 freeze 的完整源码清单，排除规则沿用 qualification 的生成目录集合。恢复到不存在的目录，保留模式，owner 明确规范化为 65534；完整消费端扫描也拒绝额外生成目录、链接和特殊文件。部分失败输出保持原样。数据层不负责宿主 Docker 编排或磁盘空间准入。

恢复后应在另一容器只读挂载消费者卷，重新核对全部文件、目录、模式和 owner，再运行构建。Windows 宿主副本另核全部字节/目录；它不保留 Linux 模式。公开 Kotlin 入口当前只接受宿主 `--repo`，使用较短的新路径可避免 Windows MAX_PATH。不要修复或覆盖原始长路径副本。

## 2026-11-07 实际证据

- 缓存导出 `developer-cache-actual-export-290da81f`：descriptor `5ee32079347e5bc107bc3196ed22ac8bd33d224f55c465e316a225fa5790be9d`；归档 936744960 字节，SHA256 `77b59c4e3a4aa96485fabf2c009ba7e7e6e05aadfd37d8f68cb22c863c5d84c6`。
- 新恢复 `developer-cache-actual-restore-70a5d3e1`：42 步实际操作全部成功；Cargo 12495 文件、Gradle 2496 文件，两层独立只读复核通过。结果 SHA256 `dcb16934a97d6717eb9a4f937cd47425f7d15218dfaae77834fef111da4aa6ac`。
- 源码导出 `source-archive-actual-export-edfb0b2d61f6`：descriptor `8bb9ff1a0f3bfcd052997ff550cc8b1ed5cdc03bd6dc64d322c0bed400f79e43`；归档 30914560 字节，SHA256 `cb2c58555bbbe5ac645c214ac9a29100ae6a329150e26d9f4c1b5e7bacf19144`。
- 新恢复 `source-archive-actual-restore-9f97d47ab8f7`：1487 文件、392 目录，全量 Linux 元数据及独立只读重开通过；主执行者复核长/短 Windows 副本全部字节和目录。短副本为 `artifacts/sr-9f97d47a`。编译和运行状态另见 [系统状态](current-system-status.md)。
- 恢复输入的 Kotlin 开发双包 `kr-83b7a1e2` 已实际强制离线重新编译；只读容器及主执行者宿主回放通过，46 文件/7 目录/13745862 字节完整匹配。宿主封印 SHA256 `90e463b188474c460700c6f8a570d269bebb4c455c0d81f7206888660b9b24e9`；当前公共包验证器和编排链重新执行通过。这不单独证明 guest 或最终交付。

上述证据位于 `artifacts/environment-rebuild-20261107/`。公共集成 Linux 控制共 52 项，51 通过，1 项仅适用于 Windows 的控制跳过。源码集成控制需要显式提供只读合成夹具 `MINGOS_SOURCE_TEST_FIXTURE`；未提供时明确跳过。这些控制不代替真实归档恢复或实际构建。
