# PROGRESS 归档 · QuoteHub

本文件存放 `PROGRESS.md` 已关闭条目的**原文**（含当时的判据与理由）；`D` 系列在前、`Q` 系列在后，各自 ID 倒序。检索入口见 [PROGRESS-index.md](PROGRESS-index.md)。

ID 规则：`D`＝原 ✅ ／ `Q`＝原 ❓ ／ `R`＝原 🔄，编号取自 **2026-10-10 首次拆分当日**的文档顺序，此后永久稳定；新增条目在**首次归档时**按当时顺序续编。

## D.10 三键一律必填：删掉 default_start_date 的三处代码兜底

- 原日期：2026-10-10
- 提交：`9fa0dcc`/`35ebfc2`/`06bd8e7`

原文：

- 2026-10-10：**三键一律必填：删掉 `default_start_date` 的三处代码兜底**（用户指正："我不是说了不要兜底值，缺参数报错吗？"）。**问题**：上一轮裁定"配置一律不兜底"时只落地了 `stocks` 与 `parquet_output_root`，`default_start_date` 仍在代码里留兜底常量——`bao_stock.py:1178`/`:1270` 的 `2000-01-01`、`bao_stock_parquet.py:919` 的 `2015-01-01`，等于把同一裁定执行了一半。**提交**：`9fa0dcc` 代码与测试、`35ebfc2` 文档、本条 PROGRESS。**改动**：① 三处 `config.get("default_start_date", …)` 全部改 `require_config_value(config, "default_start_date", …)`；② `update_all_extended` 的守卫**上提到函数开头**与 `stocks` 并列——原位置在 4/7 步（`sync_securities`/`update_industry`/`update_index_constituents` 之后），那时库已被写过一半，失败点越晚越难受，守卫的意义就是"把问题停在启动那一刻"（该函数 docstring 原文）；③ `bao_stock_parquet.py` 传 `arguments.config` 而非常量，错误信息指名实际配置文件；④ 模块 docstring 由"output_root 与代码清单无兜底"改为"三个配置键均无代码兜底"；⑤ 两份文档同步（管线 §2.3 缺省列改 **无（必填）**、注意段由单键推广为三键；采集器 §3 去掉"可省、代码兜底"）；⑥ 新增测试 `test_require_config_value_exits_on_missing_start_date`。**未动（非配置兜底）**：`bao_stock.py` 的 `_update_deposit_rates` / `_update_loan_rates` / `_update_required_reserve_ratio` 里 `start_date="2000-01-01"` 是宏观利率接口"清空重取全历史"的固定入参，与配置无关，不属兜底。**验证**：`git grep "config.get("` 仅剩 `require_config_value` 自身那次无默认值的读取与本文档历史条目；缺 `default_start_date` 时 `bao_stock.py --update-all` 与 `bao_stock_parquet.py update` 均 `配置缺少必填项 default_start_date（config.json）`、exit 1；`py_compile` 通过、`python -m pytest -q` → **37 passed**；两脚本 `--help` 退出码 0。

## D.09 两份配置合并回单一 config.json

- 原日期：2026-10-10
- 提交：`2f745eb`/`5e3e8b3`/`d26d0c8`

原文：

- 2026-10-10：**两份配置合并回单一 `config.json`**（用户裁定"合并回单一配置文件"；起因是用户当场质疑上一批的错判——"`"stocks": [...]` 不是两边都有吗？"）。**提交**：`2f745eb` 代码与配置、`5e3e8b3` 文档、本条 PROGRESS。**纠错**：上一批把"键集不同"当成了"无共享"，实测两份文件的 `stocks` 与 `default_start_date` **取值逐字相同**（合并前 `diff <(jq -S . bao_stock.json) <(jq -S . bao_stock_parquet.json)` 只差 `parquet_output_root` 一键）——重复既已存在，"拆成两份"实际是把它变成"必须手工保持同步的两份"，正是用户最初担心的"容易导致错误"。**改动六项**：① 新建 `config.json`（`stocks` / `default_start_date` / `parquet_output_root` 三键，取值沿用原两份，4 空格缩进）；② `git rm bao_stock.json bao_stock_parquet.json`（受控文件，`git show HEAD:<name>` 可取回）；③ 两脚本 `CONFIG_FILE` 同指 `config.json`；④ `bao_stock_parquet.py` 的 `--config` help 补"与 bao_stock.py 共用"；⑤ `tests/test_bao_stock.py` 六处陈旧文件名（`:22`/`:27`/`:31`/`:35`/`:39`/`:47`）改 `config.json`；⑥ 两份文档同步——管线文档 §2.3 标题、§2.2 文件名引用、§6 两处命令示例，以及那条**自相矛盾**的 ⚠️（同一段既写"各存一份，互不引用"又写"两处清单需自行保持一致"）改写为共用一份的**注意**；采集器文档 §3 的"读**自己的** `bao_stock.json`（与管线脚本相互独立）"改为"与管线脚本 `bao_stock_parquet.py` 共用同一份"。**验证**：`git grep` 陈旧文件名仅剩本文档历史条目（历史不改写）；`py_compile` 通过；`python -m pytest -q` → **36 passed**；六个改动文件逐字节核对 **CRLF=0**，代码与 JSON 最长行 ≤150。**未动**：`DB_PATH = "stock_data.db"` 仍硬编码；`default_start_date` 兜底值差异见下方 ❓。

