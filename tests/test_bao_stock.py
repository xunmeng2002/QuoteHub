"""bao_stock.py 单元测试：配置守卫、季度窗口推算、结果集收集（不碰网络）。"""

from __future__ import annotations

import json
import re
import socket
import sqlite3

from collections.abc import Iterator
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import bao_stock

from bao_stock import (
    BAOSTOCK_SESSION_LOST_ERROR_CODE,
    SOCKET_TIMEOUT_SECONDS,
    BaostockPaginationInterruptedError,
    BaostockSessionLostError,
    BaostockUpdateAbortedError,
    _default_year_quarters,
    _exit_on_update_aborted,
    _fetch_and_store_financial,
    _get_previous_trading_date,
    _quarter_window_start,
    _restrict_to_table_columns,
    collect_rows_or_raise,
    enable_socket_timeout,
    load_config,
    load_config_file,
    logout_quietly,
    read_version,
    require_config_value,
    update_deposit_rates,
    update_index_constituents,
    update_industry,
    update_trade_dates,
)

class _PagedResultSet:
    """Baostock 结果集替身，按「页」模拟分页。

    真结果集把「这一页没取到」写进 error_code 而**不抛异常**，`next()` 只返回 False；
    替身照搬这一点，否则测不出收集函数有没有把「翻页失败」当成「读完了」。

    Args:
        pages: 每页的行列表。
        fields: 字段名列表。
        failing_page_index: 第几页（从 0 起）翻页时失败，None 表示全程顺利。
        failure_code: 失败时写入 error_code 的错误码。
        failure_msg: 失败时写入 error_msg 的文案。
    """

    def __init__(self, pages: list[list[list[str]]], fields: list[str], failing_page_index: int | None = None,
                 failure_code: str = "10002007", failure_msg: str = "网络接收错误。") -> None:
        self.fields = fields
        self.error_code = "0"
        self.error_msg = ""
        self._pages = pages
        self._page_index = 0
        self._row_index = -1
        self._failing_page_index = failing_page_index
        self._failure_code = failure_code
        self._failure_msg = failure_msg

    def next(self) -> bool:
        while True:
            if self._page_index >= len(self._pages):
                return False
            if self._row_index + 1 < len(self._pages[self._page_index]):
                self._row_index += 1
                return True
            if self._page_index + 1 == self._failing_page_index:
                self.error_code = self._failure_code
                self.error_msg = self._failure_msg
                return False
            self._page_index += 1
            if self._page_index >= len(self._pages):
                return False
            self._row_index = -1

    def get_row_data(self) -> list[str]:
        return self._pages[self._page_index][self._row_index]

@pytest.fixture
def initialized_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """建好表的临时库连接，用例结束即关闭。"""
    with closing(sqlite3.connect(tmp_path / "test.db")) as connection:
        bao_stock.init_database(connection)
        yield connection

@pytest.fixture
def database_config(tmp_path: Path) -> bao_stock.JsonObject:
    """指向临时库的配置字典。

    `_run_update` 从参数接收配置、不再读 config.json，故这里直接给字典而不写文件——
    写文件等于测一条重构后不存在的代码路径。
    """
    return {
        "stocks": ["sh.600000"],
        "default_start_date": "2000-01-01",
        "database_path": str(tmp_path / "stock_data.db"),
    }

def test_require_config_value_returns_configured_value() -> None:
    config = {"stocks": ["sh.600519"]}

    assert require_config_value(config, "stocks", "config.json") == ["sh.600519"]

@pytest.mark.parametrize("invalid_config", [{}, {"stocks": []}])
def test_require_config_value_exits_on_missing_or_empty(invalid_config: dict[str, object]) -> None:
    with pytest.raises(SystemExit):
        require_config_value(invalid_config, "stocks", "config.json")

def test_require_config_value_exits_on_missing_start_date() -> None:
    with pytest.raises(SystemExit) as exit_info:
        require_config_value({"stocks": ["sh.600519"]}, "default_start_date", "config.json")

    assert "default_start_date" in str(exit_info.value)

