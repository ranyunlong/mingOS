# Linux Kotlin 来源准备镜像

此镜像是新的开发工具链环境，不代表 Kotlin 重编、Task9 或 MingOS 最终验收已通过。正式编译必须在本代封存依赖后禁网执行并保存真实日志。

构建命令：

```powershell
docker build --pull=false --progress=plain -f scripts/linux/Dockerfile.kotlin -t mingos-kotlin-win-20261107 scripts/linux
```

基础是实际从 Docker Hub 拉取并 inspect 的官方 `node:24.22.1-trixie-slim`，Dockerfile 固定 registry digest，Node 保持 24.22.1。当前仅支持 amd64。JDK/JRE 固定 Debian security 包 `21.0.13+9-1~deb13u2`，`ca-certificates` 固定 `20250419+deb13u1`；其余实际解析包版本记录在镜像 `/usr/local/share/mingos-toolchain/installed-packages.tsv`。同目录保存 HTTPS apt sources、签名索引 hash、全部下载 deb SHA-256 和初始 CA hash。

官方 Debian 索引在当前网络下载停滞，准备阶段使用 Aliyun 的 Debian 镜像。最小 Node base 没有系统 CA bundle，故先从该固定官方 Node 可执行文件导出内置 Mozilla 信任根，并显式配置 apt 的 CA 路径，再以 HTTPS 下载 Debian 签名索引。TLS、InRelease 签名和 apt 包 SHA 校验保持开启；随后安装正式 Debian CA 包。未保留 deb 实体或完整索引，因此当前是有来源记录的准备镜像，尚不是离线重建 apt 包闭包；若需离线重建须另行封存下载包和索引。

镜像默认 `USER 65534:65534`、`JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64`、`GRADLE_USER_HOME=/deps/gradle`。`/workspace` 和 `/deps/gradle` 本身为 root 所有，不作为可写构建目录；运行时需要挂载已做 owner/mode 准入的本代源码、依赖和输出目录。正式容器使用禁网、只读 root/source、cap-drop ALL、no-new-privileges，配置受控 tmpfs 和可写输出；仅版本 probe 不证明编译准入。

Gradle 不下载进镜像。必须使用仓库 `gradlew` 和 `gradle/wrapper/gradle-wrapper.properties` 指定的 8.14.5，固定分发 SHA-256：`6f74b601422d6d6fc4e1f9a1ab6522f642c2fdcbc15ae33ebd30ba3d7198e854`。联网准备 Gradle/Maven/Kotlin/Wasm 依赖后保存完整来源清单并封存；正式编译使用 `--offline --rerun-tasks --no-build-cache`，不得用预热成功代替禁网真实重编证明。

本轮 build、inspect、UID65534 禁网只读 Node/JDK probe 与 hash 证据位于 `artifacts/environment-rebuild-20261107/kotlin-linux-toolchain/`。Gradle Linux wrapper probe 和实际 Kotlin 编译由后续依赖准备阶段完成，不属于本镜像的版本 probe。