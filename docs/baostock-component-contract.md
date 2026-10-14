# 行情组件契约

本文件是 QuoteHub 作为**被消费组件**对外承诺的接口。消费方只通过两个界面接触本仓：**读它的 SQLite 库**与**跑它的命令行**；不 import 它的代码，也不写它的任何文件。

契约一经变更即为**破坏性变更**，须在 [CHANGELOG](../CHANGELOG.md) 记录（发布前累积进未发布版本，发布后才升版本号）。

## 1 自足前提

- **必须在仓根作为当前工作目录被调用**。`config.json` 与它给出的 `database_path` 都是相对文件名，换目录运行会安静地另建一个空库——症状是"跑完才发现没数据"。
- 不依赖仓外的任何路径，也不与任何兄弟仓耦合。
- 消费方应始终显式传 `--output-root`；不传会回落到本仓 `config.json` 里的 `parquet_output_root`。

## 2 界面一：SQLite 库

库文件由本仓 `config.json` 的 `database_path` 给定，缺省值即仓根 `stock_data.db`。消费方以**只读**方式打开。

> **消费前置**：库须先由维护侧在仓根跑 `python init_database.py --create` 建好（既有库改跑 `--upgrade` 认领）。
> 组件**不建表、不迁移**，库未建好或版本不符时以退出码非 0 报错，stderr 指向该脚本。这是本次唯一需消费方同步的部署动作。

> **注意**：消费方按自己的库文件名设定（示例平台为 `QUOTE_HUB_DATABASE_FILENAME`）打开文件，本仓按 `database_path` 写文件，
> **两者须指同一个文件**。默认值下两侧都是 `stock_data.db`，契约本身不变；改任一侧都要同步改另一侧，否则消费方会安静地打开另一个（空）库。

> **注意**：`database_path` 相对**当前工作目录**解析（`version.txt` 是例外，它走模块所在目录）。故以绝对路径传 `--config` 时，库仍落在调用它的工作目录下，不在配置文件旁。

### 2.1 必需的表与列

三张表全部由 `sql/schema/Create_v1.0.0.sql` 建出——本仓 schema 是**单文件全量基线**，不再有版本递进。

| 表 | 必需列 |
| ---- | ---- |
| `Securities` | `Code`, `CodeName`, `StockType`, `Exchange`, `CurrentTradeStatus` |
| `TradeDates` | `CalendarDate`, `IsTradingDay` |
| `MinuteBars` | `Code`, `Frequency`, `TradingDay` |

### 2.2 可选表

| 表 | 列 | 缺失时的行为 |
| ---- | ---- | ---- |
| `QueriedBarSpans` | `Code`, `Frequency`, `StartDay`, `EndDay` | 退化为"一段都没问过"，每次都重取 |

`QueriedBarSpans` 记的是"向上游问成功了哪一段"，**不是**"哪几天真有 bar"：停牌日与未上市日上游根本不返回数据，只按 bar 记账的缺口永远补不上，含停牌日的区间会每次重下、永不收敛。"多取只是慢，少取才是错"，故缺这张表只降级不报错。

### 2.3 表结构漂移

必需表缺失、或必需列不齐，消费方一律显式报错，**不**当成"没数据"。故本仓升级 schema 时必须同步通知消费方。

本仓建库时把 `Create_v1.0.0.sql` 内容的 CRC32（行尾归一为 LF 后计算，有符号 int32）写进 `PRAGMA user_version`，**0 表示未打戳**。
每次连库都以**精确相等**核对它，不用"大于等于"——列序是契约，库比代码要求的更新、多一列，照样静默错位；同时核对 schema 内全部 24 张表是否都在。
不符即以退出码非 0 报错，stderr 指向 `init_database.py --create` / `--upgrade`。

故改动 `Create_v1.0.0.sql`（哪怕只改注释）即改戳，既有库须重跑 `python init_database.py --upgrade` 重新打戳。
这是本仓改 schema 后消费方**唯一**需要留意的动作——组件自己不会补，也不会迁移。

## 3 界面二：命令行

### 3.1 入口

