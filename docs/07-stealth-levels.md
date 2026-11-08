# 07 隐匿等级

## 1. 定位与边界

隐匿等级控制**采集端（抓取公开列表时）**的客户端请求画像与节奏，目的是降低自动化爬虫特征与被限流概率，属于「低指纹采集」的工程实践。它**不提供匿名性**，不抵御主动指纹识别，也不影响链内流量加密。链内安全依赖各协议自带加密；绝对匿名不在本项目承诺范围内（见 [08](08-security-and-vault.md) / [14](14-compliance.md)）。

全部参数集中在 `anonchains/stealth/levels.py` 的 `LEVELS` 策略表，CLI 一个数字即可切换并写入 `crawler.stealth_level`，无需散落的 `if level == ...` 分支。

## 2. 参数对照

| 参数 \ 等级 | 1 Low | 2 Balanced（默认） | 3 Cautious | 4 Maximum |
| --- | --- | --- | --- | --- |
| `ua_pool_size` | 1 | 12 | 40 | 80 |
| `shuffle_header_order` | 否 | 是 | 是 | 是 |
| `rotate_accept_language` | 否 | 是 | 是 | 是 |
| `synthetic_referer` | 否 | 是 | 是 | 是 |
| `min_delay_ms` ~ `max_delay_ms` | 0 ~ 0 | 80 ~ 400 | 400 ~ 1600 | 1200 ~ 5000 |
| `concurrency_cap` | 64 | 32 | 8 | 3 |
| `retries` | 1 | 2 | 3 | 3 |
| `session_rotation_requests` | 0（不轮换） | 200 | 40 | 8 |
| `tls_impersonation` | 否 | 否 | 否 | 是（需 `curl_cffi`） |
| `avoid_dns_prefetch` | 否 | 是 | 是 | 是 |

`effective_concurrency(configured, policy) = max(1, min(configured, concurrency_cap))`：即使配置里写了很大的 `crawler.concurrency`，高隐匿等级也会强制压低并发。

## 3. 各级行为与适用场景

| 等级 | 名称 | 行为 | 适用 |
| --- | --- | --- | --- |
| 1 | Low | 单一固定 UA、无抖动、最大吞吐 | 本地 / 离线测试、自己维护的源 |
| 2 | Balanced | 加权 UA 池、一致性请求头、轻微抖动与限速 | 默认；常规公开列表采集 |
| 3 | Cautious | 大 UA 池、低并发、明显时序抖动、频繁重建连接 | 对限流敏感的源 |
| 4 | Maximum | 最低并发、最长抖动、TLS 指纹伪装、每 8 个请求换连接 | 需要尽量降低指纹的场景；吞吐很低 |

等级 4 缺少 `curl_cffi` 时静默降级为标准 `aiohttp` TLS 栈，并在会话层记录一次告警；其余行为（并发、抖动、UA、连接轮换）仍然生效。

## 4. 请求画像生成（`stealth/fingerprint.py` / `ua_pool.py`）

`FingerprintEngine.build()` 每次请求生成 `RequestProfile(headers, delay_seconds, impersonate, user_agent)`：

- `random_profile()` 从按真实浏览器版本分布加权的 UA 池中抽取一个**浏览器画像**（family / version / UA）；UA 池覆盖 **Chrome / Edge / Firefox / Safari / Opera** 五个家族 × Windows、macOS、Linux、Android、iOS 平台共 18 条模板，权重近似真实市场份额（Chrome ≈ 65%），等级 2 的 12 条子池也已覆盖全部五个家族；
- `consistent_headers()` 保证 `User-Agent`、`Accept`、`Accept-Encoding`、`Sec-Fetch-*`、`Accept-Language` 等与该画像**一致**：Chromium 家族（Chrome / Edge / Opera）会附带 `Sec-CH-UA*`，其中 `Sec-CH-UA-Platform` 与 `Sec-CH-UA-Mobile` **从 UA 反向推导**（Mac UA 配 `macOS`、Android UA 配 `?1`），Opera 的 Chromium 版本取自 UA 中的 `Chrome/<major>` 而非 Opera 自身版本；Firefox / Safari 不发送 client hints，避免「UA 是 Chrome、头是 curl」这类矛盾；
- `shuffle_header_order=True` 时打乱头部顺序，但保留 `User-Agent` 在首位（部分站点会拒绝 UA 被埋在后面的请求）；
- `synthetic_referer` 生成与画像一致的 Referer；`rotate_accept_language` 关闭时固定 `en-US,en;q=0.9`；
- `delay_seconds` 从策略区间均匀采样，`pause()` 在请求前 `asyncio.sleep()`；
- `should_rotate_session()` 按 `session_rotation_requests` 触发共享会话重建；
- 等级 4 且 `curl_cffi` 可用时，`impersonate` 取 `chrome131` 或 `firefox133`，由 `FetchSession` 用对应 TLS 指纹会话发起请求。

`dns_hardening_hints()` 给出运营建议（不要本地预解析目标域名、优先使用 HTTP/HTTPS 前置让 DNS 在代理侧解析、禁用直连 DNS 回退），实际执行由 `fetcher/session.py` 的代理装配与 `validator` 的出口 IP 一致性检查承担。

## 5. 与抓取器的协作

`FetchSession` 在一次抓取中共享连接池，并叠加：

- 全局令牌桶（`crawler.rate_limit_per_second`）+ 源级抖动 + 指数退避重试；
- robots.txt 判定（`respect_robots`）与 `ETag` / `Last-Modified` 增量抓取；
- 可用池前置代理反馈：有高分原生 HTTP/HTTPS 节点时把请求经其转发，冷启动直连；排除与源站同主机的节点；
- aiohttp 原生只支持 HTTP 代理，SOCKS 与特殊协议不作为采集前置（它们由级联 / 外部核心承载）。

## 6. 风险与权衡

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 吞吐下降 | 等级 3/4 并发上限 8/3，抖动最高 5s | 只对需要的源启用；大源用等级 1/2 |
| 指纹伪装不完整 | 伪装 UA/头/TLS 不等于消除浏览器/网络层特征 | 仅用于合法采集；不要依赖它做身份保护 |
| 前置代理不可信 | 免费节点可能记录或篡改采集请求 | 只采公开列表、不传凭证；出口 IP 复检 |
| DNS 泄漏 | 本地解析目标域名会暴露访问意图 | 优先 HTTP/HTTPS 前置；核对 DNS/出口一致性 |
| 过度请求 | 高并发可能触发源站限流或违反 ToS | 限速 + 遵守 robots.txt + 合理重试，必要时停用该源 |

## 7. 选择建议

- 默认使用 **2**；遇到 429 / 403 时升到 **3** 而不是盲目重试。
- 只在明确需要、且已安装 `curl_cffi` 时使用 **4**；它不会让免费节点更可信。
- 本地调试、离线测试用 **1**，可显著加快抓取。
- 修改等级：`anonchains stealth <1..4>` 或主菜单 `6 -> <1..4>`；REST 也可通过白名单项 `crawler.stealth_level` 修改。

## 8. 新增隐匿参数

1. `stealth/levels.py` 的 `StealthPolicy` 增字段，并为 4 个等级全部赋值；
2. `stealth/fingerprint.py` 或 `fetcher/session.py` 消费该字段（不要在其它模块写 `if level ==`）；
3. 补 `tests/test_stealth.py` 离线用例（注意复用同一 `FingerprintEngine` 实例以免随机序列重置）；
4. 更新本文参数表。
