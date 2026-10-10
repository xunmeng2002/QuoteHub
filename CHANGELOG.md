# 变更记录

非破坏性变更随提交记录即可；**破坏性变更**（重命名、配置键增删、CLI 参数与错误语义变化、schema 漂移）必须记在这里并升版本号。

## v1.0.0（2026-10-10）

首个版本化发布。下列条目相对发布前的开发状态而言是**破坏性**的，按旧版对接的消费方需逐条核对。

### 重命名（对外入口名变更）

- `BaoStock.py` → `bao_stock.py`
- `BaoStockParquet.py` → `bao_stock_parquet.py`

消费方若写死了旧文件名（例如以文件名判断"组件是否在位"，或据此调起下载），必须同步更新。

### 配置

- 两份按脚本命名的配置（`bao_stock.json` / `bao_stock_parquet.json`）**合并回单一 `config.json`**，两脚本共用一份清单。
- `parquet_output_root` 由代码兜底改为**必填**。
- `default_start_date` 由可省（代码兜底）改为**必填**，且只在 `update` 子命令上要求。
- 三个键一律不兜底：缺了即报错退出，不会回退到任何写死的默认值。

### 错误语义

- 配置项缺失与配置文件缺失统一走 `require_config_value` / `load_config_file`，信息形如 `配置缺少必填项 <键>（<配置文件>）` 与 `配置文件不存在: <路径>`，退出码 1。
- 管线 `bao_stock_parquet.py` 的登录失败仍为 `raise SystemExit("BaoStock 登录失败")`，退出码 1——
  该句含 `登录失败` 子串，消费方的失败分类依据不变。
- **注**：`bao_stock.py` 的登录失败一直是「记 error 后 `return`」、退出码 0，与管线不同。
- **结果集不完整由「静默截断」改为「中止」**：分页中断（`10002007` 等）与会话失效（`10001001 用户未登录`）不再把已取到的部分行当全量写库，
  改为抛 `BaostockUpdateAbortedError` 并以**退出码 1** 结束；原先这类失败会打完 `===== 全量更新完成 =====` 以退出码 0 收场，
  按退出码判断成败的消费方从此能真正看到失败。异常基类保持 `RuntimeError`，原有 `except RuntimeError` 的捕获仍有效。
- **提交粒度变化**：`--update` 由「整轮不提交」改为逐只提交，`--update-all` 改为按步提交（日 K 步另按每只股票提交）。
  中途失败不再丢掉整轮已下载数据，代价是失败后库内可能留有部分批次（重跑幂等）。
- **行业分类 / 指数成分的 `UpdateDate` 改由我方指定**（当日 / 最近交易日），不再让接口的 `updateDate` 顶掉它。
  修复了「当日删除条件匹配不上、重跑必然撞主键」的缺陷；历史遗留的按接口日期写入的行不会被自动清理。

### 数据库

- schema 累计到 v2.3.0：`MinuteBars` 自 v2.1.0、`QueriedBarSpans` 自 v2.3.0，由 `init_database` 在首次连接时自动应用，无需手工执行 SQL。

### 新增

- `version.txt`：版本号的唯一来源，`python bao_stock.py --version` 打印它。
- `tests/`：pytest 用例，覆盖纯函数、结果集收集分支与临时 SQLite 落库回归（不碰网络、不碰真实库）。

## v1.0.1（2026-10-10）

### 错误语义（破坏性）

- `bao_stock.py` 的**登录失败由「记 error 后 return」改为 `raise SystemExit`**（退出码 1）：三处入口
  （`--update` / `--sync-securities` / `--update-all` 共用的 `_run_update`）此前以退出码 0 收场，与 v1.0.0 宣称的
  「按退出码判断成败的消费方从此能真正看到失败」自相矛盾。至此两个脚本的登录失败都是退出码 1，stderr 含 `登录失败`。
- `bao_stock_parquet.py` 的 `backfill` 在区间覆盖**尚未结束的当年**时，该年**改按月度文件写出**，不再写年度文件。
  年度文件一次覆盖整年，会与之后 `update` 的日度文件、`merge-month` 的月度文件在同一天重叠，而回放 SQL
  按 `Year=*/*.parquet` 扫描且不去重；`merge-month` 的归档模式只认 8 位日度文件，动不了 4 位的年度文件。
  消费方按 `Year=*/*.parquet` 读取时行集不变，仅当年文件粒度由年改为月。
  已结束年度仍写年度文件，且不会归档该年已有的月度文件（本命令不动既有文件）——该年已有月度文件时应改用 `merge-year`。

### 缺陷修复

- `bao_stock.py` 里**十处「先 DELETE 再查询」全部改为「确认有数据再 DELETE」**：交易日历与指数成分股两处之外，
  还有财务单键表（`_fetch_and_store_financial` / `DividendData` / `PerformanceExpress` / `ForecastReports`）
  与四张「清空重取」的宏观表（`DepositRates` / `LoanRates` / `ReserveRatios` / `MoneySupply`）。
  旧顺序下上游返回 `err=0` + 0 行会提前返回，而调用方的 `commit()` 随后落盘，等于把既有数据**清空却报成功**
  （宏观表更是整表清空）。现在 0 行只记 warning 并保留库内现有数据。
- 回补/`--update` 相关文档与注释里「约 0.4% 的固有口径差」的说法纠正为实测事实：分钟量与日线量在
  2020–2022 与 2026 逐日精确相等，仅 2023–2025 有 1%~7% 的偏离。

### 内部

- `update_all` / `--sync-securities` 的「登录 → 中止转换 → 注销」编排收敛到 `_run_update` 一处（原先三份几乎相同的脚手架）。
- 新增回归用例：登录失败退出码、中止路径的注销与未提交回滚、上游 0 行不得清空日历 / 成分股 / 财务单键表 / 宏观表、回补当年写月度文件。
