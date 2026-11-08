# 05 代理源与解析

## 1. 覆盖策略（如实说明）

「采集互联网上所有可能的源」在工程上落地为三层，而不是宣称覆盖全网：

1. **内置实测公开源注册表**：`config/sources.yaml`，29 个公开免费、无需认证的列表端点；
2. **用户自定义源**：`config/sources.local.yaml`，同名覆盖内置条目；
3. **可选仓库检索发现**：`crawler.discovery_enabled = true` 时通过 GitHub 搜索 API 与仓库目录发现候选新源，结果去重后仅展示、**不自动启用**。

所有源均为公开免费列表，采集时遵守 robots.txt、目标站 ToS 与速率限制；不含付费/认证绕过、私有列表或未授权接口。

## 2. 内置源清单（29 个，24 个默认启用）

### gfpcom/free-proxy-list（16 个，12 个启用）

Wiki Raw 列表，每小时更新，覆盖主流与新兴协议。

| 名称 | 类型 | 协议 hint | 默认 |
| --- | --- | --- | --- |
| `gfpcom-vless` / `gfpcom-vmess` / `gfpcom-trojan` / `gfpcom-ss` / `gfpcom-ssr` | subscription | 对应协议 | 启用 |
| `gfpcom-hy2` / `gfpcom-hy` | subscription | `hysteria2` | 启用 |
| `gfpcom-tuic` | subscription | `tuic` | 启用 |
| `gfpcom-wireguard` | subscription | `wireguard` | 启用 |
| `gfpcom-anytls` | subscription | `anytls` | 启用 |
| `gfpcom-socks` / `gfpcom-less` | plain / subscription | `socks5` / 自动 | 启用 |
| `gfpcom-http` / `gfpcom-https` / `gfpcom-socks4` / `gfpcom-socks5` | plain | 对应协议 | **禁用**（各约 50 万行，payload 过大，按需开启） |

### 经典 Raw 列表（8 个，全部启用）

`thespeedx-http` / `thespeedx-socks4` / `thespeedx-socks5`（TheSpeedX/PROXY-List）、`roosterkid-https`（openproxylist）、`zaeem20-http` / `zaeem20-https` / `zaeem20-socks4` / `zaeem20-socks5`（FREE_PROXIES_LIST）。

### 元数据丰富 JSON / API（5 个，4 个启用）

| 名称 | 类型 | 说明 |
| --- | --- | --- |
| `monosans-json` | json | 每小时复检，含延迟 / 出口 IP / ASN / 地理位置 |
| `proxyscrape-http` | plain | ProxyScrape 公开 API（文本） |
| `proxyscrape-socks5` | json | ProxyScrape 公开 API（JSON） |
| `geonode-api` | json | 含 `anonymityLevel` / `upTime` / 国家 |
| `openproxylist-v2ray` | subscription | 可选 V2Ray 订阅包，默认禁用，启用前先确认可用性 |

可通过 `python -m anonchains sources` 或主菜单 `1 -> 3` 查看实时清单与启停状态；`1 -> 4` 可在本次运行中启停单个源。

## 3. 源字段与格式

```yaml
- name: my-source
  url: https://example.com/list.txt
  kind: plain            # plain | json | yaml | subscription | api
  protocol_hint: socks5  # 可选；纯文本无 scheme 时作为协议判定依据
  enabled: true
  weight: 3              # 权重，用于统计与优先级
  note: 备注
  headers:               # 可选请求头（会与隐匿画像合并）
    User-Agent: my-client/1.0
```

`kind` 只作为**提示**：真正分派由 `parser/detector.py` 的 `sniff_kind()` / `parse_payload()` 按内容嗅探决定，避免源站返回格式变化导致整批失败。

