# 08 安全设计与 Vault

## 1. 加密边界（用户确认的约束）

Vault 只保护**本地静态敏感数据**：节点凭证、原始订阅 / 分享链接、网关 API token、池中的 `sealed` 列。链内传输安全**依赖各协议自带加密**（TLS / 协议内加密），本项目不设计需要公共免费代理运行解密端的方案——免费节点本就不可信，让它们参与自定义解密既不安全也不可行。

Vault 不保护：操作系统内存快照、已解锁进程被调试、屏幕输出、用户自行导出的含凭证配置、链路上节点运营者可见的元数据。

## 2. 密码学组件

| 组件 | 实现 | 说明 |
| --- | --- | --- |
| KDF | Argon2id（`argon2-cffi`）或 `hashlib.scrypt` | `vault.kdf = auto` 时优先 Argon2id；缺失依赖自动降级 scrypt，算法名写入 Vault 头，跨机器可读 |
| AEAD | AES-256-GCM（默认）、ChaCha20-Poly1305 | 来自 `cryptography`；32 字节密钥、12 字节随机 nonce、16 字节 tag |
| 随机源 | `os.urandom` | salt、nonce、DEK、API token |
| 比较 | `constant_time_eq` | token 校验、口令相关比较 |
| 擦除 | `wipe(bytearray)` | 解锁密钥缓存过期 / 锁定 / 轮换时清零 |

KDF 默认参数：`KDFParams(time_cost=3, memory_cost=65536 KiB, parallelism=4, key_len=32)`（Argon2id）；scrypt `n=1<<15, r=8, p=1, dklen=32`。参数写入 Vault 头并做范围校验，防止篡改出危险弱参数。

> 命名说明：`cryptography` 提供的是 RFC 8439 **ChaCha20-Poly1305**（96-bit nonce），不是 XChaCha20 扩展 nonce 变体。代码与文档统一使用 `chacha20-poly1305`，不冒用 XChaCha20 名称。

## 3. 信封格式

### 密文块（`crypto/cipher.py`）

```
b"ANONC1" | algo_id(1) | nonce(12) | ciphertext + tag(16)
```

`header = MAGIC + algo_id + nonce` 作为 AAD 与调用方 AAD 拼接后绑定，攻击者无法替换算法 ID 或 nonce 而不被检测；解密失败（`InvalidTag`）转换为 `AuthenticationFailed`。

### Vault 文件（`crypto/vault.py`）

```
b"ANONVLT1" | header_len(4) | header_json | wrapped_len(4) | wrapped_dek
            | verifier_len(4) | verifier
```

- `header_json` 记录版本、KDF 名称与参数、salt、cipher、创建时间；
- **DEK**（随机 32 字节数据密钥）加密所有池敏感字段与 token；
- **KEK** 由主口令 + salt 经 KDF 派生，只用于封装 DEK；
- `MAGIC + header_json` 作为 AAD 绑定 `wrapped_dek` 与 `verifier`，头部篡改在 `unlock()` 时即被检测；
- `verifier` 是固定明文 `anonchains-vault-verifier-v1` 的密文，用于快速判定口令是否正确。

**信封加密的收益**：`rotate_key(new_passphrase)` 只需用新 KEK 重新封装同一个 DEK，既有池密文无需重加密，轮换是 O(1) 而非 O(池大小)。

## 4. Vault 状态机与降级

| 状态 | 含义 | 写入敏感字段 |
| --- | --- | --- |
| `uninitialised` | 文件不存在 | 不写 |
| `locked` | 文件存在但未解锁 / 密钥 TTL 过期 | 不写 |
| `unlocked` | 已解锁且 TTL 内 | 写入 `sealed` 密文 |
| `disabled` | `vault.enabled = false` | 不写 |
| `unavailable` | `cryptography` 缺失 | 不写 |

`seal_dict()` 在非 `unlocked` 时返回 `None`；`NodeStore._seal()` 把 `sealed` 置 NULL，`ON CONFLICT` 使用 `COALESCE(excluded.sealed, nodes.sealed)`，避免锁定状态下的空值覆盖已有密文。**安全不变量：任何状态下都不会把凭证 / raw 明文写入磁盘。**

