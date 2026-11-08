# 11 跨平台安装与首次启动

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| Python | `>= 3.10`；开发与实测环境 CPython 3.14.7（Windows） |
| 操作系统 | Windows / Linux / macOS；不依赖 `LD_PRELOAD`、iptables 或 Linux 专有调用 |
| 网络 | 仅抓取、校验、源发现需要联网；菜单、状态、配置、Vault、测试全部可离线运行 |
| 外部核心 | 可选：sing-box / xray 二进制，用户自备并置于 PATH 或配置绝对路径 |
| 磁盘 | `pools/` 与 `logs/` 需可写；SQLite 使用 WAL 模式 |

## 2. 安装

推荐先使用一键脚本（完整说明见 [15-scripts.md](15-scripts.md)）：

```powershell
# Windows cmd（可双击）或 PowerShell
scripts\start.bat install      # 只装依赖
scripts\start.bat              # 检测 + 启动数字菜单
scripts\check.bat              # 只检测环境
```

```bash
# Linux / macOS
chmod +x scripts/*.sh
./scripts/install.sh           # 只装依赖
./scripts/start.sh             # 检测 + 启动数字菜单
./scripts/check.sh             # 只检测环境
```

脚本会自动校验 Python >= 3.10、创建或复用 `.venv`、探测并只安装缺失的依赖，然后启动；`test.bat` / `test.sh` 可一键跑离线测试。以下是等价的手动步骤。

```bash
git clone <your-fork-url> anonchains     # 或直接使用项目目录
cd anonchains
python -m venv .venv
```

激活虚拟环境：

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS
source .venv/bin/activate
```

安装依赖：

```bash
python -m pip install -U pip
python -m pip install -r requirements.txt
# 可选增强：Argon2id KDF、TLS 指纹伪装、测试工具
python -m pip install -r requirements-optional.txt
```

或安装为包（提供 `anonchains` 命令）：

```bash
python -m pip install -e .
python -m pip install -e ".[enhanced,dev]"
```

### 依赖说明

| 包 | 锁定版本 / 范围 | 用途 | 缺失时 |
| --- | --- | --- | --- |
| `aiohttp` | `3.14.3` | 抓取、echo 校验、REST 网关 | 必需 |
| `cryptography` | `50.0.1` | AES-256-GCM / ChaCha20-Poly1305 | Vault 变为 `unavailable`，敏感字段不落盘 |
| `PyYAML` | `6.0.3` | 源清单、Clash YAML 解析 | 必需 |
| `rich` | `15.0.0` | 表格化 CLI | 自动降级纯文本 |
| `argon2-cffi` | `>=23.1.0,<27` | Argon2id KDF | 降级 `hashlib.scrypt` |
| `curl_cffi` | `>=0.11.0,<1` | 等级 4 TLS 指纹伪装 | 静默降级标准 aiohttp TLS |
| `pytest` / `pytest-asyncio` | `9.2.0` / `>=1.0,<2` | 离线单测 | 仅影响测试 |

`requirements.txt` 的版本在 CPython 3.14.7 / Windows 上验证过官方 wheel 可用。`pyproject.toml` 声明 `requires-python >= 3.10`；若在较旧版本运行，请优先使用锁定版本并跑一遍 `python -m pytest tests -q`。

## 3. 外部核心（可选）

主配置 `[core]` 段：

```ini
[core]
enabled = true
prefer = auto            ; auto | sing-box | xray
sing_box_path = sing-box
xray_path = xray
startup_timeout_seconds = 10.0
```

- 从 sing-box / Xray-core 的**官方发布页**下载对应平台二进制，放到 PATH 或填写绝对路径。
- `CoreManager` 会探测二进制、生成临时配置、启动子进程、做就绪探测；停止时删除含凭证的临时配置。
- 缺失二进制时返回明确错误与 `INSTALL_HINT`，**不会**静默改用直连。
- 只使用原生协议（http / https / socks4 / socks5）时完全不需要外部核心。

## 4. 首次启动

```bash
python -m anonchains version        # 验证入口
python -m anonchains status         # 离线综合状态
python -m anonchains                # 进入数字式主菜单
```

推荐顺序：

1. `7 -> 2` 初始化 Vault（设置主口令，至少 8 位并二次确认）；
2. `6 -> 2`（默认）或按需 `6 -> 1..4` 选择隐匿等级；
3. `1 -> 3` 查看源清单，`1 -> 4` 按需启停（大列表默认禁用）；
4. `1 -> 1` 抓取全部启用源（需联网）；
5. `3 -> 2` 跑完整闭环，产生可用池；
6. `4 -> 2` 组链，`4 -> 5` 启动本地入口；
7. 需要 REST 时 `5 -> 4` 打开配置开关，或 `5 -> 2` 临时启动。

非交互等价命令见 [04](04-cli-manual.md)。多实例运行请使用不同的 `--config` 与 `--base-dir`，避免两个进程写同一份 SQLite 池。

## 5. 目录权限与忽略

首次运行会创建：

```
pools/candidate/candidate.db (+ -wal / -shm)
pools/verified/verified.db   (+ -wal / -shm)
pools/.vault                  # 初始化后
pools/.gateway_token          # Vault 解锁时
logs/anonchains.log
```

`.gitignore` 已忽略这些运行时文件、`config/*.local.conf`、`config/sources.local.yaml`、虚拟环境与缓存。**切勿**把 `.vault`、`.db`、token 或日志提交到仓库；备份时按 [08](08-security-and-vault.md) 的清单处理。

## 6. 平台差异提示

| 平台 | 提示 |
| --- | --- |
| Windows PowerShell | `curl` 是 `Invoke-WebRequest` 别名；调用 REST 用 `Invoke-RestMethod` 或显式 `curl.exe`。路径用反斜杠或 `Path` 对象，配置里推荐正斜杠 |
| Windows | 若企业代理/防火墙拦截 GitHub Raw，抓取会 FAIL 或被 BLOCKED，属预期；可在 `sources.local.yaml` 换成可访问的镜像 |
| Linux / macOS | 低端口（<1024）需要权限；默认 8790/8791 无需。不要以 root 长期运行 |
| 全平台 | 不修改全局 git 配置、不自动初始化仓库或提交；不写入系统代理设置 |

## 7. 常见安装问题

| 现象 | 处理 |
| --- | --- |
| `ModuleNotFoundError: aiohttp` | 未激活虚拟环境或未安装 `requirements.txt` |
| `vault.state = unavailable` | 安装 `cryptography`；Vault 不可用时敏感字段不会落盘 |
| `Vault 为 locked` | 主菜单 `7 -> 3` 解锁；未按此操作仍可查看池统计 |
| `配置错误：configuration file not found` | 用 `--config` 指向 `config/anonchains.conf` |
| `option ... not found` | 配置项不存在于当前文件；检查拼写或从模板复制该行 |
| 网关起不来 | 端口被占用或 `gateway.enabled` 为 false；`5 -> 1` 查看状态，改 `port` / `entry_port` |
| 外部核心启动失败 | 检查 `core.sing_box_path` / `xray_path` 与 PATH；查看 `4 -> 9` dry-run 配置 |
| `pytest` 报缺少依赖 | 安装 `requirements-optional.txt` 中的测试工具 |

## 8. 卸载 / 重置

- 停止程序 → 删除 `.venv` 即完成依赖卸载；
- 只重置池：停止程序后删除 `pools/candidate/*.db*` 与 `pools/verified/*.db*`；
- **谨慎**：删除 `pools/.vault` 会永久丢失已封印凭证，池中的 `sealed` 列将再也无法解密。
