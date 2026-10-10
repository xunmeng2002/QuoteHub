"""bao_stock.py 单元测试：配置守卫与季度窗口推算（纯函数，不碰数据库与网络）。"""

from __future__ import annotations

import json
import re
import socket

from datetime import date
from pathlib import Path

import pytest

import bao_stock

from bao_stock import (
    SOCKET_TIMEOUT_SECONDS,
    _default_year_quarters,
    _quarter_window_start,
    enable_socket_timeout,
    load_config,
    load_config_file,
    logout_quietly,
    read_version,
    require_config_value,
)

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
