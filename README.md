# QuoteHub

BaoStock 行情采集组件：把证券宝（BaoStock）的日线、基本面与 5/15/30/60 分钟 K 线落入本地 SQLite（事实源），并把分钟线按日 / 月 / 年三代导出为 Parquet，供回测引擎与量化平台消费。

本仓是一个**独立组件**：消费方只读它的 SQLite 库、以子进程方式跑它的命令行，不 import 它的代码、也不写它的任何文件。完整契约见[组件契约](docs/baostock-component-contract.md)。

## 组成

| 文件 | 职责 |
| ---- | ---- |
| `bao_stock.py` | 日线、复权因子、财务、分红、行业、指数成分、宏观、交易日历 |
| `bao_stock_parquet.py` | 分钟线入库，并导出日 / 月 / 年三代 Parquet |
| `sql/` | 建表与升级脚本，由 `init_database` 自动应用 |
| `config.json` | 两个脚本共用的配置 |
| `version.txt` | 版本号的唯一来源 |

两个脚本共用同一个 `stock_data.db`。

## 安装

本项目在 Python 3.14.7 上验证；其他版本未做兼容性验证。

```text
pip install -r requirements.txt
```

## 配置

`config.json` 三个键，一律不兜底——**用到它的命令**上缺了即报错退出（不是每个命令都读，见下表右列）。

| 键 | 说明 | 在哪些命令上必填 |
| ---- | ---- | ---- |
| `stocks` | 证券代码列表，`sh.` / `sz.` 前缀 + 6 位代码 | 见下方说明 |
| `default_start_date` | 库中无数据时的回补起点 | 管线 `update`；`bao_stock.py` 的 `--update` / `--update-all` |
| `parquet_output_root` | Parquet 输出根，**相对当前工作目录**解析 | 管线四个子命令（`--output-root` 可替代） |

`stocks` 的适用范围：管线四个子命令（`backfill` / `update` / `merge-month` / `merge-year`），以及
`bao_stock.py` 的 `--update` / `--update-all` / `--update-financials` / `--update-dividends` /
`--update-reports`。`--codes` 只在管线上可用。其余命令（`--sync-securities` / `--update-trade-dates` /
`--update-index` / `--update-industry` / `--update-macro` / `--version`）不读配置。

## 快速开始

**必须在仓根运行**——`stock_data.db` 与 `config.json` 都是相对路径，换目录会另建一个空库。

首次建库：

```text
python bao_stock.py --sync-securities
python bao_stock.py --update-trade-dates
python bao_stock.py --update-all
python bao_stock_parquet.py backfill --start 2020-01-01
```

日常（每日收盘后）：

```text
python bao_stock.py --update
python bao_stock_parquet.py update
```

月初跑 `merge-month`、年初跑 `merge-year`（两者都不联网，只读库重写文件）。

## 测试

```text
pip install -r requirements-dev.txt
python -m pytest -q
```

用例只覆盖纯函数，不碰数据库与网络。

## 文档

- [组件契约](docs/baostock-component-contract.md) —— 消费方依赖本仓什么
- [数据采集器](docs/baostock-data-collector.md) —— `bao_stock.py` 命令详解
- [Parquet 管线](docs/baostock-parquet-pipeline.md) —— `bao_stock_parquet.py` 命令详解
- [BaoStock API 备忘](docs/BaoStockPythonAPI.md)
- [变更记录](CHANGELOG.md)

## 版本

版本号的唯一来源是仓根的 `version.txt`（取首个非空行），口径与引擎的 `engine-version.txt` 一致。

```text
python bao_stock.py --version
```
