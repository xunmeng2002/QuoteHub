# 变更记录

非破坏性变更随提交记录即可；**破坏性变更**（重命名、配置键增删、CLI 参数与错误语义变化、schema 漂移）必须记在这里。

**正式发布前不新起版本号**：改动一律累积进当前未发布版本（现为 `v1.0.0`），到发布时才落定版本号并打 tag。

## v1.0.0（未发布）

首个版本化发布。下列条目相对发布前的开发状态而言是**破坏性**的，按旧版对接的消费方需逐条核对。

### 重命名（对外入口名变更）

- `BaoStock.py` → `bao_stock.py`
- `BaoStockParquet.py` → `bao_stock_parquet.py`

消费方若写死了旧文件名（例如以文件名判断"组件是否在位"，或据此调起下载），必须同步更新。

### 配置

- 两份按脚本命名的配置（`bao_stock.json` / `bao_stock_parquet.json`）**合并回单一 `config.json`**，两脚本共用一份清单。
- **新增 `database_path` 键**：SQLite 库的位置不再写死，改由此键给定（默认值 `stock_data.db`，仍相对**当前工作目录**解析）。
  随之**移除模块级导出符号 `bao_stock.DB_PATH`**——按旧文档写过 `from bao_stock import DB_PATH` 的代码会 ImportError。
  该键与消费方自己的库文件名设定（示例平台为 `QUOTE_HUB_DATABASE_FILENAME`）是一对耦合点，改这个键须两边同改。
- `parquet_output_root` 由代码兜底改为**必填**。
- `default_start_date` 由可省（代码兜底）改为**必填**，且只在 `update` 子命令上要求。
- 四个键一律不兜底：缺了即报错退出，不会回退到任何写死的默认值。
- **除 `--version`、`--help` 与无参数外，所有调用都先读 `config.json`**。原本不读配置的五个命令
  （`bao_stock.py` 的 `--sync-securities` / `--update-index` / `--update-industry` / `--update-trade-dates` / `--update-macro`）
  此后缺 `config.json` 会以退出码 1 结束——库路径本就是每个连库命令都需要的参数，"不读配置"只是路径写死的副作用。
  **未知选项**（如 `--bogus`）原先直接打用法、退出码 0，此后同样先读配置：配置在则行为不变，配置缺失或损坏才改判退出码 1。
  另：`--help` 现在优先于其它选项（`--update --help` 只打用法，不再执行更新）。
  **此条对消费方零可见**：消费方只以子进程方式调 `bao_stock_parquet.py`，不 import 本仓代码。

### 错误语义

- 配置项缺失与配置文件缺失统一走 `require_config_value` / `load_config_file`，信息形如 `配置缺少必填项 <键>（<配置文件>）` 与 `配置文件不存在: <路径>`，退出码 1。
- **`bao_stock.py` 的登录失败由「记 error 后 return」改为 `raise SystemExit`**（退出码 1）：走 `_run_update` 的命令
  （`--update` / `--sync-securities` / `--update-all`，此后扩大到全部命令）此前以退出码 0 收场，与
  「按退出码判断成败的消费方从此能真正看到失败」自相矛盾。至此两个脚本的登录失败都是退出码 1，stderr 含 `登录失败`。
- 管线 `bao_stock_parquet.py` 的登录失败为 `raise SystemExit("BaoStock 登录失败")`，退出码 1——该句含 `登录失败` 子串。
- **结果集不完整由「静默截断」改为「中止」**：分页中断（`10002007` 等）与会话失效（`10001001 用户未登录`）不再把已取到的部分行当全量写库，
  改为抛 `BaostockUpdateAbortedError` 并以**退出码 1** 结束；原先这类失败会打完 `===== 全量更新完成 =====` 以退出码 0 收场，
  按退出码判断成败的消费方从此能真正看到失败。异常基类保持 `RuntimeError`，原有 `except RuntimeError` 的捕获仍有效。
- **管线 `bao_stock_parquet.py` 的取数中断改为「换新 socket 重登录后重试该票」**：`ingest_stock` 捕获会话失效
  （`10001001`）与分页中断（`10002007` 网络接收错误等）后调 `login_with_retry()` 另建连接并重跑该票，上限
  `SESSION_RETRY_ATTEMPTS = 3` 次；次数用尽或重登录失败才抛出，对外行为不变（退出码 1、不入库、不记已问区间）。
  必须换连接才能重试：`send_msg` 出错后既不重连也不清空接收缓冲，残留字节会让此后每次读取都错位。
  接收超时阈值随之由 300 s 调回 **120 s**——有重试兜底后超时不再致命，调小可缩短恢复时间。
  **注意该阈值由两脚本共用**（`enable_socket_timeout` 定义在 `bao_stock.py`，两边都调它），而**重试只在管线侧**：
  `bao_stock.py` 的 `--update` / `--update-all` 仍是无重试的整轮中止，120 s 对它们比 300 s 更容易触发中止
  ——但仍比 D.12 起用、D.18 实跑通的 60 s 宽一倍。
  **对消费方零可见**：仍只以退出码判定成败，新增的 warning 行不参与任何标记匹配。
