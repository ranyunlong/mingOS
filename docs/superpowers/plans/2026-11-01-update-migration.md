# Update migration implementation plan

> 目标：实现 `docs/superpowers/specs/2026-11-01-update-migration-design.md`，并形成独立 Linux/QEMU 证据。

## 1. 固定包模型和信任边界

- 修改 `crates/ming-app-host/src/package.rs`：加入 `DataMigration`、`MigrationStep`，限制 serde 字段、步骤数和路径字段；保持旧 manifest 可解析。
- 修改 `crates/ming-app-host/src/publisher.rs`：把 canonical migration bytes 纳入签名消息。
- 修改 storage fingerprint 计算，加入规范化 migration 数据。
- 在 `tests/package.rs`、`tests/package_install.rs` 增加解析、指纹变化和签名覆盖测试。

## 2. 增加私有数据 staging 事务

- 修改 `crates/ming-app-host/src/app_data.rs`：在同一 `PackageStorageAuthority` 锁下检查无活动 private session；实现 no-follow staging clone、声明式步骤执行、目录/文件配额校验和 backup/staging rename。
- 只操作目标包的 `files` 目录；公共目录和其他 package ID 必须在 API 层不可达。
- 为每个阶段返回可写 journal 所需的旧/新路径和同步回执；失败清理临时目录并保持旧目录可读。
- 增加 `tests/app_data.rs` 的成功、边界、并发和失败原子性覆盖。

## 3. 接入 PackageInstallRegistry journal

- 修改 `crates/ming-app-host/src/package_install.rs`：增加 versioned journal 格式、启动时恢复、更新事务入口和旧版本回滚。
- `install_from_manifest` 保持现有安装行为；增加 `update_from_manifest`，只对 active 更高版本使用 migration plan。
- 先写新包 content，再执行 data staging，再原子发布 record；每一步使用现有私有文件、目录同步和锁策略。
- 对无 migration plan 的兼容更新执行数据不变事务并记录明确的 `no-migration` 状态。
- 增加安装登记测试：journal 各阶段重开、失败恢复、旧内容保留、升级/降级/卸载交互。

## 4. 接入桌面启动确认

- 修改 `crates/ming-app-host/src/ming_desktop.rs` 和必要的 `desktop_catalog.rs`：更新事务完成后标记待确认版本；只在匹配 generation、fingerprint、`inputReady`、正常 result 和 worker reap 后提交 startup confirmation。
- 启动失败或超时触发 registry recovery，再重新加载旧 active package；不改变现有关闭和 QMP 输入语义。
- 增加桌面单测覆盖确认前后状态以及恢复时 sibling app 存活。

## 5. Linux 运行证据

- 使用固定 `mingos-linux-task3:aliyun-20261031` 和 `mingos-boot-infra:20261025-g2`，不修改历史 artifact。
- 运行 Rust all-targets、Linux private-file migration fixture、两次 fresh QEMU cold boot；验证旧数据、新布局、journal 清理、counter 持久性、公共目录不变和 kernel PowerDown。
- 新建 `artifacts/linux-current-20261101/update-migration-v1/independent-audit.json`，并更新 `artifacts/overall-gate-audit-20261101.json`：只有独立审计通过才把 update migration 从 `unimplemented` 改为 scope 内通过；`finalSystemAccepted` 继续保持 `false`。

## 验证顺序

1. 包模型和单元测试 RED/GREEN。
2. app_data staging/rollback 单元和 Linux focused tests。
3. registry journal/reopen tests。
4. desktop startup confirmation tests。
5. 固定 Docker release/all-targets。
6. QEMU cold pair 和独立 audit。
7. 更新总体 gate 状态与进度记录。
