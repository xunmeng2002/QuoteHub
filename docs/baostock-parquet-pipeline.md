# BaoStock 分钟线回测数据管线使用说明

`BaoStockParquet.py` 将证券宝（BaoStock）的 5/15/30/60 分钟 K 线落入 SQLite（事实源），并按日 / 月 / 年三代 Parquet 文件导出，供 QuantTrading 回测引擎的 Bar 撮合模式消费。日线与基本面数据采集（`BaoStock.py`）见 [baostock-data-collector.md](baostock-data-collector.md)。

## 目录

- [1 数据流与文件布局](#1-数据流与文件布局)
- [2 环境准备](#2-环境准备)
- [3 子命令](#3-子命令)
- [4 典型工作流](#4-典型工作流)
- [5 回测接入（QuantTrading 侧）](#5-回测接入quanttrading-侧)
- [6 幂等性与归档](#6-幂等性与归档)
- [7 已知数据口径与限制](#7-已知数据口径与限制)

## 1 数据流与文件布局

```text
BaoStock（只读增量拉取）
        │
        ▼
SQLite（stock_data.db，唯一事实源，主键 UPSERT 幂等）
        │
        ▼ 纯数据库再导出（不做文件侧合并）
<output_root>/Bar/Identity=<EXCH>.Stock/Year=<YYYY>/
    YYYYMMDD_5m.parquet   日度：update 每个交易日新增一个文件（当月只增不改）
    YYYYMM_5m.parquet     月度：merge-month 次月初整月导出，日度文件移入 archive/
    YYYY_5m.parquet       年度：merge-year 次年初整年导出，月度/日度文件移入 archive/
    archive/              被合并取代的上一代文件（回测不扫描，不删除）
```

- 文件名带频率后缀（`_5m` / `_15m` / `_30m` / `_60m`），四种频率可并存于同一目录。
- Parquet 文件不可修改，因此当月只新增日度文件；合并一律从数据库整段重新导出。
- **写入粒度是"交易所 × 时间段"**：一个 Parquet 文件 = 一个交易所的一个周期段（日 / 月 / 年），文件内包含本次命令代码清单（缺省 = `config.json` 全部）中该交易所所有代码的 bar，用 `InstrumentID` 列区分个股。不存在按单合约写文件的模式。

## 2 环境准备

### 2.1 依赖

```text
pip install baostock pandas pyarrow
```

### 2.2 运行目录

> **注意**：必须在 `D:\Gitee\QuoteHub` 目录下运行。SQLite 路径是相对路径 `stock_data.db`，换目录运行会另建一个空库。

### 2.3 配置文件（config.json）

| 键 | 说明 | 缺省 |
| --- | --- | --- |
| `stocks` | 证券代码列表，`sh.` / `sz.` 前缀 + 6 位代码 | 无（必填） |
| `default_start_date` | 库中无数据时的默认回补起点 | 代码兜底 `2015-01-01` |
| `parquet_output_root` | Parquet 输出根目录 | `D:/MdBaoStock` |

```json
{
  "stocks": ["sh.600000", "sh.600519", "sz.000001"],
  "default_start_date": "2020-01-01"
}
```

建表由 `BaoStock.init_database` 自动完成（`sql/upgrades/Update_v2.1.0.sql`），首次连接即生效，无需手工执行 SQL。

## 3 子命令

四个子命令共用参数：`--codes`（逗号分隔，缺省取配置 `stocks`）、`--frequency`（`5/15/30/60`，缺省 `5`）、`--output-root`、`--config`。

### 3.1 update —— 日常增量

```text
python BaoStockParquet.py update
python BaoStockParquet.py update --start 2024-11-01 --end 2024-11-15
```

- 每只代码从库中最后交易日续传拉取到今天，入库后为每个有数据的交易日写一个独立日度文件（包含该交易所全部配置代码）。
- 续传起点是库中最后交易日本身（而非次一日）：当日重复拉取无害（主键 UPSERT），可自愈上次中断留下的半截数据。
- 显式 `--start` 时对所有代码强制从该日起拉取。
- 需要 BaoStock 登录（联网）。

### 3.2 merge-month —— 月度合并

```text
python BaoStockParquet.py merge-month               # 缺省上月
python BaoStockParquet.py merge-month --month 202411
```

- 从库导出整月写 `YYYYMM_5m.parquet`，随后把该月全部日度文件移入 `Year=YYYY/archive/`。
- 不需要联网（只读数据库）。
- 建议每月初合并上一个自然月。

### 3.3 merge-year —— 年度合并

```text
python BaoStockParquet.py merge-year                # 缺省去年
python BaoStockParquet.py merge-year --year 2024
```

- 从库导出整年写 `YYYY_5m.parquet`，把该年全部月度文件与残留日度文件移入 archive。
- 不需要联网（只读数据库）。
- 建议每年初合并上一个自然年。

### 3.4 backfill —— 历史回补

```text
python BaoStockParquet.py backfill --start 2024-01-01 --end 2024-12-31
```

- 区间数据入库，并直接按年写出年度文件（不产生日度文件），仅触及回补区间相交的年份。
- 需要联网。首次建库或补历史缺口时使用。

## 4 典型工作流

每条命令都是"下载 → 入库 → 写 Parquet"三段中的某几段：

| 时机 | 命令 | 下载（联网） | 入库（SQLite） | 写 Parquet |
| --- | --- | --- | --- | --- |
| 首次建库 / 补历史 | `backfill --start … --end …` | 全区间分钟 + 日线 | UPSERT 全区间 | 直接写年度文件（不产生日度文件） |
| 每日收盘后 | `update` | 库中最后交易日 → 今天 | UPSERT 增量 | 每个交易日一个日度文件 |
| 每月初 | `merge-month` | 不联网 | 无 | 整月重写月度文件，日度文件入 archive |
| 每年初 | `merge-year` | 不联网 | 无 | 整年重写年度文件，月度 / 残留日度入 archive |

1. **首次建库**：配置 `config.json` 的 stocks，执行 `backfill --start <起始日>`（`--end` 缺省今天）。
2. **每日收盘后**：执行 `update`（自动续传；节假日无数据自然跳过）。
3. **每月初**：执行 `merge-month`（缺省即上月）。
4. **每年初**：执行 `merge-year`（缺省即去年）。
5. **换频率**：`--frequency 15` 再走一遍建库流程即可，文件按后缀与 5 分钟数据并存；但同一回测根目录内不要混放多频率（见第 5 节）。

> **注意**：导出写文件以交易所为单位——若用 `--codes` 只传一只，写出的文件会被重写为只含这一只（该交易所其他代码的行被挤出文件，库里不丢）。加新股票请先进 `config.json` 再跑全集，保持"文件 = 配置全集的再导出"。

## 5 回测接入（QuantTrading 侧）

回测运行配置（`QuantTrading/bin/Debug/BackTest.json`）改三处：

```json
{
  "MatchMode": 3,
  "MdDataPath": "D:\\MdBaoStock",
  "StartTradingDay": "20241001",
  "EndTradingDay": "20241231"
}
```

订阅配置（`TestBackTest.json` 等）使用交易所 + 6 位代码：

```json
{
  "ExchangeID": "SSE",
  "InstrumentID": "600519"
}
```

> **警告**：Bar 回放 SQL 按 `Identity=<交易所>.*/Year=*/*.parquet` 匹配且不按频率（Preces）过滤。同一 `MdDataPath` 下不要混放多种频率的数据文件（`archive/` 层不受影响），否则不同周期的 bar 会混入同一次回放。

| 配置项 | 取值 |
| --- | --- |
| `MatchMode` | `3`（Bar 撮合） |
| `ExchangeID` | `SSE`（sh.）/ `SZSE`（sz.） |
| `InstrumentID` | 6 位证券代码，如 `600519` |

撮合消费 High/Low/Close 三价（市价按 (H+L)/2），结算价取当日最后一根 bar 的 Close。

## 6 幂等性与归档

- **入库幂等**：`MinuteBars` 主键（Code, Frequency, Time）、`MinuteBarTradingDays` 主键（Code, TradingDay），重复拉取同一天只保留最后一条。
- **导出幂等**：合并 / 回补重跑只是从库重写同名文件，行数与内容稳定。
- **归档不删除**：被合并取代的文件重命名进 `Year=YYYY/archive/`，回测 glob 只扫描 `Year=*` 下一层，archive 内容不参与回放；archive 内已有同名文件时两侧均保留并告警。
- **覆盖收缩告警**：重写既有文件前若发现既有数据范围超出本次导出区间、或库中缺少既有文件两端的天数，会打告警日志（不阻断）——通常意味着库还没补全就执行了合并，先 `update`/`backfill` 补库。

## 7 已知数据口径与限制

- BaoStock 仅覆盖沪深 A 股与部分指数：无期货 / 期权，无 tick 与 1 分钟线，指数无分钟线。
- 分钟量合计与日线量存在约 0.4% 的固有口径差（大宗交易、集合竞价归属），单日偏差超过 1% 才打告警；导出以分钟线为准。
- 停牌 / 零成交日仍会产出 OHLC 等于前收、成交量为 0 的平价 bar；Bar 撮合无量门控，会按平价成交——数据保真优先，暂不剔除。
- 分钟线 `time` 为 bar 结束时间，导出截到分钟（`YYYYMMDDHHMM`）写入 `UpdateTs`；首根 bar 的 `PreClosePrice` 取日线 `preclose` 回填。
