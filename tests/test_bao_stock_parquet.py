"""bao_stock_parquet.py 单元测试：纯函数（不碰数据库、网络与真实文件系统）。"""

from __future__ import annotations

import pytest

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
    result = to_sqlite_value(value)

    if expected is None:
        assert result is None
    else:
        assert result == expected


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