## D.08 新版规范复审的 🟡 五项全部收口

- 原日期：2026-10-10
- 提交：`019fb3c`/`0a09d8f`/`ef1e3cc`

原文：

- 2026-10-10：**新版 python-style 复审的 🟡 五项全部收口**——用户裁定「1、2 先做了，4 保持不变，3 说清楚」。① **§3 引号**（`019fb3c`）：`bao_stock.py:375` 与 `bao_stock_parquet.py:677` 共 4 处单引号 dict key 改双引号；`tests/test_bao_stock.py` 的 `'{"stocks": []}'` 因内容含双引号、按规范允许单引号而**未动**——复核后全仓仅剩这一处单引号。② **§4 注解**（`0a09d8f`）：`tests/test_bao_stock.py` 三个测试的 `tmp_path` 补 `: Path`，并补 `from pathlib import Path`（标准库组内按模块名字母序排在 `datetime` 之后）；至此**全仓函数参数/返回值注解覆盖率 100%**。④ **§3 尾随逗号**：用户裁定**保持不变**——规则 §3 只点名「多行集合、导入列表」，且已明写「函数签名与调用实参不适用本条」，51 处多行调用维持无尾随逗号。③ **§4 `Any` 理由**（`ef1e3cc`）：按用户裁定「抽别名 + docstring」落地——9 处 `dict[str, Any]` 收敛为 `type JsonObject = dict[str, Any]`（PEP 695，只定义在 `bao_stock.py`，`bao_stock_parquet.py` 从既有的 `from bao_stock import …` 一行取用，两处引用同一对象），完整理由写进模块 docstring 的「关于 `Any`」段；剩余 `Any`（`bao_stock.py` 7／`bao_stock_parquet.py` 4）全部落在该段列的两类成因内。**偏离预览一处并说明**：预览把说明写成 `type` 语句后的 `"""…"""`——那不是 docstring（Python 仅模块/类/函数持有 docstring），只是一句无副作用的死表达式，故改为「模块 docstring 段落 ＋ 别名上方一行指针注释」。**验证**：`py_compile` 通过、`python -m pytest -q` → **36 passed**、五个文件逐字节 CRLF=0。

## D.07 按新版规范重审并把 🔴 四项改完

- 原日期：2026-10-10
- 提交：`98b6287`/`cdea6b9`/`6ddd1b8`/`28706d9`/`387cf29`

原文：

