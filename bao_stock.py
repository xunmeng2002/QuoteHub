#!/usr/bin/env python3

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

关于 `Any`：本模块及其伴生脚本的 `Any` 只有两类来源，均属"类型系统无话可说"，不是偷懒——
  - 第三方库不提供可用的精确类型：baostock 的 `ResultData` 与登录结果对象、pandas 的 DataFrame 单元格值，
    都写不出类型名，基于它们的 `Callable` 返回值亦然；
  - 配置项取值与 JSON 记录来自 `json.load()`，键集随调用方而异，用 `TypedDict` 固化反而失真。

后者统一收敛为 `JsonObject` 别名。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Never

import baostock as bs
import pandas as pd

# ---------- 配置 ----------

CONFIG_FILE = "config.json"
DB_PATH = "stock_data.db"
LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
MAX_LOGIN_RETRIES = 5
RETRY_DELAY_SECONDS = 5
SOCKET_TIMEOUT_SECONDS = 60

# 服务端在会话失效后对任何查询都返回此码（客户端源码里没有「用户未登录」这句文案，只能是服务端发的）
BAOSTOCK_SESSION_LOST_ERROR_CODE = "10001001"

# ---------- 版本 ----------

VERSION_FILE = "version.txt"

logger = logging.getLogger(__name__)

# ---------- 类型别名 ----------

# 配置与 JSON 记录的形状（为什么用 Any 见模块 docstring「关于 Any」）
type JsonObject = dict[str, Any]

# ---------- 异常 ----------

class BaostockUpdateAbortedError(RuntimeError):
    """结果集没能完整拉取，本次更新必须中止。

    继承 RuntimeError：`bao_stock_parquet` 的模块契约声明其分页函数抛 RuntimeError。
    """

class BaostockSessionLostError(BaostockUpdateAbortedError):
    """服务端已丢弃本次会话；此后任何查询都会失败，继续跑只会记下一串假成功。"""

class BaostockPaginationInterruptedError(BaostockUpdateAbortedError):
    """分页中途出错，已取到的行只是结果集的一部分。"""

# ---------- 工具函数 ----------

def collect_rows_or_raise(result_set: Any, context_label: str) -> list[list[str]]:
    """将 Baostock 结果集全部拉取为行列表；拉不全就抛异常。

    结果集接口把「这一页失败了」写进 `error_code` 而不抛异常，`next()` 只返回 False，
    于是分页中断只表现为「行数比实际少」。2026-10-10 线上即由此拿下 2000 行当 5558 行的
    全量行业分类，还记了一条成功日志。本函数把这种情况变成异常：宁可整轮中止，不留半份数据。

    Args:
        result_set: Baostock 查询返回的结果集对象。
        context_label: 出错信息里的业务上下文（证券代码、步骤名等）。

    Returns:
        行列表，每行为字段字符串列表；结果集完整时才有返回。

    Raises:
        BaostockUpdateAbortedError: 查询没返回结果集。
        BaostockSessionLostError: 服务端返回 10001001，会话已失效。
        BaostockPaginationInterruptedError: 其他错误码，行数与实际不符。
    """
    if result_set is None:
        raise BaostockUpdateAbortedError(f"{context_label}: 查询未返回结果集")

    rows: list[list[str]] = []
    while (result_set.error_code == "0") and result_set.next():
        rows.append(result_set.get_row_data())

    error_code = result_set.error_code
    if error_code == BAOSTOCK_SESSION_LOST_ERROR_CODE:
        raise BaostockSessionLostError(
            f"{context_label}: 会话已失效（{error_code} {result_set.error_msg}），已取 {len(rows)} 行；"
            "服务端此后对任何查询都会返回同一错误，重新登录后再跑"
        )
    if error_code != "0":
        raise BaostockPaginationInterruptedError(
            f"{context_label}: 结果集读取中断（{error_code} {result_set.error_msg}），"
            f"已取 {len(rows)} 行并不完整"
        )
    return rows

def _restrict_to_table_columns(frame: pd.DataFrame, table_columns: list[str]) -> pd.DataFrame:
    """只保留建表列，丢掉接口多给的字段。

    SQLite 的列名比较不区分大小写：接口返回的 `updateDate` 与建表列 `UpdateDate` 会被当成
    同一列，且接口那个排在前、值覆盖我方的值——于是 `DELETE ... WHERE UpdateDate=基准日`
    永远不命中，重跑必撞主键。2026-10-10 的行业分类、指数成分股都栽在这里。

    Args:
        frame: 接口字段构成的 DataFrame。
        table_columns: 目标表的列名白名单。

    Returns:
        仅含白名单中实际存在列的新 DataFrame。
    """
    return frame[[column for column in table_columns if column in frame.columns]]