- **提交粒度变化**：`--update` 由「整轮不提交」改为逐只提交，`--update-all` 改为按步提交（日 K 步另按每只股票提交）。
  中途失败不再丢掉整轮已下载数据，代价是失败后库内可能留有部分批次（重跑幂等）。
- **行业分类 / 指数成分的 `UpdateDate` 改由我方指定**（当日 / 最近交易日），不再让接口的 `updateDate` 顶掉它。
  修复了「当日删除条件匹配不上、重跑必然撞主键」的缺陷；历史遗留的按接口日期写入的行不会被自动清理。
- **`bao_stock_parquet.py` 的 `backfill` 区间覆盖尚未结束的当年时，该年改按月度文件写出**，不再写年度文件。
  年度文件一次覆盖整年，会与之后 `update` 的日度文件、`merge-month` 的月度文件在同一天重叠，而回放 SQL
  按 `Year=*/*.parquet` 扫描且不去重；`merge-month` 的归档模式只认 8 位日度文件，动不了 4 位的年度文件。
  消费方按 `Year=*/*.parquet` 读取时行集不变，仅当年文件粒度由年改为月。
  已结束年度仍写年度文件，且不会归档该年已有的月度文件（本命令不动既有文件）——该年已有月度文件时应改用 `merge-year`。

### 缺陷修复

- **十处「先 DELETE 再查询」全部改为「确认有数据再 DELETE」**：交易日历与指数成分股两处之外，
  还有财务单键表（`_fetch_and_store_financial` / `DividendData` / `PerformanceExpress` / `ForecastReports`）
  与四张「清空重取」的宏观表（`DepositRates` / `LoanRates` / `ReserveRatios` / `MoneySupply`）。
  旧顺序下上游返回 `err=0` + 0 行会提前返回，而调用方的 `commit()` 随后落盘，等于把既有数据**清空却报成功**
  （宏观表更是整表清空）。现在 0 行只记 warning 并保留库内现有数据。
- 回补 / `--update` 相关文档与注释里「约 0.4% 的固有口径差」的说法纠正为实测事实：分钟量与日线量在
  2020–2022 与 2026 逐日精确相等，仅 2023–2025 有 1%~7% 的偏离。

### 数据库

- **应用不再建表、不再迁移**：`bao_stock.py` 与 `bao_stock_parquet.py` 启动只连接、只增删改查。
  库未初始化或版本不符即以**退出码 1** 报错退出（且发生在联网登录之前），stderr 指向 `init_database.py`。
  **依赖"组件首次连接时自动建表"的直调方与消费方会被打破**——部署流程须先跑 `python init_database.py --create`，这是本次唯一需跨仓同步的动作。
- **删除公开导出符号 `bao_stock.init_database`**（连同代码侧那 10 行 `DROP TABLE PerformanceExpress` 迁移逻辑）；
  按旧文档写过 `from bao_stock import init_database` 的代码会 ImportError。建库与升级改由**新增的仓根脚本 `init_database.py`** 承担
  （`--create` 建新库、`--upgrade` 认领或升级既有库），它是本仓唯一执行 DDL 的入口。
- **schema 收敛为单一 `sql/schema/Create_v1.0.0.sql`**（24 张表 + 2 个索引的全量最终态），删除 `Create_v2.0.0.sql`
  与 `Update_v2.0.0.sql`~`Update_v2.3.0.sql` 共 5 个历史脚本。版本号由 v2.3.0 **回退**为 v1.0.0，与组件版本号对齐——发布前的版本切分对外没有含义；
  `sql/upgrades/` 保留同样的升级机制，但只在**正式发布时**才新增脚本。
- **新增版本戳**：建库时把 `Create_v1.0.0.sql` 内容的 CRC32（行尾归一为 LF 后计算，有符号 int32）写进 `PRAGMA user_version`，启动时**精确相等**比对，`0` 视为未打戳。
  改动该 SQL 文件（哪怕只改注释）即改戳，既有库须重跑 `python init_database.py --upgrade` 重新打戳。

### 内部

- `update_all` / `--sync-securities` 的「登录 → 中止转换 → 注销」编排收敛到 `_run_update` 一处（原先三份几乎相同的脚手架）。
- `bao_stock.py` 入口的配置加载由每个命令分支各写一遍收敛为一个加载点；用法文案抽为 `_log_usage()`。
- 新增回归用例：登录失败退出码、中止路径的注销与未提交回滚、上游 0 行不得清空日历 / 成分股 / 财务单键表 / 宏观表、回补当年写月度文件、
  缺 `database_path` 在登录之前报错、`connect_database` 按配置路径开库并拒绝未初始化的库、版本戳的 CRC32 有符号往返与换行归一。

### 新增

- `version.txt`：版本号的唯一来源，`python bao_stock.py --version` 打印它。
- `tests/`：pytest 用例，覆盖纯函数、结果集收集分支与临时 SQLite 落库回归（不碰网络、不碰仓内真实库）。
