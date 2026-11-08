# 12 测试与验收

## 1. 测试原则

- **离线优先**：`tests/` 中所有用例不访问公网。网络交互通过本地 socket、内存 SQLite、临时目录、mock / fixture 完成。
- **隔离写入**：`conftest.py::config_copy` 把 `config/anonchains.conf` 复制到 `tmp_path`，写回测试绝不污染仓库配置；`vault_path`、`isolated` 同理。
- **真实路径覆盖**：级联转发与本地入口使用本机回环 socket 做端到端测试，而不是纯 mock。
- **安全不变量测试**：Vault 锁定 / 禁用时，数据库文件中不得出现明文凭证。

运行：

```bash
python -m pip install -r requirements-optional.txt   # 含 pytest
python -m pytest tests -q
python -m pytest tests -q --tb=short                 # 失败时看短回溯
python -m pytest tests/test_cli_offline.py -q        # 只跑某一组
```

也可一键运行（自动检测/安装依赖，详见 [15-scripts.md](15-scripts.md)）：

```powershell
scripts\test.bat                # Windows cmd
.\scripts\test.ps1              # Windows PowerShell
```

```bash
./scripts/test.sh               # Linux / macOS
```

`pyproject.toml` 中 `testpaths = ["tests"]`、`addopts = "-q"`；测试内部多用 `asyncio.run()`，因此 `pytest-asyncio` 是可选依赖。

## 2. 用例文件索引

| 文件 | 覆盖内容 |
| --- | --- |
| `conftest.py` | 临时目录、配置副本、源清单、Vault 路径夹具 |
| `test_parser.py` | plain / JSON / Clash YAML / base64 订阅 / SIP002、协议 hint、私有地址过滤、去重与单源上限 |
| `test_stealth.py` | UA 池分布、头部一致性、等级策略表、抖动边界、可选 TLS 指纹降级 |
| `test_crypto_vault.py` | KDF 参数、AEAD 加解密、篡改检测、Vault 初始化/解锁/锁定/轮换、锁定降级 |
| `test_config.py` | 默认值、注释保留写回、非法值回滚、环境变量覆盖、回环校验、无硬跳数上限、幂等写与未知项报错 |
| `test_sources.py` | 内置源数量与协议覆盖、启用/禁用、自定义覆盖、Crawler 构造与汇总 |
| `test_pools.py` | 双池 upsert、`sealed` 密文、锁定 Vault 安全降级、TTL stale、评分排序、前置选取、统计分组 |
| `test_validator.py` | 出口 IP 提取、匿名度分级、评分边界、统计更新、外部协议跳过、批量汇总、调度升降级与淘汰 |
| `test_chain.py` | 5 跳、120 跳（allow_reuse）、strict / dynamic / round_robin、无凭证序列化 |
| `test_core_adapter.py` | sing-box `detour` 链、Xray `dialerProxy` 链、不支持协议 warning、outbound 字段映射、二进制缺失降级 |
| `test_gateway.py` | REST 鉴权、健康检查免鉴权、配置白名单（拒绝 `gateway.host=0.0.0.0`）、空池组链 409、配置开关、临时 token 不落明文 |
| `test_cli_offline.py` | 菜单图完整性、version、离线 status/sources/config/vault、配置与隐匿等级持久化、网关开关、交互退出路径、argparse choices |
| `test_progress.py` | 进度条百分比渲染、相对路径脱敏、状态输出路径、CLI 抓取进度、抓取/校验进度回调 |
| `test_traffic.py` | 响应上限配置与校验、压缩协商、并发重复下载合并 |

另有两个真实 socket 级用例位于 `test_chain.py` / `test_core_adapter.py` 相关测试：两跳级联与本地 SOCKS5 入口端到端。

## 3. 最小可运行验证（不联网）

按顺序执行，全部应成功退出（退出码 0）：