def test_require_config_value_names_config_file_and_cli_alternative() -> None:
    with pytest.raises(SystemExit) as exit_info:
        require_config_value({}, "parquet_output_root", "config.json", "--output-root")

    message = str(exit_info.value)
    assert "parquet_output_root" in message
    assert "config.json" in message
    assert "--output-root" in message

def test_read_version_returns_well_formed_semantic_version() -> None:
    assert re.fullmatch(r"v[0-9]+[.][0-9]+[.][0-9]+", read_version())

def test_load_config_file_exits_when_file_missing(tmp_path: Path) -> None:
    missing_config = tmp_path / "config.json"

    with pytest.raises(SystemExit) as exit_info:
        load_config_file(str(missing_config))

    assert f"配置文件不存在: {missing_config}" in str(exit_info.value)

def test_load_config_file_returns_parsed_json(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"stocks": ["sz.000001"]}), encoding="utf-8")

    assert load_config_file(str(config_path)) == {"stocks": ["sz.000001"]}

def test_load_config_reads_json_file(tmp_path: Path) -> None:
    config_path = tmp_path / "any.json"
    config_path.write_text('{"stocks": []}', encoding="utf-8")

    assert load_config(str(config_path)) == {"stocks": []}

def test_default_year_quarters_covers_four_ascending_quarters() -> None:
    quarters = _default_year_quarters()

    assert len(quarters) == 4
    assert quarters == sorted(quarters)
    assert all(1 <= quarter <= 4 for _, quarter in quarters)