# ---------- Baostock 登录 ----------

def login_with_retry(max_retries: int = MAX_LOGIN_RETRIES, delay: int = RETRY_DELAY_SECONDS) -> Any | None:
    """带重试地登录 Baostock。

    Args:
        max_retries: 最大尝试次数，默认 MAX_LOGIN_RETRIES。
        delay: 两次尝试之间的等待秒数，默认 RETRY_DELAY_SECONDS。

    Returns:
        登录成功返回登录结果对象；用完次数仍失败返回 None，由调用方决定是否中止。
    """
    for attempt in range(max_retries):
        login_result = bs.login()
        if login_result.error_code == "0":
            logger.info("Baostock 登录成功（尝试 %s 次）", attempt + 1)
            return login_result
        logger.warning("登录失败（尝试 %s/%s）: %s", attempt + 1, max_retries, login_result.error_msg)
        if attempt < max_retries - 1:
            threading.Event().wait(delay)
    return None

def enable_socket_timeout() -> None:
    """给本进程后续建立的所有 socket 装上接收超时。

    `baostock.util.socketutil.send_msg` 建 socket 时不设超时，且只在读到结尾分隔符
    `<![CDATA[]]>` 时才退出收包循环。上游大响应降速或中途断流时（2026-10-10 实测
    `query_all_stock` 约 130 KB，30 秒只到 40 KB 后停住），`recv` 会无限阻塞，进程表现为
    CPU 归零、日志停在半截；此时按 Ctrl+C 也只是把阻塞搬进 `finally` 里的 `bs.logout()`，
    看上去像“按了没反应”。这里把“永久挂死”换成“超时后报错”。

    Note:
        这是**每次 `recv`** 的超时，不是整包总时长——只有服务端连续停顿时长超过阈值才判失败，
        正常但缓慢的传输不受影响，故阈值不宜取小。baostock 的 socket 在 `login` 时创建，
        必须在首次登录之前调用本函数才会生效。
    """
    socket.setdefaulttimeout(SOCKET_TIMEOUT_SECONDS)

def logout_quietly() -> None:
    """注销 Baostock 会话；注销失败只记日志，不向上抛。

    清理阶段本就不该盖掉真正的失败原因，而网络已降级时 `bs.logout()` 会走上与查询同一个
    收包循环，反而是“看起来卡住”的来源。`KeyboardInterrupt` 不属 `Exception`，故第二次
    Ctrl+C 仍能立即中断注销并退出。
    """
    try:
        bs.logout()
    except Exception as logout_error:
        logger.warning("Baostock 注销失败（不影响已落库数据）: %s", logout_error)

# ---------- 数据库初始化 ----------

def init_database(connection: sqlite3.Connection) -> None:
    """初始化数据库：加载基础 Schema 后执行增量升级脚本，并迁移旧表结构。

    Args:
        connection: 已打开的 SQLite 连接。
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))

    schema_dir = os.path.join(base_dir, "sql", "schema")
    schema_path = os.path.join(schema_dir, "Create_v1.0.0.sql")

    if not os.path.exists(schema_path):
        logger.error("Schema 文件不存在: %s", schema_path)
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
            logger.info("升级脚本 %s 执行完成", upgrade_file)

    connection.commit()
    logger.info("数据库 Schema 初始化完成")

    # 3. 旧结构一次性迁移：PerformanceExpress 旧列为臆测口径（接口无对应字段，从未写入成功），
    #    检测到旧列即重建；升级脚本每次全量重跑且 SQL 无条件 DDL，故迁移只能放在代码侧保证幂等。
    express_columns = {row[1] for row in connection.execute("PRAGMA table_info(PerformanceExpress)")}
    if express_columns and "TotalAsset" not in express_columns:
        express_count = connection.execute("SELECT COUNT(*) FROM PerformanceExpress").fetchone()[0]
        if express_count:
            logger.warning("PerformanceExpress 为旧结构且含 %s 行数据，跳过自动迁移，请手工处理", express_count)
        else:
            connection.execute("DROP TABLE PerformanceExpress")
            with open(os.path.join(upgrades_dir, "Update_v2.2.0.sql"), "r", encoding="utf-8") as migrate_file:
                connection.executescript(migrate_file.read())
            logger.info("PerformanceExpress 旧结构（空表）已重建为 v2.2.0 列定义")

# ---------- 交易日推算 ----------

def _get_previous_trading_date(connection: sqlite3.Connection | None = None, lookback_days: int = 10) -> str | None:
    """推算最近一个交易日（不晚于昨天）。

    优先用本地 TradeDates 表查询，若表不存在或无数据则调用 Baostock API。

    Args:
        connection: 可选数据库连接，优先查本地缓存。
        lookback_days: 向前搜索天数，默认 10 天。

    Returns:
        交易日字符串 "YYYY-MM-DD"，或 None（区间内无交易日、无法确定时）。

    Raises:
        BaostockUpdateAbortedError: 本地无缓存且接口结果集拉取不完整。
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

    rows = collect_rows_or_raise(result, "交易日历查询")
    for row in reversed(rows):
        if len(row) >= 2 and row[1] == "1":  # is_trading_day == "1"
            return row[0]  # calendar_date

    logger.warning("过去 10 天未找到交易日，将使用当天作为回退")
    return None