```bash
python -m anonchains --help
python -m anonchains version
python -m anonchains status
python -m anonchains sources
python -m anonchains config-show
python -m anonchains vault
python -m anonchains gateway status
python -m pytest tests -q
```

Windows PowerShell 等价写法为 `python -m anonchains status` 等，无需改命令。交互菜单可用管道输入退出：

```bash
echo 0 | python -m anonchains       # Linux/macOS
"0" | python -m anonchains          # PowerShell（无 TTY 自动纯文本）
```

写回类验证请使用隔离配置，避免改动仓库内的 `config/anonchains.conf`：

```bash
python -m anonchains --config <临时配置副本> --base-dir <临时目录> config-set chain hops 7
python -m anonchains --config <临时配置副本> --base-dir <临时目录> stealth 3
python -m anonchains --config <临时配置副本> --base-dir <临时目录> gateway off
```

## 4. 需要联网的验收（可选、手动）

| 步骤 | 命令 / 菜单 | 期望 |
| --- | --- | --- |
| 抓取 | `1 -> 1` 或 `anonchains crawl` | 汇总表显示各源 OK / FAIL / BLOCKED，节点入候选池 |
| 闭环 | `3 -> 2` 或 `anonchains cycle` | `CycleReport` 给出 validation / promoted / demoted / removed |
| 组链 | `4 -> 2` | 输出 N 跳摘要（不含凭证） |
| 本地入口 | `4 -> 5` 后 `curl -x socks5h://127.0.0.1:8791 https://api.ipify.org?format=json` | 返回出口 IP |
| 网关 | `5 -> 4` 打开，`GET /api/health` 无需 token，`GET /api/status` 需 token | 分别返回 200 与 401/200 |
| 外部核心 | `4 -> 9` dry-run 导出，`4 -> 7` 启动 | 配置字段正确；缺二进制时给出安装指引 |

免费节点质量波动大，联网验收允许部分源 FAIL 或可用池为空；这属于外部依赖问题，不是代码缺陷。

## 5. 验收清单

- [ ] `python -m pytest tests -q` 全绿（当前 76 个用例）；
- [ ] 静态检查无 error 级诊断（`read_lints` / IDE 诊断）；
- [ ] `version` / `status` / `sources` / `config-show` / `vault` 离线可运行；
- [ ] 主菜单 1–9 均可进入子菜单，每个子菜单 `0` 可返回，主菜单 `0` 可退出；
- [ ] 隐匿等级 1–4 可切换并写入配置；无效等级被 argparse 拒绝；
- [ ] `chain.hops = 500` 通过校验（无硬上限），`hops = 0` 被拒绝；
- [ ] `gateway.host = 0.0.0.0` 被 schema 与 REST 白名单双重拒绝；
- [ ] Vault 锁定时 `pools/*.db` 中检索不到明文凭证；
- [ ] `gateway off` 在已关闭状态下幂等成功；
- [ ] 外部核心缺失时不静默直连，返回安装指引；
- [ ] `docs/` 与代码行为一致（配置字段、菜单编号、REST 路由、协议矩阵）。

## 6. 新增功能的测试要求

1. 至少 1 个离线用例；禁止在 CI 中依赖公网列表或 echo 站点。
2. 涉及落盘的用例使用 `tmp_path`；涉及配置的用例使用 `config_copy`。
3. 涉及凭证的用例断言密文 / 不落明文，并检查 `to_dict()` 默认输出。
4. 新增菜单项同步通过 `test_menu_graph_is_complete`；新增 REST 路由补鉴权与白名单用例。
5. 新增协议补解析 + outbound 映射 + `UnsupportedProtocol` 语义用例。

## 7. 已知限制

- 单元测试不覆盖真实公共源站点可用性与免费节点质量，这些通过手动联网验收观察。
- TLS 指纹伪装用例在缺 `curl_cffi` 时验证的是降级路径，而非真实浏览器指纹一致性。
- 超长链（120 跳）测试验证组装与序列化，不建立 120 个真实公网连接。
