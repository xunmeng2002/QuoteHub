#!/usr/bin/env python3

"""建库与升级脚本——本仓唯一执行 DDL 的入口。

应用侧（bao_stock.py / bao_stock_parquet.py）启动只做两件事：连接、核对版本，不建表、不迁移。
库的创建与升级都在这里，须在 QuoteHub 仓根手工执行（database_path 相对当前工作目录解析）：

    python init_database.py --create     在 config.json 的 database_path 上建新库
    python init_database.py --upgrade    认领或升级既有库

版本戳（PRAGMA user_version）是 sql/schema/Create_v1.0.0.sql 的 CRC32，既记「这个库由哪一版建库
脚本建成」，也是应用侧启动比对的期望值。改 schema 文件即改戳，须重跑 --upgrade 重新打戳。
"""

import argparse
import contextlib
import logging
import os
import sqlite3

from bao_stock import (
    CONFIG_FILE,
    existing_table_names,
    load_config_file,
    read_schema_script,
    require_config_value,
    schema_fingerprint,
)

LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

UPGRADES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql", "upgrades")

logger = logging.getLogger(__name__)

def parse_arguments() -> argparse.Namespace:
    """解析命令行；--create 与 --upgrade 必须且只能给一个。

    Returns:
        解析结果，含 create 与 upgrade 两个布尔位。
    """
    parser = argparse.ArgumentParser(description="QuoteHub 建库与升级（本仓唯一执行 DDL 的入口）")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--create", action="store_true", help="在 config.json 的 database_path 上建新库（目标库须为空）")
    actions.add_argument("--upgrade", action="store_true", help="认领或升级既有库，并写入版本戳")
    return parser.parse_args()

def create_schema(connection: sqlite3.Connection) -> None:
    """建新库：在一个事务内执行建库脚本并写入版本戳。

    Args:
        connection: 已打开的 SQLite 连接，目标库须为空。

    Raises:
        SystemExit: 目标库已有业务表——不覆盖、不合并；要重来须先删除库文件。
    """
    existing_tables = existing_table_names(connection)
    if existing_tables:
        raise SystemExit(f"目标库已有 {len(existing_tables)} 张表，--create 只用于空库；要重建请先删除库文件，要沿用既有库请改用 --upgrade")

    schema_text = read_schema_script()
    _run_ddl_in_transaction(connection, [schema_text], schema_fingerprint(schema_text), None)
    logger.info("建库完成，共 %s 张表", len(existing_table_names(connection)))

def upgrade_schema(connection: sqlite3.Connection) -> None:
    """认领或升级既有库：结构核对通过才打戳，不符则先跑升级脚本再复核。

    结构核对对所有分支统一执行——无条件重写戳会把结构不对的库盖上正确戳，应用侧再也发现不了。

    Args:
        connection: 已打开的 SQLite 连接。

    Raises:
        SystemExit: 库结构与建库脚本不符，且既有升级脚本没能修好。
    """
    schema_text = read_schema_script()
    upgrade_scripts = _read_upgrade_scripts()
    expected_structure = _structure_built_from("\n".join([schema_text, *upgrade_scripts]))

    actual_structure = _database_structure(connection)
    if actual_structure == expected_structure:
        _write_version_stamp(connection, schema_fingerprint(schema_text))
        logger.info("库结构与建库脚本一致，版本戳已写入")
        return

    if not upgrade_scripts:
        raise SystemExit(
            "库结构与建库脚本不符，且没有可用的升级脚本：\n"
            + _describe_structure_difference(expected_structure, actual_structure)
            + "\n请删掉库文件后用 --create 重建，或人工核对结构后再处理"
        )

    logger.info("库结构与建库脚本不符，执行 %s 个升级脚本后复核", len(upgrade_scripts))
    _run_ddl_in_transaction(connection, upgrade_scripts, schema_fingerprint(schema_text), expected_structure)
    logger.info("升级完成，版本戳已写入")

def _run_ddl_in_transaction(connection: sqlite3.Connection, scripts: list[str], stamp: int, expected_structure: dict[str, object] | None) -> None:
    """在一个事务里跑 DDL 脚本与版本戳；给了期望结构就在提交前复核，不符即整体回滚。

    Args:
        connection: 已打开的 SQLite 连接。
        scripts: 要依次执行的 DDL 脚本全文。
        stamp: 要写入 PRAGMA user_version 的版本戳。
        expected_structure: 执行后应达到的结构；None 表示不做复核（建新库时无需）。

    Raises:
        SystemExit: 脚本执行失败，或复核发现结构仍不符（两种情况都已回滚）。
    """
    try:
        connection.executescript("BEGIN;\n" + "\n".join(scripts))
        if expected_structure is not None:
            actual_structure = _database_structure(connection)
            if actual_structure != expected_structure:
                connection.rollback()
                raise SystemExit("升级脚本执行后库结构仍与建库脚本不符，已回滚：\n" + _describe_structure_difference(expected_structure, actual_structure))
        _write_version_stamp(connection, stamp)
    except sqlite3.Error as schema_error:
        connection.rollback()
        raise SystemExit(f"建库/升级失败，已回滚: {schema_error}") from schema_error

