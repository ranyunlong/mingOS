# Update migration contract

日期：2026-11-01。

## 目标

为已签名的应用更新增加父侧可验证的数据迁移契约。应用包内容、安装登记和应用私有数据必须作为一个可恢复事务更新；迁移失败或新版本启动确认失败时，旧版本和旧私有数据仍可启动。公共目录、其他应用目录和宿主任意路径不属于迁移范围。

## 包格式

`Manifest` 增加可选 `dataMigration` 字段。其结构固定为：

```json
{
  "fromVersion": "1.0.0",
  "steps": [
    { "op": "ensureDir", "path": "state" },
    { "op": "renameFile", "from": "counter", "to": "state/counter" },
    { "op": "copyFile", "from": "state/counter", "to": "state/counter.bak" },
    { "op": "deleteFile", "path": "obsolete" }
  ]
}
```

只允许 `ensureDir`、`copyFile`、`renameFile` 和 `deleteFile`。路径使用已有私有文件相对路径校验；禁止绝对路径、`..`、符号链接、特殊文件、覆盖式 rename、跨应用路径和公共目录。步骤数量、路径长度、文件大小和总配额使用现有 private-file 上限。`fromVersion` 必须精确匹配当前 active 安装版本；没有 `dataMigration` 的更新保留现有私有数据，不声称执行了迁移。

迁移字段参与 storage fingerprint，并参与 publisher signature message。修改迁移计划会改变登记指纹，也会被 `open_verified()` 拒绝，除非重新签名并重新安装。

## 事务和恢复

更新要求目标应用没有运行代，父侧持有安装根锁和数据根权威。登记根新增受限 journal，记录 package ID、旧/新版本与指纹、数据迁移阶段和备份目录。

1. 完整加载并验证新包、发布者签名和 migration plan。
2. 在新版本内容目录写入并同步新包，但不发布 active record。
3. 创建私有数据 staging 副本；只在 staging 上执行声明式步骤并重新校验目录边界和配额。
4. 同步 staging 后，将旧 `files` 目录改名为同目录下的 backup，再原子改名 staging 为 `files`；写入 `data-committed` journal。
5. 发布新 active record 并同步登记文件；写入 `record-committed` journal。
6. 新版本启动并完成一次父侧 `startup-confirmed` 收据后删除 backup 和 journal。

若任何步骤失败，恢复旧 active record、旧 package content 和旧 `files` 目录；恢复完成前不允许启动新版本。进程下次打开时根据 journal 阶段继续完成恢复，不能把残留临时目录当作成功更新。恢复操作本身必须幂等。

## 启动确认

启动确认只由可信父侧在匹配 package ID、generation、fingerprint、`inputReady` 和正常 worker result 后写入。启动超时、协议错误、native exit 异常、worker 未回收或 fingerprint 不匹配都算失败并触发回滚。现有日常运行和关闭契约继续适用。

## 测试和证据

- manifest 解析、fingerprint、签名覆盖和 migration plan 边界。
- 成功更新：旧数据迁移到新布局，重复重试不重复破坏数据。
- 无 migration plan 的兼容更新保留数据。
- source/version 不匹配、路径越界、符号链接、覆盖 rename、配额超限均拒绝且旧状态不变。
- staging、登记发布或启动确认失败时恢复旧版本和旧数据。
- journal 在每个阶段被重新打开后可恢复；公共文件、兄弟应用和历史包内容保持不变。
- Linux 固定 Docker 镜像下运行 Rust 回归、真实父侧私有文件迁移、两次 fresh QEMU cold boot 和独立 audit。证据必须单独写入新 artifact，保留历史 RED，`finalSystemAccepted` 只有所有总体门槛都通过后才可改变。

## 非目标

本阶段不实现远程仓库、证书链、跨用户身份、Windows 安装服务、物理输入或 Linux 硬件 GPU。迁移脚本不允许任意 Wasm/宿主代码执行；需要业务级转换时，应用必须在更新前后使用自己的私有文件 API 完成兼容格式，系统只保证声明式文件事务。