# ---------- 查最新日期 ----------

def get_last_date(connection: sqlite3.Connection, stock_code: str) -> str | None:
    """查库中该证券原始日线的最新日期，供增量下载算起点。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_code: Baostock 格式证券代码，如 'sh.600519'。

    Returns:
        最新日期 'YYYY-MM-DD'；库中无该证券数据返回 None。
    """
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

def fetch_and_store_stock(connection: sqlite3.Connection, stock_code: str, start_date: str) -> int:
    """下载单只股票的日K数据及复权因子，写入数据库。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_code: Baostock 格式证券代码，如 'sh.600519'。
        start_date: 下载起始日期 'YYYY-MM-DD'（含）。

    Returns:
        写入 DailyRawPrices 的价格行数；无新数据返回 0。

    Raises:
        BaostockUpdateAbortedError: 价格或复权因子结果集拉取不完整。
    """
    # 原始价格：不复权（Baostock adjustflag="3"）
    price_rows = collect_rows_or_raise(
        bs.query_history_k_data_plus(
            stock_code,
            "date,code,open,high,low,close,volume,amount",
            start_date=start_date,
            frequency="d",
            adjustflag="3",
        ),
        f"{stock_code} 原始价格",
    )
    if not price_rows:
        logger.info("%s 从 %s 至今无新价格数据", stock_code, start_date)
        return 0

    price_frame = pd.DataFrame(
        price_rows,
        columns=["Date", "Code", "Open", "High", "Low", "Close", "Volume", "Amount"],
    )
    for column in ["Open", "High", "Low", "Close", "Volume", "Amount"]:
        price_frame[column] = pd.to_numeric(price_frame[column], errors="coerce")

    # 复权因子：全部字段写入
    factor_result = bs.query_adjust_factor(stock_code, start_date=start_date)
    factor_rows = collect_rows_or_raise(factor_result, f"{stock_code} 复权因子")
    if factor_rows:
        raw_fields: list[str] = factor_result.fields
        logger.info("因子原始字段: %s", raw_fields)
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
        logger.info("%s 复权因子新增 %s 条", stock_code, len(factor_frame))

    price_frame.to_sql(
        "DailyRawPrices", connection, if_exists="append", index=False
    )
    logger.info("%s 原始价格新增 %s 条", stock_code, len(price_frame))
    return len(price_frame)

# ---------- 证券合约同步 ----------

