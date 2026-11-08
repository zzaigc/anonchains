# 03 配置手册

配置有两个文件：`config/anonchains.conf`（类 proxychains 的 INI 主配置）与 `config/sources.yaml`（公开源注册表）。主配置是**唯一运行时配置源**；CLI 与 REST 的写操作都通过 `ConfigManager.set_value()` 就地改写该文件并触发热更新。

## 1. 主配置字段

默认值与校验实现位于 `anonchains/config/schema.py`。所有相对路径相对 `--base-dir`（默认项目根目录）解析。

**路径约定（跨平台）**：配置文件只写相对路径（例如 `pools/candidate`、`logs/anonchains.log`），不写盘符或用户目录。程序内部会把它们解析为实际位置用于读写，但 `status`、`config-show`、REST 响应与日志中的路径统一由 `anonchains/utils/paths.py` 渲染为 POSIX 风格的相对路径；项目外的绝对路径只显示文件名，避免泄漏本机目录结构。示例：`pools/candidate/candidate.db`、`config/anonchains.conf`。

### `[chain]`

| 选项 | 默认 | 说明 / 校验 |
| --- | --- | --- |
| `hops` | `5` | 跳数，`>= 1`，**无上限**；>16 时组链记录资源告警 |
| `mode` | `strict` | `strict` / `dynamic` / `random` / `round_robin` |
| `rotation_seconds` | `0` | 自动轮换周期；`0` 关闭，`>= 0` |
| `prefer_protocols` | 空 | 逗号分隔的协议优先列表 |
| `allow_reuse` | `false` | 允许同一节点在链中重复出现（用于超长链） |

`effective_connect_timeout()` = `clamp(validator.timeout_seconds * (1 + 0.15 * (hops - 1)), 1.0, 120.0)`，为超长链提供超时预算保护，但**不构成跳数上限**。

### `[pools]`

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `candidate_dir` | `pools/candidate` | 候选池目录（生成 `candidate.db`） |
| `verified_dir` | `pools/verified` | 可用池目录（生成 `verified.db`） |
| `batch_size` | `500` | 批量写事务分片，`>= 1` |

### `[crawler]`

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `concurrency` | `32` | 抓取并发上限，实际再受隐匿等级 `concurrency_cap` 约束 |
| `timeout_seconds` | `15.0` | 单源超时，`> 0` |
| `retries` | `2` | 失败重试（指数退避 + 抖动） |
| `rate_limit_per_second` | `8.0` | 全局令牌桶速率，`> 0` |
| `max_response_mb` | `8.0` | 单个源响应解压后的硬上限（MiB），`> 0`；超限截断并告警，避免超大列表耗尽带宽/内存 |
| `stealth_level` | `2` | 1..4，CLI `6 -> n` 或 `anonchains stealth n` 写入 |
| `user_sources_file` | `config/sources.local.yaml` | 用户自定义源清单（可不存在） |
| `discovery_enabled` | `false` | 是否启用仓库检索发现（GitHub API 未认证 60 次/小时） |
| `respect_robots` | `true` | 遵守 robots.txt；**保持开启** |

### `[validator]`

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `concurrency` | `64` | 校验并发，`>= 1` |
| `timeout_seconds` | `8.0` | 单次握手/echo 超时，`> 0` |
| `interval_seconds` | `600` | 调度器周期闭环间隔 |
| `ttl_seconds` | `1800` | 复检 TTL，仅到期节点进入批次 |
| `max_consecutive_failures` | `3` | 连续失败达到该值即淘汰，`>= 1` |
| `echo_urls` | ipify / httpbin | 逗号分隔的出口 IP 回显端点 |
| `min_anonymity` | `unknown` | 升级门槛：`unknown` / `transparent` / `anonymous` / `elite` |
| `min_score` | `0.0` | 升级分数门槛 |

### `[gateway]`

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | **总开关**：REST API 与本地代理入口 |
| `host` | `127.0.0.1` | 仅允许 `127.0.0.1` / `localhost` / `::1`，其它值校验失败 |
| `port` | `8790` | REST 端口，1..65535 |
| `entry_port` | `8791` | 本地混合入口端口，须与 `port` 不同 |
| `token_required` | `true` | 关闭后 API 免鉴权（仅限完全可信的单机环境） |

### `[core]` / `[vault]` / `[logging]`

