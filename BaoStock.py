#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import baostock as bs
import pandas as pd
import sqlite3
import json
import os
import time
import sys
from datetime import datetime, timedelta
import logging

# ---------- 配置 ----------
CONFIG_FILE = 'config.json'
DB_PATH = 'stock_data.db'
LOG_LEVEL = logging.INFO
MAX_LOGIN_RETRIES = 5
RETRY_DELAY = 5

logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def login_with_retry(max_retries=MAX_LOGIN_RETRIES, delay=RETRY_DELAY):
    for i in range(max_retries):
        lg = bs.login()
        if lg.error_code == '0':
            logger.info(f"Baostock 登录成功（尝试 {i+1} 次）")
            return lg
        logger.warning(f"登录失败（尝试 {i+1}/{max_retries}）: {lg.error_msg}")
        if i < max_retries - 1:
            time.sleep(delay)
    return None


def init_db(conn):
    """初始化数据库：价格表不变，因子表重建存储所有字段"""
    # 价格表（不变）
    conn.execute("""
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
    # 删除旧的因子表（因为要加新列）
    conn.execute("DROP TABLE IF EXISTS adjust_factors")
    # 创建新因子表，存储所有原始因子数据
    conn.execute("""
        CREATE TABLE adjust_factors (
            Code TEXT NOT NULL,
            EventDate TEXT NOT NULL,      -- 除权除息日
            ForeFactor REAL,              -- 前复权因子 (foreAdjustFactor)
            BackFactor REAL,              -- 后复权因子 (backAdjustFactor)
            CumFactor REAL,               -- 累积复权因子 (adjustFactor)
            PRIMARY KEY (Code, EventDate)
        );
    """)
    conn.commit()
    logger.info("数据库表初始化完成（因子表已重建，包含所有因子字段）")


def get_last_date(conn, stock_code):
    cursor = conn.execute(
        "SELECT MAX(Date) FROM daily_raw_prices WHERE Code = ?", (stock_code,)
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


def fetch_and_store_stock(conn, stock_code, start_date):
    # 1. 原始价格（不复权）
    rs_price = bs.query_history_k_data_plus(
        stock_code,
        "date,code,open,high,low,close,volume,amount",
        start_date=start_date,
        frequency="d",
        adjustflag="3"
    )
    if rs_price.error_code != '0':
        logger.error(f"{stock_code} 价格查询失败: {rs_price.error_msg}")
        return 0

    price_list = []
    while (rs_price.error_code == '0') and rs_price.next():
        price_list.append(rs_price.get_row_data())

    if not price_list:
        logger.info(f"{stock_code} 从 {start_date} 至今无新价格数据")
        return 0

    df_price = pd.DataFrame(price_list, columns=[
        'Date', 'Code', 'Open', 'High', 'Low', 'Close', 'Volume', 'Amount'
    ])
    for col in ['Open', 'High', 'Low', 'Close', 'Volume', 'Amount']:
        df_price[col] = pd.to_numeric(df_price[col], errors='coerce')

    # 2. 复权因子：全部字段存储
    rs_factor = bs.query_adjust_factor(stock_code, start_date=start_date)
    if rs_factor.error_code == '0':
        factor_list = []
        while (rs_factor.error_code == '0') and rs_factor.next():
            factor_list.append(rs_factor.get_row_data())
        if factor_list:
            fields = rs_factor.fields
            logger.info(f"因子原始字段: {fields}")
            # 全部存入，动态映射列名
            df_factor = pd.DataFrame(factor_list, columns=fields)
            # 重命名为统一的英文字段（首字母大写）
            rename_map = {
                'code': 'Code',
                'dividOperateDate': 'EventDate',
                'foreAdjustFactor': 'ForeFactor',
                'backAdjustFactor': 'BackFactor',
                'adjustFactor': 'CumFactor'
            }
            # 只保留存在的列（实际上全部存在）
            df_factor = df_factor.rename(columns=rename_map)
            # 确保顺序
            df_factor = df_factor[['Code', 'EventDate', 'ForeFactor', 'BackFactor', 'CumFactor']]
            # 转换数值类型
            for col in ['ForeFactor', 'BackFactor', 'CumFactor']:
                df_factor[col] = pd.to_numeric(df_factor[col], errors='coerce')
            # 写入数据库
            df_factor.to_sql('adjust_factors', conn, if_exists='append', index=False)
            logger.info(f"{stock_code} 复权因子新增 {len(df_factor)} 条")
    else:
        logger.warning(f"{stock_code} 复权因子查询失败: {rs_factor.error_msg}")

    # 3. 写入价格
    df_price.to_sql('daily_raw_prices', conn, if_exists='append', index=False)
    logger.info(f"{stock_code} 原始价格新增 {len(df_price)} 条")
    return len(df_price)


def update_all():
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"配置文件 {CONFIG_FILE} 不存在")
        return

    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)

    stocks = config.get('stocks', [])
    default_start = config.get('default_start_date', '2000-01-01')
    if not stocks:
        logger.warning("配置中未指定任何股票代码")
        return

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    lg = login_with_retry()
    if lg is None:
        logger.error("❌ Baostock 登录失败，程序退出")
        conn.close()
        return

    try:
        for code in stocks:
            logger.info(f"处理 {code} ...")
            last_date = get_last_date(conn, code)
            if last_date:
                start = (datetime.strptime(last_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                logger.info(f"  上次数据截止 {last_date}，从 {start} 开始增量下载")
            else:
                start = default_start
                logger.info(f"  无历史数据，从默认日期 {start} 开始全量下载")

            if datetime.strptime(start, '%Y-%m-%d') > datetime.today():
                logger.info(f"  {code} 已是最新，跳过")
                continue

            fetch_and_store_stock(conn, code, start)
    finally:
        bs.logout()
        conn.close()
        logger.info("数据库连接关闭，Baostock 注销")


def load_and_calc_adjusted(conn, stock_code, start_date=None, end_date=None):
    """演示如何读取因子并计算复权（可后续完善）"""
    # 此函数仅作示例，暂时不修改，你可以之后自己用存储的因子计算
    pass


if __name__ == '__main__':
    if '--update' in sys.argv:
        update_all()
    else:
        print("执行更新请添加参数 --update，例如：python BaoStock.py --update")