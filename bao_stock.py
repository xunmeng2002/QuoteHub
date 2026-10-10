#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""A股全量数据下载器（Baostock → SQLite）。

覆盖 BaoStock 所有 API：
  - 日K行情（不复权 + 复权因子）
  - 季频财务数据（6 类：盈利能力、营运能力、成长能力、偿债能力、现金流量、杜邦分析）
  - 除权除息 / 业绩快报 / 业绩预告
  - 沪深300 / 上证50 / 中证500 成分股
  - 行业分类
  - 交易日历
  - 宏观经济（存款利率、贷款利率、存款准备金率、货币供应量）

用法: python bao_stock.py [选项]
       python bao_stock.py --update             # 日K行情增量更新
       python bao_stock.py --sync-securities     # 证券列表同步
       python bao_stock.py --update-all          # 全量更新所有数据
       python bao_stock.py --update-financials   # 财务数据
       python bao_stock.py --update-macro        # 宏观经济数据
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from typing import Any, Callable

import baostock as bs
import pandas as pd

# ---------- 配置 ----------

CONFIG_FILE = "bao_stock.json"
DB_PATH = "stock_data.db"
LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
MAX_LOGIN_RETRIES = 5
RETRY_DELAY_SECONDS = 5

logger = logging.getLogger(__name__)


# ---------- 工具函数 ----------


def _collect_rows(result_set: Any) -> list[list[str]]:
    """将 Baostock 结果集全部拉取为行列表。"""
    rows: list[list[str]] = []
    while (result_set.error_code == "0") and result_set.next():
        rows.append(result_set.get_row_data())
    return rows


# ---------- Baostock 登录 ----------


def login_with_retry(
    max_retries: int = MAX_LOGIN_RETRIES,
    delay: int = RETRY_DELAY_SECONDS,
) -> Any | None:
    for attempt in range(max_retries):
        login_result = bs.login()
        if login_result.error_code == "0":
            logger.info(f"Baostock 登录成功（尝试 {attempt + 1} 次）")
            return login_result
        logger.warning(
            f"登录失败（尝试 {attempt + 1}/{max_retries}）: {login_result.error_msg}"
        )
        if attempt < max_retries - 1:
            threading.Event().wait(delay)
    return None


# ---------- 数据库初始化 ----------


def init_database(connection: sqlite3.Connection) -> None:
    """初始化数据库：加载基础 Schema 后执行增量升级脚本。"""
    base_dir = os.path.dirname(os.path.abspath(__file__))

    schema_dir = os.path.join(base_dir, "sql", "schema")
    schema_path = os.path.join(schema_dir, "Create_v1.0.0.sql")

    if not os.path.exists(schema_path):
        logger.error(f"Schema 文件不存在: {schema_path}")
        return

    with open(schema_path, "r", encoding="utf-8") as schema_file:
        connection.executescript(schema_file.read())
    logger.info("基础 Schema（v1.0.0）加载完成")

    upgrades_dir = os.path.join(base_dir, "sql", "upgrades")
    if os.path.isdir(upgrades_dir):
        upgrade_files = sorted(
            f for f in os.listdir(upgrades_dir) if f.endswith(".sql")
        )
        for upgrade_file in upgrade_files:
            upgrade_path = os.path.join(upgrades_dir, upgrade_file)
            with open(upgrade_path, "r", encoding="utf-8") as upgrade_file_handle:
                connection.executescript(upgrade_file_handle.read())
            logger.info(f"升级脚本 {upgrade_file} 执行完成")

    connection.commit()
    logger.info("数据库 Schema 初始化完成")

    # 3. 旧结构一次性迁移：PerformanceExpress 旧列为臆测口径（接口无对应字段，从未写入成功），
    #    检测到旧列即重建；升级脚本每次全量重跑且 SQL 无条件 DDL，故迁移只能放在代码侧保证幂等。
    express_columns = {row[1] for row in connection.execute("PRAGMA table_info(PerformanceExpress)")}
    if express_columns and "TotalAsset" not in express_columns:
        express_count = connection.execute("SELECT COUNT(*) FROM PerformanceExpress").fetchone()[0]
        if express_count:
            logger.warning(f"PerformanceExpress 为旧结构且含 {express_count} 行数据，跳过自动迁移，请手工处理")
        else:
            connection.execute("DROP TABLE PerformanceExpress")
            with open(os.path.join(upgrades_dir, "Update_v2.2.0.sql"), "r", encoding="utf-8") as migrate_file:
                connection.executescript(migrate_file.read())
            logger.info("PerformanceExpress 旧结构（空表）已重建为 v2.2.0 列定义")


# ---------- 交易日推算 ----------


def _get_previous_trading_date(
    connection: sqlite3.Connection | None = None,
    lookback_days: int = 10,
) -> str | None:
    """推算最近一个交易日（不晚于昨天）。

    优先用本地 TradeDates 表查询，若表不存在或无数据则调用 Baostock API。

    Args:
        connection: 可选数据库连接，优先查本地缓存。
        lookback_days: 向前搜索天数，默认 10 天。

    Returns:
        交易日字符串 "YYYY-MM-DD"，或 None（无法确定时）。
    """
    today = datetime.today()
    yesterday = today - timedelta(days=1)

    if connection is not None:
        try:
            cutoff = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
            end = yesterday.strftime("%Y-%m-%d")
            cursor = connection.execute(
                """SELECT MAX(CalendarDate)
                   FROM TradeDates
                   WHERE CalendarDate BETWEEN ? AND ?
                     AND IsTradingDay = 1""",
                (cutoff, end),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]
        except sqlite3.OperationalError as error:
            logger.debug("本地 TradeDates 不可用，降级到 Baostock API：%s", error)

    start = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end = yesterday.strftime("%Y-%m-%d")
    result = bs.query_trade_dates(start_date=start, end_date=end)
    if result is None:
        logger.warning("交易日历查询返回空，将使用当天作为回退")
        return None
    if result.error_code != "0":
        logger.warning(f"交易日历查询失败（推算交易日时将使用当天）: {result.error_msg}")
        return None

    rows = _collect_rows(result)
    for row in reversed(rows):
        if len(row) >= 2 and row[1] == "1":  # is_trading_day == "1"
            return row[0]  # calendar_date

    logger.warning("过去 10 天未找到交易日，将使用当天作为回退")
    return None


