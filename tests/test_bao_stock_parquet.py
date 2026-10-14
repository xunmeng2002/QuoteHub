"""bao_stock_parquet.py 单元测试：纯函数与回补导出粒度（不碰网络、不碰真实输出根）。"""

from __future__ import annotations

import sqlite3

from collections.abc import Iterator
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import bao_stock
import bao_stock_parquet
import init_database

from bao_stock_parquet import (
    _spans_are_contiguous,
    bar_file_path,
    detect_cumulative_mode,
    get_last_month,
    group_codes_by_exchange,
    merge_day_spans,
    split_stock_code,
    to_sqlite_value,
)

@pytest.mark.parametrize(
    ("stock_code", "expected_exchange_and_instrument"),
    [
        ("sh.600519", ("SSE", "600519")),
        ("sz.000001", ("SZSE", "000001")),
        ("SH.600519", ("SSE", "600519")),
    ],
)
def test_split_stock_code_maps_prefix_to_exchange(stock_code: str, expected_exchange_and_instrument: tuple[str, str]) -> None:
    assert split_stock_code(stock_code) == expected_exchange_and_instrument

@pytest.mark.parametrize("unrecognized_code", ["600519", "sh.", "xx.600519", ""])
def test_split_stock_code_rejects_unrecognized(unrecognized_code: str) -> None:
    with pytest.raises(ValueError):
        split_stock_code(unrecognized_code)

def test_group_codes_by_exchange_keeps_input_order_within_exchange() -> None:
    grouped = group_codes_by_exchange(["sh.600000", "sz.000001", "sh.600519"])

    assert grouped == {"SSE": ["sh.600000", "sh.600519"], "SZSE": ["sz.000001"]}

@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (float("nan"), None), (0, 0), (1.5, 1.5)],
)
def test_to_sqlite_value_maps_nan_to_none(value: object, expected: object) -> None:
    converted_value = to_sqlite_value(value)

    if expected is None:
        assert converted_value is None
    else:
        assert converted_value == expected

def test_merge_day_spans_merges_adjacent_spans() -> None:
    merged = merge_day_spans([("2024-01-01", "2024-01-05"), ("2024-01-06", "2024-01-10")])

    assert merged == [("2024-01-01", "2024-01-10")]

def test_merge_day_spans_keeps_gapped_spans_separate() -> None:
    merged = merge_day_spans([("2024-01-01", "2024-01-05"), ("2024-01-08", "2024-01-10")])

    assert merged == [("2024-01-01", "2024-01-05"), ("2024-01-08", "2024-01-10")]

@pytest.mark.parametrize(
    ("next_start", "current_end", "expected"),
    [
        ("2024-01-06", "2024-01-05", True),
        ("2024-01-05", "2024-01-05", True),
        ("2024-01-07", "2024-01-05", False),
    ],
)
def test_spans_are_contiguous_bounds(next_start: str, current_end: str, expected: bool) -> None:
    assert _spans_are_contiguous(next_start, current_end) is expected

def test_bar_file_path_builds_hive_style_path() -> None:
    path = bar_file_path("D:/md", "SSE", "202411", "5")

    assert path.replace("\\", "/") == "D:/md/Bar/Identity=SSE.Stock/Year=2024/202411_5m.parquet"

def test_get_last_month_returns_six_digit_year_month() -> None:
    last_month = get_last_month()

    assert len(last_month) == 6
    assert last_month.isdigit()

def test_detect_cumulative_mode_recognizes_intraday_cumulative() -> None:
    assert detect_cumulative_mode([10.0, 25.0, 40.0], 40.0) is True

@pytest.mark.parametrize(
    ("bar_values", "daily_total"),
    [([1.0, 3.0, 2.0], 6.0), ([0.0, 0.0], 0.0), ([1.0, 2.0], float("nan")), ([], 5.0)],
)
def test_detect_cumulative_mode_defaults_to_incremental(bar_values: list[float], daily_total: float) -> None:
    assert detect_cumulative_mode(bar_values, daily_total) is False


