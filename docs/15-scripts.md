# 15 一键脚本

`scripts/` 提供跨平台的一键启动、环境检测、依赖安装与离线测试入口。全部逻辑集中在 3 个主脚本，其余 9 个是转发用薄封装；脚本不做 git 初始化/提交、不改全局配置、不写入任何密钥。

## 1. 文件清单

| 文件 | 平台 | 作用 |
| --- | --- | --- |
| `start.bat` | Windows cmd | 一键启动（双击进入数字菜单；传参则执行对应命令） |
| `start.ps1` | PowerShell 5.1 / pwsh | 主逻辑：检测、安装、启动、测试 |
| `start.sh` | Linux / macOS / Git Bash | 主逻辑（bash） |
| `install.bat` / `install.ps1` / `install.sh` | 三平台 | 只检测并安装依赖，不启动 |
| `check.bat` / `check.ps1` / `check.sh` | 三平台 | 只检测环境，**不创建文件、不安装** |
| `test.bat` / `test.ps1` / `test.sh` | 三平台 | 运行离线 `pytest` 套件（缺 pytest 时安装可选依赖） |

`.bat` 与 `*.ps1` 的薄封装都转调 `start.ps1`；`*.sh` 薄封装转调 `start.sh`。新增能力请改主脚本，薄封装保持一行转发。

## 2. 统一行为

每次运行按顺序执行：

1. 定位项目根目录（脚本所在目录的上一级），切换到该目录；
2. 解析 Python 解释器（见第 4 节），校验版本 `>= 3.10`；
3. 探测依赖：核心 `aiohttp` / `cryptography` / `yaml` / `rich`，可选 `argon2.low_level` / `curl_cffi` / `pytest`；
4. 核心依赖缺失时执行 `pip install -r requirements.txt`；`--enhanced` 时追加 `requirements-optional.txt`；
5. 按模式启动 CLI / 运行测试 / 仅报告 / 装完即退出。

依赖检测用 Python 自身 `try: __import__(...)` 完成，不依赖 `pip list` 文本解析。**核心依赖已存在时跳过安装**，因此已完成安装的机器可完全离线一键启动。

## 3. 模式与参数

| 模式 / 开关 | PowerShell / `.bat` | `.sh` | 说明 |
| --- | --- | --- | --- |
| 启动（默认） | 无参数 | 无参数 | 自动检测/安装后进入数字菜单 |
| 检测 | `check` / `-CheckOnly` | `--check` | 只报告；核心依赖或主配置缺失时退出码 `1` |
| 安装 | `install` / `-InstallOnly` | `--install` | 建 venv、装依赖后退出 |
| 测试 | `test` / `-Test` | `--test` | 跑 `python -m pytest tests -q` |
| 可选依赖 | `-Enhanced` | `--enhanced` | 追加安装 `requirements-optional.txt` |
| 跳过虚拟环境 | `-NoVenv` | `--no-venv` | 使用当前/系统 Python |
| 帮助 | `-h` / `--help` | `-h` / `--help` | 显示脚本用法 |
| 停止解析 | `--` | `--` | 其后参数原样透传给 CLI |

PowerShell 侧同时接受 `-` / `--` 前缀与不带前缀的模式词（`check`、`install`、`test`）；脚本开关必须放在 CLI 参数之前。`start.ps1` 手工解析 `$args`（不使用 `param()`），保证 `--config` 之类的 CLI 选项保持原始顺序。

### 退出码

| 码 | 含义 |
| --- | --- |
| `0` | 成功；启动模式下与 `anonchains` CLI 退出码一致并透传 |
| `1` | 环境/依赖/Python 版本检查失败，或脚本内部错误 |
| `2` | CLI 参数错误（argparse）或未知脚本选项 |
| `130` | CLI 被 Ctrl+C 中断 |

## 4. 解释器与虚拟环境策略

优先级从上到下：

1. 已激活的 `VIRTUAL_ENV`（若其中存在 Python）；
2. 项目根 `.venv`（Windows 为 `Scripts/python.exe`，POSIX 为 `bin/python`；`start.sh` 两种都会识别，便于 Git Bash）；
3. **系统 Python 已具备全部核心依赖** → 直接使用，不创建 venv（离线最快路径）；
4. 否则创建 `.venv` 并把依赖装进该环境。

`-NoVenv` / `--no-venv` 直接使用系统 Python，适合 conda、系统级安装或 CI 已装好依赖的场景。`check` 模式不会创建 `.venv`。`.venv`、`pools/`、`logs/` 均已被 `.gitignore` 忽略。

