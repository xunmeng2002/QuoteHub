"""BaoStock 分钟线 → QuantTrading 回测 Bar Parquet 导出器。

将 BaoStock 的 5/15/30/60 分钟 K 线（股票）转换为 QuantTrading 回测引擎
（D:\\Gitee\\QuantTrading）Bar 撮合模式消费的 Parquet 文件：

    <output_root>/Bar/Identity=<EXCH>.Stock/Year=<YYYY>/<YYYY>_0.parquet

列名与类型逐一对齐既有数据（D:\\Md\\Bar\\Identity=CFFEX.IF，int64 时间戳、
decimal128(24,8) 价格、ZSTD 压缩）；QuantTrading 侧的 Bar 回放 SQL 按
Identity=<交易所>.*/ 匹配文件且不按精度过滤，因此本导出器写入独立的
output_root（不与既有 1m 数据混放），避免同窗口混精度重放。

用法：
    python BaoStockParquet.py                                  # 按 config.json 全量
    python BaoStockParquet.py --start 2024-01-01 --end 2024-12-31
    python BaoStockParquet.py --codes sh.600519,sz.000001 --output-root D:/MdBaoStock

注意：
    - BaoStock 仅有 5/15/30/60 分钟线，指数无分钟线，无 tick/1 分钟/盘口。
    - 股票回测需在 QuantTrading 的 BackTest.json 中将 MdDataPath 指向 output_root，
      ExchangeID 用 SSE/SZSE，InstrumentID 用 6 位代码（如 600519）。
"""

import argparse
import json
import logging
import os
import time
from decimal import Decimal
from typing import Any

import baostock as bs
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from BaoStock import _collect_rows, login_with_retry

logger = logging.getLogger(__name__)

# ---------- 配置 ----------

CONFIG_FILE = "config.json"
DEFAULT_OUTPUT_ROOT = "D:/MdBaoStock"
DEFAULT_FREQUENCY = "5"

MINUTE_K_FIELDS = "date,time,code,open,high,low,close,volume,amount"
DAILY_K_FIELDS = "date,code,preclose,volume,amount"

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

STOCK_EXCHANGE_PREFIXES = {"sh": "SSE", "sz": "SZSE"}


# ---------- 工具函数 ----------


def split_stock_code(stock_code: str) -> tuple[str, str]:
    """拆分 'sh.600519' 为交易所代码与 6 位证券代码。"""
    prefix, _, instrument_id = stock_code.partition(".")
    exchange_id = STOCK_EXCHANGE_PREFIXES.get(prefix.lower())
    if exchange_id is None or not instrument_id:
        raise ValueError(f"无法识别的证券代码: {stock_code}（应为 sh.600519 / sz.000001 格式）")
    return exchange_id, instrument_id


def query_minute_bars(
    stock_code: str,
    start_date: str,
    end_date: str,
    frequency: str,
) -> pd.DataFrame:
    """拉取分钟 K 线并转为数值 DataFrame（BaoStock 返回值全为字符串）。"""
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
    minute_frame = pd.DataFrame(_collect_rows(result_set), columns=MINUTE_K_FIELDS.split(","))
    for column in ("open", "high", "low", "close", "volume", "amount"):
        minute_frame[column] = pd.to_numeric(minute_frame[column], errors="coerce")
    invalid_count = int(minute_frame["close"].isna().sum())
    if invalid_count > 0:
        logger.warning(f"{stock_code} 有 {invalid_count} 根分钟 bar 价格缺失（停牌等），已丢弃")
        minute_frame = minute_frame.dropna(subset=["close"])
    return minute_frame.sort_values("time").reset_index(drop=True)