def sync_securities(connection: sqlite3.Connection) -> None:
    """同步全市场证券列表及交易状态（每日更新）。

    从 Baostock 拉取最新全市场证券列表，与本地 securities 表对比，
    自动处理：新股 IPO → 插入，退市 → 标记，更名/停复牌 → 记录日志。

    Args:
        connection: 已打开的 SQLite 连接（须已执行 init_database）。
    """
    # 用最近一个交易日查询，避免当天盘中数据未就绪
    query_date = _get_previous_trading_date(connection) or datetime.today().strftime(
        "%Y-%m-%d"
    )
    logger.info("证券同步基准日期: %s", query_date)

    all_stocks_result = bs.query_all_stock(day=query_date)
    all_stocks = pd.DataFrame(
        collect_rows_or_raise(all_stocks_result, f"{query_date} 全市场证券列表"),
        columns=all_stocks_result.fields,
    )
    if all_stocks.empty:
        logger.warning("%s 证券列表为空（Baostock 服务端可能异常），跳过本次证券同步", query_date)
        return

    all_stocks.columns = all_stocks.columns.str.strip()
    logger.info("基准日期 %s 全市场证券总数: %s", query_date, len(all_stocks))

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
        basic_df = pd.DataFrame(
            collect_rows_or_raise(basic_result, "全市场证券基本信息"), columns=basic_result.fields
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
            logger.info("新增证券 %s %s（%s）", code, stock_row["code_name"], type_label)

    for code in removed_codes:
        connection.execute(
            """UPDATE Securities
               SET DelistDate = ?, CurrentTradeStatus = '0', UpdatedAt = ?
               WHERE Code = ?""",
            (query_date, query_date, code),
        )
        logger.info("证券 %s 已退市，标记退市日期 %s", code, query_date)

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
            logger.info("证券 %s 更名: 「%s」→「%s」", code, local_name, current_name)

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
            logger.info("证券 %s %s（%s → %s）", code, status_label, local_status, current_status)

    connection.commit()
    logger.info("证券同步完成: 总数 %s, 新增 %s, 退市 %s", len(today_codes), len(new_codes), len(removed_codes))

# ------------------------------------------------------------------
# 通用财务数据采集器
# ------------------------------------------------------------------

def _fetch_and_store_financial(connection: sqlite3.Connection, code: str, year: int, quarter: int,
                               query_func: Callable[..., Any], table_name: str, numeric_fields: list[str]) -> int:
    """通用财务数据采集器。

    调用 Baostock 财务查询接口，将结果写入指定 SQLite 表。
    取到数据后才删除相同 (Code, Year, Quarter) 的旧数据，故重跑幂等；上游 0 行时保留库内现有数据。

    Args:
        connection: 已打开的 SQLite 连接。
        code: Baostock 格式证券代码。
        year: 会计年度。
        quarter: 季度 1-4。
        query_func: Baostock 财务查询函数，接收 (code, year, quarter) 三个字符串参数。
        table_name: 目标表名；调用方以字面量传入，不来自外部输入。
        numeric_fields: 需转为数值类型的列名列表。

    Returns:
        写入行数；接口成功但无数据时返回 0。

    Raises:
        BaostockUpdateAbortedError: 结果集拉取不完整（未返回结果集、分页中断或会话失效）。
    """
    result = query_func(code, str(year), str(quarter))
    rows = collect_rows_or_raise(result, f"{code} {table_name} {year}Q{quarter}")
    if not rows:
        logger.info("%s %s %sQ%s 无数据，保留库内现有数据", code, table_name, year, quarter)
        return 0

    connection.execute(
        f"DELETE FROM {table_name} WHERE Code = ? AND Year = ? AND Quarter = ?",
        (code, year, quarter),
    )

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
    logger.info("%s %s %sQ%s 写入 %s 条", code, table_name, year, quarter, len(frame))
    return len(frame)

def _default_year_quarters() -> list[tuple[int, int]]:
    """获取最近 4 个季度的 (year, quarter) 列表（按时间升序）。

    Returns:
        4 个 (年, 季) 元组，最早的一季在前。
    """
    today = datetime.today()
    quarters: list[tuple[int, int]] = []
    for i in range(4):
        total = today.year * 12 + today.month - 1 - i * 3
        y = total // 12
        q = (total % 12) // 3 + 1
        quarters.append((y, q))
    return list(reversed(quarters))

def _quarter_window_start(quarters_back: int = 4) -> str:
    """最近 quarters_back 个季度窗口的起始日期（最早那季的首日），供按日期区间查询的接口使用。

    Args:
        quarters_back: 向前回溯的季度数，默认 4。

    Returns:
        该季度首日 'YYYY-MM-01'。
    """
    today = datetime.today()
    quarter_start_month_index = (today.month - 1) // 3 * 3
    total_months = today.year * 12 + quarter_start_month_index - quarters_back * 3
    return f"{total_months // 12:04d}-{total_months % 12 + 1:02d}-01"

# ------------------------------------------------------------------
# 季频财务数据（6 类）
# ------------------------------------------------------------------

def update_financial_data(connection: sqlite3.Connection, stock_codes: list[str], years: list[int] | None = None,
                          quarters: list[int] | None = None) -> None:
    """遍历股票列表，下载 6 大财务指标数据。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_codes: Baostock 格式证券代码列表。
        years: 指定年份列表；与 quarters 任一为 None 时改取最近 4 个季度。
        quarters: 指定季度列表 1-4；与 years 任一为 None 时改取最近 4 个季度。
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
    logger.info("财务数据更新完成，总写入 %s 条", total)

# ------------------------------------------------------------------
# 除权除息信息
# ------------------------------------------------------------------

def update_dividend_data(connection: sqlite3.Connection, stock_codes: list[str], years: list[int] | None = None) -> None:
    """下载除权除息信息。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_codes: Baostock 格式证券代码列表。
        years: 指定年份列表，默认最近 4 年（含今年）。
    """
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
                result = bs.query_dividend_data(code, str(year), year_type)
                rows = collect_rows_or_raise(result, f"{code} 除权除息 {year}/{year_type}")
                if not rows:
                    continue

                connection.execute(
                    "DELETE FROM DividendData WHERE Code=? AND Year=? AND YearType=?",
                    (code, year, year_type),
                )
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
    logger.info("除权除息数据更新完成，总写入 %s 条", total)

# ------------------------------------------------------------------
# 业绩快报
# ------------------------------------------------------------------

def update_performance_express(connection: sqlite3.Connection, stock_codes: list[str], start_date: str | None = None,
                               end_date: str | None = None) -> None:
    """下载业绩快报。接口按日期区间查询（非年+季），Year/Quarter 由统计日期推导。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_codes: Baostock 格式证券代码列表。
        start_date: 区间起始日 'YYYY-MM-DD'；为 None 时取最近 4 个季度窗口起点。
        end_date: 区间结束日 'YYYY-MM-DD'；为 None 时取今天。
    """
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
        result = bs.query_performance_express_report(
            code, start_date=start_date, end_date=end_date
        )
        rows = collect_rows_or_raise(result, f"{code} 业绩快报 {start_date}~{end_date}")
        if not rows:
            continue

        connection.execute(
            "DELETE FROM PerformanceExpress WHERE Code=? AND (PubDate>=? OR StatDate>=?)",
            (code, start_date, start_date),
        )
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
    logger.info("业绩快报更新完成，总写入 %s 条（%s ~ %s）", total, start_date, end_date)

# ------------------------------------------------------------------
# 业绩预告
# ------------------------------------------------------------------

def update_forecast_reports(connection: sqlite3.Connection, stock_codes: list[str], start_date: str | None = None,
                            end_date: str | None = None) -> None:
    """下载业绩预告。接口按日期区间查询（非年+季），Year/Quarter 由统计日期推导。

    Args:
        connection: 已打开的 SQLite 连接。
        stock_codes: Baostock 格式证券代码列表。
        start_date: 区间起始日 'YYYY-MM-DD'；为 None 时取最近 4 个季度窗口起点。
        end_date: 区间结束日 'YYYY-MM-DD'；为 None 时取今天。
    """
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
        result = bs.query_forecast_report(
            code, start_date=start_date, end_date=end_date
        )
        rows = collect_rows_or_raise(result, f"{code} 业绩预告 {start_date}~{end_date}")
        if not rows:
            continue

        connection.execute(
            "DELETE FROM ForecastReports WHERE Code=? AND (PubDate>=? OR StatDate>=?)",
            (code, start_date, start_date),
        )
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
    logger.info("业绩预告更新完成，总写入 %s 条（%s ~ %s）", total, start_date, end_date)

# ------------------------------------------------------------------
# 指数成分股
# ------------------------------------------------------------------

def update_index_constituents(connection: sqlite3.Connection) -> None:
    """更新沪深300 / 上证50 / 中证500 成分股。

    Args:
        connection: 已打开的 SQLite 连接。
    """
    query_date = _get_previous_trading_date(connection) or datetime.today().strftime(
        "%Y-%m-%d"
    )
    logger.info("指数成分股基准日期: %s", query_date)
    indices: list[tuple[str, Callable[[str], Any]]] = [
        ("hs300", bs.query_hs300_stocks),
        ("sz50", bs.query_sz50_stocks),
        ("zz500", bs.query_zz500_stocks),
    ]
    total = 0

    for index_code, query_func in indices:
        result = query_func(query_date)
        rows = collect_rows_or_raise(result, f"{index_code} 成分股 {query_date}")
        if not rows:
            logger.warning("%s 成分股 %s 上游返回 0 行，保留库内当日现有数据", index_code, query_date)
            continue
        frame = pd.DataFrame(rows, columns=result.fields)
        frame["IndexCode"] = index_code
        frame = frame.rename(
            columns={"code": "Code", "code_name": "CodeName"}
        )
        frame = _restrict_to_table_columns(
            frame, ["IndexCode", "Code", "CodeName", "UpdateDate"]
        )
        frame["UpdateDate"] = query_date
        connection.execute(
            "DELETE FROM IndexConstituents WHERE IndexCode=? AND UpdateDate=?",
            (index_code, query_date),
        )
        frame.to_sql("IndexConstituents", connection, if_exists="append", index=False)
        total += len(frame)
        logger.info("%s 成分股 %s 只", index_code, len(frame))

    logger.info("指数成分股更新完成，总写入 %s 条", total)

# ------------------------------------------------------------------
# 行业分类
# ------------------------------------------------------------------

def update_industry(connection: sqlite3.Connection) -> None:
    """更新行业分类信息，同时更新 Securities 表中的行业字段。

    Args:
        connection: 已打开的 SQLite 连接。
    """
    today = datetime.today().strftime("%Y-%m-%d")

    result = bs.query_stock_industry()
    rows = collect_rows_or_raise(result, "行业分类")
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
    frame = _restrict_to_table_columns(
        frame, ["Code", "CodeName", "Industry", "IndustryClassification", "UpdateDate"]
    )

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

    logger.info("行业分类更新完成，新增 %s 条，同步 Securities 行业信息 %s 条", len(frame), updated_count)

# ------------------------------------------------------------------
# 交易日历
# ------------------------------------------------------------------

def update_trade_dates(connection: sqlite3.Connection, start_date: str = "1990-12-19", end_date: str | None = None) -> None:
    """更新交易日历。

    Args:
        connection: 已打开的 SQLite 连接。
        start_date: 区间起始日 'YYYY-MM-DD'，默认 '1990-12-19'（Baostock 数据起点）。
        end_date: 区间结束日 'YYYY-MM-DD'，默认今天。
    """
    if end_date is None:
        end_date = datetime.today().strftime("%Y-%m-%d")

    result = bs.query_trade_dates(start_date=start_date, end_date=end_date)
    rows = collect_rows_or_raise(result, f"交易日历 {start_date}~{end_date}")
    if not rows:
        logger.warning("交易日历 %s~%s 上游返回 0 行，保留库内现有数据", start_date, end_date)
        return

    frame = pd.DataFrame(rows, columns=result.fields)
    frame["is_trading_day"] = pd.to_numeric(frame["is_trading_day"], errors="coerce")
    frame = frame.rename(
        columns={
            "calendar_date": "CalendarDate",
            "is_trading_day": "IsTradingDay",
        }
    )
    connection.execute("DELETE FROM TradeDates WHERE CalendarDate >= ?", (start_date,))
    frame[["CalendarDate", "IsTradingDay"]].to_sql(
        "TradeDates", connection, if_exists="append", index=False
    )
    logger.info("交易日历更新完成，共 %s 条", len(frame))

# ------------------------------------------------------------------
# 宏观经济数据（5 类）
# ------------------------------------------------------------------

def update_deposit_rates(connection: sqlite3.Connection) -> None:
    """更新存款利率（清空重取，起始日固定 2000-01-01）。

    Args:
        connection: 已打开的 SQLite 连接。
    """
    result = bs.query_deposit_rate_data(start_date="2000-01-01")
    rows = collect_rows_or_raise(result, "存款利率")
    if not rows:
        logger.warning("存款利率上游返回 0 行，保留库内现有数据")
        return

    connection.execute("DELETE FROM DepositRates")
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
    logger.info("存款利率更新完成，共 %s 条", len(frame))

def update_loan_rates(connection: sqlite3.Connection) -> None:
    """更新贷款利率（清空重取，起始日固定 2000-01-01）。

    Args:
        connection: 已打开的 SQLite 连接。
    """
    result = bs.query_loan_rate_data(start_date="2000-01-01")
    rows = collect_rows_or_raise(result, "贷款利率")
    if not rows:
        logger.warning("贷款利率上游返回 0 行，保留库内现有数据")
        return

    connection.execute("DELETE FROM LoanRates")
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
    logger.info("贷款利率更新完成，共 %s 条", len(frame))

def update_reserve_ratios(connection: sqlite3.Connection) -> None:
    """更新存款准备金率（清空重取，起始日固定 2000-01-01）。

    Args:
        connection: 已打开的 SQLite 连接。
    """
    result = bs.query_required_reserve_ratio_data(start_date="2000-01-01")
    rows = collect_rows_or_raise(result, "准备金率")
    if not rows:
        logger.warning("准备金率上游返回 0 行，保留库内现有数据")
        return

    connection.execute("DELETE FROM ReserveRatios")
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
    logger.info("存款准备金率更新完成，共 %s 条", len(frame))

def update_money_supply(connection: sqlite3.Connection) -> None:
    """更新月度货币供应量（M0/M1/M2，清空重取，起始月固定 2000-01）。

    Args:
        connection: 已打开的 SQLite 连接。
    """
    end_month = datetime.today().strftime("%Y-%m")
    result = bs.query_money_supply_data_month(
        start_date="2000-01", end_date=end_month
    )
    rows = collect_rows_or_raise(result, "货币供应量")
    if not rows:
        logger.warning("货币供应量上游返回 0 行，保留库内现有数据")
        return

    connection.execute("DELETE FROM MoneySupply")
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
    logger.info("货币供应量更新完成，共 %s 条", len(frame))

# ------------------------------------------------------------------
# 全量更新调度
# ------------------------------------------------------------------

def _exit_on_update_aborted(abort_error: BaostockUpdateAbortedError) -> Never:
    """把「整轮中止」讲清楚，并以非 0 退出码收场。

    原先这类失败会以 exit 0 结束、日志里还留着「全量更新完成」，只有人肉读日志才看得出没跑完。

    Args:
        abort_error: 触发中止的异常。

    Raises:
        SystemExit: 退出码 1。
    """
    logger.error("更新已中止：%s", abort_error)
    logger.error("已提交的批次保留在库中，未提交的部分已回滚；排除网络/会话问题后重跑即可")
    raise SystemExit(1) from abort_error

def _run_update(task_func: Callable[[sqlite3.Connection], None]) -> None:
    """登录 Baostock → 执行任务 → 注销的统一流程。

    Args:
        task_func: 接收 SQLite 连接的数据更新函数。

    Raises:
        SystemExit: 登录失败或任务被中止时以退出码 1 退出。两者同码是刻意的：调用方按
            退出码判成败，登录失败若以 0 收场，消费方会把“什么都没做”当成“做完了”。
    """
    with contextlib.closing(sqlite3.connect(DB_PATH)) as connection:
        init_database(connection)
        login_result = login_with_retry()
        if login_result is None:
            raise SystemExit("BaoStock 登录失败，操作取消")
        try:
            task_func(connection)
            connection.commit()
        except BaostockUpdateAbortedError as abort_error:
            _exit_on_update_aborted(abort_error)
        finally:
            logout_quietly()

def update_all_extended(connection: sqlite3.Connection, config: JsonObject) -> None:
    """全量更新所有数据类别。

    Args:
        connection: 已打开的 SQLite 连接。
        config: 解析后的配置字典，须含 stocks 与 default_start_date 键。

    Raises:
        BaostockUpdateAbortedError: 任一结果集拉取不完整；已提交的批次保留，当前批次回滚。

    Note:
        按步骤提交，4/7 日 K 步另按每只股票提交：全量更新动辄数十分钟，末尾一次提交意味着
        任何一处中止都会把前面跑完的成果一起丢掉。
    """
    stock_codes: list[str] = require_config_value(config, "stocks", CONFIG_FILE)
    default_start: str = require_config_value(config, "default_start_date", CONFIG_FILE)

    logger.info("===== 全量更新开始 =====")

    logger.info("--- 1/7 证券同步 ---")
    sync_securities(connection)

    logger.info("--- 2/7 行业分类 ---")
    update_industry(connection)
    connection.commit()

    logger.info("--- 3/7 指数成分股 ---")
    update_index_constituents(connection)
    connection.commit()

    if stock_codes:
        logger.info("--- 4/7 日K行情 ---")
        for stock_code in stock_codes:
            logger.info("处理 %s ...", stock_code)
            last_date = get_last_date(connection, stock_code)
            if last_date:
                start = (
                    datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
                ).strftime("%Y-%m-%d")
            else:
                start = default_start
            if datetime.strptime(start, "%Y-%m-%d") <= datetime.today():
                fetch_and_store_stock(connection, stock_code, start)
                connection.commit()

    if stock_codes:
        logger.info("--- 5/7 财务数据 & 除权除息 & 业绩 ---")
        update_financial_data(connection, stock_codes)
        update_dividend_data(connection, stock_codes)
        update_performance_express(connection, stock_codes)
        update_forecast_reports(connection, stock_codes)
        connection.commit()

    logger.info("--- 6/7 宏观经济 ---")
    update_deposit_rates(connection)
    update_loan_rates(connection)
    update_reserve_ratios(connection)
    update_money_supply(connection)
    connection.commit()

    logger.info("--- 7/7 交易日历 ---")
    update_trade_dates(connection)

    connection.commit()
    logger.info("===== 全量更新完成 =====")

# ---------- 主流程 ----------

def load_config(config_path: str) -> JsonObject:
    """读取 JSON 配置文件。

    Args:
        config_path: 配置文件路径。

    Returns:
        解析后的配置字典。
    """
    with open(config_path, "r", encoding="utf-8") as file:
        return json.load(file)

def load_config_file(config_file: str) -> JsonObject:
    """读取配置文件；文件不存在即报错退出——不伪造一份空配置顶上。

    原先文件缺失时返回 `{"stocks": []}`，于是"配置没写"与"配置写空"表现一致。

    Args:
        config_file: 配置文件路径。

    Returns:
        解析后的配置字典。

    Raises:
        SystemExit: 配置文件不存在。
    """
    if not os.path.exists(config_file):
        raise SystemExit(f"配置文件不存在: {config_file}")
    return load_config(config_file)

def require_config_value(config: JsonObject, config_key: str, config_file: str, command_line_alternative: str | None = None) -> Any:
    """取必填配置项；缺失或为空即报错退出——配置一律不兜底。

    兜底会让"配置漏写"与"配置写对"表现一致：代码清单悄悄变空、输出路径悄悄落到别处，
    两者都没有任何提示。缺了就直接失败，把问题停在启动那一刻。

    Args:
        config: 解析后的配置字典。
        config_key: 配置键名。
        config_file: 配置文件名，仅用于拼错误提示。
        command_line_alternative: 等价的命令行参数名；给出后错误提示会附带该替代途径。

    Returns:
        配置项的值。

    Raises:
        SystemExit: 配置项缺失、为空或为空集合。
    """
    config_value = config.get(config_key)
    if not config_value:
        alternatives = f"，或用 {command_line_alternative} 传入" if command_line_alternative else ""
        raise SystemExit(f"配置缺少必填项 {config_key}（{config_file}）{alternatives}")
    return config_value

def read_version() -> str:
    """读取组件版本号。

    `version.txt` 是版本号的唯一来源——若在每个源文件里各写一份字面量，改版时
    就得逐个文件改，漏一个就出现两个版本号。口径与引擎的 `engine-version.txt`
    一致：取首个非空行。

    Returns:
        版本号字符串，形如 `v1.0.0`。

    Raises:
        SystemExit: 版本文件缺失或为空。
    """
    version_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), VERSION_FILE)
    if not os.path.exists(version_path):
        raise SystemExit(f"版本文件不存在: {version_path}")
    with open(version_path, "r", encoding="utf-8") as version_file:
        for version_line in version_file:
            if version_line.strip():
                return version_line.strip()
    raise SystemExit(f"版本文件为空: {version_path}")

def update_all(connection: sqlite3.Connection, config: JsonObject) -> None:
    """遍历配置中的股票代码，增量更新行情数据。

    Args:
        connection: 已打开的 SQLite 连接。
        config: 解析后的配置字典，须含 stocks 与 default_start_date 键。

    Raises:
        BaostockUpdateAbortedError: 结果集拉取不完整，交由 `_run_update` 转成退出码 1。

    Note:
        逐只提交：原先只在连接关闭时收尾，而关闭不提交等于整轮回滚——下载半天一行不落库。
        “登录 → 中止转换 → 注销”这段编排归 `_run_update` 统一承担，本函数只做任务本身。
    """
    stock_codes: list[str] = require_config_value(config, "stocks", CONFIG_FILE)
    default_start: str = require_config_value(config, "default_start_date", CONFIG_FILE)

    sync_securities(connection)

    for stock_code in stock_codes:
        logger.info("处理 %s ...", stock_code)
        last_date = get_last_date(connection, stock_code)
        if last_date:
            start = (
                datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
            ).strftime("%Y-%m-%d")
            logger.info("  上次数据截止 %s，从 %s 开始增量下载", last_date, start)
        else:
            start = default_start
            logger.info("  无历史数据，从默认日期 %s 开始全量下载", start)

        if datetime.strptime(start, "%Y-%m-%d") > datetime.today():
            logger.info("  %s 已是最新，跳过", stock_code)
            continue

        fetch_and_store_stock(connection, stock_code, start)
        connection.commit()

# ---------- 入口 ----------

if __name__ == "__main__":
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    enable_socket_timeout()

    if "--version" in sys.argv:
        print(read_version())
    elif "--update" in sys.argv:
        config = load_config_file(CONFIG_FILE)
        _run_update(lambda conn: update_all(conn, config))
    elif "--sync-securities" in sys.argv:
        _run_update(sync_securities)
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
            """业绩快报与业绩预告的合并任务，供 _run_update 调用。

            证券代码取自所在分支的闭包变量 stock_codes，不由本函数接收。

            Args:
                conn: 由 _run_update 打开并传入的 SQLite 连接。
            """
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
            """宏观经济 4 类数据的合并任务，供 _run_update 调用。

            Args:
                conn: 由 _run_update 打开并传入的 SQLite 连接。
            """
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
