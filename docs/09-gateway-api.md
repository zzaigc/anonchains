# 09 本地网关与 REST API

## 1. 总开关与组成

`gateway.enabled` 是**唯一总开关**，同时控制：

- REST 管理 API：`http://127.0.0.1:8790`（`gateway.port`）；
- 本地代理入口：`socks5://127.0.0.1:8791`（`gateway.entry_port`，同时支持 HTTP CONNECT 与 SOCKS4）。

`GatewayServer.start(force=False)` 在配置关闭时拒绝启动；CLI 的 `5 -> 2` 使用 `force=True` 做临时诊断启动。`apply_config_switch()` 在配置热更新后把运行态与 `enabled` 对齐：开启则启动，关闭则停止。`status()` 返回 `running` / `enabled` / `host` / `api_port` / `entry_port` / `base_url` / `entry` / `token` / `background_tasks`。

绑定地址只允许回环（`127.0.0.1` / `localhost` / `::1`），schema 直接拒绝其它值。

## 2. 鉴权

- 默认 `gateway.token_required = true`。除 `/` 与 `/api/health` 外，所有 `/api/*` 需：
  `Authorization: Bearer <token>` 或 `X-Anonchains-Token: <token>`。
- token 由 `TokenManager` 用 `secrets.token_urlsafe(32)` 生成；Vault 解锁时以 AAD `b"gateway-token"` 封印到 `pools/.gateway_token`，Vault 未解锁 / 禁用时仅保留在内存（重启即变），**绝不写明文**。
- 校验使用 `constant_time_eq`；失败返回 `401 {"error":"unauthorized"}`。
- 完整 token 在网关启动时通过日志/CLI 显示一次；`GET /api/token` 只返回预览（前 6 后 4 位）与 `required` / `ephemeral` / `persisted`。
- 轮换：CLI `5 -> 5` 或 `TokenManager.rotate()`；Vault 解锁后 `refresh()` 会把内存临时 token 持久化。
- 错误中间件把非 HTTP 异常转换为 `500 {"error": "...", "detail": "<脱敏且截断>"}`，不 dump 大 payload。

## 3. 路由清单

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 否 | 健康检查 |
| GET | `/api/health` | 否 | 健康检查（含 `gateway_enabled`） |
| GET | `/api/status` | 是 | 综合状态：配置摘要、双池统计、链、网关、核心、调度器、源 |
| GET | `/api/config` | 是 | 脱敏后的完整配置 |
| PATCH | `/api/config` | 是 | 修改**白名单内**配置项 |
| GET | `/api/pools/candidate?limit=20` | 是 | 候选池统计 + 样本（`limit` 裁剪到 1..200） |
| GET | `/api/pools/verified?limit=20` | 是 | 可用池统计 + Top 样本 |
| GET | `/api/chain` | 是 | 当前链（无链时 `{"hops":0,"nodes":[]}`） |
| POST | `/api/chain/rotate` | 是 | 立即组链；空池返回 `409` |
| POST | `/api/crawl` | 是 | 后台抓取，立即返回 `202` |
| POST | `/api/validate` | 是 | 后台仅复检闭环，返回 `202` |
| POST | `/api/cycle` | 是 | 后台完整闭环，返回 `202` |
| GET | `/api/sources` | 是 | 源统计 + 全部源定义 |
| POST | `/api/sources/{name}/toggle` | 是 | `{"enabled": true/false}`，未知源 `404` |
| GET | `/api/vault` | 是 | Vault 状态 |
| POST | `/api/vault/unlock` | 是 | `{"passphrase": "..."}`，失败 `400` |
| POST | `/api/vault/lock` | 是 | 锁定 Vault |
| GET | `/api/core` | 是 | 外部核心状态 |
| POST | `/api/core/start` | 是 | 启动外部核心；无活动链 `409`，失败 `503` |
| POST | `/api/core/stop` | 是 | 停止外部核心 |
| GET | `/api/entry` | 是 | 本地入口状态 |
| GET | `/api/token` | 是 | token 状态（预览，不返回完整值） |

长任务（crawl / validate / cycle / core start 中的耗时部分）通过 `GatewayContext.spawn()` 登记到 `background` 集合；返回 `202 {"accepted": true, "operation": "..."}`，进度可通过 `/api/status` 的 `scheduler.last_report` 观察。网关停止时统一 `cancel_all()`。

## 4. 配置写白名单

`WRITABLE_OPTIONS`（`gateway/api.py`）只允许修改：

```
chain.hops, chain.mode, chain.rotation_seconds,
crawler.stealth_level, crawler.rate_limit_per_second,
validator.interval_seconds, validator.ttl_seconds,
gateway.enabled, gateway.token_required
```

其它项（如 `gateway.host`、`vault.path`）必须编辑配置文件。这样即使本机有恶意进程拿到 token，也不能把管理接口暴露到公网或改写 Vault 位置。请求体：

```json
{"section": "chain", "option": "hops", "value": 7}
```

成功返回 `{"ok": true, "section": "chain", "option": "hops", "value": 7}`；非白名单返回 `400 {"error": "option not writable", "allowed": [...]}`。写入走 `ConfigManager.set_value()`，沿用注释保留、校验后落盘、幂等与热更新语义。

## 5. 请求示例

Windows PowerShell（注意 `curl` 是 `Invoke-WebRequest` 别名，建议用 `curl.exe` 或 `Invoke-RestMethod`）：

```powershell
$token = "<启动日志中的完整 token>"
$h = @{ Authorization = "Bearer $token" }

Invoke-RestMethod http://127.0.0.1:8790/api/health
Invoke-RestMethod -Headers $h http://127.0.0.1:8790/api/status
Invoke-RestMethod -Headers $h -Method Patch -ContentType "application/json" `
  -Body '{"section":"crawler","option":"stealth_level","value":3}' `
  http://127.0.0.1:8790/api/config
Invoke-RestMethod -Headers $h -Method Post http://127.0.0.1:8790/api/crawl
Invoke-RestMethod -Headers $h -Method Post http://127.0.0.1:8790/api/chain/rotate
```

Linux / macOS：

```bash
TOKEN="<token>"
curl -s http://127.0.0.1:8790/api/health
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8790/api/pools/verified?limit=5
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8790/api/validate
```

## 6. 本地代理入口用法

网关启动时会同时启动 `LocalEntryServer`（回环）。应用程序可配置：

```
socks5://127.0.0.1:8791
http://127.0.0.1:8791      # HTTP CONNECT
```

示例：

```bash
curl -x socks5h://127.0.0.1:8791 https://api.ipify.org?format=json
curl -x http://127.0.0.1:8791   https://api.ipify.org?format=json
```

前提：`ChainRotator.current` 非空且链为原生协议；外部协议链需先启动 sing-box / xray 并使用核心的入站端口。`GET /api/entry` 返回 `running` / `port` / `connections` / 字节计数。

> 免费节点不可信，本地入口不会自动提供端到端加密；用它访问敏感账号或传输机密是不安全的（见 [08](08-security-and-vault.md)）。

## 7. 关闭与清理

`gateway off`、`5 -> 3`、或 `GatewayServer.stop()` 会：清理 `AppRunner`、停止本地入口、取消后台任务、停止外部核心（并删除含凭证的临时配置）。`AppContext.shutdown()` 额外关闭两个 SQLite 连接并锁定 Vault。REST 关闭后 `/api/*` 全部不可达，池数据仍保留在磁盘。
