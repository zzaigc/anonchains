# 04 命令行手册

## 1. 启动方式

```bash
python -m anonchains                 # 数字式多级菜单（默认）
python main.py                      # 等价入口
anonchains ...                      # 安装为包后的 console script
python -m anonchains status         # 非交互命令
python -m anonchains --config <path> --base-dir <dir> status
```

也可使用一键脚本（自动检测 Python 与依赖，详见 [15](15-scripts.md)）：`scripts\start.bat`（Windows cmd）、`.\scripts\start.ps1`（PowerShell）、`./scripts/start.sh`（Linux / macOS）。脚本参数会原样透传给 CLI，例如 `scripts\start.bat status` 或 `./scripts/start.sh -- --config <path> status`。

全局参数：`--config` 指定主配置文件（默认 `config/anonchains.conf`），`--base-dir` 指定运行时数据根目录（默认项目根目录）。二者常用于隔离测试与多实例。

`UI` 为零依赖自绘渲染：ANSI 颜色（Windows 10+ 控制台自动启用，`NO_COLOR` 关闭）、CJK 全角宽度感知的对齐表格——列宽按终端宽度收缩并截断（省略号），**从不折行**，在 cmd.exe / Windows Terminal / 任意 CJK 字体下均保持版面完整。主菜单标题下常驻一行状态摘要（隐匿等级、跳数、双池计数、网关开关）。输入通过标准 `input()`，`EOF` 视为 `0`（返回/退出）。

退出码：`0` 成功；`2` 配置错误 / 未知源 / argparse 参数错误；`130` Ctrl+C 中断。

## 2. 主菜单

| 数字 | 子菜单 |
| --- | --- |
| `1` | 代理源与爬取 |
| `2` | 双池管理（候选池 / 可用池） |
| `3` | 校验与筛选 |
| `4` | 链路与转发 |
| `5` | 本地网关与 API |
| `6` | 隐匿等级（数字选择） |
| `7` | 加密 Vault |
| `8` | 系统配置与状态 |
| `9` | 后台调度器 |
| `0` | 退出 |

在任意子菜单按 `0` 返回上一级；主菜单按 `0` 退出（回车确认，输入 `n` 取消）。无效数字会提示并重显菜单。

## 3. 子菜单对照

### `1` 代理源与爬取

| 数字 | 功能 | 说明 |
| --- | --- | --- |
| `1` | 抓取全部启用源 | 需联网；逐源输出 OK / FAIL / BLOCKED |
| `2` | 抓取单个源 | 先列出启用源，按序号选择 |
| `3` | 查看源清单 | 名称、启用、类型、协议 hint、权重、URL + 统计 |
| `4` | 启用 / 禁用源 | 输入源名称，仅本次运行生效 |
| `5` | 发现更多公开源 | 需联网 GitHub API，结果**不自动启用** |
| `0` | 返回主菜单 | |

抓取时显示实时进度条，参考样式：

```text
[██████████░░░░░░░░░░░░]  42%  9/21 源 | 成功 9 失败 3 拦截 0 | 解析 32095 条 | 入池 22717 条
完成：源 9/21 成功 | 解析 32095 条 | 入池 22717 条 | 未变更 2 | 耗时 18.5s
失败源 3 个（前 3 条）：
  - some-source: ClientConnectorError ...
```

每完成一个源刷新一次（TTY 用回车符原地刷新，非 TTY 自动降级纯文本），随后输出汇总、前 5 条失败原因与逐源表格。`3 -> 1/2/3` 的校验阶段同样显示 `done/total` 百分比与通过 / 跳过 / 失败计数。

### `2` 双池管理

| 数字 | 功能 |
| --- | --- |
| `1` | 查看候选池样本（最多 30 条）+ 统计 |
| `2` | 查看可用池 Top 30 + 统计 |
| `3` | 两个池的统计 JSON |
| `4` | 清空两个池（需输入 `yes` 确认，危险） |
| `0` | 返回主菜单 |

### `3` 校验与筛选

| 数字 | 功能 |
| --- | --- |
| `1` | 校验候选池待检节点 |
| `2` | 立即跑完整闭环（抓取 + 校验 + 升降级） |
| `3` | 仅复检（跳过抓取） |
| `0` | 返回主菜单 |

### `4` 链路与转发

| 数字 | 功能 | 备注 |
| --- | --- | --- |
| `1` | 查看当前链 | 跳序、协议、地址、分数、是否需外部核心 |
| `2` | 组装 / 重组链 | 空池时会提示先抓取并校验 |
| `3` | 设置跳数（无上限） | 写入 `chain.hops` |
| `4` | 切换链模式 | 按序号选 `strict` / `dynamic` / `random` / `round_robin` |
| `5` | 启动本地代理入口 | `socks5://127.0.0.1:<entry_port>`，同时支持 HTTP CONNECT |
| `6` | 停止本地代理入口 | |
| `7` | 启动外部核心（特殊协议） | 缺失二进制时给出安装指引 |
| `8` | 停止外部核心 | |
| `9` | 导出核心配置（dry-run） | 含凭证，勿外传 |
| `10` | 安装外部核心（自动下载 sing-box） | 落盘 `vendor/sing-box/`，成功后自动写 `core.sing_box_path` 并热更新 |
| `0` | 返回主菜单 | |