- 2026-10-10：**按新版 `rules/python-style.md` 重审本仓并把 🔴 四项改完**（用户裁定「修 🔴 全部四项」、空行「按新规压到 1」；逐项分开提交）。上次那轮「按修订版审查」实际对照的是**旧版条文**，本轮才真正对上 2026-10-07 新版。**手段**：对 5 个 `.py` 做 AST 扫描（`from __future__` 覆盖／参数与返回值注解缺失／裸 `except`／`eval`·`exec`·`os.system`·`shell=True`／`typing.List` 等旧式写法／单引号字面量／多行签名长度／连续空行段／`Args` 段是否复述类型），配合逐字节行尾核对。**五项提交**：① `98b6287` **§2 删编码声明**——`bao_stock.py` 第 2 行 `# -*- coding: utf-8 -*-` 去掉、保留 shebang（PEP 3120 已固定 UTF-8）。② `cdea6b9` **§4 补 `from __future__ import annotations`**——`conftest.py` 与两个测试文件原先漏了（两个主脚本本就有）。③ `6ddd1b8` **§3 签名换行**——23 处多行签名里 16 处（含 1 处测试签名）并成单行，**6 处**超 150 改「对齐续行 + 贪心填至 ≤120」的水平填充。**判定比上轮多出 3 处**：`update_financial_data` 158／`update_performance_express` 156／`update_forecast_reports` 153 也超线（上轮口径漏了返回注解与 `x: int = 1` 的两侧空格）。**折叠不用 `ast.unparse`**——它会把 `max_retries: int = RETRY_DELAY_SECONDS` 压成 `int=RETRY_DELAY_SECONDS`，与本仓「带注解默认值两侧留空格」（PEP 8，规范 §3 示例 `name: str = "default"` 同款）的既有口径相反；改用**按原文去缩进拼接**，并对每处回验「重新解析后 `args`/`returns` 的 `ast.unparse` 与原文逐字相等」。④ `28706d9` **§10 日志惰性化**——69 处 `logger.*(f"…")` 改 `logger.*("…%s…", args)`（`bao_stock.py` 57／`bao_stock_parquet.py` 12）。**先探明风险面**：147 个插值点**全是纯 `{expr}`**（无 `!r`、无格式说明符），故替换无歧义；文本里的字面 `%` 一律转义为 `%%`（parquet 两处「偏差 …%」）；8 处跨行调用顺带并回单行。自检：残留 f-string 日志 0、逐条「`%s` 计数 == 实参数」。⑤ `387cf29` **§3 空行 2→1**——108 处（`bao_stock.py` 46／parquet 43／两个测试 8+11），全仓现无 3 行及以上空行段；用正则 `\n(?:[ \t]*\n){2,} → \n\n` 压缩，**不碰文件末尾换行**，改后 `ast.dump`（不含位置）与改前**逐文件全等**，证明纯删空行。**验证**：`python -m pytest -q` → **36 passed**（五批每批复跑）；全仓 `.py` **0 行超 150**；五个文件**逐字节**核对 CRLF=0（`Edit` 与 `Path.write_text` 都会把整文件转 CRLF，故每轮改完必查；本批全用 `write_bytes` 落盘）。**未改（新版 🟡，见 ❓）**：单引号 dict key 4 处、测试函数 3 个 `tmp_path` 参数缺注解、19 处 `Any` 未写理由、51 处多行调用无尾随逗号（规则未覆盖调用实参，属灰区）。

## D.06 按修订版规范全项目审查并整改

- 原日期：2026-10-10
- 提交：—

原文：

- 2026-10-10：**按修订版 `rules/python-style.md` 全项目审查并整改**（用户裁定："修 🔴 + 🟠 中危"、文件名改 snake_case、§8 asyncio 条款豁免）。手段：对三个 `.py` 做 **AST 扫描**（类型注解/裸 `except`/`assert`/`eval`/`time.sleep`/裸线程/`import *`/可变默认参数/`%` 与 `.format`/模块级全局/函数体内注释）+ 人工看导入块与 docstring。**审查结论（合规面）**：类型注解 **100% 覆盖**（含嵌套函数）、SQL **全参数化**无拼接、无 camelCase、无 `eval`/`exec`/`assert`/`import *`/裸 `threading.Thread`/`shell=True`、无未使用导入、`X | None` 风格统一。**整改八项**：① **🔴 空 `except`**（`bao_stock.py` `_get_previous_trading_date`）原 `except Exception: pass` 改成 `except sqlite3.OperationalError as error: logger.debug("本地 TradeDates 不可用，降级到 Baostock API：%s", error)`——违反 Harness §6「`except` 块不得为空，必须记录日志」与 style §10「只捕获预期类型」，原写法会把 `NameError` 这类编程错误一并吞掉并伪装成"表不存在"。② **数据库连接生命周期**（style §6「不得手动 `close()`」）：原 `with sqlite3.connect(...)` 只提交事务、**不关连接**（Python 的知名坑），改为 `contextlib.closing(sqlite3.connect(DB_PATH))`（`bao_stock.py` 三处：`_run_update` / `update_all` / `__main__` 的 `--sync-securities`）；`bao_stock_parquet.py` 的 `connection = connect_database()` + `finally: connection.close()` 改为 `with contextlib.closing(connect_database()) as connection:`。③ **`logging.basicConfig` 双重配置失效**：`bao_stock.py` 原在**模块级**执行 `basicConfig`（导入即副作用），使 `bao_stock_parquet.py` 的 `main()` 里那次调用成为空操作、其声明的日志格式从未生效；改为各脚本入口配置（`bao_stock.py` 移进 `if __name__ == "__main__":`，抽出 `LOG_FORMAT` 常量）。④ **`time.sleep`**（style §7 明禁）：`login_with_retry` 退避改 `threading.Event().wait(delay)`，随之删掉不再使用的 `import time`。⑤ **文件名改 snake_case**（style §1）：`BaoStock.py`→`bao_stock.py`、`BaoStockParquet.py`→`bao_stock_parquet.py`，**配置同步**为 `bao_stock.json` / `bao_stock_parquet.json`（沿用上一条"配置按脚本名命名"的既定原则）；`from BaoStock import …`→`from bao_stock import …`；两份文档与本文档引用一并更新（**本文档此前各条目里的旧文件名已整体替换为新名，日期与结论未动**）。⑥ **删 `probe_volume_mismatch.py`**（一次性探针，文件自称"用完可删"；含模块级 `sqlite3.connect`、缺模块 docstring、无 `__main__` 保护）。⑦ **补 `from __future__ import annotations`**（`bao_stock_parquet.py`）+ **导入分组修正**：`sqlite3` 由第三方组归回标准库（`bao_stock.py`）、`contextlib` 按字母序插入。⑧ **清理叙述性函数体内注释 23 行**（style §10「禁止在函数体内写注释解释逻辑」）——删的是 `# 1/7 证券同步`、`# 1. 获取全市场证券列表`、`# 名称变更`、`# 写入行业分类表` 这类步骤编号/逻辑复述；**保留**属 Harness §4 允许的"外部库的坑 / 性能"类（`只保留目标列，防御 Baostock 新增意外字段`、`先全部加载到内存，避免 5000+ 次单独查询`、`接口按公告日期过滤…否则重跑触发主键冲突`、`PerformanceExpress 旧结构一次性迁移`，以及 Parquet 侧的正则易错点与"bar 结束时间"口径注）。**豁免记录**：style §8「所有 I/O 优先 asyncio」与项目性质冲突（baostock 与 sqlite3 均为同步阻塞库、无并发需求），**用户裁定豁免**；§7 的 `time.sleep` 禁令仍适用且已整改。**验证**：`py_compile` 通过；`bao_stock.py --help`、`bao_stock_parquet.py --help`、`update --help` 均退出码 0；残留 grep（`time.sleep` / `except Exception` / `.close()` / 旧文件名）**全零**；全部文件字节级核对为 **LF**（Edit 工具会整文件转 CRLF，故每轮改完必查）。**未改（style 🟡 低档，见 ❓）**：import 分组已修但 `logger` 模块级全局未加 `g_` 前缀（属通用惯例，另判豁免）；docstring 的用途描述/覆盖率、行宽标准、§9 测试缺失——本轮未动。