`key_ttl_seconds`（默认 900）控制解锁后的 DEK 缓存；`0` 表示直到手动锁定。缓存过期即视为 `locked`，再次写入敏感字段前需重新解锁。所有池查询不依赖解密，因此锁定状态仍可查看端点与统计。

## 5. 敏感数据清单与处理

| 数据 | 保护 |
| --- | --- |
| 节点 `credential` / `raw` | Vault 封印，AAD 绑定 `stable_key()`，写入 `sealed` 列 |
| REST API token | `TokenManager` 用 AAD `b"gateway-token"` 封印到 `pools/.gateway_token`；Vault 不可用时用内存临时 token，**绝不落盘**，重启即变 |
| 主口令 | 仅经 `getpass` / `ANONCHAINS_VAULT_PASSPHRASE` 输入，不写配置、不写日志、不落盘 |
| 外部核心临时配置 | 含凭证，写入临时目录，`CoreManager.stop()` 时删除 |
| 日志 | `RedactingFilter` 脱敏口令、token、代理凭证与 URL 中的用户信息；错误详情截断 300 字符，不 dump 大 payload；高频失败日志采样限流 |
| CLI / REST 输出 | `ProxyNode.to_dict()` 默认不含凭证与 raw；`config-show` / `/api/config` 经 `redact_mapping()` |

## 6. 网关与网络安全

- `gateway.host` 只允许 `127.0.0.1` / `localhost` / `::1`，schema 校验直接拒绝其它值；REST 与本地入口均**仅回环**。
- REST 默认要求 `Authorization: Bearer <token>` 或 `X-Anonchains-Token`，常量时间比较；`token_required = false` 仅限完全可信的单机调试。
- 配置写操作受 `WRITABLE_OPTIONS` 白名单约束，不能通过 API 把网关绑到公网或改 Vault 路径。
- `GatewayContext.spawn()` 登记后台任务，`stop()` 统一取消；`AppContext.shutdown()` 关闭 DB、锁定 Vault。
- 解析器过滤私有 / 保留地址，避免把内网端点写入池并被用作跳板。

## 7. 威胁模型：高匿不等于绝对匿名

**不能防御**：

- 恶意 / 被控制的免费节点运营者记录或篡改流量（尤其是明文 HTTP）；
- 时序关联、流量大小与访问模式关联；
- DNS 泄漏、WebRTC / 浏览器指纹、应用层账号关联；
- 本机被入侵、内存取证、键盘记录、屏幕录制；
- 出口节点看到的目标地址（非 HTTPS 时还包括内容）；
- 源站 / ISP / 国家级观察者的元数据关联。

**能缓解**：

- 采集端的低指纹请求画像与限速（[07](07-stealth-levels.md)）；
- 本地静态凭证被磁盘窃取（Vault）；
- 本地 API 被局域网其它主机访问（回环绑定 + token）；
- 日志与 CLI 输出意外泄露凭证（脱敏）；
- 日志 I/O 风暴（采样限流）。

**使用建议**：绝不通过免费公共代理传输真实凭据、机密或未加密敏感数据；需要强机密性时使用可信的、端到端加密的通道；把多跳链视为「降低单点关联风险的工程手段」，不是匿名保证。

## 8. 运维检查清单

- [ ] 首次运行初始化 Vault（主菜单 `7 -> 2`），口令至少 8 位并离线保管；
- [ ] 确认 `.gitignore` 覆盖 `pools/*.db`、`.vault`、`.gateway_token`、`config/*.local.*` 与 `logs/`；
- [ ] 备份时同时备份两个 `.db`、`.vault` 与口令（分开存放）；
- [ ] 定期检查 `python -m anonchains status` 中 `vault.state` 与 `gateway.token.ephemeral`；
- [ ] 导出核心配置（`4 -> 9`）后及时删除，内含凭证；
- [ ] 轮换口令后立即验证旧密文仍可解密（`7 -> 1` 查看状态 + 池读取）；
- [ ] 不共享 API token；轮换后更新调用方。