def _write_version_stamp(connection: sqlite3.Connection, stamp: int) -> None:
    """写入版本戳并提交当前事务。

    Args:
        connection: 已打开的 SQLite 连接。
        stamp: 版本戳；PRAGMA 不接受占位符，只能拼进 SQL，故此处只允许 int。
    """
    connection.execute(f"PRAGMA user_version = {stamp}")
    connection.commit()

def _database_structure(connection: sqlite3.Connection) -> dict[str, object]:
    """库结构的规范化快照：表（小写名）→ 列与索引，用于逐项比对。

    只看 PRAGMA（table_info / index_list / index_info），不读 `sqlite_master.sql` 原文——约束名、
    注释与空白差异不该让结构比对误判。表名取自库自身、PRAGMA 又不接受占位符，故拼进 SQL：
    值不是外部输入，来源只有本仓 schema。

    Args:
        connection: 已打开的 SQLite 连接。

    Returns:
        表名 → (列列表, 索引列表)；列为 (名, 类型, 非空, 默认值, 主键序号)，按 cid 序。
    """
    structure: dict[str, object] = {}
    for table_name in existing_table_names(connection):
        columns = [
            (row[1].lower(), (row[2] or "").upper(), row[3], row[4], row[5])
            for row in connection.execute(f'PRAGMA table_info("{table_name}")')
        ]
        indexes = []
        for index_row in connection.execute(f'PRAGMA index_list("{table_name}")'):
            if index_row[3] == "pk":
                continue
            index_columns = [
                column_row[2].lower()
                for column_row in connection.execute(f'PRAGMA index_info("{index_row[1]}")')
            ]
            indexes.append((index_row[1].lower(), index_columns))
        structure[table_name.lower()] = (columns, sorted(indexes))
    return structure

def _structure_built_from(ddl_script: str) -> dict[str, object]:
    """在内存库里跑一遍给定 DDL，取其结构作为期望值。

    期望结构只能由维护侧算——应用侧被要求不执行任何 DDL。

    Args:
        ddl_script: 建库脚本与升级脚本拼成的 DDL 全文。

    Returns:
        规范化结构快照，形态见 _database_structure。
    """
    with contextlib.closing(sqlite3.connect(":memory:")) as reference_connection:
        reference_connection.executescript(ddl_script)
        return _database_structure(reference_connection)

def _describe_structure_difference(expected_structure: dict[str, object], actual_structure: dict[str, object]) -> str:
    """把两套结构的差异列成人读的几行——报错要能直接指出缺什么、多什么。

    Args:
        expected_structure: 建库脚本给出的结构。
        actual_structure: 目标库当前的结构。

    Returns:
        逐行差异文本。
    """
    differences: list[str] = []
    for missing_table in sorted(set(expected_structure) - set(actual_structure)):
        differences.append(f"  缺表: {missing_table}")
    for extra_table in sorted(set(actual_structure) - set(expected_structure)):
        differences.append(f"  多表: {extra_table}")
    for shared_table in sorted(set(expected_structure) & set(actual_structure)):
        if expected_structure[shared_table] != actual_structure[shared_table]:
            differences.append(f"  表 {shared_table} 的列或索引不符")
    return "\n".join(differences)

def _read_upgrade_scripts() -> list[str]:
    """按文件名顺序读取 sql/upgrades/ 下的升级脚本。

    Returns:
        升级脚本全文列表；目录缺失或为空时返回空列表。
    """
    if not os.path.isdir(UPGRADES_DIR):
        return []
    script_names = sorted(name for name in os.listdir(UPGRADES_DIR) if name.endswith(".sql"))
    scripts: list[str] = []
    for script_name in script_names:
        with open(os.path.join(UPGRADES_DIR, script_name), "r", encoding="utf-8") as script_file:
            scripts.append(script_file.read())
    return scripts

def main() -> None:
    """命令行入口：读配置取库路径，按动作建库或升级。"""
    arguments = parse_arguments()
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    config = load_config_file(CONFIG_FILE)
    database_path: str = require_config_value(config, "database_path", CONFIG_FILE)

    with contextlib.closing(sqlite3.connect(database_path)) as connection:
        if arguments.create:
            create_schema(connection)
        else:
            upgrade_schema(connection)

if __name__ == "__main__":
    main()