## D.05 pytest 套件＋季度窗口缺陷＋docstring

- 原日期：2026-10-10
- 提交：`0815d45`/`3254128`/`3595c30`

原文：

- 2026-10-10：**补 pytest 测试套件 + 修测试抓出的季度窗口缺陷 + docstring 全量转 Google 风格**（用户裁定：🟡 四项里做 ④ 建测试套件、①② 全套改 Google 风格，逐项分开提交；③ 行宽不做）。**提交**：`0815d45` 测试套件、`3254128` 缺陷修复、`3595c30` docstring。**① 测试套件**：新增 `conftest.py`（把仓根插入 `sys.path`——仓内无 `pyproject.toml`/`pytest.ini`，否则 `tests/` 下 import 不到被测脚本）与 `tests/` 两份用例共 36 个，只覆盖纯函数、不碰数据库/网络/真实文件系统；`python -m pytest -q` → 36 passed。**新增第三方依赖仅 pytest**（开发期，环境已装 9.1.1），**尚未写入任何依赖清单**，待用户确认后再补（Harness §2）。**② 测试抓出的真实缺陷**：`_quarter_window_start()` 返回的不是"某季度首日"而是季度第二个月——`(today.month - 1) // 3 * 3 + 1` 末尾的 `+1` 把 **0 基**月份索引当成了 1 基月份数。2026-10-10 实测 `quarters_back=4` 得 `2025-11-01`，应为 `2025-10-01`；与其自身 docstring「最早那季的首日」矛盾，且与兄弟函数 `_default_year_quarters`（用 `today.year * 12 + today.month - 1`）的 0 基写法不一致，判定为**笔误而非有意**。**影响**：`update_performance_express` 与 `update_forecast_reports` 两处 `start_date = start_date or _quarter_window_start()`，窗口起点各后移一个月 → 最早一季少取/少刷一个月。去掉 `+1`、变量改名 `quarter_start_month_index` 后修复；**方向是"多取/多刷一个月"（补漏）而非缩小范围**，调用方显式传 `start_date` 时行为不变。**③ docstring**：`python-style.md` §10 允许函数级 docstring 用 Google 风格说明参数与返回值，此前全项目只有 `_get_previous_trading_date` 一处照此写，其余 56 处是单句或散文段、另 10 个函数无 docstring（`bao_stock.py` 的 `login_with_retry`/`get_last_date`/`_update_reports`/`_update_macro`，`bao_stock_parquet.py` 的 `connect_database`/`get_last_month`/`parse_arguments`/`main`/`convert`/`add_common_arguments`）。本次统一为"摘要行 + 按需 `Args`/`Returns`/`Raises`/`Note`"；写 `Args` **不复述参数名**，只补名字给不出的信息（取值域、格式、默认值语义、单位），`Raises` 只列函数体真会抛的异常。**验证**：`py_compile` 通过、两脚本 `--help` 与 `update --help` 均 exit 0、36 测试全绿；docstring 那批用**剥掉 docstring 后比对 AST** 证明两份文件**代码零改动**；全部文件**字节级**核对为 LF（Edit 工具会整文件转 CRLF，故每轮改完必查）。

