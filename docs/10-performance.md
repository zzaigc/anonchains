# 10 性能设计

## 1.  workload 特征

采集与校验都是 **I/O 密集**型：瓶颈是源站限流、出口回显 RTT 与免费节点的不稳定性，而不是 CPU。设计用 `asyncio` + 有界信号量把并发控制在可预测范围，避免在 Python 层引入线程/进程池的复杂度。

| 阶段 | 并发来源 | 上限 |
| --- | --- | --- |
| 抓取 | `Crawler` + `FetchSession` | `min(crawler.concurrency, StealthPolicy.concurrency_cap)` |
| 抓取速率 | 全局令牌桶 | `crawler.rate_limit_per_second`（默认 8/s） |
| 校验 | `Validator.validate_many()` 的 `bounded_map` | `validator.concurrency`（默认 64） |
| 复检批量 | `Scheduler._collect_stale()` | 候选池 `4 × concurrency`，可用池 `2 × concurrency` |
| 链轮换 | `ChainRotator` 环形游标 | O(1) |

## 2. 复杂度

| 操作 | 复杂度 | 说明 |
| --- | --- | --- |
| 解析单源 | O(N) | N 为行数 / 条目数，单源封顶 `MAX_NODES_PER_SOURCE = 50_000` |
| 去重 | O(1) 期望 | `stable_key()` 哈希集合 + SQLite 主键 |
| 池写入 | O(N/B) 次事务 | `executemany` 按 `pools.batch_size`（默认 500）分片 |
| 组链 | O(hops) | 排序在池取样阶段完成，轮换不重建整条链 |
| 轮换 | O(1) | 环形指针；`dynamic` 仅补齐失效 hop |
| Vault 派生 | 昂贵（有意） | 解锁一次派生 KEK，DEK 按 `key_ttl_seconds` 缓存，池加解密不再重复 KDF |
| 口令轮换 | O(1) | 信封加密只重新封装 DEK，不重加密池数据 |

## 3. 热路径优化

- **统一网络栈**：`aiohttp` 同时承担抓取、echo 校验、REST 网关；共享 `ClientSession` 连接池与统一超时 / 重试 / 代理装配语义，避免多套 HTTP 库冲突。
- **增量校验**：只复检 `last_checked_at` 早于 `now - ttl_seconds` 的节点；候选池与可用池分别取样并按 `stable_key()` 合并，避免每轮全量重扫。
- **增量抓取**：`ETag` / `Last-Modified` 缓存，未变更源不重复解析；robots 判定结果按域名缓存。
- **批量写入**：`NodeStore` 单连接 + `RLock` + WAL，`executemany` 批量事务；非敏列建索引（`score` / `last_checked_at` / `protocol` / `host`），查询不触发全表解密。
- **失败控制**：超时熔断、指数退避 + 抖动、连续失败淘汰；高频失败日志采样限流，防止日志 I/O 风暴。
- **配置解析缓存**：`ConfigManager` 用 mtime + 内容摘要判断变更，无变更直接复用；校验失败回滚上一版。
- **连接轮换**：隐匿策略按请求数触发会话重建，等级越高越频繁（等级 4 每 8 个请求）。

### 3.1 流量优化（`fetcher/session.py`、`forwarder/cascade.py`）

| 措施 | 效果 |
| --- | --- |
| 完全相同 URL + 条件请求头 + 前置代理的并发 GET 合并为一次下载（`_inflight` + `asyncio.shield`） | 重复源定义 / 自定义源覆盖不再重复消耗带宽；合并次数在会话关闭时以 INFO 记录 |
| 流式分块读取 + `crawler.max_response_mb`（默认 8 MiB）硬上限 | 50 万行级大列表不会把整包读进内存，超限截断并告警 |
| 明确 `Accept-Encoding: gzip, deflate` + `auto_decompress=True` | 压缩传输且解压行为确定；不再声明无 brotli 依赖时无法解码的 `br` |
| `TCPConnector(limit, limit_per_host, keepalive_timeout=30, ttl_dns_cache=300)` | 连接复用、单域名不过载、DNS 结果缓存；会话轮换会避开仍在进行中的下载 |
| ETag / `If-None-Match` / 304 与 robots.txt 按域名缓存 | 未变更源零解析、每域名只取一次 robots |
| `pump()` 使用 256 KiB 块（与本地入口 `StreamReader` limit 对齐） | 多跳隧道减少读写系统调用，不引入额外缓冲层 |