def query_daily_bars(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """拉取日线（取 preclose 供分钟 bar 首根回填，volume/amount 供口径交叉校验）。"""
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
    daily_frame = pd.DataFrame(_collect_rows(result_set), columns=DAILY_K_FIELDS.split(","))
    for column in ("preclose", "volume", "amount"):
        daily_frame[column] = pd.to_numeric(daily_frame[column], errors="coerce")
    return daily_frame.set_index("date")


def detect_cumulative_mode(bar_values: list[float], daily_total: float) -> bool:
    """判别单日分钟量额是日内累计（True）还是本周期增量（False）。

    累计口径的判据：序列单调不减且末值≈当日总量；全零或对不上时按增量处理。
    """
    if daily_total is None or pd.isna(daily_total) or not bar_values:
        return False
    if all(value == 0 for value in bar_values):
        return False
    monotonic = all(bar_values[i] <= bar_values[i + 1] for i in range(len(bar_values) - 1))
    tolerance = max(1.0, abs(daily_total) * 1e-6)
    return monotonic and abs(bar_values[-1] - daily_total) <= tolerance


def build_bar_frame(
    minute_frame: pd.DataFrame,
    daily_frame: pd.DataFrame,
    exchange_id: str,
    instrument_id: str,
    preces: str,
) -> pd.DataFrame:
    """将单只股票的分钟线映射为 Bar Parquet 行（Volume/LastTraded 语义见 detect_cumulative_mode）。"""
    rows: list[dict[str, Any]] = []
    mismatch_days = 0
    previous_close = None
    for trading_day, day_bars in minute_frame.groupby("date", sort=True):
        day_bars = day_bars.sort_values("time")
        closes = day_bars["close"].tolist()
        daily_row = daily_frame.loc[trading_day] if trading_day in daily_frame.index else None
        if daily_row is None:
            logger.warning(f"{exchange_id}.{instrument_id} {trading_day} 无日线 preclose，沿用前收盘")
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
        # 分钟量合计与日量存在 ~0.4% 的固有口径差（大宗交易/集合竞价归属），仅对 >1% 的偏差告警
        if not volume_cumulative and daily_row is not None and not pd.isna(daily_row["volume"]):
            if abs(float(volume.iloc[-1]) - float(daily_row["volume"])) > max(1.0, float(daily_row["volume"]) * 0.01):
                mismatch_days += 1

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
    if mismatch_days > 0:
        logger.warning(f"{exchange_id}.{instrument_id} 有 {mismatch_days} 天分钟量与日量对不上（请人工核对）")
    return pd.DataFrame(rows, columns=[field.name for field in BAR_PARQUET_SCHEMA])


DECIMAL_COLUMNS = [field.name for field in BAR_PARQUET_SCHEMA if pa.types.is_decimal(field.type)]


def to_decimal_series(series: pd.Series) -> pd.Series:
    """将 float/Decimal/None 混合列统一为 Decimal（pyarrow 的 float→decimal128 cast 不支持，需显式转换）。"""
    def convert(value: Any) -> Decimal | None:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        if isinstance(value, Decimal):
            return value.quantize(Decimal("0.00000001"))
        return Decimal(repr(round(float(value), 8)))

    return series.map(convert)


def merge_write_year_file(year_file: str, new_frame: pd.DataFrame, start_trading_day: int, end_trading_day: int) -> int:
    """幂等写入年度文件：覆盖同 InstrumentID + 日期段的旧行后追加，按（UpdateTs, InstrumentID）排序。"""
    instrument_ids = set(new_frame["InstrumentID"])
    if os.path.exists(year_file):
        existing_frame = pq.read_table(year_file).to_pandas()
        mask = (
            existing_frame["InstrumentID"].isin(instrument_ids)
            & (existing_frame["TradingDay"] >= start_trading_day)
            & (existing_frame["TradingDay"] <= end_trading_day)
        )
        existing_frame = existing_frame[~mask]
        merged_frame = pd.concat([existing_frame, new_frame], ignore_index=True)
    else:
        merged_frame = new_frame
    merged_frame = merged_frame.sort_values(["UpdateTs", "InstrumentID"]).reset_index(drop=True)
    for column in DECIMAL_COLUMNS:
        merged_frame[column] = to_decimal_series(merged_frame[column])
    os.makedirs(os.path.dirname(year_file), exist_ok=True)
    table = pa.Table.from_pandas(merged_frame, schema=BAR_PARQUET_SCHEMA, preserve_index=False)
    pq.write_table(table, year_file, compression="zstd")
    return len(merged_frame)


# ---------- 导出入口 ----------


def export_baostock_bar_parquet(
    stock_codes: list[str],
    start_date: str,
    end_date: str,
    output_root: str,
    frequency: str,
) -> None:
    """逐合约下载并转换，按（交易所, 年份）聚合后写入年度 Parquet 文件。"""
    preces = f"{frequency}m"
    frames_by_year: dict[tuple[str, str], list[pd.DataFrame]] = {}
    for stock_code in stock_codes:
        exchange_id, instrument_id = split_stock_code(stock_code)
        logger.info(f"下载 {stock_code} {start_date} ~ {end_date} frequency={frequency}")
        minute_frame = query_minute_bars(stock_code, start_date, end_date, frequency)
        if minute_frame.empty:
            logger.warning(f"{stock_code} 区间内无分钟数据，跳过")
            continue
        daily_frame = query_daily_bars(stock_code, start_date, end_date)
        bar_frame = build_bar_frame(minute_frame, daily_frame, exchange_id, instrument_id, preces)
        logger.info(f"{stock_code} 转换得到 {len(bar_frame)} 根 bar")
        for trading_year, year_frame in bar_frame.groupby(bar_frame["TradingDay"] // 10000):
            frames_by_year.setdefault((exchange_id, str(trading_year)), []).append(year_frame)

    for (exchange_id, trading_year), frames in sorted(frames_by_year.items()):
        year_file = os.path.join(output_root, "Bar", f"Identity={exchange_id}.Stock", f"Year={trading_year}", f"{trading_year}_0.parquet")
        merged_frame = pd.concat(frames, ignore_index=True)
        start_trading_day = int(start_date.replace("-", ""))
        end_trading_day = int(end_date.replace("-", ""))
        row_count = merge_write_year_file(year_file, merged_frame, start_trading_day, end_trading_day)
        logger.info(f"写入 {year_file}（共 {row_count} 行）")


def load_config(config_file: str) -> dict[str, Any]:
    with open(config_file, "r", encoding="utf-8") as config_handle:
        return json.load(config_handle)


# ---------- 命令行入口 ----------


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BaoStock 分钟线 → QuantTrading Bar Parquet 导出器")
    parser.add_argument("--config", default=CONFIG_FILE, help="配置文件路径（提供 stocks 与 default_start_date）")
    parser.add_argument("--codes", default=None, help="逗号分隔的证券代码，缺省取配置文件 stocks")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD，缺省取配置 default_start_date")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD，缺省取今天")
    parser.add_argument("--output-root", default=None, help="Parquet 输出根目录，缺省取配置 parquet_output_root 或 D:/MdBaoStock")
    parser.add_argument("--frequency", default=DEFAULT_FREQUENCY, choices=["5", "15", "30", "60"], help="分钟频率")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    config = load_config(arguments.config)
    stock_codes = arguments.codes.split(",") if arguments.codes else config["stocks"]
    start_date = arguments.start or config.get("default_start_date", "2015-01-01")
    end_date = arguments.end or time.strftime("%Y-%m-%d")
    output_root = arguments.output_root or config.get("parquet_output_root", DEFAULT_OUTPUT_ROOT)
    logger.info(f"导出 {stock_codes} {start_date} ~ {end_date} → {output_root} frequency={arguments.frequency}")

    if login_with_retry() is None:
        raise SystemExit("BaoStock 登录失败")
    try:
        export_baostock_bar_parquet(stock_codes, start_date, end_date, output_root, arguments.frequency)
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