## D.04 共用配置拆为两份（结论当日被推翻）

- 原日期：2026-10-10
- 提交：—

原文：

- 2026-10-10：**共用配置拆为两份按脚本命名的独立文件**（用户裁定："没有一处共享定义？那似乎没有共用的比较了，还容易导致错误。拆分为两个独立的配置吧，使用各自脚本名称独自命名更明确"）。**查证前提（当日即被用户推翻，见下方 ✅ 合并条）**：用户先问"两个脚本共用一份配置文件吗"，当时判为**"两者之间没有任何共享定义"**——此判断只比对了"键集是否相同"，漏看**同名键的取值**：两份文件的 `stocks` 与 `default_start_date` 是**逐字相同的重复**，仅 `parquet_output_root` 为管线独有。既有重复就不是"无共享"，故本条**拆成两份的结论已于同日被用户裁定推翻**，改动内容按原样保留作历史记录。**改动**：`git rm config.json`（可 `git show HEAD:config.json` 取回）；新增 `bao_stock.json`（`stocks`＋`default_start_date`）与 `bao_stock_parquet.json`（＋`parquet_output_root`）；两脚本 `CONFIG_FILE` 各自改指本名文件，`bao_stock_parquet.py` 的 `--config` help 改为引用 `CONFIG_FILE` 常量；两份文档的标题/键表/示例/引用同步；管线文档 §2.3 新增 ⚠️——两份 `stocks` **各存一份、互不引用**，本管线导出是"以交易所为整文件重写"，漏加一份就会让那只票的 bar 悄悄不出现在文件里，故**改清单须两份一起改**。**收尾发现并修的一处不一致**（拆分后复测才暴露）：`bao_stock_parquet.py` 的 `main()` 仍调它自己那份 `load_config()`（裸 `open`），缺默认配置时抛 `FileNotFoundError` 裸回溯，与 `bao_stock.py` 的干净 `配置文件不存在: …` 不一致——改为共用 `bao_stock.py` 的 `load_config_file()`，并删掉本文件里重复的 `load_config` 与随之空置的 `import json`（DRY，同一逻辑不再两处）。**验证（临时目录内四路径实测）**：A `bao_stock.py` 缺默认配置 → `配置文件不存在: bao_stock.json`、exit 1；B `bao_stock_parquet.py` 缺默认配置 → `配置文件不存在: bao_stock_parquet.json`、exit 1（**修复前**此处是 `FileNotFoundError` 回溯）；C 缺 `parquet_output_root` → `配置缺少必填项 parquet_output_root（bao_stock_parquet.json），或用 --output-root 传入`、exit 1；D `--config` 指向不存在文件 → 信息**带该实际路径**、exit 1。临时目录（含 import 生成的 `__pycache__`）已逐层 `rmdir` 删净；两文件行尾做**字节级**核对，均 LF 且与 `HEAD` 一致（Edit 工具会整文件转 CRLF，故每轮改完必查）。**遗留（未改，同前）**：`DB_PATH = "stock_data.db"` 仍硬编码；`default_start_date` 两脚本兜底值不一致（`bao_stock.py` `2000-01-01` vs `bao_stock_parquet.py` `2015-01-01`）。

## D.03 本机依赖环境重建（修上条镜像源阻塞）

- 原日期：2026-10-10
- 提交：—

原文：

