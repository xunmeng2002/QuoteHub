"""BaoStock 分钟线 → SQLite → QuantTrading 回测 Bar Parquet 管线。

数据流：BaoStock 只读增量拉取 → SQLite（stock_data.db，唯一事实源）→ 三代 Parquet 文件：

    <output_root>/Bar/Identity=<EXCH>.Stock/Year=<YYYY>/
        YYYYMMDD_<freq>m.parquet   日度：update 每个交易日新增一个文件（Parquet 不可修改，当月只增不改）
        YYYYMM_<freq>m.parquet     月度：merge-month 于次月初从库导出整月，日度文件移入 archive/
        YYYY_<freq>m.parquet       年度：merge-year 于次年初从库导出整年，月度/日度文件移入 archive/
        archive/                   被合并取代的上一代文件（回测 glob Year=*/*.parquet 不扫描，不删除）

幂等性由 SQLite 主键承担：入库一律 INSERT OR REPLACE，重复拉取同一天只会有最后一条生效；
Parquet 文件成为数据库的纯再导出（不做文件侧合并），从根上消除"掩码跨精度误删"问题。

另有一张 `QueriedBarSpans`（v2.3.0）：按「代码 + 频率」记**已向上游问成功的日区间**，供调用方
判断"这段还用不用再取"。它与 `MinuteBars` 是两件事：停牌日在 `MinuteBars` 里永远没有行，而在
账里被记为"问过了、上游没有"，于是那个缺口不会被反复重取。

子命令：
    update      增量入库并写出当日 Parquet（缺省从库中最后交易日续传，含当日重拉自愈）
    merge-month [--month YYYYMM]   缺省上月：整月导出 + 归档日度文件
    merge-year  [--year YYYY]      缺省去年：整年导出 + 归档月度/日度文件
    backfill --start --end         区间入库并直接写出年度 Parquet（不产生日度文件）

注意：
    - BaoStock 仅有 5/15/30/60 分钟线，指数无分钟线，无 tick/1 分钟/盘口。
    - 文件名带频率后缀（_5m）：四种频率可并存于同一目录，回测按 Identity 目录 + 日期过滤。
    - 股票回测需在 QuantTrading 的 BackTest.json 中将 MdDataPath 指向 output_root，
      ExchangeID 用 SSE/SZSE，InstrumentID 用 6 位代码（如 600519）。
    - SQLite 路径复用 BaoStock.DB_PATH（stock_data.db），须在 QuoteHub 目录下运行；
      建表由 BaoStock.init_database 自动应用（sql/upgrades/Update_v2.1.0.sql）。
    - output_root 与代码清单均**无代码兜底**：由配置键 parquet_output_root / stocks
      （或命令行 --output-root / --codes）提供，两处都缺即报错退出。
"""

from __future__ import annotations

import argparse
import calendar
import contextlib
import fnmatch
import logging
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import baostock as bs
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from bao_stock import DB_PATH, _collect_rows, init_database, load_config_file, login_with_retry, require_config_value

logger = logging.getLogger(__name__)

# ---------- 配置 ----------

CONFIG_FILE = "bao_stock_parquet.json"
CONFIG_KEY_OUTPUT_ROOT = "parquet_output_root"
DEFAULT_FREQUENCY = "5"

MINUTE_K_FIELDS = "date,time,code,open,high,low,close,volume,amount"
DAILY_K_FIELDS = "date,code,preclose,volume,amount"

# 已问区间相隔几天以内算同一段。1 = 首尾相接（`2024-12-31` 与 `2025-01-01`）也并成一段：
# 它们之间没有哪一天漏问。
DAY_SPAN_ADJACENCY_DAYS = 1

# 列名与类型镜像 D:\Md\Bar 既有文件（MdReader 的 union_by_name 跨文件查询要求类型一致）
BAR_PARQUET_SCHEMA = pa.schema([
    ("TradingDay", pa.int64()),
    ("ExchangeID", pa.string()),
    ("InstrumentID", pa.string()),
    ("UpdateTs", pa.int64()),
    ("Preces", pa.string()),
    ("LastTraded", pa.float64()),
    ("Volume", pa.float64()),
    ("LastTurnover", pa.decimal128(24, 8)),
    ("Turnover", pa.decimal128(24, 8)),
    ("OpenInterest", pa.float64()),
    ("Open", pa.decimal128(24, 8)),
    ("High", pa.decimal128(24, 8)),
    ("Low", pa.decimal128(24, 8)),
    ("Close", pa.decimal128(24, 8)),
    ("HighestPrice", pa.decimal128(24, 8)),
    ("LowestPrice", pa.decimal128(24, 8)),
    ("SettlementPrice", pa.decimal128(24, 8)),
    ("PreSettlementPrice", pa.decimal128(24, 8)),
    ("PreClosePrice", pa.decimal128(24, 8)),
    ("PreOpenInterest", pa.float64()),
    ("ProductClass", pa.string()),
    ("ProductID", pa.string()),
    ("UnderlyingID", pa.string()),
])

DECIMAL_COLUMNS = [field.name for field in BAR_PARQUET_SCHEMA if pa.types.is_decimal(field.type)]

STOCK_EXCHANGE_PREFIXES = {"sh": "SSE", "sz": "SZSE"}

# ---------- 工具函数 ----------