| 解析器 | 处理内容 |
| --- | --- |
| `plain.py` | `ip:port`、带协议前缀文本、带认证行、`user:pass@host:port`；过滤私有/保留地址 |
| `json_parser.py` | 数组 / 嵌套结构 / 公开 API 响应（`ip`/`port`/`protocol`/`anonymityLevel`/`country`/`latency` 等字段），无效 JSON 显式抛 `ParseError` |
| `clash_yaml.py` | Clash `proxies:` 列表，映射 `server`/`port`/`type`/`uuid`/`password`/`sni`/`alpn` 等 |
| `subscription.py` | base64 订阅包、SIP002、分享链接 `vless://` `vmess://` `ss://` `ssr://` `trojan://` `hysteria2://` `tuic://` `wireguard://` `anytls://` 等 |
| `detector.py` | 取前 64 KiB 样本嗅探，按注册顺序选择解析器；无匹配时回退纯文本；单源上限 `MAX_NODES_PER_SOURCE = 50_000` |

解析结果统一产出 `ProxyNode`，再按 `stable_key()` 去重。单行错误不会中断整源；整源解析失败抛 `ParseError` 并在抓取汇总中记为 FAIL。

### 私有地址过滤

`plain.py` 使用 `utils.net.is_private_address()` 拒绝私有/保留地址端点，避免把 `127.0.0.1`、RFC1918 等写入池并被用作跳板。解析器新增路径也应复用该判定。

## 4. 自定义源

创建 `config/sources.local.yaml`（已被 `.gitignore` 忽略）：

```yaml
version: 1
sources:
  - name: my-clash-subscription
    url: https://example.org/clash.yaml
    kind: yaml
    enabled: true
    weight: 5
    headers:
      User-Agent: my-client/1.0
  - name: gfpcom-http          # 同名覆盖：把默认禁用的大列表打开
    enabled: true
```

`SourceRegistry.load()` 先读内置清单，再用自定义清单按 `name` 覆盖/追加；`toggle()` 只影响当前进程，`stats()` 给出总数 / 启用数 / 按 kind 与协议的分布。

## 5. 抓取行为

`fetcher/session.py` + `fetcher/crawler.py`：

- 共享 `aiohttp.ClientSession` 连接池（`limit_per_host` / keepalive / DNS 缓存），相同 URL 的并发请求合并为一次下载；单源响应按 `crawler.max_response_mb` 流式截断；隐匿画像（UA、Accept-Language、Referer、头部顺序）由 `FingerprintEngine` 按等级生成；
- 全局令牌桶限速、源级抖动、指数退避重试；`ETag` / `Last-Modified` 增量抓取，避免重复下载大列表；
- `respect_robots = true` 时遵守 robots.txt；被拒绝的结果标记 `BLOCKED` 而不是反复重试；
- 冷启动直连；可用池有高分原生 HTTP/HTTPS 节点后自动选作前置代理，`FrontProxyProvider` 排除同主机以避免自引用；
- `curl_cffi` 存在且等级 4 时提供 TLS 指纹伪装，缺失时静默降级为标准 aiohttp TLS 栈。

抓取结果 `FetchResult` 含 `spec` / `nodes` / `ok` / `blocked` / `elapsed_ms` / `error`；`summarise()` 生成汇总供 CLI、调度器与 REST 使用。

## 6. 可选发现机制

`discovery` 段字段：`enabled`、`max_results`、`search_queries`、`github_repositories`、`raw_candidate_paths`、`file_extensions`。`SourceDiscoverer` 使用 GitHub 搜索 API 找候选仓库，再检查仓库目录中的原始列表路径，产出 `CandidateSource`（名称 / 类型 / URL），去重后**只展示不启用**。未认证 API 限 60 次/小时，默认关闭，调用前 CLI 会二次确认。

## 7. 风险与合规

- 公开免费节点**不可信**：可能记录流量、注入内容、随时下线；绝不传输真实凭据或敏感数据。
- 列表可能含恶意端点或内网地址：解析器做私有地址过滤，但用户仍需自行判断是否启用某源。
- 大列表（`gfpcom-*` 的 50 万行级文件）默认禁用；启用前评估带宽、内存与源站压力。
- 只抓取公开列表，遵守 robots.txt / ToS / 限速；不实现攻击、未授权访问或绕过付费能力。详见 [14](14-compliance.md)。
