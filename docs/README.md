# 文档索引

本目录集中存放 anonchains 的全部设计文档，供后续开发者或其它 AI 迭代时快速建立一致的心智模型。**修改代码时请同步维护对应文档**。

## 阅读顺序（建议）

| 顺序 | 文档 | 目的 |
| --- | --- | --- |
| 1 | [00-overview.md](00-overview.md) | 目标、范围边界、术语表 |
| 2 | [01-architecture.md](01-architecture.md) | 四层架构、模块依赖、核心数据流 |
| 3 | [02-data-model-and-pools.md](02-data-model-and-pools.md) | `ProxyNode` 契约、双池状态机、持久化 |
| 4 | [03-configuration.md](03-configuration.md) | 主配置 / 源清单字段手册与热更新 |
| 5 | [04-cli-manual.md](04-cli-manual.md) | 数字菜单对照与非交互命令 |
| 6 | [05-proxy-sources.md](05-proxy-sources.md) | 内置源、格式解析、扩展与发现 |
| 7 | [06-protocol-adapters.md](06-protocol-adapters.md) | 协议矩阵、内置级联、外部核心 |
| 8 | [07-stealth-levels.md](07-stealth-levels.md) | 隐匿等级参数与风险权衡 |
| 9 | [08-security-and-vault.md](08-security-and-vault.md) | 加密格式、密钥管理、威胁模型 |
| 10 | [09-gateway-api.md](09-gateway-api.md) | REST 接口、鉴权、本地入口 |
| 11 | [10-performance.md](10-performance.md) | 并发模型、复杂度与调优 |
| 12 | [11-installation.md](11-installation.md) | 跨平台安装与首次启动 |
| 13 | [12-testing-and-acceptance.md](12-testing-and-acceptance.md) | 离线测试与验收清单 |
| 14 | [13-roadmap.md](13-roadmap.md) | 后续迭代方向与接手约定 |
| 15 | [14-compliance.md](14-compliance.md) | 合规边界与风险警示 |
| 16 | [15-scripts.md](15-scripts.md) | 一键脚本：启动、检测、安装与测试 |
| 17 | [16-legal-use-and-disclaimer.md](16-legal-use-and-disclaimer.md) | 授权红队使用边界、免责与二次开发规则 |

只想快速跑起来：读 [15-scripts.md](15-scripts.md) + [11-installation.md](11-installation.md) + [04-cli-manual.md](04-cli-manual.md)。只做安全评审：读 [08-security-and-vault.md](08-security-and-vault.md) + [14-compliance.md](14-compliance.md)。使用前请先阅读 [16-legal-use-and-disclaimer.md](16-legal-use-and-disclaimer.md)（授权前提、免责与二次开发规则）。

## 按任务找文档

| 我要… | 看哪里 |
| --- | --- |
| 增加一个代理源 | [05](05-proxy-sources.md) `自定义源` |
| 增加一种解析格式 | [05](05-proxy-sources.md) `解析器注册表` + [01](01-architecture.md) `扩展点` |
| 增加一种协议 | [06](06-protocol-adapters.md) `新增协议清单` |
| 改配置项 / 加配置段 | [03](03-configuration.md) + `anonchains/config/schema.py` |
| 加菜单功能 | [04](04-cli-manual.md) + `anonchains/cli/menus.py`、`commands.py` |
| 加 REST 接口 | [09](09-gateway-api.md) + `anonchains/gateway/api.py` |
| 理解升降级规则 | [02](02-data-model-and-pools.md) + `anonchains/scheduler/jobs.py` |
| 排查性能问题 | [10](10-performance.md) |
| 用一键脚本启动 / 检测 / 装依赖 | [15](15-scripts.md) + `scripts/` |

## 迭代约定（重要）

- **单一数据源**：`ProxyNode`（`anonchains/models/proxy_node.py`）贯穿全链路；新增字段必须同时考虑解析、SQLite 列、Vault 封印、`to_dict()` 脱敏与文档。
- **敏感信息**：凭证 / 订阅原文 / API token 一律经 Vault 封印，绝不写明文、绝不打日志、绝不在 `to_dict()` 默认输出中出现。
- **离线可测**：新功能至少配一个不访问公网的 pytest 用例；网络交互需可注入或 mock。
- **降级优先**：可选依赖缺失、Vault 未解锁、外部核心缺失、网关关闭、冷启动无网这五种情况必须有明确安全路径。
- **跨平台**：不依赖 `LD_PRELOAD`、iptables、Linux 专有系统调用；网络统一走 `aiohttp` / `asyncio`。
- **合规**：只采集公开免费列表，限速并遵守 robots.txt；不实现攻击或绕过身份验证的能力；文档需明确「高匿不等于绝对匿名」。

## 文档与实际实现的一致性说明

- 计划中曾提及 XChaCha20-Poly1305；`cryptography` 实际提供的是 RFC 8439 ChaCha20-Poly1305（96-bit nonce），代码与本文档均按 `chacha20-poly1305` 准确命名，避免误导。
- 「所有可能的源」落地为内置实测公开源注册表 + 自定义源 + 可选仓库检索发现，不宣称覆盖全网。
- 链内传输安全依赖各协议自带加密；Vault 只保护本地静态数据，不设计需要公共免费代理运行解密端的方案。