- 2026-10-10：**本机依赖环境重建**（修掉上面那条 ⚠️ 的阻塞）。用户裁定**装回全局 Python 3.14.7**、不建 `.venv`。**镜像源改写到 pip 真正会读的位置**：`python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple` 与 `global.timeout 60` → 落 `C:\Users\15031\AppData\Roaming\pip\pip.ini`（80 B），`pip config list` 当场读回两行，证明生效。**装的是原基线版本、不是最新版**：`baostock==0.9.3`、`pandas==3.0.5`、`numpy==2.5.3`、`pyarrow==25.0.1`（先 `pip index versions` 确认四者在该源上均有对应版本再装），连带 `python-dateutil 2.9.0.post0`、`six 1.17.0`、`tzdata 2026.5`。**四个包在 cp314 上都有 win_amd64 wheel**（pandas 10.0 MB / numpy 12.7 MB / pyarrow 28.6 MB 均为预编译 wheel），**未退化成源码编译**——这消掉了 ❓ 里"重装前建议确认有 cp314 wheel"那条担忧。**验证**：`bao_stock_parquet.py --help` 与 `bao_stock.py --help` 均退出码 0。**过程瑕疵一条**：`pip index versions` 期间对 `simple/pip/` 那次请求报 `SSL: UNEXPECTED_EOF_WHILE_READING`，但同一轮后续请求全部成功、安装全程无重试——判为镜像侧瞬断，未处理。**注**：本次未做 baostock 登录端到端（库里已有数据、`--update-all` 仍属 ❓），故"端到端可用"这条**仍未证实**。

## D.02 行情根随 MdBaoStock 迁仓改指

- 原日期：2026-10-10
- 提交：—

原文：