@pytest.fixture
def initialized_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """建好表并打好版本戳的临时库连接，用例结束即关闭。"""
    with closing(sqlite3.connect(tmp_path / "test.db")) as connection:
        init_database.create_schema(connection)
        yield connection

def test_connect_database_opens_configured_path(tmp_path: Path) -> None:
    """库路径取自配置键 database_path；库须已由 init_database.py 建好，管线只连不建。"""
    configured_path = tmp_path / "configured.db"
    with closing(sqlite3.connect(configured_path)) as setup_connection:
        init_database.create_schema(setup_connection)

    with closing(bao_stock_parquet.connect_database({"database_path": str(configured_path)}, "config.json")) as connection:
        stored_tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()

    assert configured_path.exists()
    assert ("MinuteBars",) in stored_tables

def test_connect_database_refuses_uninitialized_database(tmp_path: Path) -> None:
    """应用侧不再建表：库未初始化即以退出码 1 报错，并指向 init_database.py。"""
    with pytest.raises(SystemExit) as exit_info:
        bao_stock_parquet.connect_database({"database_path": str(tmp_path / "empty.db")}, "config.json")

    assert "init_database.py --create" in str(exit_info.value)

def test_connect_database_names_the_config_file_it_was_given(tmp_path: Path) -> None:
    """错误提示须指向实际读的那个配置路径，而非常量 CONFIG_FILE——管线支持 --config。"""
    other_config_file = str(tmp_path / "elsewhere.json")

    with pytest.raises(SystemExit) as exit_info:
        bao_stock_parquet.connect_database({}, other_config_file)

    assert "database_path" in str(exit_info.value)
    assert other_config_file in str(exit_info.value)

