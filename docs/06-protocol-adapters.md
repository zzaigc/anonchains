# 06 协议适配与转发

## 1. 协议矩阵

| 协议 | 解析 | 内置 Python 级联 | 校验探针 | sing-box outbound | Xray outbound |
| --- | --- | --- | --- | --- | --- |
| `http` / `https` | 支持 | 支持（HTTPS 仅最后一跳） | HTTP CONNECT + echo | `http`（+TLS） | `http` |
| `socks4` / `socks5` | 支持 | 支持（SOCKS5 含认证） | 原生握手 + echo | `socks` v4/v5 | `socks` |
| `vless` / `vmess` | 支持 | 否 | 标记需外部核心 | `vless` / `vmess` | `vless` / `vmess` |
| `ss` / `ssr` | 支持 | 否 | 标记需外部核心 | `shadowsocks` | `shadowsocks` |
| `trojan` | 支持 | 否 | 标记需外部核心 | `trojan` | `trojan` |
| `wireguard` | 支持 | 否 | 标记需外部核心 | `wireguard` | 不支持（返回 `None`） |
| `hysteria2` / `tuic` | 支持 | 否 | 标记需外部核心 | `hysteria2` / `tuic` | 不支持 |
| `anytls` / `mtproto` / `ssr` | 支持 | 否 | 标记需外部核心 | 视版本支持情况；不可表达则跳过并告警 | 不支持 |
| `clash` / `unknown` | 容器/兜底 | 否 | 跳过 | 跳过 | 跳过 |

判定常量：`NATIVE_PROTOCOLS = {http, https, socks4, socks5}`，其余（除 `clash` / `unknown`）自动归入 `EXTERNAL_PROTOCOLS`。校验器对外部协议返回 `skipped` 结果，不会假装验证成功；`Chain.requires_external_core` 会据此标记整条链。

**不猜测、不静默直连**：`to_singbox_outbound()` / `to_xray_outbound()` 无法表达某协议时返回 `None`，`generate_*()` 记录 warning 并跳过该跳，而不是替换为 `direct`。

## 2. 内置 N 跳级联转发（`forwarder/cascade.py`）

原理：每一跳都是一条纯 TCP 隧道。代理接受 `CONNECT` / SOCKS 命令后，连接变成到下一地址的原始字节管道，因此可以在**同一条 stream** 上继续写下一跳的握手。重复 N 次即 N 跳，无需 `LD_PRELOAD`、无需外部二进制，跨平台可用。

```
client -> hop1: CONNECT hop2:port -> hop2: CONNECT hop3:port -> ... -> target
```

关键实现：

- `cascade_connect(nodes, target_host, target_port, timeout)` 建立贯穿全链的 `Tunnel(reader, writer)`；`cascade_connect_chain(chain, ...)` 为链对象封装；`pump()` 双向转发字节。
- 每跳有统一 deadline（`_remaining()`），避免超长链无限等待；`AppConfig.effective_connect_timeout()` 按跳数放大预算并封顶 120s。
- `socks5_greet()` 支持用户名/口令；`http_connect_to()` 支持 Proxy-Authorization；`socks4_connect_to()` 处理 SOCKS4 握手。
- 任何一跳协议不在 `NATIVE_PROTOCOLS` → 抛 `UnsupportedProtocol`，链应改由外部核心承载。

**限制**：

1. HTTPS（TLS-to-proxy）跳必须是**最后一跳**：`StreamWriter.start_tls()` 无法在单条 stream 上嵌套多次。
2. 只支持 TCP 隧道语义；UDP / QUIC 类协议交给外部核心。
3. 链内不叠加自定义加密，依赖各协议自带 TLS / 加密；对免费节点不可假设机密性。

## 3. 本地入口（`forwarder/local_server.py`）

`LocalEntryServer` 仅绑定回环地址（`127.0.0.1` / `localhost` / `::1`），在同一端口混合识别 SOCKS4、SOCKS5、HTTP CONNECT 请求，把每条连接交给 `cascade_connect_chain()` 走当前活动链。应用可直接配置：

```
socks5://127.0.0.1:8791
http://127.0.0.1:8791     # HTTP CONNECT
```

`status()` 提供 `running` / `port` / `connections` / `bytes_in` / `bytes_out`。启动前需先组装链（`ChainProvider` 返回 `None` 时拒绝连接）。`GatewayServer.stop()` 与 `AppContext.shutdown()` 都会停止入口。

## 4. 外部核心适配