- 2026-10-10：行情根随 `MdBaoStock` 迁仓而改指。行情实体已从 `D:\MdBaoStock` 迁到 `D:\Gitee\QuantTrading\MdBaoStock`（QuantTrading 侧 `Configs/BackTest.json` 的 `MdDataPath` = `../../MdBaoStock`，相对 CWD `bin/<Config>` 即该仓根）。**根因是配置键从未写进 `config.json`**：`bao_stock_parquet.py:719` 读的是 `config.get("parquet_output_root", DEFAULT_OUTPUT_ROOT)`，键缺失时永远落到常量兜底，故表现为"硬编码"。改动四条：① `config.json` 补 `parquet_output_root`；② `bao_stock_parquet.py:57` 常量与 `:692` help 文案改新值（help 改引用常量，消掉同一字面量两处各写一份的漂移）——**此条已被下方 2026-10-10 收尾条取代：该常量随后按用户裁定直接删除，改为配置必填**；③ 文档 `docs/baostock-parquet-pipeline.md` 的缺省值列、config 示例、`BackTest.json` 示例（改回相对值 `../../MdBaoStock`、路径由 `bin/Debug` 改为 `Configs/BackTest.json`）同步，并点明取值按 **CWD** 而非配置文件解析。取相对值 `../QuantTrading/MdBaoStock` 而非绝对值：与隔壁仓「仓根相对路径、取值与副本路径无关」口径一致；QuoteHub 本就强制在仓根运行（`stock_data.db` 为相对路径），不额外增加脆弱性。**验证**：`os.path.abspath` 解析为 `D:\Gitee\QuantTrading\MdBaoStock`、目录存在、`Bar/` 下 `Identity=SSE.Stock` / `Identity=SZSE.Stock` 两身份目录俱全。**`--help` 复跑通过**（环境重建后，见下条）：`bao_stock_parquet.py --help` 与 `bao_stock.py --help` 均退出码 0，且 `update --help` 显示的缺省值已是 `../QuantTrading/MdBaoStock`（证明 help 文案确实引用常量而非另写一份）。**跨仓旁记（未处理）**：`QuantTrading/bin/Debug/BackTest.json` 仍留旧值 `D:\MdBaoStock`（同目录 Release 那份已是 `../../MdBaoStock`），属构建产物拷贝，下次跑原生 Debug 构建会读旧路径。
  **代码审查（`code-reviewer`，0 严重 / 0 高 / 2 中 / 4 可选）后补两处改动**：① **中——文档指向错了运行时真正读的文件**（我引入）。实测 QuantTrading `CMakeLists.txt:219` 是 `copy_config_file(TestBackTest Configs/BackTest.json)`，即 `Configs/` 是**源头**、运行进程读的是**构建时拷进 `bin/<Config>/` 的那份**；我原先把文档从 `bin/Debug` 改成 `Configs/` 却不提拷贝这步，照着改会"改完不生效"。已在文档补 ⚠️ 段，写明须重新构建或改运行目录副本，并用 `bin/Debug` 至今停在旧值作为实证。② **中——绝对路径改相对后引入"错 CWD 静默写错目录"隐患**（旧值 `D:/MdBaoStock` 与 CWD 无关，新值依赖 CWD，而 `bar_file_path` 只做 `os.path.join`、`write_bar_file` 用 `os.makedirs(exist_ok=True)`，不报错也不告警）。加 `resolve_output_root()`：归一化为绝对路径并 `logger.info` 打出生效值——**未**选"目录不存在就退出"那种写法，因为 `backfill` 建全新导出根本是正常用法，硬失败会误伤。**验证**：三条取值路径（config 值 / 常量兜底 / `--output-root` 覆盖）分别解析为 `D:\Gitee\QuantTrading\MdBaoStock`、`D:\Gitee\QuantTrading\MdBaoStock`、`D:\tmp\foo`；从 `D:/` 运行时日志首行打出 `D:\QuantTrading\MdBaoStock`，证明错值当场可见而非静默。三个 `--help` 均退出码 0。另顺手补 `config.json` 缺的末尾换行（违反 `.editorconfig` 的 `insert_final_newline`，属**既有**问题非本次回归）。**未采纳的可选项**：常量更名（改为在 docstring 里讲清语义）、argparse help 的 `%` 转义脆弱性（当前取值无 `%`）、markdown 超长行（该文件既有 20+ 行已超）。
  **2026-10-10 用户裁定的收尾：配置键缺失一律报错，代码里不留兜底常量。** 用户指示"使用配置化方案，不要代码里面兜底常量"、"缺失必要的配置直接报错即可"。**改动**：① **删掉 `DEFAULT_OUTPUT_ROOT` 常量**（原先它持有与新配置值相同的字面量，两份并存本身就是漂移隐患）；② 新增 `require_config_value(config, config_key, command_line_alternative, config_file)`——取不到即 `raise SystemExit`，错误信息**指名实际用的那份配置文件**（传 `--config` 时不会误报成 `config.json`）；③ `resolve_output_root()` 改为只用"命令行 or 必填配置"，再归一化打日志；④ **`stocks` 一并收进同一守卫**——原先 `config["stocks"]` 缺键抛裸 `KeyError` 回溯，同属"必要配置要给明确错误"；⑤ 模块 docstring 的注意项与文档 2.3 表格（缺省列由 `../QuantTrading/MdBaoStock` 改为 **无（必填）**）同步。**保留**：`default_start_date` 按文档仍属可选项、保留代码兜底（文档明写"代码兜底 `2015-01-01`"）——**此项已于 2026-10-10 撤销，见下方 ✅「三键一律必填」条**；`--output-root` / `--codes` 命令行覆盖照旧可用。**验证（三条错误/正常路径实测）**：缺 `parquet_output_root` → `配置缺少必填项 parquet_output_root（<实际配置文件路径>），或用 --output-root 传入`、exit 1、无回溯；缺 `stocks` → 同类信息、exit 1；`--output-root` + `--codes` 同时给出时仍可绕过必填、正常走到 `Parquet 输出根: D:\tmp\probe`。**测试残留已清除**（探针那次 `merge-month` 在 `D:/tmp/probe` 下写出了一棵真 Parquet 树，已逐层 `rmdir` 移除；**真实树未被碰动**——已核对 `MdBaoStock/Bar/Identity=SSE.Stock/Year=2026/` 仍只有原来的 `2026_5m.parquet`）。**⚠️ 上述审查段里"常量更名"那条已被本条取代**：常量本身已删，无需更名。
  **2026-10-10 续（用户点头，同一规矩扩到 `bao_stock.py`）**：用户答"改了"。**改动**：① 守卫**上移为单一来源**——`require_config_value` 与新增的 `load_config_file` 定义在 `bao_stock.py`，`bao_stock_parquet.py` 改为 `from BaoStock import ... require_config_value` 并删掉我先前在它里面写的那份重复（避免同一逻辑两处）；参数顺序统一为 `(config, config_key, config_file, command_line_alternative=None)`，`alternative` 可省（`bao_stock.py` 没有 `--codes`/`--output-root` 这类命令行替代）。② **删掉 `_load_config_safe()`**——它在配置文件缺失时返回 `{"stocks": [], "default_start_date": "2000-01-01"}`，是这套静默兜底的**源头**（伪造一份空配置顶上，让"配置没写"与"配置写空"完全一致）；代之以 `load_config_file()`：文件不存在即 `raise SystemExit("配置文件不存在: …")`。③ 6 处 `config.get("stocks", [])` 全部改为 `require_config_value(config, "stocks", CONFIG_FILE)`（`:1153` `update_all_extended`、`update_all` 与 `__main__` 的 `--update-financials`/`--update-dividends`/`--update-reports`；后者原先在 lambda 内**各调两次**，改为一处取值后传给闭包）。④ `update_all()` 里原有的 `logger.error("配置文件…不存在") + return` 与 `if not stock_codes: logger.warning(…); return` 两条"软退出"一并由硬报错取代。**判定依据**：`stocks` 对这四个命令都是目标清单本身（`--update-all` 的 4/7、5/7 步亦然），缺了就是配置错，不是可用的空模式；文档第 25 行本来就把 `--update-all` 写成"七步全量"。**保留**：`default_start_date` 仍可省、代码兜底 `2000-01-01`（文档明写）——**此项已于 2026-10-10 撤销，见下方 ✅「三键一律必填」条**；与 stocks 无关的全市场级命令（`--update-index`/`--update-industry`/`--update-trade-dates`/`--update-macro`）本就不读配置，未受影响。**验证（六条路径实测，均在临时目录内跑、未碰真实 `config.json` 与库）**：① 无配置文件 → `配置文件不存在: config.json`、exit 1；② 缺 `stocks`（`--update-financials`）→ `配置缺少必填项 stocks（config.json）`、exit 1；③ 缺 `stocks`（`--update-reports`）→ 同②；④ `bao_stock_parquet.py --config <其他文件>` 缺 `stocks` → 信息**指名该实际文件**且带"或用 --codes 传入"、exit 1；⑤ 缺 `parquet_output_root` → 同类信息、exit 1；⑥ 正常配置 → `parquet_output_root` 解析为 `D:\Gitee\QuantTrading\MdBaoStock`、`stocks` 取到 `['sh.600519']`。两脚本 `--help` 均退出码 0，`py_compile` 通过；`config.get("stocks", …)`、`_load_config_safe`、`DEFAULT_OUTPUT_ROOT` 三项残留 grep 均为零。文档 `docs/baostock-data-collector.md` §3 补齐"stocks 必填、缺失即报错"与全市场级命令不受影响的说明。**已知遗留（未改）**：`update_all_extended` 里 `if stock_codes:` 的两处守卫（`:1170`、`:1186`）因 stocks 现由函数开头强制非空而**恒为真**，属死分支；留着无害，但清理它要重排 20+ 行的缩进，未顺手动。

