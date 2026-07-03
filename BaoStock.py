#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""A股日K行情数据下载器（Baostock → SQLite）。

支持增量更新，自动下载原始不复权价格和复权因子。
用法: python BaoStock.py --update
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any

import baostock as bs
import pandas as pd
import sqlite3

# ---------- 配置 ----------

CONFIG_FILE = "config.json"
DB_PATH = "stock_data.db"
LOG_LEVEL = logging.INFO
MAX_LOGIN_RETRIES = 5
RETRY_DELAY_SECONDS = 5

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
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
            time.sleep(delay)
    return None


# ---------- 数据库初始化 ----------


def init_database(connection: sqlite3.Connection) -> None:
    """初始化数据库：从 schema 文件加载 DDL，清理旧命名表。"""
    schema_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "sql", "schema"
    )
    schema_path = os.path.join(schema_dir, "Create_v1.0.0.sql")

    if not os.path.exists(schema_path):
        logger.error(f"Schema 文件不存在: {schema_path}")
        return

    with open(schema_path, "r", encoding="utf-8") as schema_file:
        connection.executescript(schema_file.read())

    connection.commit()
    logger.info("数据库 Schema 初始化完成（v1.0.0）")


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
    # 1. 原始价格（不复权，adjustflag="3"）
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

    # 2. 复权因子：全部字段存储
    factor_result = bs.query_adjust_factor(stock_code, start_date=start_date)
    if factor_result.error_code == "0":
        factor_rows = _collect_rows(factor_result)
        if factor_rows:
            raw_fields: list[str] = factor_result.fields
            logger.info(f"因子原始字段: {raw_fields}")
            factor_frame = pd.DataFrame(factor_rows, columns=raw_fields)

            # 动态构建字段映射（仅映射已知字段，其余忽略）
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

    # 3. 写入价格
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
    today = datetime.today().strftime("%Y-%m-%d")

    # 1. 获取今日全市场证券列表
    result = bs.query_all_stock(day=today)
    if result.error_code != "0":
        logger.error(f"获取证券列表失败: {result.error_msg}")
        return

    all_stocks = pd.DataFrame(
        _collect_rows(result), columns=result.fields
    )
    if all_stocks.empty:
        logger.info(f"{today} 无证券列表返回（可能非交易日），跳过证券同步")
        return

    all_stocks.columns = all_stocks.columns.str.strip()
    logger.info(f"今日全市场证券总数: {len(all_stocks)}")

    # 2. 对比本地已有数据
    local_codes: set[str] = set(
        row[0] for row in connection.execute("SELECT Code FROM Securities")
    )
    today_codes: set[str] = set(all_stocks["code"].tolist())

    new_codes = today_codes - local_codes
    removed_codes = local_codes - today_codes
    common_codes = local_codes & today_codes

    # 3. 新股 IPO
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
                    today,
                ),
            )
            type_label = "股票" if basic.get("type") == "1" else "非股票"
            logger.info(f"新增证券 {code} {stock_row['code_name']}（{type_label}）")

    # 4. 退市处理
    for code in removed_codes:
        connection.execute(
            """UPDATE Securities
               SET DelistDate = ?, CurrentTradeStatus = '0', UpdatedAt = ?
               WHERE Code = ?""",
            (today, today, code),
        )
        logger.info(f"证券 {code} 已退市，标记退市日期 {today}")

    # 5. 常态检查 common_codes：更名、停复牌
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

        # 名称变更
        if local_name != current_name:
            connection.execute(
                "INSERT INTO NameChangeLogs (Code, Date, OldName, NewName) "
                "VALUES (?, ?, ?, ?)",
                (code, today, local_name, current_name),
            )
            connection.execute(
                "UPDATE Securities SET CodeName = ?, UpdatedAt = ? WHERE Code = ?",
                (current_name, today, code),
            )
            logger.info(f"证券 {code} 更名: 「{local_name}」→「{current_name}」")

        # 交易状态变更（停牌/复牌）
        if local_status != current_status:
            connection.execute(
                "INSERT INTO TradeStatusLogs (Code, Date, TradeStatus) "
                "VALUES (?, ?, ?)",
                (code, today, current_status),
            )
            connection.execute(
                "UPDATE Securities SET CurrentTradeStatus = ?, UpdatedAt = ? "
                "WHERE Code = ?",
                (current_status, today, code),
            )
            status_label = "复牌" if current_status == "1" else "停牌"
            logger.info(f"证券 {code} {status_label}（{local_status} → {current_status}）")

    connection.commit()
    logger.info(
        f"证券同步完成: 总数 {len(today_codes)}, "
        f"新增 {len(new_codes)}, 退市 {len(removed_codes)}"
    )


# ---------- 主流程 ----------


def load_config(config_path: str) -> dict[str, Any]:
    """读取 JSON 配置文件。"""
    with open(config_path, "r", encoding="utf-8") as file:
        return json.load(file)


def update_all() -> None:
    """遍历配置中的股票代码，增量更新行情数据。"""
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"配置文件 {CONFIG_FILE} 不存在")
        return

    config = load_config(CONFIG_FILE)
    stock_codes: list[str] = config.get("stocks", [])
    default_start: str = config.get("default_start_date", "2000-01-01")

    if not stock_codes:
        logger.warning("配置中未指定任何股票代码")
        return

    with sqlite3.connect(DB_PATH) as connection:
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
    if "--update" in sys.argv:
        update_all()
    elif "--sync-securities" in sys.argv:
        with sqlite3.connect(DB_PATH) as connection:
            init_database(connection)
            login_result = login_with_retry()
            if login_result is None:
                logger.error("Baostock 登录失败，证券同步无法继续")
            else:
                try:
                    sync_securities(connection)
                finally:
                    bs.logout()
    else:
        logger.info("用法: python BaoStock.py --update | --sync-securities")