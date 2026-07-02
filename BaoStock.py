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
    """初始化数据库：创建价格表，重建因子表。"""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS daily_raw_prices (
            Code TEXT NOT NULL,
            Date TEXT NOT NULL,
            Open REAL,
            High REAL,
            Low REAL,
            Close REAL,
            Volume REAL,
            Amount REAL,
            PRIMARY KEY (Code, Date)
        );
    """)
    # 因子表可能因字段变动而重建
    connection.execute("DROP TABLE IF EXISTS adjust_factors")
    connection.execute("""
        CREATE TABLE adjust_factors (
            Code TEXT NOT NULL,
            EventDate TEXT NOT NULL,
            ForeFactor REAL,
            BackFactor REAL,
            CumFactor REAL,
            PRIMARY KEY (Code, EventDate)
        );
    """)
    connection.commit()
    logger.info("数据库表初始化完成（因子表已重建）")


# ---------- 查最新日期 ----------


def get_last_date(
    connection: sqlite3.Connection,
    stock_code: str,
) -> str | None:
    cursor = connection.execute(
        "SELECT MAX(Date) FROM daily_raw_prices WHERE Code = ?",
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
                "adjust_factors", connection, if_exists="append", index=False
            )
            logger.info(f"{stock_code} 复权因子新增 {len(factor_frame)} 条")
    else:
        logger.warning(f"{stock_code} 复权因子查询失败: {factor_result.error_msg}")

    # 3. 写入价格
    price_frame.to_sql(
        "daily_raw_prices", connection, if_exists="append", index=False
    )
    logger.info(f"{stock_code} 原始价格新增 {len(price_frame)} 条")
    return len(price_frame)


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
    else:
        logger.info("执行更新请添加参数 --update，例如：python BaoStock.py --update")