| 段 | 选项 | 默认 | 说明 |
| --- | --- | --- | --- |
| `core` | `enabled` | `true` | 是否允许使用外部核心 |
| `core` | `prefer` | `auto` | `auto` / `sing-box` / `xray` |
| `core` | `sing_box_path` / `xray_path` | `sing-box` / `xray` | 可执行文件名或绝对路径 |
| `core` | `startup_timeout_seconds` | `10.0` | 就绪探测超时 |
| `vault` | `enabled` | `true` | 关闭后敏感字段不落盘 |
| `vault` | `path` | `pools/.vault` | Vault 文件位置 |
| `vault` | `kdf` | `auto` | `auto` / `argon2id` / `scrypt`；显式选 argon2id 但缺依赖会报错 |
| `vault` | `key_ttl_seconds` | `900` | 派生密钥缓存 TTL；`0` 表示直到手动锁定 |
| `vault` | `cipher` | `aes-256-gcm` | 或 `chacha20-poly1305` |
| `logging` | `level` | `INFO` | 日志级别 |
| `logging` | `file` | `logs/anonchains.log` | 日志文件；目录自动创建 |
| `logging` | `redact` | `true` | 启用 `RedactingFilter` |
| `logging` | `sample_window_seconds` | `5.0` | 高频重复日志采样窗口 |

## 2. 环境变量覆盖

格式 `ANONCHAINS_<SECTION>_<OPTION>`，例如：

```powershell
$env:ANONCHAINS_CHAIN_HOPS = "9"
$env:ANONCHAINS_CRAWLER_STEALTH_LEVEL = "3"
python -m anonchains status
```

- 段名与选项名小写匹配；按下划线**首处**切分，因此 `rotation_seconds` 这类带下划线的选项可用。
- 仅覆盖文件中**已存在**的选项（`has_option` 检查），不会凭空新增配置项。
- 覆盖在解析阶段生效，因此 `set_value()` 校验写回时也会带上环境变量，避免落盘一个实际不生效的值。
- 另有 `ANONCHAINS_VAULT_PASSPHRASE`，仅用于 CLI 非交互输入主口令（见 [04](04-cli-manual.md)），不是配置覆盖。

## 3. 热更新与写回

`ConfigManager`：

- `load()` / `reload()` 通过 mtime + 内容摘要检测变更；解析或校验失败时**保留上一份有效配置**并告警，绝不降级到无效状态。
- `watch(stop_event)` 后台轮询（默认 1.5s），变更后通知监听器；`AppContext.apply_config()` 重新装配引擎、校验器、链策略、调度器引用与入口超时。
- `set_value(section, option, value)` 在原文本中只替换目标行，**保留注释与排版**；写盘前用 `AppConfig.from_parser()` 完整校验，非法值不会落盘；成功后再 `reload()` 触发热更新。
- 写回是**幂等**的：新值与旧值相同则直接返回当前配置，不写盘、不触发无谓的重载通知；选项不存在则抛 `ConfigError("option ... not found")`。
- REST 的配置写入额外受 `WRITABLE_OPTIONS` 白名单约束（见 [09](09-gateway-api.md)），避免本地 API 成为任意配置后门。

## 4. 源清单 schema（`config/sources.yaml`）

```yaml
version: 1
sources:
  - name: my-clash-subscription        # 唯一名称
    url: https://example.com/clash.yaml
    kind: yaml                          # plain | json | yaml | subscription | api
    protocol_hint: clash                # 可选，纯文本无 scheme 时按此判定协议
    enabled: true
    weight: 5                           # 优先级/统计权重
    note: optional description
    headers:                            # 可选自定义请求头
      User-Agent: my-client/1.0
discovery:
  enabled: false
  max_results: 20
  search_queries: [...]
  github_repositories: [...]
  raw_candidate_paths: [...]
  file_extensions: [...]
```

`config/sources.local.yaml` 使用相同 schema；同名条目**覆盖**内置源。详见 [05](05-proxy-sources.md)。

## 5. 常见改法

| 需求 | 做法 |
| --- | --- |
| 改成 9 跳 | `config-set chain hops 9` 或主菜单 `4 -> 3` |
| 提高隐匿等级 | `stealth 3` 或主菜单 `6 -> 3` |
| 打开网关 | `gateway on` 或主菜单 `5 -> 4`（也可编辑 `gateway.enabled = true` 后自动热更新） |
| 停用某个源 | 主菜单 `1 -> 4`（本次运行生效）或在 `sources.yaml` 中 `enabled: false` |
| 提高可用池质量 | 调高 `validator.min_anonymity` / `min_score`，缩短 `ttl_seconds` |
| 降低采集压力 | 降低 `crawler.concurrency`、`rate_limit_per_second`，或提高隐匿等级 |

## 6. 安全提示

- 不要把主口令、API token 或节点凭证写进配置文件；它们属于 Vault / 池数据。
- 不要把 `gateway.host` 改为非回环地址——schema 会直接拒绝。
- 配置文件含注释，写回工具会保留；手工编辑时避免把注释写成 `key = value` 形式。
- 备份配置时与 `pools/.vault`、主口令分开保管。