`bao_stock_parquet.py`——消费方实际只调它。`python bao_stock_parquet.py --version` 打印版本号。

### 3.2 消费方使用的唯一子命令

```text
python bao_stock_parquet.py backfill --start <YYYY-MM-DD> --end <YYYY-MM-DD> --frequency 5 --codes <逗号分隔的全集> --output-root <输出根>
```

| 参数 | 约束 |
| ---- | ---- |
| `--start` / `--end` | `--start` 必填；`--end` **可省**（缺省今天），消费方应显式传；日期形如 `YYYY-MM-DD` |
| `--frequency` | `5` / `15` / `30` / `60`，缺省 `5` |
| `--codes` | 省略会回落到配置的 `stocks`；消费方**应显式传全集**——导出按交易所整文件重写，少传一个就会把该合约从文件里挤掉（库里不丢） |
| `--output-root` | **必须显式传**：不传会落到本仓 `config.json` 里的值 |

选 `backfill` 而非 `update`：前者按区间直接写年度文件、不产生日度文件，与输出根现有的年度文件同构。若区间落入**尚未结束的当年**，该年改按月度文件写出（年度文件会与后续 `update`/`merge-month` 的日度、月度文件重复覆盖同一天，而回放 SQL 不去重）；当年的年度文件由次年的 `merge-year` 生成。另：`backfill` **不改动既有文件**，若某已结束年度已有 `merge-month` 产出的月度文件，再回补该年会让年度与月度文件同时被 `Year=*/*.parquet` 扫到，此时应改调 `merge-year`。

### 3.3 退出码与错误识别

| 情形 | 表现 |
| ---- | ---- |
| 成功 | 退出码 0。**但 0 不代表有数据**——库中（或所请求区间）无数据时组件既不告警也不报错，只是没有产出文件；仅 `merge-*` 会为此记一条 warning |
| schema 未初始化 / 不符 | 退出码非 0，且 **stderr 含子串 `init_database.py`**。**在联网登录之前就会退出**，故这一类的失败不消耗上游会话 |
| 登录失败 | 退出码非 0，且 **stderr 含子串 `登录失败`**（原句为 `BaoStock 登录失败`）。**该子串不能单独用于判定**，见下方注意 |
| 其它失败 | 退出码非 0，stderr 无 `登录失败` 标记 |

除 `init_database.py` 与 `登录失败` 外，消费方不解析本组件的任何输出。

上游抖动（分页中断、接收超时）由组件自行**换新连接重试该票**（上限 3 次），仍失败才以退出码非 0 结束——
消费方**无需**为这类抖动自建重试循环，退出码非 0 即代表重试已用尽。

> **注意**：`登录失败` **不是**登录失败的专属标记。`login_with_retry` 每次重试失败都会往 stderr 打一行
> `登录失败（尝试 n/m）`，若首次失败、第二次成功，进程**以退出码 0 正常结束**而 stderr 已含该子串。
> 故消费方必须**先看退出码**，`登录失败` 只用于给失败分类，不能替代退出码判定。

## 4 输出产物

导出根下按 `Identity=<板块>/Year=<年>/` 存放 `<年>_<频率>.parquet`：

```text
Bar/Identity=SSE.Stock/Year=2024/2024_5m.parquet
Bar/Identity=SZSE.Stock/Year=2024/2024_5m.parquet
```

- 板块目录名：`SSE.Stock` 对应 `sh.` 前缀，`SZSE.Stock` 对应 `sz.` 前缀。
- 文件内以 `InstrumentID` 列（6 位代码）区分个股。
- **写入粒度是"交易所 × 时间段"**：一个文件 = 一个交易所的一个周期段，不存在按单合约写文件的模式。
- `archive/` 子目录存放被合并取代的上一代文件，回测不扫描、不删除。

## 5 本组件不做的事

- 不提供 tick / 1 分钟 / 盘口数据（上游 BaoStock 不提供）。
- 不在被调用之外主动写输出根下的任何文件。
- 不远程暴露接口；消费方式只有"库 + 命令行"。

## 6 相关文档

- [数据采集器](baostock-data-collector.md)
- [Parquet 管线](baostock-parquet-pipeline.md)