## 5. 用法示例

### Windows cmd（可双击）

```bat
scripts\start.bat                  REM 一键启动，进入数字式主菜单
scripts\start.bat status           REM 直接打印综合状态
scripts\start.bat check            REM 只检测环境
scripts\start.bat install -Enhanced
scripts\start.bat test
```

无参数运行时 `.bat` 会在 CLI 退出后 `pause`，方便双击查看输出；传参时不暂停，便于脚本化调用。`.bat` 优先用 `pwsh`，否则用 `powershell.exe`，并以进程级 `-ExecutionPolicy Bypass` 调用 `start.ps1`（不修改系统执行策略）。

### Windows PowerShell

```powershell
.\scripts\start.ps1
.\scripts\start.ps1 status
.\scripts\check.ps1
.\scripts\install.ps1 -Enhanced
.\scripts\test.ps1 -NoVenv
.\scripts\start.ps1 -NoVenv -- --config config/anonchains.conf status
```

若手动执行 `.ps1` 被执行策略拦截，用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1`；或直接使用 `.bat`。

### Linux / macOS

```bash
chmod +x scripts/*.sh              # 首次使用
./scripts/start.sh                 # 一键启动
./scripts/start.sh status
./scripts/check.sh
./scripts/install.sh --enhanced
./scripts/test.sh
./scripts/start.sh -- --config /tmp/anonchains.conf status
```

没有可执行位时可用 `bash scripts/start.sh ...`。`.sh` 使用 `#!/usr/bin/env bash` + `set -euo pipefail`，不依赖 `sudo`，也不写入系统路径。

## 6. 参数透传

脚本开关之后的所有参数会原样传给 `python -m anonchains`：

```bat
scripts\start.bat gateway status
scripts\start.bat stealth 3
scripts\start.bat -NoVenv -- --config config\anonchains.conf sources
```

```bash
./scripts/start.sh crawl --source thespeedx-http
./scripts/start.sh -- --config /tmp/anonchains.conf status
```

注意 `--config` / `--base-dir` 是 argparse 的**全局**选项，必须放在子命令之前；使用 `--` 显式结束脚本选项可避免歧义。CLI 完整命令见 [04](04-cli-manual.md)。

## 7. 常见问题

| 现象 | 处理 |
| --- | --- |
| `Python not found` | 安装 Python >= 3.10 并加入 PATH；Windows 可用 `py` 启动器安装官方版本 |
| `Python >= 3.10 is required` | 切换默认 Python 或先激活符合版本的虚拟环境 |
| Windows 上 `python` 指向 Microsoft Store 占位程序 | 安装官方 Python 并调整 PATH 顺序；脚本会给出版本报错而不是静默失败 |
| `pip install failed` | 检查网络/代理；或先手动 `python -m pip install -r requirements.txt` |
| 双击 `.bat` 窗口一闪而过 | 用无参数双击会自动 `pause`；或从终端运行查看完整输出 |
| `.ps1` 被执行策略拒绝 | 使用 `.bat`，或用 `-ExecutionPolicy Bypass`（仅当前进程） |
| Linux 下 `Permission denied` | `chmod +x scripts/*.sh`，或改用 `bash scripts/start.sh` |
| `check` 返回 1 | 核心依赖或 `config/anonchains.conf` 缺失，运行 `install` 脚本 |
| 可选依赖长期显示 missing | 属正常降级（scrypt / 标准 TLS 栈）；需要时用 `-Enhanced` 安装 |
| 测试提示安装 pytest | `test` 模式会自动安装 `requirements-optional.txt`；也可预先手动安装 |

## 8. 安全与维护约定

- 脚本只读取 `requirements*.txt` 与 `config/anonchains.conf`，不读取或写入 Vault、token、池数据；不输出任何凭证。
- `pip install` 目标仅限所选解释器（优先隔离的 `.venv`），不使用 `--user` 或系统级 sudo 安装。
- 不执行 `git init` / `commit` / `push`，不修改 git 或 PowerShell 的全局配置。
- 主脚本输出统一为 ASCII，避免 Windows PowerShell 5.1 按 ANSI 读取 UTF-8 无 BOM 脚本时乱码；用户可见文案在 `docs/` 中为中文。
- 新增脚本或参数：改 3 个主脚本 + 对应薄封装，并同步更新本文、`docs/README.md` 索引与根 `README.md` 的快速开始。