`max_response_mb` 可在 `config/anonchains.conf` 调整；校验非法值（<= 0）会在配置阶段被拒绝。

## 4. 资源保护

| 机制 | 位置 | 作用 |
| --- | --- | --- |
| `effective_connect_timeout()` | `config/schema.py` | `clamp(base × (1 + 0.15 × (hops-1)), 1.0, 120.0)`，超长链超时预算封顶但**不限制跳数** |
| `_SOFT_RESOURCE_WARNING = 16` | `chain/builder.py` | 超过 16 跳仅记录告警 |
| `MAX_NODES_PER_SOURCE = 50_000` | `parser/base.py` | 单源解析上限，防止 50 万行大列表撑爆内存 |
| `crawler.max_response_mb` | `config/schema.py`、`fetcher/session.py` | 单源响应解压大小上限，默认 8 MiB |
| `bounded_map` / 信号量 | `utils/concurrency.py` | 抓取与校验并发上限 |
| 令牌桶 | `utils/concurrency.py` | 全局请求速率限制 |
| `key_ttl_seconds` | `crypto/vault.py` | 解锁密钥缓存 TTL，到期自动视为锁定 |

## 5. 已知瓶颈与调优

| 现象 | 原因 | 建议 |
| --- | --- | --- |
| 抓取很慢 | 隐匿等级 3/4 并发低、抖动大 | 仅对敏感源用高等级；默认等级 2 |
| 源返回 429 / 403 | 速率过高或 robots 禁止 | 降低 `rate_limit_per_second` / `concurrency`；停用该源；`respect_robots` 保持 true |
| 校验通过率低 | 免费节点质量差、echo 端点被墙 | 调整 `echo_urls`、`timeout_seconds`；提高 `min_score` 而非增加重试 |
| 可用池长期为空 | 门槛过高 / 复检不及时 | 检查 `min_anonymity` / `min_score` / `max_consecutive_failures`；缩短 `ttl_seconds` |
| 大列表内存高 | 50 万行级源启用 | 保持默认禁用；必要时调小 `MAX_NODES_PER_SOURCE` 并重启 |
| 链连接超时 | 跳数多、节点慢 | 降低 `hops`、提高 `validator.timeout_seconds`（`effective_connect_timeout` 封顶 120s） |
| SQLite 锁竞争 | 多进程同时写同一池 | 单进程运行；多实例用不同 `--base-dir` |
| CPU 在解锁瞬间高 | Argon2id 有意昂贵 | 属预期；避免在循环中反复 `initialize()` / `unlock()` |

## 6. 可观测性

- 日志：统一 `logging`，`RedactingFilter` 脱敏 + 采样限流；抓取/校验的批量耗时与通过率在 INFO 级输出。
- 状态：`anonchains status` 与 `GET /api/status` 给出双池统计、链状态、网关、核心、调度器摘要。
- 调度报告：`CycleReport` 含 `duration_seconds`、`crawl`（源成功/失败/节点数）、`validation`（通过/跳过/失败）、`promoted` / `demoted` / `removed`。
- 入口计数：`GET /api/entry` 返回 `connections` / `bytes_in` / `bytes_out`。

## 7. 压测建议（离线优先）

1. 用 `tests/` 的本地 socket / 内存 SQLite 夹具验证逻辑正确性，不依赖公网。
2. 需要吞吐基线时，起本地 HTTP 服务模拟源站与 echo 端点，注入为 `SourceSpec` / `echo_urls`。
3. 逐步提高 `crawler.concurrency` 与 `validator.concurrency`，观察 RTT、失败率与日志量；以源站限流为上限，不要以本机 CPU 为上限。
4. 记录 `CycleReport.duration_seconds` 与池增长曲线，验证增量复检确实只处理 TTL 到期节点。
