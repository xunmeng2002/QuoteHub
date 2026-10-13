"""init_database.py 单元测试：建库、认领与结构核对（只碰临时库，不碰网络）。"""

import sqlite3

from contextlib import closing
from pathlib import Path

import pytest

import bao_stock
import init_database

def test_create_schema_builds_every_table_in_the_script(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh.db"

    with closing(sqlite3.connect(database_path)) as connection:
        init_database.create_schema(connection)
        stored_tables = set(bao_stock.existing_table_names(connection))
        stored_stamp = connection.execute("PRAGMA user_version").fetchone()[0]

    assert stored_tables == bao_stock.schema_table_names(bao_stock.read_schema_script())
    assert stored_stamp == bao_stock.expected_schema_fingerprint()

def test_create_schema_refuses_database_that_already_has_tables(tmp_path: Path) -> None:
    with closing(sqlite3.connect(tmp_path / "existing.db")) as connection:
        init_database.create_schema(connection)

        with pytest.raises(SystemExit) as exit_info:
            init_database.create_schema(connection)

    assert "--create 只用于空库" in str(exit_info.value)

def test_upgrade_schema_adopts_matching_database_and_stays_idempotent(tmp_path: Path) -> None:
    """存量库（user_version 为 0）结构相符即认领；重跑不得报错，也不得改变表集。"""
    database_path = tmp_path / "adopt.db"
    expected_tables = bao_stock.schema_table_names(bao_stock.read_schema_script())

    with closing(sqlite3.connect(database_path)) as connection:
        init_database.create_schema(connection)
        connection.execute("PRAGMA user_version = 0")

        init_database.upgrade_schema(connection)
        init_database.upgrade_schema(connection)

        adopted_stamp = connection.execute("PRAGMA user_version").fetchone()[0]
        adopted_tables = set(bao_stock.existing_table_names(connection))

    assert adopted_stamp == bao_stock.expected_schema_fingerprint()
    assert adopted_tables == expected_tables

def test_upgrade_schema_reports_structure_mismatch(tmp_path: Path) -> None:
    database_path = tmp_path / "broken.db"

    with closing(sqlite3.connect(database_path)) as connection:
        init_database.create_schema(connection)
        connection.execute("DROP TABLE QueriedBarSpans")

        with pytest.raises(SystemExit) as exit_info:
            init_database.upgrade_schema(connection)

    assert "queriedbarspans" in str(exit_info.value).lower()

def test_upgrade_schema_rewrites_stamp_when_structure_matches(tmp_path: Path) -> None:
    """被旧戳或错戳盖过的库，只要结构对就重写戳（结构不对时一律不写，见上一条）。"""
    database_path = tmp_path / "restamp.db"

    with closing(sqlite3.connect(database_path)) as connection:
        init_database.create_schema(connection)
        connection.execute("PRAGMA user_version = 12345")

        init_database.upgrade_schema(connection)

        assert connection.execute("PRAGMA user_version").fetchone()[0] == bao_stock.expected_schema_fingerprint()
def test_every_index_in_the_script_follows_the_naming_rule(tmp_path: Path) -> None:
    """索引名一律 Idx{表}{列} / Uidx{表}{列}（database-style.md §1），防止再退回 IX_ 之类。"""
    database_path = tmp_path / "indexes.db"

    with closing(sqlite3.connect(database_path)) as connection:
        init_database.create_schema(connection)
        index_names = [
            index_row[1]
            for table_name in bao_stock.existing_table_names(connection)
            for index_row in connection.execute(f'PRAGMA index_list("{table_name}")')
            if index_row[3] != "pk"
        ]

    assert index_names
    assert all(index_name.startswith(("Idx", "Uidx")) for index_name in index_names)