def test_run_backfill_exports_in_progress_year_as_monthly_files(
    initialized_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回补带未结束年度时只写月度文件：年度文件会与当年 update/merge 的日度、月度文件重复覆盖同一天。"""
    completed_year = date.today().year - 1
    in_progress_year = str(date.today().year)
    for trading_day in (f"{completed_year}-06-02", f"{in_progress_year}-10-09"):
        initialized_connection.execute(
            "INSERT INTO MinuteBars (Code, Frequency, TradingDay, Time, Open, High, Low, Close, Volume, Amount) "
            "VALUES ('sh.600000', '5', ?, ?, '1', '1', '1', '1', '1', '1')",
            (trading_day, trading_day.replace("-", "") + "150000000"),
        )
    initialized_connection.commit()
    exported_months: list[str] = []
    exported_periods: list[str] = []

    monkeypatch.setattr(bao_stock_parquet, "ingest_stock", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bao_stock_parquet, "run_merge_month",
        lambda connection, stock_codes, frequency, output_root, month: exported_months.append(month),
    )
    monkeypatch.setattr(
        bao_stock_parquet, "export_bars_from_database",
        lambda *args, **kwargs: exported_periods.append(args[7]),
    )

    bao_stock_parquet.run_backfill(
        initialized_connection, ["sh.600000"], "5", "unused-output-root",
        f"{completed_year}-01-01", f"{in_progress_year}-12-31",
    )

    assert exported_months == [f"{in_progress_year}10"]
    assert exported_periods == [str(completed_year)]

def test_ingest_stock_relogins_and_retries_after_interrupted_fetch(
    initialized_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取数中断即重登录并整票重试：baostock 一超时就废掉 socket，不换连接则此后每次查询都失败。"""
    fetch_count = 0
    login_count = 0

    def interrupt_then_succeed(*args: object, **kwargs: object) -> list[str]:
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 1:
            raise bao_stock.BaostockPaginationInterruptedError("分页中断")
        return ["2026-10-13"]

    def record_login(*args: object, **kwargs: object) -> object:
        nonlocal login_count
        login_count += 1
        return object()

    monkeypatch.setattr(bao_stock_parquet, "_ingest_stock_once", interrupt_then_succeed)
    monkeypatch.setattr(bao_stock_parquet, "login_with_retry", record_login)

    recovered_days = bao_stock_parquet.ingest_stock(initialized_connection, "sh.600000", "5", "2020-01-01", "2026-10-13")

    assert recovered_days == ["2026-10-13"]
    assert fetch_count == 2
    assert login_count == 1

def test_ingest_stock_gives_up_after_retry_limit(
    initialized_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试有上限：尝试次数用尽即把原错误抛出，不无限重登录。"""
    login_count = 0

    def always_interrupt(*args: object, **kwargs: object) -> list[str]:
        raise bao_stock.BaostockSessionLostError("会话已失效")

    def record_login(*args: object, **kwargs: object) -> object:
        nonlocal login_count
        login_count += 1
        return object()

    monkeypatch.setattr(bao_stock_parquet, "_ingest_stock_once", always_interrupt)
    monkeypatch.setattr(bao_stock_parquet, "login_with_retry", record_login)

    with pytest.raises(bao_stock.BaostockSessionLostError):
        bao_stock_parquet.ingest_stock(initialized_connection, "sh.600000", "5", "2020-01-01", "2026-10-13")

    assert login_count == bao_stock_parquet.SESSION_RETRY_ATTEMPTS - 1

def test_ingest_stock_stops_when_relogin_fails(
    initialized_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重登录失败即放弃该票，不再拿已废的会话白试；原错误原样上抛以便留下根因。"""
    fetch_count = 0
    login_count = 0

    def always_interrupt(*args: object, **kwargs: object) -> list[str]:
        nonlocal fetch_count
        fetch_count += 1
        raise bao_stock.BaostockPaginationInterruptedError("分页中断")

    def fail_login(*args: object, **kwargs: object) -> None:
        nonlocal login_count
        login_count += 1

    monkeypatch.setattr(bao_stock_parquet, "_ingest_stock_once", always_interrupt)
    monkeypatch.setattr(bao_stock_parquet, "login_with_retry", fail_login)

    with pytest.raises(bao_stock.BaostockPaginationInterruptedError):
        bao_stock_parquet.ingest_stock(initialized_connection, "sh.600000", "5", "2020-01-01", "2026-10-13")

    assert fetch_count == 1
    assert login_count == 1

def test_ingest_stock_leaves_no_partial_rows_when_fetch_is_interrupted(
    initialized_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """中断不落半份数据：日线失败时已取到的分钟线也不得入库、不得记已问区间——整票重试的安全性正靠这一条。"""
    minute_frame = pd.DataFrame(
        [["2026-10-13", "20261013150000000", "sh.600000", "1", "1", "1", "1", "1", "1"]],
        columns=bao_stock_parquet.MINUTE_K_FIELDS.split(","),
    )

    def raise_on_daily(*args: object, **kwargs: object) -> pd.DataFrame:
        raise bao_stock.BaostockPaginationInterruptedError("日线分页中断")

    monkeypatch.setattr(bao_stock_parquet, "query_minute_bars", lambda *args, **kwargs: minute_frame)
    monkeypatch.setattr(bao_stock_parquet, "query_daily_bars", raise_on_daily)
    monkeypatch.setattr(bao_stock_parquet, "login_with_retry", lambda *args, **kwargs: object())

    with pytest.raises(bao_stock.BaostockPaginationInterruptedError):
        bao_stock_parquet.ingest_stock(initialized_connection, "sh.600000", "5", "2020-01-01", "2026-10-13")

    stored_minute_bar_count = initialized_connection.execute("SELECT COUNT(*) FROM MinuteBars").fetchone()[0]
    stored_span_count = initialized_connection.execute("SELECT COUNT(*) FROM QueriedBarSpans").fetchone()[0]

    assert stored_minute_bar_count == 0
    assert stored_span_count == 0