## D.01 环境与依赖初建（pip 解析失败 / 依赖补齐 / 清华镜像）

- 原日期：2026-10-10
- 提交：—

原文：

- 修复 `pip install baostock` 报 `ResolutionImpossible` 的问题。
  根因是 pypi.org 连接超时（非依赖冲突），已改用清华镜像安装。
- 全局 Python 3.14.5 补齐依赖：baostock 0.9.3、pandas 3.0.5、numpy 2.5.3、pyarrow 25.0.1。
- 写入 pip 永久镜像配置 `C:\Users\15031\pip\pip.ini`（清华源，timeout 60s）。
  **⚠️ 2026-10-10 更正**：该路径**不是 pip 会读取的位置**——Windows 用户级配置的标准位置是 `%APPDATA%\pip\pip.ini`（即 `C:\Users\15031\AppData\Roaming\pip\pip.ini`），`C:\Users\15031\pip\` 不是任何一级配置目录。故这条当时**很可能实际未生效**，上次能装成功或许靠的是命令行显式 `-i`。正确落点见下方 ✅ 2026-10-10 环境重建条。
- 验证：baostock 端到端登录 + K 线取数成功；`bao_stock.py` 与 `bao_stock_parquet.py` 均可正常加载（`--help` 退出码 0）。

> **⚠️ 2026-10-10 复核：本节事实（上面 4 条）已全部失效。** 本机全局 Python 现为 **3.14.7**（非 3.14.5），其 `site-packages` 里 **baostock / pandas / pyarrow / numpy 四个包一个都没有**（只剩 fastapi、httpx、pytest、SQLAlchemy、pillow 等另一批包），`pip config list` 为空、`C:\Users\15031\pip\` 目录连同其中的清华源 `pip.ini` 也不存在。实测 `python bao_stock_parquet.py --help` 立即 `ModuleNotFoundError: No module named 'baostock'`。推断该 Python 安装被重装/替换过（site-packages 是另一套），但**属推断，未取证**。上面四条不删，作为"当时曾成立"的记录保留。→ **同日已恢复，见下方 ✅ 2026-10-10 环境重建条。**

## Q.07 DB_PATH 仍是仓根硬编码相对路径

- 原日期：2026-10-10
- 结论：2026-10-13 改为配置键 `database_path`，导出符号 `DB_PATH` 一并删除——见 D.15。

原文：

- `DB_PATH = "stock_data.db"` 仍是仓根硬编码相对路径，没改成配置项（与契约 §1「必须在仓根运行」自洽，故当初判为未动而非缺陷）。见归档 D.09。

## Q.04 Python 运行时是否锁定小版本

- 原日期：2026-10-10
- 结论：2026-10-13 用户裁定——**基线只定大版本 3.14，小版本不指定**；README「安装」段据此改写。

原文：

- 是否需要把 Python 运行时也锁定为其他 LTS 版本（原基线 3.14.5，本机现为 3.14.7，且 3.14.7 已被本次重建沿用）。