def split_stock_code(stock_code: str) -> tuple[str, str]:
    """拆分 'sh.600519' 为交易所代码与 6 位证券代码。

    Args:
        stock_code: Baostock 格式证券代码，交易所前缀大小写不限。

    Returns:
        (交易所代码, 6 位证券代码)；交易所代码取 'SSE' / 'SZSE'。

    Raises:
        ValueError: 交易所前缀无法识别，或 "." 后缺少证券代码。
    """
    prefix, _, instrument_id = stock_code.partition(".")
    exchange_id = STOCK_EXCHANGE_PREFIXES.get(prefix.lower())
    if exchange_id is None or not instrument_id:
        raise ValueError(f"无法识别的证券代码: {stock_code}（应为 sh.600519 / sz.000001 格式）")
    return exchange_id, instrument_id

def group_codes_by_exchange(stock_codes: list[str]) -> dict[str, list[str]]:
    """按交易所分组证券代码（同一天/月/年的文件按交易所聚合全部代码）。

    Args:
        stock_codes: Baostock 格式证券代码列表。

    Returns:
        交易所代码 → 该所证券代码列表，组内保持输入顺序。

    Raises:
        ValueError: 列表中含无法识别的证券代码。
    """
    codes_by_exchange: dict[str, list[str]] = {}
    for stock_code in stock_codes:
        exchange_id, _ = split_stock_code(stock_code)
        codes_by_exchange.setdefault(exchange_id, []).append(stock_code)
    return codes_by_exchange

