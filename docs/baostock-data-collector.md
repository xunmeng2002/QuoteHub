# bao_stock.py 基础数据采集使用说明

`bao_stock.py` 是 QuoteHub 的基础数据采集脚本：证券列表、日 K 行情、复权因子、季频财务、除权除息、业绩报告、指数成分、行业分类、交易日历、宏观经济，全部落入 SQLite（`stock_data.db`）。分钟线落 Parquet 管线见 [baostock-parquet-pipeline.md](baostock-parquet-pipeline.md)。

## 目录

- [1 功能总览](#1-功能总览)
- [2 数据表](#2-数据表)
- [3 环境准备](#3-环境准备)
- [4 命令详解](#4-命令详解)
- [5 典型工作流](#5-典型工作流)
- [6 增量与覆盖语义](#6-增量与覆盖语义)
- [7 注意事项与限制](#7-注意事项与限制)

## 1 功能总览

```text
python bao_stock.py [选项]        # 无参数打印用法
```

| 命令 | 内容 | 联网 |
| --- | --- | --- |
| `--update` | 证券同步 + 日 K 行情 + 复权因子（按 config stocks 增量） | 是 |
| `--sync-securities` | 全市场证券列表（新股 / 退市 / 更名 / 停复牌留痕） | 是 |
| `--update-all` | 七步全量：证券 → 行业 → 指数成分 → 日 K → 财务 / 除权 / 业绩 → 宏观 → 交易日历 | 是 |
| `--update-financials` | 6 类季频财务（盈利 / 营运 / 成长 / 偿债 / 现金流 / 杜邦） | 是 |
| `--update-dividends` | 除权除息信息（默认最近 4 年） | 是 |
| `--update-reports` | 业绩快报 + 业绩预告 | 是 |
| `--update-index` | 指数成分股（沪深 300 / 上证 50 / 中证 500） | 是 |
| `--update-industry` | 行业分类 | 是 |
| `--update-trade-dates` | 交易日历 | 是 |
| `--update-macro` | 宏观 4 类：存贷款利率 / 存款准备金率 / 货币供应 | 是 |

## 2 数据表

| 分组 | 表 |
| --- | --- |
| 行情 | `DailyRawPrices`（日 K，不复权）、`AdjustFactors`（前 / 后 / 累计复权因子） |
| 证券 | `Securities`、`NameChangeLogs`（更名）、`TradeStatusLogs`（停复牌） |
| 财务（季频） | `ProfitData`、`OperationData`、`GrowthData`、`BalanceData`、`CashFlowData`、`DuPontData` |
| 公司事件 | `DividendData`、`PerformanceExpress`（快报）、`ForecastReports`（预告） |
| 参考数据 | `IndexConstituents`、`IndustryClassification`、`TradeDates`、`DepositRates`、`LoanRates`、`ReserveRatios`、`MoneySupply` |

分钟线表 `MinuteBars` / `MinuteBarTradingDays` 由 `bao_stock_parquet.py` 使用（同一数据库，见管线文档）。

## 3 环境准备

- 与管线脚本共用环境：在 `D:\Gitee\QuoteHub` 目录下运行（`stock_data.db` 与配置文件均为相对路径），依赖 `baostock`、`pandas`。
- 本脚本与管线脚本 `bao_stock_parquet.py` **共用同一份** `config.json`（见管线文档 §2.3），`stocks` 全仓只此一处。其中的 `stocks` 是日 K / 财务 / 分红等按股票拉取命令的目标清单，**必填**：文件缺失、或键缺失/为空时脚本立即报错退出（`配置文件不存在: config.json` / `配置缺少必填项 stocks（config.json）`），不会静默跳过。与 `stocks` 无关的全市场级命令（`--update-index`、`--update-industry`、`--update-trade-dates`、`--update-macro`）不读配置，不受影响。
- 此处**没有** `--config` 之类的选项：配置文件名固定在代码里（`CONFIG_FILE`），只能靠"在仓根运行"定位。
- `default_start_date` 是库内无历史数据时的下载起点（该项**可省**，代码兜底 `2000-01-01`）。
- 建表自动完成：`init_database` 依次执行 `sql/schema/Create_v1.0.0.sql` 与 `sql/upgrades/` 下全部升级脚本。

## 4 命令详解

### 4.1 `--update`（日常主力命令）

1. 先做一次证券同步（新股自动进入 `Securities`）。
2. 对 config 中每只股票：查库内日 K 最后日期，从次一日起增量下载（不复权，`adjustflag="3"`），追加 `DailyRawPrices`；同一次调用顺带下载复权因子追加 `AdjustFactors`。
3. 库内无该股历史时从 `default_start_date` 全量下载；已最新则跳过。

### 4.2 `--update-all`（一条龙）

按固定顺序执行七步：证券同步 → 行业分类 → 指数成分股 → 日 K 行情（同 4.1 增量）→ 财务 / 除权除息 / 业绩快报预告 → 宏观 4 类 → 交易日历。首次建库用它最省事；日常单跑耗时明显长于 `--update`。

### 4.3 分类命令

`--sync-securities`：用最近交易日（依赖 `TradeDates`，为空则用今天）拉全市场列表，与库内对比——新股插入、退市标记、更名 / 停复牌写入日志表。

`--update-financials`：默认拉**最近 4 个季度**；写入前删除同 `(Code, Year, Quarter)` 旧数据再插入（重跑幂等）。

`--update-dividends`：默认拉**最近 4 年**（当年与之前 3 年）。

`--update-index` / `--update-industry` / `--update-trade-dates` / `--update-macro`：全市场级参考数据，与 config stocks 无关，整表刷新。

## 5 典型工作流

1. **首次建库**：

```text
python bao_stock.py --sync-securities
python bao_stock.py --update-trade-dates
python bao_stock.py --update-all
```

交易日历先建，证券同步的"最近交易日"才有依据。

2. **日常（每日收盘后）**：

```text
python bao_stock.py --update
python bao_stock_parquet.py update
```

前者补日 K 与基本面清单，后者补分钟线并写当日 Parquet。

3. **节奏性维护**：财报季（4 / 8 / 10 月）跑 `--update-financials` 与 `--update-reports`；分红除权密集期（5-7 月）跑 `--update-dividends`；月初跑 `--update-index`、`--update-industry`、`--update-macro`。

## 6 增量与覆盖语义

| 数据 | 策略 |
| --- | --- |
| 日 K 行情 | 按库内最后日期续传，只追加不覆盖；中断重跑无害 |
| 复权因子 | 追加（`if_exists="append"`） |
| 季频财务 | 先删后插同 `(Code, Year, Quarter)`，重跑幂等 |
| 业绩快报 / 预告 | 按最近 4 个季度窗口整段先删后插（`StatDate >= 窗口起点`），年 / 季由统计日期推导 |
| 证券列表 | 与库内全量对比，差异留痕 |
| 参考数据（日历 / 宏观 / 行业 / 指数） | 整表刷新 |

## 7 注意事项与限制

- **历史深度**：CLI 的财务只拉最近 4 个季度、分红只拉最近 4 年；要更早的历史需在代码里给 `update_financial_data` / `update_dividend_data` 传 `years` / `quarters` 参数（CLI 未暴露）。
- **价格不复权**：`DailyRawPrices` 存原始价，除权日前后价格不连续；需要复权价时用 `AdjustFactors` 的前复权 / 后复权因子自行换算。
- 分钟线（5/15/30/60 分钟）不在本脚本范围，走 `bao_stock_parquet.py`。
- 两个脚本共用同一 `stock_data.db`：`bao_stock_parquet.py` 的建表升级脚本由同一 `init_database` 自动应用，互相不影响对方数据。
