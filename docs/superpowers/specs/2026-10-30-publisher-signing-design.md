# 发布者签名与安装信任根设计

日期：2026-10-30。

## 目标

把现有父侧安装登记从“登记过的本地内容”提升为可验证的发布者身份：安装和桌面启动在签名模式下必须验证包的 Ed25519 签名、公钥 `keyId`、版本、manifest 身份字段和完整 Wasm 内容。

## 信任边界

- 安装根仍由可信父进程预先创建，要求绝对路径、当前拥有者和仅拥有者权限。
- `trusted-publishers-v1` 是安装根中的父侧 keyring。它只保存 `keyId`、`active|revoked` 状态和 32 字节 Ed25519 公钥；文件以临时文件、`sync_all`、原子 rename 和目录同步写入。
- keyring 的轮换和撤销在下一次打开登记根时生效。运行中的桌面不动态重新加载 keyring，避免信任状态在一次启动内改变。
- 签名验证不把包自报的 `publisherKeyId` 当成信任；必须在 keyring 中找到同名且 active 的公钥。
- 内容指纹仍用于安装代和数据授权绑定；签名证明发布者授权，不能替代父侧登记、用户同意或多用户隔离。

## 包格式与签名消息

manifest 增加可选字段：

```json
{
  "publisherKeyId": "release-2026",
  "publisherSignature": "base64-ed25519-signature"
}
```

两个字段必须同时出现；旧 manifest 没有字段时仍可由开发模式加载，但签名模式拒绝无签名包。签名覆盖固定二进制消息，而不是 JSON 文本：

```text
ming-package-signature-v1\0
keyId length:u64be, keyId bytes
schemaVersion:u32be
id length:u64be, id bytes
version length:u64be, version bytes
sdkVersion:u32be
entry length:u64be, entry bytes
privateStorage:u8
publicStorage:u8
wasm length:u64be, wasm bytes
```

这样 JSON 字段顺序、空白和可选字段序列化不会改变签名语义。签名和 key ID 不纳入内容指纹本身；key ID 纳入签名消息，使同一内容不能被另一 key 的签名重新解释为同一发布身份。

## API 与接入

- `PublisherTrustStore` 负责加载、原子更新、轮换和撤销 keyring，并提供对 `Package` 的签名验证。
- `PackageInstallRegistry::open()` 保留当前开发兼容行为；新增 `open_verified()`，要求存在合法 keyring，并在 `install_package`、`install_from_manifest`、`verify_package` 和活动内容解析时验证签名。
- `--install-root` 的桌面生产路径使用 `open_verified()`；未配置安装根的 schema v1/v2 开发路径不改变。
- 安装记录仍只绑定 `packageId + version + SHA-256`，因此签名状态变化不会破坏现有数据目录；撤销 key 后下一次桌面启动拒绝该发布者的包。

## 错误与测试

- 缺少一半签名字段、非法 key ID、错误 base64/长度、未知 key、已撤销 key、内容或 manifest 任一字段被篡改，都必须在安装或启动前失败。
- 测试覆盖签名消息的字段边界、有效签名、篡改拒绝、key rotation/revocation、keyring 损坏和开发模式兼容。
- 固定 Rust/Docker 离线验证需要锁定 `ed25519-dalek` 及其传递依赖；不能使用自定义或伪造的哈希签名替代 Ed25519。

## 非目标

本阶段不实现远程仓库、证书链、硬件密钥、多用户身份、安装 UI、更新迁移或断电设备验收。这些仍是后续独立阶段。