# ---------- 查最新日期 ----------


def get_last_date(
    connection: sqlite3.Connection,
    stock_code: str,
) -> str | None:
    cursor = connection.execute(
        """SELECT
               MAX(Date)
           FROM
               DailyRawPrices
           WHERE
               Code = ?""",
        (stock_code,),
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


# ---------- 下载并存储单只股票 ----------


def fetch_and_store_stock(
    connection: sqlite3.Connection,
    stock_code: str,
    start_date: str,
) -> int:
    """下载单只股票的日K数据及复权因子，写入数据库。

    Returns:
        写入的价格行数。
    """
    # 原始价格：不复权（Baostock adjustflag="3"）
    price_result = bs.query_history_k_data_plus(
        stock_code,
        "date,code,open,high,low,close,volume,amount",
        start_date=start_date,
        frequency="d",
        adjustflag="3",
    )
    if price_result.error_code != "0":
        logger.error(f"{stock_code} 价格查询失败: {price_result.error_msg}")
        return 0

    price_rows = _collect_rows(price_result)
    if not price_rows:
        logger.info(f"{stock_code} 从 {start_date} 至今无新价格数据")
        return 0

    price_frame = pd.DataFrame(
        price_rows,
        columns=["Date", "Code", "Open", "High", "Low", "Close", "Volume", "Amount"],
    )
    for column in ["Open", "High", "Low", "Close", "Volume", "Amount"]:
        price_frame[column] = pd.to_numeric(price_frame[column], errors="coerce")

    # 复权因子：全部字段写入
    factor_result = bs.query_adjust_factor(stock_code, start_date=start_date)
    if factor_result.error_code == "0":
        factor_rows = _collect_rows(factor_result)
        if factor_rows:
            raw_fields: list[str] = factor_result.fields
            logger.info(f"因子原始字段: {raw_fields}")
            factor_frame = pd.DataFrame(factor_rows, columns=raw_fields)

            field_mapping: dict[str, str] = {
                "code": "Code",
                "dividOperateDate": "EventDate",
                "foreAdjustFactor": "ForeFactor",
                "backAdjustFactor": "BackFactor",
                "adjustFactor": "CumFactor",
            }
            factor_frame = factor_frame.rename(columns=field_mapping)
            # 只保留目标列，防御 Baostock 新增意外字段
            target_columns = ["Code", "EventDate", "ForeFactor", "BackFactor", "CumFactor"]
            factor_frame = factor_frame[
                [col for col in target_columns if col in factor_frame.columns]
            ]
            for column in ["ForeFactor", "BackFactor", "CumFactor"]:
                if column in factor_frame.columns:
                    factor_frame[column] = pd.to_numeric(
                        factor_frame[column], errors="coerce"
                    )

            factor_frame.to_sql(
                "AdjustFactors", connection, if_exists="append", index=False
            )
            logger.info(f"{stock_code} 复权因子新增 {len(factor_frame)} 条")
    else:
        logger.warning(f"{stock_code} 复权因子查询失败: {factor_result.error_msg}")

    price_frame.to_sql(
        "DailyRawPrices", connection, if_exists="append", index=False
    )
    logger.info(f"{stock_code} 原始价格新增 {len(price_frame)} 条")
    return len(price_frame)


# ---------- 证券合约同步 ----------


def sync_securities(connection: sqlite3.Connection) -> None:
    """同步全市场证券列表及交易状态（每日更新）。

    从 Baostock 拉取最新全市场证券列表，与本地 securities 表对比，
    自动处理：新股 IPO → 插入，退市 → 标记，更名/停复牌 → 记录日志。
    """
    # 用最近一个交易日查询，避免当天盘中数据未就绪
    query_date = _get_previous_trading_date(connection) or datetime.today().strftime(
        "%Y-%m-%d"
    )
    logger.info(f"证券同步基准日期: {query_date}")

    result = bs.query_all_stock(day=query_date)
    if result.error_code != "0":
        logger.error(f"获取证券列表失败: {result.error_msg}")
        return

    all_stocks = pd.DataFrame(
        _collect_rows(result), columns=result.fields
    )
    if all_stocks.empty:
        logger.warning(
            f"{query_date} 证券列表为空（Baostock 服务端可能异常），跳过本次证券同步"
        )
        return

    all_stocks.columns = all_stocks.columns.str.strip()
    logger.info(f"基准日期 {query_date} 全市场证券总数: {len(all_stocks)}")

    local_codes: set[str] = set(
        row[0] for row in connection.execute("SELECT Code FROM Securities")
    )
    today_codes: set[str] = set(all_stocks["code"].tolist())

    new_codes = today_codes - local_codes
    removed_codes = local_codes - today_codes
    common_codes = local_codes & today_codes

    if new_codes:
        # 批量获取基本信息，避免逐个查询
        basic_map: dict[str, dict[str, str]] = {}
        basic_result = bs.query_stock_basic(code="")
        if basic_result.error_code == "0":
            basic_df = pd.DataFrame(
                _collect_rows(basic_result), columns=basic_result.fields
            )
            basic_df.columns = basic_df.columns.str.strip()
            basic_map = {
                row["code"]: {
                    "ipoDate": row["ipoDate"],
                    "outDate": row["outDate"],
                    "type": row["type"],
                }
                for _, row in basic_df.iterrows()
                if row["code"] in new_codes
            }
        else:
            logger.warning(f"新股基本信息批量查询失败: {basic_result.error_msg}")

        for code in new_codes:
            stock_row = all_stocks[all_stocks["code"] == code].iloc[0]
            basic = basic_map.get(code, {})
            out_date = basic.get("outDate", "")
            connection.execute(
                """INSERT INTO Securities
                   (Code, CodeName, IpoDate, DelistDate, StockType, Exchange,
                    CurrentTradeStatus, UpdatedAt)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    code,
                    stock_row["code_name"],
                    basic.get("ipoDate", ""),
                    None if out_date in (None, "") else out_date,
                    basic.get("type", ""),
                    code[:2],
                    str(int(stock_row["tradeStatus"])),
                    query_date,
                ),
            )
            type_label = "股票" if basic.get("type") == "1" else "非股票"
            logger.info(f"新增证券 {code} {stock_row['code_name']}（{type_label}）")

    for code in removed_codes:
        connection.execute(
            """UPDATE Securities
               SET DelistDate = ?, CurrentTradeStatus = '0', UpdatedAt = ?
               WHERE Code = ?""",
            (query_date, query_date, code),
        )
        logger.info(f"证券 {code} 已退市，标记退市日期 {query_date}")

    #    先全部加载到内存，避免 5000+ 次单独查询
    local_info_map: dict[str, tuple[str, str]] = {
        row[0]: (row[1], str(row[2] if row[2] else ""))
        for row in connection.execute(
            "SELECT Code, CodeName, CurrentTradeStatus FROM Securities"
        )
    }

    for code in common_codes:
        stock_row = all_stocks[all_stocks["code"] == code].iloc[0]
        local_info = local_info_map.get(code)
        if local_info is None:
            continue

        local_name, local_status = local_info
        current_name = stock_row["code_name"]
        current_status = str(int(stock_row["tradeStatus"]))

        if local_name != current_name:
            connection.execute(
                "INSERT INTO NameChangeLogs (Code, Date, OldName, NewName) "
                "VALUES (?, ?, ?, ?)",
                (code, query_date, local_name, current_name),
            )
            connection.execute(
                "UPDATE Securities SET CodeName = ?, UpdatedAt = ? WHERE Code = ?",
                (current_name, query_date, code),
            )
            logger.info(f"证券 {code} 更名: 「{local_name}」→「{current_name}」")

        if local_status != current_status:
            connection.execute(
                "INSERT INTO TradeStatusLogs (Code, Date, TradeStatus) "
                "VALUES (?, ?, ?)",
                (code, query_date, current_status),
            )
            connection.execute(
                "UPDATE Securities SET CurrentTradeStatus = ?, UpdatedAt = ? "
                "WHERE Code = ?",
                (current_status, query_date, code),
            )
            status_label = "复牌" if current_status == "1" else "停牌"
            logger.info(f"证券 {code} {status_label}（{local_status} → {current_status}）")

    connection.commit()
    logger.info(
        f"证券同步完成: 总数 {len(today_codes)}, "
        f"新增 {len(new_codes)}, 退市 {len(removed_codes)}"
    )


# ------------------------------------------------------------------
# 通用财务数据采集器
# ------------------------------------------------------------------


def _fetch_and_store_financial(
    connection: sqlite3.Connection,
    code: str,
    year: int,
    quarter: int,
    query_func: Callable[..., Any],
    table_name: str,
    numeric_fields: list[str],
) -> int:
    """通用财务数据采集器。

    调用 Baostock 财务查询接口，将结果写入指定 SQLite 表。
    写入前会删除相同 (Code, Year, Quarter) 的旧数据。

    Returns:
        写入行数。
    """
    connection.execute(
        f"DELETE FROM {table_name} WHERE Code = ? AND Year = ? AND Quarter = ?",
        (code, year, quarter),
    )

    result = query_func(code, str(year), str(quarter))
    if result is None:
        logger.info(f"{code} {table_name} {year}Q{quarter} 无返回值")
        return 0
    if result.error_code != "0":
        logger.error(
            f"{code} {table_name} 查询失败 ({year}Q{quarter}): {result.error_msg}"
        )
        return 0

    rows = _collect_rows(result)
    if not rows:
        logger.info(f"{code} {table_name} {year}Q{quarter} 无数据")
        return 0

    frame = pd.DataFrame(rows, columns=result.fields)
    frame["Year"] = year
    frame["Quarter"] = quarter
    frame = frame.rename(
        columns={
            "code": "Code",
            "pubDate": "PubDate",
            "statDate": "StatDate",
        }
    )

    for col in numeric_fields:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    frame.to_sql(table_name, connection, if_exists="append", index=False)
    logger.info(f"{code} {table_name} {year}Q{quarter} 写入 {len(frame)} 条")
    return len(frame)


def _default_year_quarters() -> list[tuple[int, int]]:
    """获取最近 4 个季度的 (year, quarter) 列表（按时间升序）。"""
    today = datetime.today()
    quarters: list[tuple[int, int]] = []
    for i in range(4):
        total = today.year * 12 + today.month - 1 - i * 3
        y = total // 12
        q = (total % 12) // 3 + 1
        quarters.append((y, q))
    return list(reversed(quarters))


def _quarter_window_start(quarters_back: int = 4) -> str:
    """最近 quarters_back 个季度窗口的起始日期（最早那季的首日），供按日期区间查询的接口使用。"""
    today = datetime.today()
    quarter_start_month_index = (today.month - 1) // 3 * 3
    total_months = today.year * 12 + quarter_start_month_index - quarters_back * 3
    return f"{total_months // 12:04d}-{total_months % 12 + 1:02d}-01"


# ------------------------------------------------------------------
# 季频财务数据（6 类）
# ------------------------------------------------------------------


def update_financial_data(
    connection: sqlite3.Connection,
    stock_codes: list[str],
    years: list[int] | None = None,
    quarters: list[int] | None = None,
) -> None:
    """遍历股票列表，下载 6 大财务指标数据。

    Args:
        years: 指定年份列表，默认最近 4 个季度的年份。
        quarters: 指定季度列表，默认 [1,2,3,4]。
    """
    if years is None or quarters is None:
        yq_pairs = _default_year_quarters()
    else:
        yq_pairs = [(y, q) for y in years for q in quarters]

    financial_apis: list[tuple[Any, str, list[str]]] = [
        (
            bs.query_profit_data,
            "ProfitData",
            [
                "roeAvg", "npMargin", "gpMargin", "netProfit", "epsTTM",
                "MBRevenue", "totalShare", "liqaShare",
            ],
        ),
        (
            bs.query_operation_data,
            "OperationData",
            [
                "NRTurnRatio", "NRTurnDays", "INVTurnRatio", "INVTurnDays",
                "CATurnRatio", "AssetTurnRatio",
            ],
        ),
        (
            bs.query_growth_data,
            "GrowthData",
            ["YOYEquity", "YOYAsset", "YOYNI", "YOYEPSBasic", "YOYPNI"],
        ),
        (
            bs.query_balance_data,
            "BalanceData",
            [
                "currentRatio", "quickRatio", "cashRatio", "YOYLiability",
                "liabilityToAsset", "assetToEquity",
            ],
        ),
        (
            bs.query_cash_flow_data,
            "CashFlowData",
            [
                "CAToAsset", "NCAToAsset", "tangibleAssetToAsset",
                "ebitToInterest", "CFOToOR", "CFOToNP", "CFOToGr",
            ],
        ),
        (
            bs.query_dupont_data,
            "DuPontData",
            [
                "dupontROE", "dupontAssetStoEquity", "dupontAssetTurn",
                "dupontPnitoni", "dupontNitogr", "dupontTaxBurden",
                "dupontIntburden", "dupontEbittogr",
            ],
        ),
    ]

    total = 0
    for code in stock_codes:
        for query_func, table_name, numeric_fields in financial_apis:
            for year, quarter in yq_pairs:
                total += _fetch_and_store_financial(
                    connection, code, year, quarter,
                    query_func, table_name, numeric_fields,
                )
    logger.info(f"财务数据更新完成，总写入 {total} 条")


# ------------------------------------------------------------------
# 除权除息信息
# ------------------------------------------------------------------


def update_dividend_data(
    connection: sqlite3.Connection,
    stock_codes: list[str],
    years: list[int] | None = None,
) -> None:
    """下载除权除息信息。"""
    if years is None:
        today = datetime.today()
        years = list(range(today.year - 3, today.year + 1))

    column_map = {
        "code": "Code",
        "dividPreNoticeDate": "DividPreNoticeDate",
        "dividAgmPumDate": "DividAgmPumDate",
        "dividPlanAnnounceDate": "DividPlanAnnounceDate",
        "dividPlanDate": "DividPlanDate",
        "dividRegistDate": "DividRegistDate",
        "dividOperateDate": "DividOperateDate",
        "dividPayDate": "DividPayDate",
        "dividStockMarketDate": "DividStockMarketDate",
        "dividCashPsBeforeTax": "DividCashPsBeforeTax",
        "dividCashPsAfterTax": "DividCashPsAfterTax",
        "dividStocksPs": "DividStocksPs",
        "dividCashStock": "DividCashStock",
        "dividReserveToStockPs": "DividReserveToStockPs",
    }
    numeric_fields = [
        "DividCashPsBeforeTax", "DividCashPsAfterTax", "DividStocksPs",
        "DividCashStock", "DividReserveToStockPs",
    ]

    total = 0
    for code in stock_codes:
        for year in years:
            for year_type in ("report", "operate"):
                connection.execute(
                    "DELETE FROM DividendData WHERE Code=? AND Year=? AND YearType=?",
                    (code, year, year_type),
                )
                result = bs.query_dividend_data(code, str(year), year_type)
                if result.error_code != "0":
                    logger.warning(
                        f"{code} 除权除息查询失败 ({year}/{year_type}): {result.error_msg}"
                    )
                    continue
                rows = _collect_rows(result)
                if not rows:
                    continue
                frame = pd.DataFrame(rows, columns=result.fields)
                frame["Year"] = year
                frame["YearType"] = year_type
                frame = frame.rename(columns=column_map)
                for col in numeric_fields:
                    if col in frame.columns:
                        frame[col] = pd.to_numeric(frame[col], errors="coerce")
                frame = frame.drop_duplicates(
                    subset=["Code", "Year", "YearType", "DividOperateDate"]
                )
                frame.to_sql("DividendData", connection, if_exists="append", index=False)
                total += len(frame)
    logger.info(f"除权除息数据更新完成，总写入 {total} 条")


# ------------------------------------------------------------------
# 业绩快报
# ------------------------------------------------------------------


def update_performance_express(
    connection: sqlite3.Connection,
    stock_codes: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
) -> None:
    """下载业绩快报。接口按日期区间查询（非年+季），Year/Quarter 由统计日期推导。"""
    start_date = start_date or _quarter_window_start()
    end_date = end_date or datetime.today().strftime("%Y-%m-%d")
    total = 0

    column_map = {
        "code": "Code",
        "performanceExpPubDate": "PubDate",
        "performanceExpUpdateDate": "UpdateDate",
        "performanceExpStatDate": "StatDate",
        "performanceExpressTotalAsset": "TotalAsset",
        "performanceExpressNetAsset": "NetAsset",
        "performanceExpressEPSChgPct": "EPSChgPct",
        "performanceExpressROEWa": "ROEWa",
        "performanceExpressEPSDiluted": "EPSDiluted",
        "performanceExpressGRYOY": "GRYOY",
        "performanceExpressOPYOY": "OPYOY",
    }
    numeric_fields = ["TotalAsset", "NetAsset", "EPSChgPct", "ROEWa", "EPSDiluted", "GRYOY", "OPYOY"]
    target_columns = ["Code", "Year", "Quarter", "PubDate", "UpdateDate", "StatDate",
                      "TotalAsset", "NetAsset", "EPSChgPct", "ROEWa", "EPSDiluted", "GRYOY", "OPYOY"]

    for code in stock_codes:
        # 接口按公告日期过滤，会返回 StatDate 早于窗口起点的行（如 8 月公告的中报快报）；
        # 删除范围需同时覆盖 PubDate，否则重跑触发主键冲突
        connection.execute(
            "DELETE FROM PerformanceExpress WHERE Code=? AND (PubDate>=? OR StatDate>=?)",
            (code, start_date, start_date),
        )
        result = bs.query_performance_express_report(
            code, start_date=start_date, end_date=end_date
        )
        if result is None:
            logger.warning(f"{code} 业绩快报查询无返回值（{start_date} ~ {end_date}）")
            continue
        if result.error_code != "0":
            logger.warning(f"{code} 业绩快报查询失败: {result.error_msg}")
            continue
        rows = _collect_rows(result)
        if not rows:
            continue
        frame = pd.DataFrame(rows, columns=result.fields)
        frame = frame.rename(columns=column_map)
        frame["Year"] = frame["StatDate"].str[:4].astype(int)
        frame["Quarter"] = (frame["StatDate"].str[5:7].astype(int) - 1) // 3 + 1
        for col in numeric_fields:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
        # API 返回字段多于建表列，只保留目标列；同季多行（快报修订）按 PK 去重保留最新公告
        frame = frame[[col for col in target_columns if col in frame.columns]]
        frame = frame.drop_duplicates(subset=["Code", "Year", "Quarter"], keep="last")
        frame.to_sql(
            "PerformanceExpress", connection, if_exists="append", index=False
        )
        total += len(frame)
    logger.info(f"业绩快报更新完成，总写入 {total} 条（{start_date} ~ {end_date}）")


# ------------------------------------------------------------------
# 业绩预告
# ------------------------------------------------------------------


def update_forecast_reports(
    connection: sqlite3.Connection,
    stock_codes: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
) -> None:
    """下载业绩预告。接口按日期区间查询（非年+季），Year/Quarter 由统计日期推导。"""
    start_date = start_date or _quarter_window_start()
    end_date = end_date or datetime.today().strftime("%Y-%m-%d")
    total = 0

    field_mapping = {
        "code": "Code",
        "profitForcastExpPubDate": "PubDate",
        "profitForcastExpStatDate": "StatDate",
        "profitForcastType": "ForecastType",
        "profitForcastAbstract": "Abstract",
        "profitForcastChgPctUp": "ChgPctUp",
        "profitForcastChgPctDwn": "ChgPctDwn",
    }
    numeric_fields = ["ChgPctUp", "ChgPctDwn"]
    target_columns = ["Code", "Year", "Quarter", "PubDate", "StatDate", "ForecastType", "Abstract", "ChgPctUp", "ChgPctDwn"]

    for code in stock_codes:
        # 同业绩快报：接口按公告日期过滤，删除范围需同时覆盖 PubDate 保证重跑幂等
        connection.execute(
            "DELETE FROM ForecastReports WHERE Code=? AND (PubDate>=? OR StatDate>=?)",
            (code, start_date, start_date),
        )
        result = bs.query_forecast_report(
            code, start_date=start_date, end_date=end_date
        )
        if result is None:
            logger.warning(f"{code} 业绩预告查询无返回值（{start_date} ~ {end_date}）")
            continue
        if result.error_code != "0":
            logger.warning(f"{code} 业绩预告查询失败: {result.error_msg}")
            continue
        rows = _collect_rows(result)
        if not rows:
            continue
        frame = pd.DataFrame(rows, columns=result.fields)
        frame = frame.rename(columns=field_mapping)
        frame["Year"] = frame["StatDate"].str[:4].astype(int)
        frame["Quarter"] = (frame["StatDate"].str[5:7].astype(int) - 1) // 3 + 1
        for col in numeric_fields:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
        # API 返回字段多于建表列，只保留目标列（同季多份预告由 PK 中的 PubDate 区分）
        frame = frame[[col for col in target_columns if col in frame.columns]]
        frame.to_sql(
            "ForecastReports", connection, if_exists="append", index=False
        )
        total += len(frame)
    logger.info(f"业绩预告更新完成，总写入 {total} 条（{start_date} ~ {end_date}）")


# ------------------------------------------------------------------
# 指数成分股
# ------------------------------------------------------------------


def update_index_constituents(connection: sqlite3.Connection) -> None:
    """更新沪深300 / 上证50 / 中证500 成分股。"""
    query_date = _get_previous_trading_date(connection) or datetime.today().strftime(
        "%Y-%m-%d"
    )
    logger.info(f"指数成分股基准日期: {query_date}")
    indices: list[tuple[str, Callable[[str], Any]]] = [
        ("hs300", bs.query_hs300_stocks),
        ("sz50", bs.query_sz50_stocks),
        ("zz500", bs.query_zz500_stocks),
    ]
    total = 0

    for index_code, query_func in indices:
        connection.execute(
            "DELETE FROM IndexConstituents WHERE IndexCode=? AND UpdateDate=?",
            (index_code, query_date),
        )
        result = query_func(query_date)
        if result is None:
            logger.warning(f"{index_code} 查询返回空")
            continue
        if result.error_code != "0":
            logger.warning(f"{index_code} 查询失败: {result.error_msg}")
            continue
        rows = _collect_rows(result)
        if not rows:
            continue
        frame = pd.DataFrame(rows, columns=result.fields)
        frame["IndexCode"] = index_code
        frame["UpdateDate"] = query_date
        frame = frame.rename(
            columns={"code": "Code", "code_name": "CodeName"}
        )
        frame.to_sql("IndexConstituents", connection, if_exists="append", index=False)
        total += len(frame)
        logger.info(f"{index_code} 成分股 {len(frame)} 只")

    logger.info(f"指数成分股更新完成，总写入 {total} 条")


# ------------------------------------------------------------------
# 行业分类
# ------------------------------------------------------------------


def update_industry(connection: sqlite3.Connection) -> None:
    """更新行业分类信息，同时更新 Securities 表中的行业字段。"""
    today = datetime.today().strftime("%Y-%m-%d")

    result = bs.query_stock_industry()
    if result is None:
        logger.error("行业分类查询返回空")
        return
    if result.error_code != "0":
        logger.error(f"行业分类查询失败: {result.error_msg}")
        return

    rows = _collect_rows(result)
    if not rows:
        logger.info("行业分类无数据")
        return

    frame = pd.DataFrame(rows, columns=result.fields)

    column_map = {
        "code": "Code",
        "code_name": "CodeName",
        "industry": "Industry",
        "industryClassification": "IndustryClassification",
    }
    frame = frame.rename(columns=column_map)

    connection.execute(
        "DELETE FROM IndustryClassification WHERE UpdateDate=?", (today,)
    )
    frame["UpdateDate"] = today
    frame.to_sql(
        "IndustryClassification", connection, if_exists="append", index=False
    )

    updated_count = 0
    for _, row in frame.iterrows():
        connection.execute(
            "UPDATE Securities SET IndustryCode=?, IndustryName=?, UpdatedAt=? "
            "WHERE Code=?",
            (
                row.get("Industry", ""),
                row.get("IndustryClassification", ""),
                today,
                row["Code"],
            ),
        )
        updated_count += 1

    logger.info(
        f"行业分类更新完成，新增 {len(frame)} 条，"
        f"同步 Securities 行业信息 {updated_count} 条"
    )


# ------------------------------------------------------------------
# 交易日历
# ------------------------------------------------------------------


def update_trade_dates(
    connection: sqlite3.Connection,
    start_date: str = "1990-12-19",
    end_date: str | None = None,
) -> None:
    """更新交易日历。"""
    if end_date is None:
        end_date = datetime.today().strftime("%Y-%m-%d")

    connection.execute("DELETE FROM TradeDates WHERE CalendarDate >= ?", (start_date,))

    result = bs.query_trade_dates(start_date=start_date, end_date=end_date)
    if result.error_code != "0":
        logger.error(f"交易日历查询失败: {result.error_msg}")
        return

    rows = _collect_rows(result)
    if not rows:
        return

    frame = pd.DataFrame(rows, columns=result.fields)
    frame["is_trading_day"] = pd.to_numeric(frame["is_trading_day"], errors="coerce")
    frame = frame.rename(
        columns={
            "calendar_date": "CalendarDate",
            "is_trading_day": "IsTradingDay",
        }
    )
    frame[["CalendarDate", "IsTradingDay"]].to_sql(
        "TradeDates", connection, if_exists="append", index=False
    )
    logger.info(f"交易日历更新完成，共 {len(frame)} 条")


# ------------------------------------------------------------------
# 宏观经济数据（5 类）
# ------------------------------------------------------------------


def update_deposit_rates(connection: sqlite3.Connection) -> None:
    """更新存款利率。"""
    connection.execute("DELETE FROM DepositRates")
    result = bs.query_deposit_rate_data(start_date="2000-01-01")
    if result is None:
        logger.error("存款利率查询返回空")
        return
    if result.error_code != "0":
        logger.error(f"存款利率查询失败: {result.error_msg}")
        return
    rows = _collect_rows(result)
    if not rows:
        return
    frame = pd.DataFrame(rows, columns=result.fields)
    frame = frame.rename(
        columns={
            "pubDate": "PubDate",
            "demandDepositRate": "DemandDepositRate",
            "fixedDepositRate3Month": "FixedDepositRate3Month",
            "fixedDepositRate6Month": "FixedDepositRate6Month",
            "fixedDepositRate1Year": "FixedDepositRate1Year",
            "fixedDepositRate2Year": "FixedDepositRate2Year",
            "fixedDepositRate3Year": "FixedDepositRate3Year",
            "fixedDepositRate5Year": "FixedDepositRate5Year",
            "installmentFixedDepositRate1Year": "InstallmentFixedDepositRate1Year",
            "installmentFixedDepositRate3Year": "InstallmentFixedDepositRate3Year",
            "installmentFixedDepositRate5Year": "InstallmentFixedDepositRate5Year",
        }
    )
    numeric_cols = [
        "DemandDepositRate", "FixedDepositRate3Month", "FixedDepositRate6Month",
        "FixedDepositRate1Year", "FixedDepositRate2Year", "FixedDepositRate3Year",
        "FixedDepositRate5Year", "InstallmentFixedDepositRate1Year",
        "InstallmentFixedDepositRate3Year", "InstallmentFixedDepositRate5Year",
    ]
    for col in numeric_cols:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame.to_sql("DepositRates", connection, if_exists="append", index=False)
    logger.info(f"存款利率更新完成，共 {len(frame)} 条")


def update_loan_rates(connection: sqlite3.Connection) -> None:
    """更新贷款利率。"""
    connection.execute("DELETE FROM LoanRates")
    result = bs.query_loan_rate_data(start_date="2000-01-01")
    if result is None:
        logger.error("贷款利率查询返回空")
        return
    if result.error_code != "0":
        logger.error(f"贷款利率查询失败: {result.error_msg}")
        return
    rows = _collect_rows(result)
    if not rows:
        return
    frame = pd.DataFrame(rows, columns=result.fields)
    frame = frame.rename(
        columns={
            "pubDate": "PubDate",
            "loanRate6Month": "LoanRate6Month",
            "loanRate6MonthTo1Year": "LoanRate6MonthTo1Year",
            "loanRate1YearTo3Year": "LoanRate1YearTo3Year",
            "loanRate3YearTo5Year": "LoanRate3YearTo5Year",
            "loanRateAbove5Year": "LoanRateAbove5Year",
            "mortgateRateBelow5Year": "MortgateRateBelow5Year",
            "mortgateRateAbove5Year": "MortgateRateAbove5Year",
        }
    )
    numeric_cols = [
        "LoanRate6Month", "LoanRate6MonthTo1Year", "LoanRate1YearTo3Year",
        "LoanRate3YearTo5Year", "LoanRateAbove5Year",
        "MortgateRateBelow5Year", "MortgateRateAbove5Year",
    ]
    for col in numeric_cols:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame.to_sql("LoanRates", connection, if_exists="append", index=False)
    logger.info(f"贷款利率更新完成，共 {len(frame)} 条")


def update_reserve_ratios(connection: sqlite3.Connection) -> None:
    """更新存款准备金率。"""
    connection.execute("DELETE FROM ReserveRatios")
    result = bs.query_required_reserve_ratio_data(start_date="2000-01-01")
    if result is None:
        logger.error("准备金率查询返回空")
        return
    if result.error_code != "0":
        logger.error(f"准备金率查询失败: {result.error_msg}")
        return
    rows = _collect_rows(result)
    if not rows:
        return
    frame = pd.DataFrame(rows, columns=result.fields)
    frame = frame.rename(
        columns={
            "pubDate": "PubDate",
            "effectiveDate": "EffectiveDate",
            "bigInstitutionsRatioPre": "BigInstitutionsRatioPre",
            "bigInstitutionsRatioAfter": "BigInstitutionsRatioAfter",
            "mediumInstitutionsRatioPre": "MediumInstitutionsRatioPre",
            "mediumInstitutionsRatioAfter": "MediumInstitutionsRatioAfter",
        }
    )
    numeric_cols = [
        "BigInstitutionsRatioPre", "BigInstitutionsRatioAfter",
        "MediumInstitutionsRatioPre", "MediumInstitutionsRatioAfter",
    ]
    for col in numeric_cols:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame.to_sql("ReserveRatios", connection, if_exists="append", index=False)
    logger.info(f"存款准备金率更新完成，共 {len(frame)} 条")


def update_money_supply(connection: sqlite3.Connection) -> None:
    """更新月度货币供应量（M0/M1/M2）。"""
    connection.execute("DELETE FROM MoneySupply")
    end_month = datetime.today().strftime("%Y-%m")
    result = bs.query_money_supply_data_month(
        start_date="2000-01", end_date=end_month
    )
    if result is None:
        logger.error("货币供应量查询返回空")
        return
    if result.error_code != "0":
        logger.error(f"货币供应量查询失败: {result.error_msg}")
        return
    rows = _collect_rows(result)
    if not rows:
        return
    frame = pd.DataFrame(rows, columns=result.fields)
    frame = frame.rename(
        columns={
            "statYear": "StatYear",
            "statMonth": "StatMonth",
            "m0Month": "M0Month",
            "m0YOY": "M0YOY",
            "m0ChainRelative": "M0ChainRelative",
            "m1Month": "M1Month",
            "m1YOY": "M1YOY",
            "m1ChainRelative": "M1ChainRelative",
            "m2Month": "M2Month",
            "m2YOY": "M2YOY",
            "m2ChainRelative": "M2ChainRelative",
        }
    )
    numeric_cols = [
        "M0Month", "M0YOY", "M0ChainRelative",
        "M1Month", "M1YOY", "M1ChainRelative",
        "M2Month", "M2YOY", "M2ChainRelative",
    ]
    for col in numeric_cols:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["StatYear"] = pd.to_numeric(frame["StatYear"], errors="coerce").astype(int)
    frame["StatMonth"] = pd.to_numeric(frame["StatMonth"], errors="coerce").astype(int)
    frame.to_sql("MoneySupply", connection, if_exists="append", index=False)
    logger.info(f"货币供应量更新完成，共 {len(frame)} 条")


# ------------------------------------------------------------------
# 全量更新调度
# ------------------------------------------------------------------


def _run_update(task_func: Callable[[sqlite3.Connection], None]) -> None:
    """登录 Baostock → 执行任务 → 注销的统一流程。"""
    with contextlib.closing(sqlite3.connect(DB_PATH)) as connection:
        init_database(connection)
        login_result = login_with_retry()
        if login_result is None:
            logger.error("Baostock 登录失败，操作取消")
            return
        try:
            task_func(connection)
            connection.commit()
        finally:
            bs.logout()


def update_all_extended(
    connection: sqlite3.Connection, config: dict[str, Any]
) -> None:
    """全量更新所有数据类别。"""
    stock_codes: list[str] = require_config_value(config, "stocks", CONFIG_FILE)

    logger.info("===== 全量更新开始 =====")

    logger.info("--- 1/7 证券同步 ---")
    sync_securities(connection)

    logger.info("--- 2/7 行业分类 ---")
    update_industry(connection)

    logger.info("--- 3/7 指数成分股 ---")
    update_index_constituents(connection)

    if stock_codes:
        logger.info("--- 4/7 日K行情 ---")
        default_start = config.get("default_start_date", "2000-01-01")
        for stock_code in stock_codes:
            logger.info(f"处理 {stock_code} ...")
            last_date = get_last_date(connection, stock_code)
            if last_date:
                start = (
                    datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
                ).strftime("%Y-%m-%d")
            else:
                start = default_start
            if datetime.strptime(start, "%Y-%m-%d") <= datetime.today():
                fetch_and_store_stock(connection, stock_code, start)

    if stock_codes:
        logger.info("--- 5/7 财务数据 & 除权除息 & 业绩 ---")
        update_financial_data(connection, stock_codes)
        update_dividend_data(connection, stock_codes)
        update_performance_express(connection, stock_codes)
        update_forecast_reports(connection, stock_codes)

    logger.info("--- 6/7 宏观经济 ---")
    update_deposit_rates(connection)
    update_loan_rates(connection)
    update_reserve_ratios(connection)
    update_money_supply(connection)

    logger.info("--- 7/7 交易日历 ---")
    update_trade_dates(connection)

    connection.commit()
    logger.info("===== 全量更新完成 =====")


# ---------- 主流程 ----------


def load_config(config_path: str) -> dict[str, Any]:
    """读取 JSON 配置文件。"""
    with open(config_path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_config_file(config_file: str) -> dict[str, Any]:
    """读取配置文件；文件不存在即报错退出——不伪造一份空配置顶上。

    原先文件缺失时返回 `{"stocks": []}`，于是"配置没写"与"配置写空"表现一致。
    """
    if not os.path.exists(config_file):
        raise SystemExit(f"配置文件不存在: {config_file}")
    return load_config(config_file)


def require_config_value(
    config: dict[str, Any],
    config_key: str,
    config_file: str,
    command_line_alternative: str | None = None,
) -> Any:
    """取必填配置项；缺失或为空即报错退出——配置一律不兜底。

    兜底会让"配置漏写"与"配置写对"表现一致：代码清单悄悄变空、输出路径悄悄落到别处，
    两者都没有任何提示。缺了就直接失败，把问题停在启动那一刻。
    """
    config_value = config.get(config_key)
    if not config_value:
        alternatives = f"，或用 {command_line_alternative} 传入" if command_line_alternative else ""
        raise SystemExit(f"配置缺少必填项 {config_key}（{config_file}）{alternatives}")
    return config_value


def update_all() -> None:
    """遍历配置中的股票代码，增量更新行情数据。"""
    config = load_config_file(CONFIG_FILE)
    stock_codes: list[str] = require_config_value(config, "stocks", CONFIG_FILE)
    default_start: str = config.get("default_start_date", "2000-01-01")

    with contextlib.closing(sqlite3.connect(DB_PATH)) as connection:
        init_database(connection)

        login_result = login_with_retry()
        if login_result is None:
            logger.error("Baostock 登录失败，程序退出")
            return

        try:
            sync_securities(connection)

            for stock_code in stock_codes:
                logger.info(f"处理 {stock_code} ...")
                last_date = get_last_date(connection, stock_code)
                if last_date:
                    start = (
                        datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
                    ).strftime("%Y-%m-%d")
                    logger.info(
                        f"  上次数据截止 {last_date}，从 {start} 开始增量下载"
                    )
                else:
                    start = default_start
                    logger.info(
                        f"  无历史数据，从默认日期 {start} 开始全量下载"
                    )

                if datetime.strptime(start, "%Y-%m-%d") > datetime.today():
                    logger.info(f"  {stock_code} 已是最新，跳过")
                    continue

                fetch_and_store_stock(connection, stock_code, start)
        finally:
            bs.logout()
            logger.info("Baostock 注销")


# ---------- 入口 ----------

if __name__ == "__main__":
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)

    if "--update" in sys.argv:
        update_all()
    elif "--sync-securities" in sys.argv:
        with contextlib.closing(sqlite3.connect(DB_PATH)) as connection:
            init_database(connection)
            login_result = login_with_retry()
            if login_result is None:
                logger.error("Baostock 登录失败，证券同步无法继续")
            else:
                try:
                    sync_securities(connection)
                finally:
                    bs.logout()
    elif "--update-all" in sys.argv:
        config = load_config_file(CONFIG_FILE)
        _run_update(lambda conn: update_all_extended(conn, config))
    elif "--update-financials" in sys.argv:
        config = load_config_file(CONFIG_FILE)
        stock_codes = require_config_value(config, "stocks", CONFIG_FILE)
        _run_update(lambda conn: update_financial_data(conn, stock_codes))
    elif "--update-dividends" in sys.argv:
        config = load_config_file(CONFIG_FILE)
        stock_codes = require_config_value(config, "stocks", CONFIG_FILE)
        _run_update(lambda conn: update_dividend_data(conn, stock_codes))
    elif "--update-reports" in sys.argv:
        config = load_config_file(CONFIG_FILE)
        stock_codes = require_config_value(config, "stocks", CONFIG_FILE)
        def _update_reports(conn: sqlite3.Connection) -> None:
            update_performance_express(conn, stock_codes)
            update_forecast_reports(conn, stock_codes)
        _run_update(_update_reports)
    elif "--update-index" in sys.argv:
        _run_update(update_index_constituents)
    elif "--update-industry" in sys.argv:
        _run_update(update_industry)
    elif "--update-trade-dates" in sys.argv:
        _run_update(update_trade_dates)
    elif "--update-macro" in sys.argv:
        def _update_macro(conn: sqlite3.Connection) -> None:
            update_deposit_rates(conn)
            update_loan_rates(conn)
            update_reserve_ratios(conn)
            update_money_supply(conn)
        _run_update(_update_macro)
    else:
        logger.info(
            "用法: python bao_stock.py [选项]\n\n"
            "选项:\n"
            "  --update             增量更新日K行情\n"
            "  --sync-securities    同步全市场证券列表\n"
            "  --update-all         全量更新所有数据\n"
            "  --update-financials  更新财务数据（6类指标）\n"
            "  --update-dividends   更新除权除息信息\n"
            "  --update-reports     更新业绩快报和业绩预告\n"
            "  --update-index       更新指数成分股（沪深300/上证50/中证500）\n"
            "  --update-industry    更新行业分类\n"
            "  --update-trade-dates 更新交易日历\n"
            "  --update-macro       更新宏观经济数据（4类）"
        )