@pytest.mark.parametrize("quarters_back", [1, 2, 4, 8])
def test_quarter_window_start_is_first_day_of_earliest_quarter(quarters_back: int) -> None:
    today = date.today()
    current_quarter_index = (today.year * 12 + today.month - 1) // 3
    earliest_quarter_index = current_quarter_index - quarters_back
    earliest_first_day = date(earliest_quarter_index * 3 // 12, earliest_quarter_index * 3 % 12 + 1, 1)

    assert _quarter_window_start(quarters_back) == earliest_first_day.isoformat()

def test_enable_socket_timeout_sets_process_wide_default_timeout() -> None:
    original_timeout = socket.getdefaulttimeout()

    try:
        socket.setdefaulttimeout(None)
        enable_socket_timeout()

        assert socket.getdefaulttimeout() == SOCKET_TIMEOUT_SECONDS
    finally:
        socket.setdefaulttimeout(original_timeout)

def test_logout_quietly_swallows_logout_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_network_error() -> None:
        raise OSError("网络接收错误。")

    monkeypatch.setattr(bao_stock.bs, "logout", raise_network_error)

    logout_quietly()

def test_collect_rows_or_raise_returns_rows_from_every_page() -> None:
    result_set = _PagedResultSet([[["sh.600000"], ["sh.600004"]], [["sh.600006"]]], fields=["code"])

    assert collect_rows_or_raise(result_set, "全市场证券列表") == [["sh.600000"], ["sh.600004"], ["sh.600006"]]

def test_collect_rows_or_raise_rejects_result_set_truncated_mid_pagination() -> None:
    result_set = _PagedResultSet([[["row-1"]], [["row-2"]], [["row-3"]]], fields=["code"], failing_page_index=2)

    with pytest.raises(BaostockPaginationInterruptedError) as raised_error:
        collect_rows_or_raise(result_set, "行业分类")

    assert "行业分类" in str(raised_error.value)
    assert "已取 2 行" in str(raised_error.value)

def test_collect_rows_or_raise_reports_lost_session() -> None:
    result_set = _PagedResultSet(
        [[["row-1"]], [["row-2"]]],
        fields=["code"],
        failing_page_index=1,
        failure_code=BAOSTOCK_SESSION_LOST_ERROR_CODE,
        failure_msg="用户未登录",
    )

    with pytest.raises(BaostockSessionLostError) as raised_error:
        collect_rows_or_raise(result_set, "行业分类")

    assert BAOSTOCK_SESSION_LOST_ERROR_CODE in str(raised_error.value)

def test_collect_rows_or_raise_rejects_missing_result_set() -> None:
    with pytest.raises(BaostockUpdateAbortedError):
        collect_rows_or_raise(None, "行业分类")

def test_baostock_update_aborted_error_keeps_runtime_error_contract() -> None:
    assert issubclass(BaostockUpdateAbortedError, RuntimeError)

def test_exit_on_update_aborted_exits_with_non_zero_code() -> None:
    """中止必须以非 0 退出码收场：原先这类失败以 exit 0 结束，只有读日志才看得出没跑完。"""
    with pytest.raises(SystemExit) as raised_error:
        _exit_on_update_aborted(BaostockSessionLostError("行业分类: 会话已失效（10001001 用户未登录）"))

    assert raised_error.value.code == 1

def test_restrict_to_table_columns_drops_interface_column_with_same_case_insensitive_name() -> None:
    frame = pd.DataFrame([{"updateDate": "2026-10-05", "code": "sh.600000", "UpdateDate": "2026-10-10"}])

    restricted_frame = _restrict_to_table_columns(frame, ["UpdateDate"])

    assert list(restricted_frame.columns) == ["UpdateDate"]
    assert restricted_frame["UpdateDate"].tolist() == ["2026-10-10"]

def test_update_all_commits_downloaded_rows(
    database_config: bao_stock.JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--update` 原先整轮不提交：连接关闭即回滚，下载完一行不落库。"""
    database_path = Path(database_config["database_path"])
    downloaded_rows: list[tuple[str, str]] = []

    def store_downloaded_row(connection: sqlite3.Connection, stock_code: str, start_date: str) -> int:
        connection.execute(
            "INSERT INTO DailyRawPrices (Code, Date) VALUES (?, ?)", (stock_code, start_date)
        )
        downloaded_rows.append((stock_code, start_date))
        return 1

    class _SuccessfulLogin:
        error_code: str = "0"

    monkeypatch.setattr(bao_stock, "login_with_retry", lambda *args, **kwargs: _SuccessfulLogin())
    monkeypatch.setattr(bao_stock, "sync_securities", lambda connection: None)
    monkeypatch.setattr(bao_stock, "fetch_and_store_stock", store_downloaded_row)
    monkeypatch.setattr(bao_stock, "logout_quietly", lambda: None)

    bao_stock._run_update(lambda connection: bao_stock.update_all(connection, database_config), database_config)

    with closing(sqlite3.connect(database_path)) as reopened_connection:
        stored_rows = reopened_connection.execute("SELECT Code, Date FROM DailyRawPrices").fetchall()

    assert downloaded_rows == [("sh.600000", "2000-01-01")]
    assert stored_rows == [("sh.600000", "2000-01-01")]

def test_update_trade_dates_keeps_calendar_when_upstream_returns_no_rows(
    initialized_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上游 err=0 + 0 行时不得清空日历——旧实现先 DELETE 再查询，空结果早退后 DELETE 仍被随后的 commit 落盘。"""
    initialized_connection.execute(
        "INSERT INTO TradeDates (CalendarDate, IsTradingDay) VALUES ('2026-10-09', 1)"
    )
    monkeypatch.setattr(
        bao_stock.bs, "query_trade_dates",
        lambda start_date, end_date: _PagedResultSet([[]], fields=["calendar_date", "is_trading_day"]),
    )

    update_trade_dates(initialized_connection)
    initialized_connection.commit()

    stored_dates = initialized_connection.execute(
        "SELECT CalendarDate, IsTradingDay FROM TradeDates"
    ).fetchall()
    assert stored_dates == [("2026-10-09", 1)]

def test_update_index_constituents_keeps_snapshot_when_upstream_returns_no_rows(
    initialized_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """某指数 err=0 + 0 行时不得先删掉当日快照再 continue——先查后删才留下库里那份。"""
    monkeypatch.setattr(
        bao_stock.bs, "query_trade_dates",
        lambda start_date, end_date: _PagedResultSet(
            [[["2026-10-09", "1"]]], fields=["calendar_date", "is_trading_day"]
        ),
    )
    for query_name in ("query_hs300_stocks", "query_sz50_stocks", "query_zz500_stocks"):
        monkeypatch.setattr(
            bao_stock.bs, query_name,
            lambda query_date: _PagedResultSet([[]], fields=["updateDate", "code", "code_name"]),
        )
    initialized_connection.execute(
        "INSERT INTO IndexConstituents (IndexCode, Code, CodeName, UpdateDate) "
        "VALUES ('hs300', 'sh.600000', '浦发银行', '2026-10-09')"
    )

    update_index_constituents(initialized_connection)

    stored_rows = initialized_connection.execute(
        "SELECT IndexCode, Code, UpdateDate FROM IndexConstituents"
    ).fetchall()
    assert stored_rows == [("hs300", "sh.600000", "2026-10-09")]

def test_run_update_logs_out_and_rolls_back_uncommitted_rows_when_aborted(
    database_config: bao_stock.JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """中止路径必须注销并丢掉未提交事务——「已提交的批次保留、未提交的回滚」是本次承诺的一部分。"""
    database_path = Path(database_config["database_path"])
    events: list[str] = []

    class _SuccessfulLogin:
        error_code: str = "0"

    def insert_then_abort(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO DailyRawPrices (Code, Date) VALUES ('sh.600000', '2026-10-09')"
        )
        events.append("inserted_uncommitted")
        raise BaostockSessionLostError("用户未登录")

    monkeypatch.setattr(bao_stock, "login_with_retry", lambda *args, **kwargs: _SuccessfulLogin())
    monkeypatch.setattr(bao_stock, "logout_quietly", lambda: events.append("logout"))

    with pytest.raises(SystemExit) as raised_error:
        bao_stock._run_update(insert_then_abort, database_config)

    assert raised_error.value.code == 1
    assert events == ["inserted_uncommitted", "logout"]
    with closing(sqlite3.connect(database_path)) as reopened_connection:
        uncommitted_rows = reopened_connection.execute("SELECT COUNT(*) FROM DailyRawPrices").fetchone()
    assert uncommitted_rows == (0,)

def test_run_update_exits_nonzero_when_login_fails(
    database_config: bao_stock.JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登录失败必须以退出码 1 收场——按退出码判成败的调用方不能把「什么都没做」当「做完了」。"""
    monkeypatch.setattr(bao_stock, "login_with_retry", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as raised_error:
        bao_stock._run_update(lambda connection: None, database_config)

    # SystemExit 携带字符串时解释器以状态 1 退出并把该串写进 stderr；两项都要，故断言内容而非 1。
    assert raised_error.value.code != 0
    assert "登录失败" in str(raised_error.value.code)

def test_run_update_requires_database_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """库路径缺配置项即报错，且必须发生在登录之前——不能白登一次再喊缺键。"""
    monkeypatch.setattr(bao_stock, "login_with_retry", lambda *args, **kwargs: pytest.fail("缺键不该走到登录"))

    with pytest.raises(SystemExit) as raised_error:
        bao_stock._run_update(lambda connection: None, {"stocks": ["sh.600000"]})

    assert "database_path" in str(raised_error.value.code)

def test_update_industry_keeps_own_update_date_and_survives_second_run(
    initialized_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接口字段 updateDate 若顶掉我方的 UpdateDate，重跑必撞 (Code, UpdateDate) 主键。"""
    monkeypatch.setattr(
        bao_stock.bs,
        "query_stock_industry",
        lambda: _PagedResultSet(
            [[["2026-10-05", "sh.600000", "浦发银行", "J66货币金融服务", "证监会行业分类"]]],
            fields=["updateDate", "code", "code_name", "industry", "industryClassification"],
        ),
    )

    update_industry(initialized_connection)
    update_industry(initialized_connection)

    stored_rows = initialized_connection.execute(
        "SELECT Code, UpdateDate FROM IndustryClassification"
    ).fetchall()

    assert stored_rows == [("sh.600000", date.today().isoformat())]

def test_update_index_constituents_keeps_base_date_and_survives_second_run(
    initialized_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """与行业分类同根：接口的 updateDate 顶掉我方 UpdateDate 后，重跑必撞 (IndexCode, Code, UpdateDate) 主键。"""
    monkeypatch.setattr(
        bao_stock.bs,
        "query_trade_dates",
        lambda start_date, end_date: _PagedResultSet(
            [[["2026-10-09", "1"]]], fields=["calendar_date", "is_trading_day"]
        ),
    )
    for query_name in ("query_hs300_stocks", "query_sz50_stocks", "query_zz500_stocks"):
        monkeypatch.setattr(
            bao_stock.bs,
            query_name,
            lambda query_date: _PagedResultSet(
                [[["2026-10-05", "sh.600000", "浦发银行"]]], fields=["updateDate", "code", "code_name"]
            ),
        )

    update_index_constituents(initialized_connection)
    update_index_constituents(initialized_connection)

    stored_rows = initialized_connection.execute(
        "SELECT IndexCode, Code, UpdateDate FROM IndexConstituents ORDER BY IndexCode"
    ).fetchall()

    assert stored_rows == [
        ("hs300", "sh.600000", "2026-10-09"),
        ("sz50", "sh.600000", "2026-10-09"),
        ("zz500", "sh.600000", "2026-10-09"),
    ]

def test_get_previous_trading_date_aborts_when_session_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    """接口拉取不完整时中止，而不是悄悄回退到「今天」——回退会让证券同步基准日悄悄变成非交易日。"""
    monkeypatch.setattr(
        bao_stock.bs,
        "query_trade_dates",
        lambda start_date, end_date: _PagedResultSet(
            [[["2026-10-09", "1"]]],
            fields=["calendar_date", "is_trading_day"],
            failing_page_index=1,
            failure_code=BAOSTOCK_SESSION_LOST_ERROR_CODE,
            failure_msg="用户未登录",
        ),
    )

    with pytest.raises(BaostockSessionLostError):
        _get_previous_trading_date()

def test_fetch_and_store_financial_keeps_rows_when_upstream_returns_no_rows(
    initialized_connection: sqlite3.Connection
) -> None:
    """单键财务表 err=0 + 0 行时不得删掉该键既有行——先查后删才留得住。"""
    initialized_connection.execute(
        "INSERT INTO BalanceData (Code, Year, Quarter, CurrentRatio) VALUES ('sh.600000', 2026, 2, 1.5)"
    )
    query_func = lambda code, year, quarter: _PagedResultSet(
        [[]], fields=["code", "pubDate", "statDate", "currentRatio"]
    )

    written_rows = _fetch_and_store_financial(
        initialized_connection, "sh.600000", 2026, 2, query_func, "BalanceData", ["CurrentRatio"]
    )

    assert written_rows == 0
    stored_rows = initialized_connection.execute(
        "SELECT Code, Year, Quarter, CurrentRatio FROM BalanceData"
    ).fetchall()
    assert stored_rows == [("sh.600000", 2026, 2, 1.5)]

def test_update_deposit_rates_keeps_rows_when_upstream_returns_no_rows(
    initialized_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清空重取的宏观表 err=0 + 0 行时不得整表清空——旧实现先 DELETE 全表再查询。"""
    initialized_connection.execute(
        "INSERT INTO DepositRates (PubDate, DemandDepositRate) VALUES ('2026-10-09', 0.35)"
    )
    monkeypatch.setattr(
        bao_stock.bs, "query_deposit_rate_data",
        lambda start_date: _PagedResultSet([[]], fields=["pubDate", "demandDepositRate"]),
    )

    update_deposit_rates(initialized_connection)
    initialized_connection.commit()

    stored_rows = initialized_connection.execute(
        "SELECT PubDate, DemandDepositRate FROM DepositRates"
    ).fetchall()
    assert stored_rows == [("2026-10-09", 0.35)]