### 配置生成（`core_adapter/config_gen.py`）

对链 `A -> B -> C -> target`：

- **sing-box**：每个 hop 生成带 `tag` 的 outbound，后一跳设置 `"detour": <前一跳 tag>`；路由 `final` 指向最后一跳，另加 `direct` outbound 兜底。入站为 `mixed` 类型。
- **Xray**：每个 hop 生成 outbound，通过 `streamSettings.sockopt.dialerProxy` 指向前一跳 tag（针对 Xray-core v26.x 字段验证）；原生协议映射为 `http` / `socks`，VLESS/VMESS/SS/Trojan 走各自协议设置。
- 两者都生成 `GeneratedConfig(core, config, warnings, listen_host, listen_port, exported_hops)`，可用 `to_json()` 导出。主菜单 `4 -> 9` 提供 dry-run 导出（**含凭证，勿外传**）。
- DNS 不做硬编码公共解析器（免费代理后置 often 不可达），默认 `{"strategy": "prefer_ipv4"}` 并附带 warning：如严格要求 DNS 泄漏防护，需自行在核心配置带 `detour` 的 DoH 解析器。

### 字段映射（`core_adapter/node_outbound.py`）

`_value()` 从 `credential` 与 `params` 中按大小写/连字符归一化查找别名，支持 `sni` / `alpn` / `insecure` / `reality pbk,sid` / `fingerprint` 等常见字段：

| 类别 | sing-box | Xray |
| --- | --- | --- |
| HTTP / SOCKS | `http` / `socks`（version 4/5）+ 用户名口令 | `http` / `socks` + `servers[].users` |
| VLESS | `uuid` / `flow` / `tls` | `vnext[].users` + `encryption` / `flow` |
| VMESS | `uuid` / `alter_id` / `security` | `vnext[].users` + `alterId` |
| SS | `method` / `password` | `servers[]` + `method` |
| Trojan | `password` + 强制 TLS | `servers[]` + TLS 设置 |
| Hysteria2 / TUIC / WireGuard | `hysteria2` / `tuic` / `wireguard`（private_key、peer_public_key、local_address、mtu） | 不支持 |

TLS 助手 `tls_enabled()` / `singbox_tls()` / `xray_tls()` 统一处理 `security=tls|reality|xtls`、`serverName`、`alpn`、`allowInsecure`、`utls.fingerprint`。

### 进程管理（`core_adapter/process.py`）

`CoreManager`：按 `core.prefer` 与配置路径探测二进制 → 生成含凭证的临时配置 → 启动子进程 → 就绪探测（超时 `startup_timeout_seconds`）→ `status()` / `stop()`。停止时删除临时配置文件，避免凭证残留。二进制缺失时返回 `(False, message)` 并给出 `INSTALL_HINT`，不会静默降级为直连。

## 5. 链路模式与轮换（`chain/`）

| 模式 | 行为 |
| --- | --- |
| `strict` | 取评分最高的前 N 个不同节点；池不足且不允许复用则报错 |
| `dynamic` | 池不足时降级为实际可用跳数，并尽量保留仍存活的 hop |
| `random` | 随机采样 |
| `round_robin` | 环形游标 O(1) 轮换 |

`ChainBuilder` 无硬跳数上限（>16 仅告警），`allow_reuse=True` 时允许同一节点重复以构造超长链；`ChainRotator` 管理 `current`、`rotate()`、`ensure()`、`invalidate_dead()`。评分排序时优先 `prefer_protocols`，其次原生协议、分数、延迟。

## 6. 新增协议清单

1. `models/proxy_node.py`：`Protocol` 加枚举；确认它自动归入 `EXTERNAL_PROTOCOLS`（或显式加入 `NATIVE_PROTOCOLS` 并实现握手）。
2. `parser/subscription.py` / `json_parser.py` / `clash_yaml.py`：补 scheme / type 映射与凭证、参数提取。
3. `validator/probes.py`：若原生可实现，补握手探针；否则保持 `UnsupportedProtocol` 跳过语义。
4. `core_adapter/node_outbound.py`：补 sing-box / Xray 字段映射；不可表达时返回 `None`。
5. `tests/test_parser.py`、`tests/test_core_adapter.py`：补离线用例。
6. 更新本文的协议矩阵与 [02](02-data-model-and-pools.md) 的枚举表。

**不要**为不支持的协议伪造 `direct` 兜底——那会产生「看起来连上了、实际没有走代理」的严重误导。