### `5` 本地网关与 API

| 数字 | 功能 |
| --- | --- |
| `1` | 查看网关状态（running / enabled / 端口 / token 状态 / 后台任务数） |
| `2` | 启动网关（含本地入口），`force=True` 可绕过配置开关；控制台打印完整 token |
| `3` | 停止网关 |
| `4` | 配置开关 true / false（写 `gateway.enabled` 并同步运行态） |
| `5` | 显示 / 轮换 API token |
| `0` | 返回主菜单 |

### `6` 隐匿等级

| 数字 | 功能 |
| --- | --- |
| `1` | 等级 1 Low（单一 UA、无抖动、最大吞吐） |
| `2` | 等级 2 Balanced（默认） |
| `3` | 等级 3 Cautious |
| `4` | 等级 4 Maximum（最低并发 + TLS 指纹，需 `curl_cffi`） |
| `5` | 查看当前等级详情表 |
| `0` | 返回主菜单 |

选择后写入 `crawler.stealth_level` 并立即重建 `FingerprintEngine`。详见 [07](07-stealth-levels.md)。

### `7` 加密 Vault

| 数字 | 功能 |
| --- | --- |
| `1` | 查看状态（state / kdf / cipher / TTL / 可用 KDF） |
| `2` | 初始化（设置主口令，至少 8 位并二次确认） |
| `3` | 解锁 |
| `4` | 锁定（敏感字段不再写盘） |
| `5` | 修改主口令（只重封 DEK，已有密文仍可解密） |
| `0` | 返回主菜单 |

口令输入使用 `getpass`（不回显）。自动化环境可设置 `ANONCHAINS_VAULT_PASSPHRASE` 覆盖；该变量仅用于 CLI 输入，不会写入配置或日志。

### `8` 系统配置与状态

| 数字 | 功能 |
| --- | --- |
| `1` | 综合状态（配置、源、池、链、Vault、网关、核心、调度器） |
| `2` | 查看配置（脱敏） |
| `3` | 修改配置项（`section.option=value`，如 `chain.hops=7`） |
| `4` | 手动热重载配置 |
| `5` | 合规与使用边界提示 |
| `0` | 返回主菜单 |

### `9` 后台调度器

| 数字 | 功能 |
| --- | --- |
| `1` | 启动周期任务（抓取 / 复检 / 轮换） |
| `2` | 停止周期任务 |
| `3` | 立即执行一轮 |
| `4` | 查看上轮 `CycleReport` |
| `0` | 返回主菜单 |

## 4. 非交互命令

| 命令 | 网络 | 说明 |
| --- | --- | --- |
| `status` | 否 | 打印综合状态 JSON |
| `version` | 否 | 打印版本号（不构造 `AppContext`） |
| `sources` | 否 | 列出源清单 |
| `config-show` | 否 | 打印脱敏配置 |
| `vault` | 否 | 打印 Vault 状态 |
| `config-set <section> <option> <value>` | 否 | 写配置并热更新 |
| `stealth <1..4>` | 否 | 设置隐匿等级 |
| `gateway <on\|off\|status>` | 否 | 开关 / 查询网关 |
| `crawl [--source NAME]` | 是 | 抓取（可指定单个源） |
| `validate` | 是 | 仅复检闭环 |
| `cycle` | 是 | 完整闭环 |
| `chain [--hops N]` | 否 | 显示当前链，可先设置跳数 |

示例：

```bash
python -m anonchains version
python -m anonchains stealth 3
python -m anonchains config-set chain hops 9
python -m anonchains gateway status
python -m anonchains crawl --source thespeedx-http
python -m anonchains cycle
```

`gateway off` 是幂等的：当前已关闭时不会写盘、不会报错；`gateway on` 会写配置并按配置启动 REST 与本地入口。

## 5. 典型操作序列

```
python -m anonchains
7 -> 2        # 初始化 Vault
6 -> 2        # 选择隐匿等级（默认）
1 -> 1        # 抓取全部启用源
3 -> 2        # 完整闭环，产生可用池
2 -> 2        # 查看可用池
4 -> 3        # 设置跳数（例如 7）
4 -> 2        # 组链
4 -> 5        # 启动本地入口，应用配置 socks5://127.0.0.1:8791
5 -> 4        # 打开网关开关（可选）
0             # 退出
```

## 6. 菜单扩展约定

`anonchains/cli/menus.py` 定义 `MAIN` 与 `SUBMENUS`（每个子菜单必须有 `0` 返回项），`anonchains/cli/commands.py` 的 `ACTIONS` 映射动作名到异步处理器；`tests/test_cli_offline.py::test_menu_graph_is_complete` 会校验所有 `item.action` 都能命中 `ACTIONS` 或 `SUBMENUS`。新增功能时三处同步修改，并补一个离线用例。