def to_sqlite_value(value: Any) -> Any:
    """NaN 一律转 None（SQLite 将 NaN 存为 NULL，显式转换避免依赖该隐式行为）。

    Args:
        value: 待入库的标量。

    Returns:
        NaN 对应 None，其余原样返回。
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value

def resolve_output_root(command_line_output_root: str | None, config: dict[str, Any], config_file: str) -> str:
    """解析 Parquet 输出根为绝对路径，并把生效值记进日志。

    取值只有两个来源：命令行 `--output-root`、配置键 `parquet_output_root`（必填，见
    `require_config_value`）。配置里的相对值按**当前工作目录**（而非配置文件所在目录）
    解析，故归一化后打印生效值：否则在别的目录运行会在克隆旁静默建树（`os.makedirs`
    不报错），回测侧看不到。

    Args:
        command_line_output_root: 命令行 --output-root，可为 None。
        config: 解析后的配置字典。
        config_file: 配置文件名，仅用于拼错误提示。

    Returns:
        绝对路径形式的输出根。

    Raises:
        SystemExit: 命令行与配置里都没有输出根。
    """
    configured_output_root = command_line_output_root or require_config_value(config, CONFIG_KEY_OUTPUT_ROOT, config_file, "--output-root")
    absolute_output_root = os.path.abspath(configured_output_root)
    logger.info("Parquet 输出根: %s", absolute_output_root)
    return absolute_output_root

# ---------- BaoStock 拉取 ----------

def collect_rows_or_raise(result_set: Any, stock_code: str) -> list[list[str]]:
    """收集结果集；分页中途失败时**抛**，而不是静默截断。

    `_collect_rows` 的 while 条件是 `error_code == "0"`，中途某一页失败时它只是跳出循环、把已经
    拿到的行返回。调用方若照此入库，得到的是一个**截断却看不出被截断**的帧；再把它记进
    `QueriedBarSpans` 的账，那个洞就再也不会被补。故收集完必须再看一次错误码。

    Args:
        result_set: Baostock 查询返回的结果集对象。
        stock_code: 证券代码，仅用于拼错误提示。

    Returns:
        行列表，每行为字段字符串列表。

    Raises:
        RuntimeError: 分页过程中出错（此时返回的行不完整）。
    """
    rows = _collect_rows(result_set)

    if result_set.error_code != "0":
        raise RuntimeError(f"结果集分页中断: {stock_code} {result_set.error_msg}")

    return rows

def query_minute_bars(stock_code: str, start_date: str, end_date: str, frequency: str) -> pd.DataFrame:
    """拉取分钟 K 线并转为数值 DataFrame（BaoStock 返回值全为字符串）。

    Args:
        stock_code: Baostock 格式证券代码。
        start_date: 起始日期 'YYYY-MM-DD'（含）。
        end_date: 结束日期 'YYYY-MM-DD'（含）。
        frequency: 分钟频率 '5' / '15' / '30' / '60'。

    Returns:
        按 time 升序的分钟帧；close 缺失的 bar（停牌等）已丢弃。

    Raises:
        RuntimeError: 查询失败或分页中断。
    """
    result_set = bs.query_history_k_data_plus(
        stock_code,
        MINUTE_K_FIELDS,
        start_date=start_date,
        end_date=end_date,
        frequency=frequency,
        adjustflag="3",
    )
    if result_set.error_code != "0":
        raise RuntimeError(f"query_history_k_data_plus 失败: {stock_code} {result_set.error_msg}")
    minute_frame = pd.DataFrame(collect_rows_or_raise(result_set, stock_code), columns=MINUTE_K_FIELDS.split(","))
    for column in ("open", "high", "low", "close", "volume", "amount"):
        minute_frame[column] = pd.to_numeric(minute_frame[column], errors="coerce")
    invalid_count = int(minute_frame["close"].isna().sum())
    if invalid_count > 0:
        logger.warning("%s 有 %s 根分钟 bar 价格缺失（停牌等），已丢弃", stock_code, invalid_count)
        minute_frame = minute_frame.dropna(subset=["close"])
    return minute_frame.sort_values("time").reset_index(drop=True)

def query_daily_bars(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """拉取日线（取 preclose 供分钟 bar 首根回填，volume/amount 供口径交叉校验）。

    Args:
        stock_code: Baostock 格式证券代码。
        start_date: 起始日期 'YYYY-MM-DD'（含）。
        end_date: 结束日期 'YYYY-MM-DD'（含）。

    Returns:
        以 date 为索引的日线帧。

    Raises:
        RuntimeError: 查询失败或分页中断。
    """
    result_set = bs.query_history_k_data_plus(
        stock_code,
        DAILY_K_FIELDS,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag="3",
    )
    if result_set.error_code != "0":
        raise RuntimeError(f"query_history_k_data_plus 失败: {stock_code} {result_set.error_msg}")
    daily_frame = pd.DataFrame(collect_rows_or_raise(result_set, stock_code), columns=DAILY_K_FIELDS.split(","))
    for column in ("preclose", "volume", "amount"):
        daily_frame[column] = pd.to_numeric(daily_frame[column], errors="coerce")
    return daily_frame.set_index("date")

def detect_cumulative_mode(bar_values: list[float], daily_total: float) -> bool:
    """判别单日分钟量额是日内累计（True）还是本周期增量（False）。

    累计口径的判据：序列单调不减且末值≈当日总量；全零或对不上时按增量处理。

    Args:
        bar_values: 单日各 bar 的量或额，按时间升序。
        daily_total: 当日日线总量；为 None 或 NaN 时无从比对，按增量处理。

    Returns:
        True 表示该列为日内累计口径。
    """
    if daily_total is None or pd.isna(daily_total) or not bar_values:
        return False
    if all(value == 0 for value in bar_values):
        return False
    monotonic = all(bar_values[i] <= bar_values[i + 1] for i in range(len(bar_values) - 1))
    tolerance = max(1.0, abs(daily_total) * 1e-6)
    return monotonic and abs(bar_values[-1] - daily_total) <= tolerance

# ---------- SQLite 入库 / 读取（事实源） ----------

def connect_database() -> sqlite3.Connection:
    """打开数据库连接并确保 Schema 已就绪。

    Returns:
        SQLite 连接；调用方须用 `contextlib.closing` 保证关闭。
    """
    connection = sqlite3.connect(DB_PATH)
    init_database(connection)
    return connection

def upsert_minute_bars(connection: sqlite3.Connection, stock_code: str, frequency: str, minute_frame: pd.DataFrame) -> None:
    """分钟 bar 原始值入库，主键（Code, Frequency, Time）保证重复拉取幂等。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_code: Baostock 格式证券代码。
        frequency: 分钟频率。
        minute_frame: query_minute_bars 返回的分钟帧。
    """
    records = [
        (
            stock_code, frequency, row.date, row.time,
            to_sqlite_value(row.open), to_sqlite_value(row.high),
            to_sqlite_value(row.low), to_sqlite_value(row.close),
            to_sqlite_value(row.volume), to_sqlite_value(row.amount),
        )
        for row in minute_frame.itertuples(index=False)
    ]
    connection.executemany(
        "INSERT OR REPLACE INTO MinuteBars "
        "(Code, Frequency, TradingDay, Time, Open, High, Low, Close, Volume, Amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        records,
    )

def upsert_trading_day_stats(connection: sqlite3.Connection, stock_code: str, daily_frame: pd.DataFrame) -> None:
    """日度统计（preclose/volume/amount）入库，主键（Code, TradingDay）保证幂等。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_code: Baostock 格式证券代码。
        daily_frame: query_daily_bars 返回的日线帧（以 date 为索引）。
    """
    stats_frame = daily_frame.reset_index()
    records = [
        (stock_code, row.date, to_sqlite_value(row.preclose), to_sqlite_value(row.volume), to_sqlite_value(row.amount))
        for row in stats_frame.itertuples(index=False)
    ]
    connection.executemany(
        "INSERT OR REPLACE INTO MinuteBarTradingDays (Code, TradingDay, Preclose, Volume, Amount) VALUES (?, ?, ?, ?, ?)",
        records,
    )

def upsert_queried_bar_span(connection: sqlite3.Connection, stock_code: str, frequency: str, start_day: str, end_day: str) -> None:
    """把「这一次向上游问成功的日区间」并进该合约该频率的账。

    先并再写：不并的话一次调用留一行，一年下来几百行；并了之后常驻的是互不相接的几段，
    通常每合约一两行。区间是**闭**的，故首尾相接的两段（`2024-12-31` 与 `2025-01-01`）
    也算同一段——它们之间的每一天都被问过。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_code: Baostock 格式证券代码。
        frequency: 分钟频率。
        start_day: 本次问成功的区间起点 'YYYY-MM-DD'（含）。
        end_day: 本次问成功的区间终点 'YYYY-MM-DD'（含）。
    """
    existing_rows = connection.execute(
        "SELECT StartDay, EndDay FROM QueriedBarSpans WHERE Code = ? AND Frequency = ?",
        (stock_code, frequency),
    ).fetchall()

    merged_spans = merge_day_spans(
        [(row[0], row[1]) for row in existing_rows] + [(start_day, end_day)]
    )

    connection.execute(
        "DELETE FROM QueriedBarSpans WHERE Code = ? AND Frequency = ?",
        (stock_code, frequency),
    )
    connection.executemany(
        "INSERT INTO QueriedBarSpans (Code, Frequency, StartDay, EndDay) VALUES (?, ?, ?, ?)",
        [
            (stock_code, frequency, span_start, span_end)
            for span_start, span_end in merged_spans
        ],
    )

def merge_day_spans(day_spans: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """把重叠或首尾相接的日区间并成互不相接的几段（按起点升序）。

    Args:
        day_spans: 日区间列表，每段为 (起点, 终点)，闭区间。

    Returns:
        合并后的区间列表，按起点升序。
    """
    merged: list[list[str]] = []

    for span_start, span_end in sorted(day_spans):
        if merged and _spans_are_contiguous(span_start, merged[-1][1]):
            if date.fromisoformat(span_end) > date.fromisoformat(merged[-1][1]):
                merged[-1][1] = span_end
            continue

        merged.append([span_start, span_end])

    return [(span_start, span_end) for span_start, span_end in merged]

def _spans_are_contiguous(next_start: str, current_end: str) -> bool:
    """下一段是否接着当前段：重叠，或正好从当前段的次日开始。

    Args:
        next_start: 下一段的起点 'YYYY-MM-DD'。
        current_end: 当前段的终点 'YYYY-MM-DD'。

    Returns:
        True 表示两段可并为一段。
    """
    return (
        date.fromisoformat(next_start).toordinal()
        <= date.fromisoformat(current_end).toordinal() + DAY_SPAN_ADJACENCY_DAYS
    )

def ingest_stock(connection: sqlite3.Connection, stock_code: str, frequency: str, start_date: str, end_date: str) -> list[str]:
    """拉取单只股票区间数据并入库，返回有分钟数据的交易日（'YYYY-MM-DD' 升序）。

    账在**两路查询都成功之后**才记：`query_minute_bars` / `query_daily_bars` 在上游失败时都会
    抛，抛了就写到账这一步；能记上就说明这一段的答案是上游给的，而不是我们被中断后猜的。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_code: Baostock 格式证券代码。
        frequency: 分钟频率。
        start_date: 起始日期 'YYYY-MM-DD'（含）。
        end_date: 结束日期 'YYYY-MM-DD'（含）。

    Returns:
        有分钟数据的交易日列表，'YYYY-MM-DD' 升序。

    Raises:
        RuntimeError: 分钟或日线查询失败、分页中断（此时不入库、不记账）。
    """
    minute_frame = query_minute_bars(stock_code, start_date, end_date, frequency)
    daily_frame = query_daily_bars(stock_code, start_date, end_date)
    upsert_minute_bars(connection, stock_code, frequency, minute_frame)
    upsert_trading_day_stats(connection, stock_code, daily_frame)
    upsert_queried_bar_span(connection, stock_code, frequency, start_date, end_date)
    connection.commit()
    logger.info("%s 入库 %s 根 %s 分钟 bar / %s 个交易日统计（%s ~ %s）", stock_code, len(minute_frame), frequency, len(daily_frame), start_date, end_date)
    return sorted(minute_frame["date"].unique())

def get_last_trading_day(connection: sqlite3.Connection, stock_code: str, frequency: str) -> str | None:
    """库中该合约最后有分钟数据的交易日；无数据返回 None。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_code: Baostock 格式证券代码。
        frequency: 分钟频率。

    Returns:
        最后交易日 'YYYY-MM-DD'；库中无该合约该频率的数据返回 None。
    """
    row = connection.execute(
        "SELECT MAX(Time) FROM MinuteBars WHERE Code = ? AND Frequency = ?",
        (stock_code, frequency),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return datetime.strptime(row[0][:8], "%Y%m%d").date().isoformat()

def load_minute_bars(connection: sqlite3.Connection, stock_code: str, frequency: str, start_day: str, end_day: str) -> pd.DataFrame:
    """按交易日区间读回分钟 bar（列名对齐 BaoStock 原始帧，供 build_bar_frame 复用）。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_code: Baostock 格式证券代码。
        frequency: 分钟频率。
        start_day: 起始交易日 'YYYY-MM-DD'（含）。
        end_day: 结束交易日 'YYYY-MM-DD'（含）。

    Returns:
        分钟帧，列名与 BaoStock 原始字段一致（date/time/open/…）。
    """
    return pd.read_sql_query(
        "SELECT TradingDay AS date, Time AS time, Open AS open, High AS high, Low AS low, "
        "Close AS close, Volume AS volume, Amount AS amount "
        "FROM MinuteBars WHERE Code = ? AND Frequency = ? AND TradingDay >= ? AND TradingDay <= ? ORDER BY Time",
        connection, params=(stock_code, frequency, start_day, end_day),
    )

def load_trading_day_stats(connection: sqlite3.Connection, stock_code: str, start_day: str, end_day: str) -> pd.DataFrame:
    """按交易日区间读回日度统计（索引对齐 query_daily_bars 输出）。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_code: Baostock 格式证券代码。
        start_day: 起始交易日 'YYYY-MM-DD'（含）。
        end_day: 结束交易日 'YYYY-MM-DD'（含）。

    Returns:
        以 date 为索引的日度统计帧（preclose/volume/amount）。
    """
    stats_frame = pd.read_sql_query(
        "SELECT TradingDay AS date, Preclose AS preclose, Volume AS volume, Amount AS amount "
        "FROM MinuteBarTradingDays WHERE Code = ? AND TradingDay >= ? AND TradingDay <= ? ORDER BY TradingDay",
        connection, params=(stock_code, start_day, end_day),
    )
    return stats_frame.set_index("date")

# ---------- Bar 行构建（与旧版一致：原始量额 → 累计/增量语义映射） ----------

def build_bar_frame(minute_frame: pd.DataFrame, daily_frame: pd.DataFrame, exchange_id: str, instrument_id: str, preces: str) -> pd.DataFrame:
    """将单只股票的分钟线映射为 Bar Parquet 行（Volume/LastTraded 语义见 detect_cumulative_mode）。

    Args:
        minute_frame: 该合约的分钟帧（load_minute_bars 输出）。
        daily_frame: 该合约的日度统计帧（load_trading_day_stats 输出）。
        exchange_id: 交易所代码 'SSE' / 'SZSE'。
        instrument_id: 6 位证券代码。
        preces: 写入 Preces 列的价格精度串，如 '5m'。

    Returns:
        (bar_frame, mismatch_records)：前者列序对齐 BAR_PARQUET_SCHEMA，
        后者为分钟量合计与日线量偏差 >1% 的交易日明细。
    """
    rows: list[dict[str, Any]] = []
    mismatch_records: list[dict[str, Any]] = []
    previous_close = None
    for trading_day, day_bars in minute_frame.groupby("date", sort=True):
        day_bars = day_bars.sort_values("time")
        closes = day_bars["close"].tolist()
        daily_row = daily_frame.loc[trading_day] if trading_day in daily_frame.index else None
        if daily_row is None:
            logger.warning("%s.%s %s 无日线 preclose，沿用前收盘", exchange_id, instrument_id, trading_day)
        pre_close = float(daily_row["preclose"]) if daily_row is not None and not pd.isna(daily_row["preclose"]) else previous_close

        volume_cumulative = detect_cumulative_mode(
            day_bars["volume"].tolist(), float(daily_row["volume"]) if daily_row is not None else None
        )
        amount_cumulative = detect_cumulative_mode(
            day_bars["amount"].tolist(), float(daily_row["amount"]) if daily_row is not None else None
        )
        last_traded = day_bars["volume"].diff().fillna(day_bars["volume"]) if volume_cumulative else day_bars["volume"]
        volume = day_bars["volume"] if volume_cumulative else day_bars["volume"].cumsum()
        last_turnover = day_bars["amount"].diff().fillna(day_bars["amount"]) if amount_cumulative else day_bars["amount"]
        turnover = day_bars["amount"] if amount_cumulative else day_bars["amount"].cumsum()
        # 分钟量合计与日量存在 ~0.4% 的固有口径差（大宗交易/集合竞价归属），>1% 记明细，>5% 逐日告警
        if not volume_cumulative and daily_row is not None and not pd.isna(daily_row["volume"]):
            daily_volume = float(daily_row["volume"])
            minute_total = float(volume.iloc[-1])
            if abs(minute_total - daily_volume) > max(1.0, daily_volume * 0.01):
                mismatch_records.append({
                    "InstrumentID": instrument_id,
                    "TradingDay": trading_day,
                    "MinuteVolumeSum": minute_total,
                    "DailyVolume": daily_volume,
                    "DeviationPercent": round((minute_total - daily_volume) / daily_volume * 100, 2),
                })

        for index, (_, bar) in enumerate(day_bars.iterrows()):
            bar_pre_close = pre_close if index == 0 else closes[index - 1]
            rows.append({
                "TradingDay": int(trading_day.replace("-", "")),
                "ExchangeID": exchange_id,
                "InstrumentID": instrument_id,
                # BaoStock 分钟线 time 为 bar 结束时间（YYYYMMDDHHMMSSsss），截到分钟与既有数据一致
                "UpdateTs": int(bar["time"][:12]),
                "Preces": preces,
                "LastTraded": float(last_traded.iloc[index]),
                "Volume": float(volume.iloc[index]),
                "LastTurnover": float(last_turnover.iloc[index]),
                "Turnover": float(turnover.iloc[index]),
                "OpenInterest": 0.0,
                "Open": float(bar["open"]),
                "High": float(bar["high"]),
                "Low": float(bar["low"]),
                "Close": float(bar["close"]),
                "HighestPrice": float(bar["high"]),
                "LowestPrice": float(bar["low"]),
                "SettlementPrice": None,
                "PreSettlementPrice": bar_pre_close,
                "PreClosePrice": bar_pre_close,
                "PreOpenInterest": 0.0,
                "ProductClass": "Stock",
                "ProductID": instrument_id[:3],
                "UnderlyingID": "",
            })
        previous_close = closes[-1]
    return pd.DataFrame(rows, columns=[field.name for field in BAR_PARQUET_SCHEMA]), mismatch_records

def to_decimal_series(series: pd.Series) -> pd.Series:
    """将 float/Decimal/None 混合列统一为 Decimal（pyarrow 的 float→decimal128 cast 不支持，需显式转换）。

    Args:
        series: 待转换的列。

    Returns:
        Decimal 列；None/NaN 保持 None，其余量化到 8 位小数。
    """
    def convert(value: Any) -> Decimal | None:
        """把单个值转为 8 位小数的 Decimal。

        Args:
            value: float / Decimal / None。

        Returns:
            Decimal；None 与 NaN 返回 None。
        """
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        if isinstance(value, Decimal):
            return value.quantize(Decimal("0.00000001"))
        return Decimal(repr(round(float(value), 8)))

    return series.map(convert)

# ---------- Parquet 写出 / 归档 ----------

def bar_file_path(output_root: str, exchange_id: str, period: str, frequency: str) -> str:
    """三代文件共用命名：period 为 'YYYY'（年）/ 'YYYYMM'（月）/ 'YYYYMMDD'（日），频率作后缀。

    Args:
        output_root: Parquet 输出根（绝对路径）。
        exchange_id: 交易所代码 'SSE' / 'SZSE'。
        period: 文件周期标识 'YYYY' / 'YYYYMM' / 'YYYYMMDD'。
        frequency: 分钟频率，作文件名后缀。

    Returns:
        该文件的完整路径。
    """
    return os.path.join(output_root, "Bar", f"Identity={exchange_id}.Stock", f"Year={period[:4]}", f"{period}_{frequency}m.parquet")

def write_bar_file(parquet_file: str, bar_frame: pd.DataFrame) -> None:
    """排序 + Decimal 化后按既有 schema 写单文件（纯数据库再导出，不做文件侧合并）。

    Args:
        parquet_file: 目标文件路径，父目录不存在时自动创建。
        bar_frame: build_bar_frame 返回的 Bar 帧。
    """
    ordered_frame = bar_frame.sort_values(["UpdateTs", "InstrumentID"]).reset_index(drop=True)
    for column in DECIMAL_COLUMNS:
        ordered_frame[column] = to_decimal_series(ordered_frame[column])
    os.makedirs(os.path.dirname(parquet_file), exist_ok=True)
    table = pa.Table.from_pandas(ordered_frame, schema=BAR_PARQUET_SCHEMA, preserve_index=False)
    pq.write_table(table, parquet_file, compression="zstd")

def archive_file(file_path: str) -> None:
    """将上一代文件移入同目录 archive/（重命名而非删除；回测 glob 不扫描 archive 层）。

    Args:
        file_path: 待归档文件路径。
    """
    archive_dir = os.path.join(os.path.dirname(file_path), "archive")
    os.makedirs(archive_dir, exist_ok=True)
    target_path = os.path.join(archive_dir, os.path.basename(file_path))
    if os.path.exists(target_path):
        logger.warning("archive 内已存在同名文件，两侧均保留不动: %s", target_path)
        return
    os.rename(file_path, target_path)
    logger.info("归档 %s → %s", file_path, target_path)

def archive_matching_files(directory: str, patterns: list[str]) -> None:
    """将目录顶层匹配 glob 模式的文件归档（合并命令在写出新文件后调用）。

    Args:
        directory: 待扫描目录，只扫顶层不递归。
        patterns: glob 模式列表；为空或目录不存在时直接返回。
    """
    if not patterns or not os.path.isdir(directory):
        return
    for file_name in os.listdir(directory):
        file_path = os.path.join(directory, file_name)
        if not os.path.isfile(file_path):
            continue
        if any(fnmatch.fnmatch(file_name, pattern) for pattern in patterns):
            archive_file(file_path)

def write_mismatch_report(target_file: str, period: str, frequency: str, mismatch_records: list[dict[str, Any]]) -> None:
    """将分钟量与日线量偏差 >1% 的交易日明细写成同目录 CSV（正偏差 = 分钟多于日线），>5% 的天逐日告警。

    Args:
        target_file: 本次写出的 bar 文件路径，用于推出 CSV 的位置与命名。
        period: 文件周期标识 'YYYY' / 'YYYYMM' / 'YYYYMMDD'。
        frequency: 分钟频率。
        mismatch_records: build_bar_frame 返回的偏差明细；为空则不写文件。
    """
    for record in mismatch_records:
        if abs(record["DeviationPercent"]) > 5:
            logger.warning("%s %s 分钟量与日量偏差 %s%%，超过 5%%", record["InstrumentID"], record["TradingDay"], record["DeviationPercent"])
    if not mismatch_records:
        return
    report_file = os.path.join(os.path.dirname(target_file), f"{period}_{frequency}m_mismatch.csv")
    report_frame = pd.DataFrame(mismatch_records).sort_values(["InstrumentID", "TradingDay"])
    report_frame.to_csv(report_file, index=False, encoding="utf-8-sig")
    logger.warning("%s 共 %s 天分钟量与日量偏差超 1%%，明细见 %s", os.path.basename(target_file), len(mismatch_records), report_file)

def warn_coverage_shrink(connection: sqlite3.Connection, frequency: str, start_day: str, end_day: str, target_file: str) -> None:
    """重写既有文件前检查覆盖收缩（仅告警不阻断）：
    1) 既有 span 超出本次导出区间——重写后文件范围变窄；
    2) 库中该频率在该区间完全没有既有文件两端的天数——重写将丢失库外天数。

    Args:
        connection: 已打开的 SQLite 连接。
        frequency: 分钟频率。
        start_day: 本次导出区间起点 'YYYY-MM-DD'。
        end_day: 本次导出区间终点 'YYYY-MM-DD'。
        target_file: 即将被重写的文件路径；不存在时不检查。
    """
    if not os.path.exists(target_file):
        return
    existing_days = pq.read_table(target_file, columns=["TradingDay"]).column("TradingDay").to_pylist()
    if not existing_days:
        return
    existing_min, existing_max = min(existing_days), max(existing_days)
    period_min = int(start_day.replace("-", ""))
    period_max = int(end_day.replace("-", ""))
    if existing_min < period_min or existing_max > period_max:
        logger.warning("%s 既有覆盖 %s~%s 超出本次导出区间 %s~%s，重写将收缩范围", target_file, existing_min, existing_max, period_min, period_max)
    row = connection.execute(
        "SELECT MIN(TradingDay), MAX(TradingDay) FROM MinuteBars WHERE Frequency = ? AND TradingDay >= ? AND TradingDay <= ?",
        (frequency, start_day, end_day),
    ).fetchone()
    if row[0] is None:
        return
    database_min = int(row[0].replace("-", ""))
    database_max = int(row[1].replace("-", ""))
    if existing_min < database_min or existing_max > database_max:
        logger.warning("%s 既有覆盖 %s~%s 超出库中数据 %s~%s，重写将丢失库外天数", target_file, existing_min, existing_max, database_min, database_max)

# ---------- 导出（数据库 → Parquet，唯一出口） ----------

def export_bars_from_database(connection: sqlite3.Connection, output_root: str, frequency: str, exchange_id: str,
                              stock_codes: list[str], start_day: str, end_day: str, period: str,
                              archive_patterns: list[str]) -> None:
    """从库导出 [start_day, end_day] 全部代码到 period 命名文件；写出后归档匹配的上一代文件。

    Args:
        connection: 已打开的 SQLite 连接。
        output_root: Parquet 输出根（绝对路径）。
        frequency: 分钟频率。
        exchange_id: 交易所代码 'SSE' / 'SZSE'。
        stock_codes: 该交易所下参与导出的证券代码列表。
        start_day: 导出区间起点 'YYYY-MM-DD'（含）。
        end_day: 导出区间终点 'YYYY-MM-DD'（含）。
        period: 文件周期标识，决定文件名与 Year= 分区。
        archive_patterns: 写出后需归档的 glob 模式；日度导出传空列表。
    """
    frames: list[pd.DataFrame] = []
    mismatch_records: list[dict[str, Any]] = []
    for stock_code in stock_codes:
        minute_frame = load_minute_bars(connection, stock_code, frequency, start_day, end_day)
        if minute_frame.empty:
            continue
        stats_frame = load_trading_day_stats(connection, stock_code, start_day, end_day)
        _, instrument_id = split_stock_code(stock_code)
        bar_frame, code_mismatches = build_bar_frame(minute_frame, stats_frame, exchange_id, instrument_id, f"{frequency}m")
        frames.append(bar_frame)
        mismatch_records.extend(code_mismatches)
    if not frames:
        logger.warning("%s %s ~ %s 库中无 %s 分钟数据，跳过导出", exchange_id, start_day, end_day, frequency)
        return
    merged_frame = pd.concat(frames, ignore_index=True)
    target_file = bar_file_path(output_root, exchange_id, period, frequency)
    warn_coverage_shrink(connection, frequency, start_day, end_day, target_file)
    write_bar_file(target_file, merged_frame)
    logger.info("写入 %s（%s 行，%s ~ %s）", target_file, len(merged_frame), start_day, end_day)
    write_mismatch_report(target_file, period, frequency, mismatch_records)
    archive_matching_files(os.path.dirname(target_file), archive_patterns)

# ---------- 子命令 ----------

def run_update(connection: sqlite3.Connection, stock_codes: list[str], frequency: str, output_root: str,
               start_date: str | None, end_date: str, default_start_date: str) -> None:
    """增量入库并按日写出 Parquet。续传起点为库中最后交易日本身（当日重拉自愈半截数据，
    幂等由主键保证）；显式 --start 时对所有代码强制从该日起拉取。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_codes: 参与更新的证券代码列表。
        frequency: 分钟频率。
        output_root: Parquet 输出根（绝对路径）。
        start_date: 命令行 --start；为 None 时按库中最后交易日续传。
        end_date: 结束日期 'YYYY-MM-DD'。
        default_start_date: 库中无数据时的起始日期。
    """
    codes_by_exchange = group_codes_by_exchange(stock_codes)
    trading_days_by_exchange: dict[str, set[str]] = {}
    for stock_code in stock_codes:
        effective_start = start_date or get_last_trading_day(connection, stock_code, frequency) or default_start_date
        if effective_start > end_date:
            logger.info("%s 已是最新（库中最后交易日 %s 不早于 %s），跳过", stock_code, effective_start, end_date)
            continue
        trading_days = ingest_stock(connection, stock_code, frequency, effective_start, end_date)
        exchange_id, _ = split_stock_code(stock_code)
        trading_days_by_exchange.setdefault(exchange_id, set()).update(trading_days)

    for exchange_id, trading_days in sorted(trading_days_by_exchange.items()):
        for trading_day in sorted(trading_days):
            export_bars_from_database(
                connection, output_root, frequency, exchange_id, codes_by_exchange[exchange_id],
                trading_day, trading_day, trading_day.replace("-", ""), archive_patterns=[],
            )

def get_last_month() -> str:
    """上一个自然月。

    Returns:
        上月标识 'YYYYMM'，如 '202609'。
    """
    last_month_last_day = datetime.today().replace(day=1) - timedelta(days=1)
    return last_month_last_day.strftime("%Y%m")

def run_merge_month(connection: sqlite3.Connection, stock_codes: list[str], frequency: str, output_root: str, month: str) -> None:
    """整月导出为月度文件，归档该月全部日度文件。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_codes: 参与合并的证券代码列表。
        frequency: 分钟频率。
        output_root: Parquet 输出根（绝对路径）。
        month: 月份标识 'YYYYMM'。
    """
    year = int(month[:4])
    month_number = int(month[4:6])
    period_start = f"{year}-{month_number:02d}-01"
    period_end = f"{year}-{month_number:02d}-{calendar.monthrange(year, month_number)[1]:02d}"
    # '202409[0-9][0-9]_5m.parquet' 精确匹配该月 8 位日度文件（年/月文件名位数不同不会被误归档）
    archive_patterns = [f"{month}[0-9][0-9]_{frequency}m.parquet"]
    for exchange_id, exchange_codes in group_codes_by_exchange(stock_codes).items():
        export_bars_from_database(
            connection, output_root, frequency, exchange_id, exchange_codes,
            period_start, period_end, month, archive_patterns,
        )

def run_merge_year(connection: sqlite3.Connection, stock_codes: list[str], frequency: str, output_root: str, year: str) -> None:
    """整年导出为年度文件，归档该年全部月度文件与残留日度文件。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_codes: 参与合并的证券代码列表。
        frequency: 分钟频率。
        output_root: Parquet 输出根（绝对路径）。
        year: 年份标识 'YYYY'。
    """
    # '2024[0-9][0-9]_5m.parquet' 同时命中 6 位月度与 8 位日度文件（年份后必须跟数字，
    # 年度文件自身 '2024_5m.parquet' 不会命中）
    archive_patterns = [f"{year}[0-9][0-9]_{frequency}m.parquet"]
    for exchange_id, exchange_codes in group_codes_by_exchange(stock_codes).items():
        export_bars_from_database(
            connection, output_root, frequency, exchange_id, exchange_codes,
            f"{year}-01-01", f"{year}-12-31", year, archive_patterns,
        )

def run_backfill(connection: sqlite3.Connection, stock_codes: list[str], frequency: str, output_root: str, start_date: str, end_date: str) -> None:
    """区间入库并直接写出年度 Parquet（历史回补不产生日度文件）；仅触及回补区间相交的年份。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_codes: 参与回补的证券代码列表。
        frequency: 分钟频率。
        output_root: Parquet 输出根（绝对路径）。
        start_date: 回补起始日期 'YYYY-MM-DD'。
        end_date: 回补结束日期 'YYYY-MM-DD'。
    """
    for stock_code in stock_codes:
        ingest_stock(connection, stock_code, frequency, start_date, end_date)
    for exchange_id, exchange_codes in group_codes_by_exchange(stock_codes).items():
        placeholders = ", ".join("?" * len(exchange_codes))
        year_rows = connection.execute(
            f"SELECT DISTINCT substr(TradingDay, 1, 4) FROM MinuteBars "
            f"WHERE Frequency = ? AND TradingDay >= ? AND TradingDay <= ? AND Code IN ({placeholders}) ORDER BY 1",
            [frequency, start_date, end_date, *exchange_codes],
        ).fetchall()
        for (year,) in year_rows:
            export_bars_from_database(
                connection, output_root, frequency, exchange_id, exchange_codes,
                f"{year}-01-01", f"{year}-12-31", year, archive_patterns=[],
            )

# ---------- 命令行入口 ----------

def parse_arguments() -> argparse.Namespace:
    """解析命令行参数。

    Returns:
        含 command 及各子命令专属参数的 Namespace。
    """
    parser = argparse.ArgumentParser(description="BaoStock 分钟线 → SQLite → QuantTrading Bar Parquet 管线")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_arguments(sub_parser: argparse.ArgumentParser) -> None:
        """给子命令挂上四个共通参数（--config / --codes / --frequency / --output-root）。

        Args:
            sub_parser: 目标子命令的 ArgumentParser。
        """
        sub_parser.add_argument(
            "--config", default=CONFIG_FILE,
            help=f"配置文件路径，缺省 {CONFIG_FILE}（提供 stocks / default_start_date / parquet_output_root）",
        )
        sub_parser.add_argument("--codes", default=None, help="逗号分隔的证券代码，缺省取配置文件 stocks")
        sub_parser.add_argument("--frequency", default=DEFAULT_FREQUENCY, choices=["5", "15", "30", "60"], help="分钟频率")
        sub_parser.add_argument("--output-root", default=None, help=f"Parquet 输出根目录（相对当前工作目录）；缺省取配置 {CONFIG_KEY_OUTPUT_ROOT}，两处都无则报错退出")

    update_parser = subparsers.add_parser("update", help="增量入库并写出当日 Parquet 文件")
    add_common_arguments(update_parser)
    update_parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD，缺省从库中最后交易日续传")
    update_parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD，缺省取今天")

    month_parser = subparsers.add_parser("merge-month", help="从库导出上月整月 Parquet 并归档日度文件")
    add_common_arguments(month_parser)
    month_parser.add_argument("--month", default=None, help="月份 YYYYMM，缺省取上月")

    year_parser = subparsers.add_parser("merge-year", help="从库导出去年整年 Parquet 并归档月度/日度文件")
    add_common_arguments(year_parser)
    year_parser.add_argument("--year", default=None, help="年份 YYYY，缺省取去年")

    backfill_parser = subparsers.add_parser("backfill", help="区间入库并直接写出年度 Parquet（不产生日度文件）")
    add_common_arguments(backfill_parser)
    backfill_parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    backfill_parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD，缺省取今天")

    return parser.parse_args()

def main() -> None:
    """命令行入口：按子命令分派，并保证 Baostock 注销与数据库连接关闭。"""
    arguments = parse_arguments()
    config = load_config_file(arguments.config)
    stock_codes = arguments.codes.split(",") if arguments.codes else require_config_value(config, "stocks", arguments.config, "--codes")
    frequency = arguments.frequency
    default_start_date = config.get("default_start_date", "2015-01-01")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    output_root = resolve_output_root(arguments.output_root, config, arguments.config)

    with contextlib.closing(connect_database()) as connection:
        logged_in = False
        try:
            if arguments.command in ("update", "backfill"):
                if login_with_retry() is None:
                    raise SystemExit("BaoStock 登录失败")
                logged_in = True

            if arguments.command == "update":
                run_update(connection, stock_codes, frequency, output_root,
                           arguments.start, arguments.end or time.strftime("%Y-%m-%d"), default_start_date)
            elif arguments.command == "merge-month":
                run_merge_month(connection, stock_codes, frequency, output_root,
                                arguments.month or get_last_month())
            elif arguments.command == "merge-year":
                run_merge_year(connection, stock_codes, frequency, output_root,
                               arguments.year or str(datetime.today().year - 1))
            elif arguments.command == "backfill":
                run_backfill(connection, stock_codes, frequency, output_root,
                             arguments.start, arguments.end or time.strftime("%Y-%m-%d"))
        finally:
            if logged_in:
                bs.logout()

if __name__ == "__main__":
    main()
