# anonchains

模块化、跨平台的高匿多跳代理链编排工具：从公开免费代理源采集节点，经并发校验沉淀为可用池，再按配置组装成任意跳数的链路，并通过本地网关 / 本地代理入口对外提供。

> 本项目只做「采集 → 验证 → 组织 → 使用」的本地编排。**仅限获得书面授权的红队 / 渗透测试等合法安全评估使用**；仅抓取公开免费列表，遵守 robots.txt、服务条款与速率限制；不提供攻击、未授权访问、凭证窃取或绕过付费能力。**高匿不等于绝对匿名**，免费公共节点不可信，切勿传输真实凭据或敏感数据。使用前必读 [合法使用声明与免责](docs/16-legal-use-and-disclaimer.md) 与 [合规声明](docs/14-compliance.md)；二次开发须遵守该文件中的规则与 MIT 许可证。

## 能力概览

- **任意跳数链路**：`chain.hops` 默认 5，可配置为任意正整数，无硬编码上限；支持 `strict` / `dynamic` / `random` / `round_robin` 四种组链与轮换模式，可自动定时轮换。
- **类 proxychains 配置**：INI 风格主配置 `config/anonchains.conf`，支持环境变量覆盖、注释保留的就地写回、校验失败回滚与热重载。
- **全网公开源采集**：内置 29 个公开源（GitHub Raw 列表、公开 API、订阅样例），覆盖 http/https/socks4/socks5/vless/vmess/ss/ssr/trojan/wireguard/hysteria2/tuic/anytls 等；支持自定义源、格式自动探测与可选的仓库检索发现。"全网"如实落地为内置注册表 + 自定义源 + 可选发现，不宣称覆盖所有站点。
- **分级隐匿采集**：1–4 级策略表集中控制 UA 池、请求头熵、时序抖动、并发上限、重试、连接轮换与 TLS 指纹伪装（可选 `curl_cffi`）。
- **双池闭环**：候选池与可用池各自独立 SQLite（WAL）持久化；并发握手 + 出口 IP 回显 + 匿名度分级 + 评分；TTL 增量复检、失效降级与淘汰；可用池节点反哺为采集前置代理（冷启动直连，带自引用防护）。
- **本地网关**：`gateway.enabled` 一键开关 REST 管理 API 与本地混合 SOCKS4/SOCKS5/HTTP CONNECT 入口；仅绑定回环地址、Bearer token 鉴权、配置写白名单、长任务后台执行。
- **加密保险库**：Argon2id（缺失时降级 `hashlib.scrypt`）+ AES-256-GCM / ChaCha20-Poly1305 信封加密保护节点凭证、原始订阅与网关 token；Vault 锁定时敏感字段不落明文；日志脱敏、常量时间比较、内存擦除。
- **数字式多级 CLI**：主菜单按数字进入子菜单，子菜单按数字执行功能；同时提供非交互命令，全部离线测试可跑通。

## 快速开始

要求 Python 3.10+（开发与实测环境为 CPython 3.14.7），Windows / Linux / macOS 均可。

一键脚本（自动检测 Python、创建/复用 `.venv`、检测并安装缺失依赖，详见 [脚本手册](docs/15-scripts.md)）：

```powershell
# Windows cmd（可双击）
scripts\start.bat install      # 只装依赖
scripts\start.bat              # 检测并进入数字式主菜单
scripts\check.bat              # 只检测环境
scripts\test.bat               # 离线单元测试
```

```bash
# Linux / macOS
chmod +x scripts/*.sh
./scripts/install.sh
./scripts/start.sh
./scripts/check.sh
./scripts/test.sh
```

手动安装（等价步骤）：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r requirements-optional.txt   # 可选：Argon2id / TLS 指纹 / 测试工具

python -m anonchains version
python -m anonchains status
python -m anonchains            # 进入数字式主菜单
python -m pytest tests -q       # 离线单元测试
```

也可用 `python main.py ...`、安装后的 `anonchains ...` 命令，或 `scripts/start.*` 一键脚本。首次使用建议在主菜单 `7 -> 2` 初始化 Vault，再 `1 -> 1` 抓取、`3 -> 2` 跑完整闭环、`4 -> 2` 组链。需要联网的操作仅抓取 / 校验 / 发现与首次依赖安装，状态与菜单完全离线可用。

## 文档

全部设计文档集中在 [`docs/`](docs/README.md)：架构、数据模型与双池、配置手册、CLI 手册、代理源、协议适配、隐匿等级、安全与 Vault、网关 API、性能、安装、测试验收、路线图、合规声明、一键脚本手册与合法使用/免责声明。后续开发者或其它 AI 请先阅读 `docs/README.md` 的阅读顺序与扩展点索引，并遵守 `docs/16-legal-use-and-disclaimer.md` 的二次开发规则。

项目内所有配置、状态输出与日志中的路径均为**相对项目根目录**的形式（不会写入本机绝对路径）；抓取与校验界面提供实时进度条、百分比与统计汇总。

## 关键路径

| 路径 | 说明 |
| --- | --- |
| `config/anonchains.conf` | 主配置（类 proxychains INI） |
| `config/sources.yaml` | 内置公开源注册表 |
| `config/sources.local.yaml` | 用户自定义源（可选，不提交） |
| `pools/candidate/candidate.db` | 候选池 SQLite（运行时生成） |
| `pools/verified/verified.db` | 可用池 SQLite（运行时生成） |
| `pools/.vault` | Vault 密文头与封装密钥（绝不提交） |
| `anonchains/` | 分层源码 |
| `scripts/` | 一键启动 / 检测 / 安装 / 测试脚本（`.bat` `.ps1` `.sh`） |
| `tests/` | 离线单元测试 |
| `docs/` | 全套设计文档 |

## 依赖

运行依赖锁定在 `requirements.txt`：`aiohttp`、`cryptography`、`PyYAML`、`rich`。可选增强在 `requirements-optional.txt`：`argon2-cffi`（Argon2id）、`curl_cffi`（TLS 指纹）、`pytest` / `pytest-asyncio`。sing-box / xray 二进制由用户自备，仅用于 Python 无法原生实现的协议，缺失时优雅降级并给出安装指引。

## 许可

MIT。使用本项目即表示接受 `docs/14-compliance.md` 中的合规与使用边界。
