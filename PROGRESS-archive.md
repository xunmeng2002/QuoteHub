# PROGRESS 归档 · QuoteHub

本文件存放 `PROGRESS.md` 已关闭条目的**原文**（含当时的判据与理由）；`D` 系列在前、`Q` 系列在后，各自 ID 倒序。检索入口见 [PROGRESS-index.md](PROGRESS-index.md)。

ID 规则：`D`＝原 ✅ ／ `Q`＝原 ❓ ／ `R`＝原 🔄，编号取自 **2026-10-10 首次拆分当日**的文档顺序，此后永久稳定；新增条目在**首次归档时**按当时顺序续编。

## D.15 版本号回到单一 v1.0.0 ＋ DB 路径由硬编码改为配置键 database_path

- 原日期：2026-10-13
- 提交：`1476759`

原文：

- **D.15** 2026-10-13：**版本号回到单一 `v1.0.0` ＋ DB 路径由硬编码改为配置键 `database_path`**（用户裁定："版本号定为v1.0.0，在正式发布前的修改都不要增加版本号。DB_PATH 可以考虑放到配置文件里面去。"）。**① 版本号回退**：`version.txt` 由 `v1.0.1` 改回 `v1.0.0`，`CHANGELOG.md` 的 `v1.0.0`／`v1.0.1` 两节**并为一节 `## v1.0.0（未发布）`**——两节都还没打 tag、没发 Release（`git tag -l` 为空），发布前的版本切分对外毫无含义，只会让 CHANGELOG 与实际发布物对不上；CHANGELOG 头部改写为"发布前一律累积进当前未发布版本，到发布时才落定版本号并打 tag"（本条同时是**版本号口径**：**正式发布前不新起版本号**）。**② `DB_PATH` → `database_path`**（结掉 Q.07；该符号是导出物，用户已明确授权删除）：`config.json` 增第四键 `"database_path": "stock_data.db"`，**不挪库文件位置**——挪了消费方的只读开库点要同时改，集成有断档风险；相对值与 `parquet_output_root` 同口径、按 CWD 解析。`bao_stock.py` 删掉 `DB_PATH = "stock_data.db"`，改在 `_run_update` 内 `require_config_value(config, "database_path", CONFIG_FILE)`，**域函数签名一律没动**（故只给 `_run_update` 加了一个 `config` 参数，8 个调用点各自传入）。`bao_stock_parquet.py` 的 `connect_database(config, config_file)` 穿 `config_file` 而**不是**模块常量 `CONFIG_FILE`——`main()` 支持 `--config <路径>`，用常量拼错误提示会指向一个从没读过的文件（先例 `resolve_output_root`）。**③ 行为变化（须记 CHANGELOG）**：原本不读配置的 5 个命令（`--sync-securities`／`--update-index`／`--update-industry`／`--update-trade-dates`／`--update-macro`）此后也要读 `config.json`——库路径本就是每个连库命令都要的参数，"不读配置"只因当初路径写死了。**对消费方零可见**：契约 §3.1 写明消费方只以子进程调 `bao_stock_parquet.py`、不 import 本仓代码，故只影响直接用 `bao_stock.py` 的人；这句已写进 CHANGELOG，免得下次跨仓对表被误判成契约变更。另记：未知选项（如 `--bogus`）此前直接打用法、退出码 0，此后会先尝试读配置、仅在配置缺失或损坏时改判退出码 1；`--version` 与 `--help`／无参数不受影响。**④ DRY（§5）**：`__main__` 里 **8 个分支各写一遍**的 `config = load_config_file(CONFIG_FILE)` 收敛为"版本／用法守卫之后的单次加载"，结尾那段内联用法文案提为模块级私有函数 `_log_usage()`；不新增 `sys.exit(0)` 惯例，也不引入 options 清单常量（那会让选项名出现第二份真相来源）。**⑤ 两处口径裁定**：Q.04 结掉——Python 基线**只定大版本 3.14、小版本不指定**，README「安装」段据此由 `3.14.x` 改写；Q.05 删除——六份语言规范的细核属规则仓（`../AI`）事务，不记本仓，**编号不复用**（索引备注已注明）。**验证**：`python -m pytest -q` → **61 passed**（新增 3 例：`_run_update` 缺 `database_path` 时须在**登录之前**报错、`connect_database` 在配置指定路径建库建表、错误提示须指着 `--config` 传进来的那个路径）。临时仓根 `%TEMP%\quotehub-check-CRrR` 内实测四条路径：无 `config.json` 时 `--version` 仍打印 `v1.0.0`、退出码 0；缺文件 → `配置文件不存在: config.json`、退出码 1；有文件缺键 → `配置缺少必填项 database_path（config.json）`、退出码 1；`database_path` 指向临时库时 `--update-trade-dates` 把 **13083 行**写进临时 `check.db`，而仓根真库 `stock_data.db` 的 mtime 未变。两脚本 `--help` 退出码 0、`--version` 均 `v1.0.0`；改动文件逐字节核对 CRLF=0、行长 ≤150。**代码审查（`code-reviewer`）**：**0 严重 / 0 高 / 1 中 / 3 低 / 4 可选**。中一项是**文档口径与代码不符**——重构后未知选项（如 `--bogus`）会先经配置加载，配置缺失时以退出码 1 结束，而 CHANGELOG／README／采集器文档当时写作"只有 `--version`／`--help`／无参数不读配置"，读者会以为未知选项走的是同一条路。**核实后按"改文档、不改代码"处理**：该行为在计划里已声明为刻意变化；要按审查建议做短路，就得再列一份选项名常量，"选项名出现第二份真相来源"正是本次重构要避免的（§5 拒绝过度封装），故只把三处措辞改准（并补"`--help` 现在优先于其它选项，`--update --help` 只打用法"一句）。低三项亦全改：CHANGELOG 里"`--update`／`--sync-securities`／`--update-all` 三处入口共用 `_run_update`"是合并前的旧口径（现已为**全部命令**共用，已改述）、示例 `from BaoStock import DB_PATH` 的模块名大小写已改 `bao_stock`、README 与采集器文档的口径同中一项一并改准。**可选项未采纳**：`stocks` 三处重复取用（仅 3/10 分支需要，提前统一会让其余命令多一次无谓校验并改变报错顺序）、两脚本"取 `database_path` → 连库 → `init_database`"序列的跨文件合并（一侧是 `closing` 上下文、一侧返回连接，控制流确有差别）、`__main__` 无用例触及（入口难直接测，本次也未改其行为契约）、源文件里既有的 `from __future__ import annotations`（本批未增删，属既存偏差）。**提交**：`1476759`（本批一并）。

## D.14 回补当年改月度文件 ＋ 登录失败退出码 ＋ 先查后删 ＋ DRY 收敛 ＋ 口径差文档纠正

- 原日期：2026-10-10
- 提交：`08b6133`

原文：

- **D.14** 2026-10-10：**回补当年改月度文件 ＋ 登录失败退出码 ＋ 先查后删 ＋ DRY 收敛 ＋ 口径差文档纠正**（用户裁定"四项全修"、PROGRESS 归档"现在就拆分"）。**① 当年年度文件重叠**：`backfill` 区间含**尚未结束的当年**时会写 `2026_5m.parquet`（一年一文件、覆盖整年），而之后 `update` 的日度文件、`merge-month` 的月度文件都会与它在同一天重叠；消费方回放 SQL（`QuantTrading/src/BackTest/MdReader.cpp`）按 `Year=*/*.parquet` 扫描且**不去重**（只 `ORDER BY UpdateTs`），同一天会被回放两次。改法：当年改走 `run_merge_month` 按月写出（`YYYYMM_5m.parquet`），当年不再写年度文件（留给次年 `merge-year`）；`merge-month` 的归档模式只认 8 位日度文件，动不了 4 位的年度文件，故重叠真会发生。**已落地的数据修整**：先逐月 `merge-month --month 202601…202610` 把当年物化为月度文件，再删掉那两份临时年度文件——删前已核对**键集完全等价**（`(InstrumentID, UpdateTs)` 无年度独有、无月度独有、无重复），删后逐 `Year=` 目录复核"行数 == 去重键数"。消费方按 `Year=*/*.parquet` 读到的行集不变，仅当年文件粒度由年改为月。**② 登录失败退出码**：`bao_stock.py` 的 `login_with_retry` 失败原本"记 error 后 return"，三处入口（`--update`/`--sync-securities`/`--update-all` 共用的 `_run_update`）以**退出码 0** 收场，与 v1.0.0 宣称的"按退出码判成败的消费方从此能真正看到失败"自相矛盾；改为 `raise SystemExit("BaoStock 登录失败，操作取消")`——退出码 1、stderr 仍含 `登录失败` 子串，消费方的失败分类依据不变。**③ 先删后查 → 先查后删**：`update_trade_dates` 与 `update_index_constituents` 原先先 `DELETE` 再查上游，上游 `err=0` 但返回 0 行时提前 return、`_run_update` 随后提交——等于把交易日历／当日成分股快照**清空却报成功**；改为查成功且有行才 `DELETE`，0 行只记 warning 并保留库内现有数据。**④ DRY**：三份几乎相同的「登录 → 中止转换 → 注销并回滚」脚手架收敛到 `_run_update(operation)` 一处，`update_all` 随之改为接收 `connection`。**⑤ 口径差纠正**：文档与代码注释里"约 0.4% 的固有口径差"实测不成立——分钟量合计与日线量在 2020–2022 与 2026 **逐日精确相等**（0.000%），仅 2023–2025 有部分交易日偏离 1%~7%（金额同步偏离），已按实测改写 `docs/baostock-parquet-pipeline.md` §6/§7、`docs/baostock-component-contract.md` §3.2 与代码注释。**验证**：新增回归用例（登录失败退出码非 0、中止路径注销且回滚未提交行、上游 0 行不得清空日历／成分股、回补当年写月度文件），`version.txt` 升 `v1.0.1`，`CHANGELOG.md` 增 v1.0.1 段。

- **D.14 续（同一批未提交改动）2026-10-10：code-reviewer 审出四条，逐条核实后全部落地。****① 测试净损失（我自己造的，最重）**：上一轮把 `tests/test_bao_stock_parquet.py` **整份覆盖**，原文件 11 个用例（`split_stock_code` / `group_codes_by_exchange` / `to_sqlite_value` / `merge_day_spans` / `_spans_are_contiguous` / `bar_file_path` / `get_last_month` / `detect_cumulative_mode`）只剩 1 个，`pytest` 从 51 掉到 32 我却当成"数字记错了"——**记错的不是旧记录，是我漏看了掉幅**。已 `git show HEAD:tests/test_bao_stock_parquet.py` 取回全部原用例并与新用例合并，现 **58 passed**。**教训**：整份覆盖一个已有文件等于静默删除，改测试文件前先 `git diff --stat` 看掉幅。**② 「先查后删」只改了一半**：同类路径还有 8 处——财务单键表 4 处（`_fetch_and_store_financial` / `DividendData` / `PerformanceExpress` / `ForecastReports`，0 行时 `continue`/`return 0`）与四张宏观表（`DepositRates` / `LoanRates` / `ReserveRatios` / `MoneySupply`，0 行时整表已被 `DELETE`），全部按同一模式把 `DELETE` 移到"确认有行"之后、0 行记 warning 并保留库内数据；新增 2 个回归用例（财务单键 `BalanceData`、宏观 `DepositRates`）。**③ `run_backfill` 三处自造问题**：两条近乎相同的 `MinuteBars` 查询收敛为助手 `_query_backfilled_months`（DRY）；补 `if not stock_codes: return`（空清单会拼出 `Code IN ()` 非法 SQL）；注释「偏离 1%~7%」与文档「单日最大 7.3%」对齐。**④ 未采纳一条**：审查建议把登录失败也改成 `SystemExit(1)` ＋ `logger.error` 以求载荷类型一致——那会动到消费方正匹配的 stderr 子串（见 Q.02），不擅自改。**另记一条新未决**（Q.09）：`backfill` 写已结束年度的年度文件时不归档该年既有月度文件，本命令刻意不动既有文件，该情形应用 `merge-year`，已在两处文档与 docstring 写明。`python -m pytest -q` → **58 passed**。**提交**：`08b6133`（本条与续条一并）。

## D.13 --update-all 的假成功与行业分类主键崩溃

- 原日期：2026-10-10
- 提交：`4de4779`

原文：

- **D.13** 2026-10-10：**`--update-all` 的假成功与行业分类主键崩溃一并修复**（用户裁定「严格收集 + 失效即中止」）。**两个独立缺陷**：
  ① 16:43 那次全量更新 2/7 之后每步都打印「用户未登录」，却仍以 `===== 全量更新完成 =====` 收场、退出码 0——根因是 `_collect_rows` 的 while 条件是
  `error_code == "0"`，分页中途失败只跳出循环、把已取到的行交出去（实测行业分类只拿到 2000 行当 5558 行的全量），调用方「查询失败就 return」的前置判断永远看不到这个错误。
  ② 17:11 复跑抛 `UNIQUE constraint failed: IndustryClassification.Code, IndustryClassification.UpdateDate`——根因是接口字段 `updateDate` 与建表列 `UpdateDate`
  在 SQLite 里是**同一列**（列名比较不区分大小写），且接口那个排在前、值覆盖我方的值（存进去的是 `2026-10-05`，DELETE 键却是当天），于是 DELETE 永不命中、重跑必撞主键；
  `update_index_constituents` 同型（探针实测其字段同构）。
  **改动**：① 删掉宽松的 `_collect_rows`，改为严格 `collect_rows_or_raise(result_set, context_label)` 与异常层级
  `BaostockUpdateAbortedError(RuntimeError)` / `BaostockSessionLostError`（10001001 会话失效）/
  `BaostockPaginationInterruptedError`，`None` 结果集同样中止；16 处调用点全部切换，连带删掉失效的「`error_code != 0` 就 return/continue」与两处「`result is None`」判断；
  `bao_stock_parquet.py` 删掉自己那份同名函数、改为从 `bao_stock` 导入（其文档契约声明抛 RuntimeError，新基类即是）。② 新增 `_restrict_to_table_columns` 按建表列白名单裁帧，`update_industry` 与
  `update_index_constituents` 各用一次（白名单与 `Update_v2.0.0.sql` 列定义一致）。③ 中止语义与提交粒度：`_run_update` / `--sync-securities` / `update_all()` 捕获中止异常 →
  `_exit_on_update_aborted` 记两行日志并 `SystemExit(1)`；`update_all_extended` 改为每批、每只股票提交；`update_all()` 补上原先**完全缺失**的 `connection.commit()`——
  `sqlite3` 关闭不提交即整轮回滚，`--update` 此前「下载完一行不落库」（实测复现：insert 后 close，重开 0 行）。
  **验证**：`python -m pytest -q` → **51 passed**（新增 11 例：分页替身、两个异常分支、`None` 结果集、RuntimeError 契约、白名单裁列、退出码 1、
  行业分类与指数成分各跑两遍的落库回归例、`_get_previous_trading_date` 中止例、`--update` 落库回归例）。
  **真接口各跑两遍**（临时库，未碰 `stock_data.db`）：
  行业分类两遍均成功、共 **5558** 行、`UpdateDate` 全为 `2026-10-10`、重复 Code 0；指数成分股两遍均成功、`hs300 300 + sz50 50 + zz500 500`、`UpdateDate` 全为查询基准日 `2026-10-09`。
  **离线时序实测**（探针以桩代三步）：第 3 步抛会话失效 → 日志两行、退出码 1、**第 1/2 步落库保留**、第 3 步未提交部分 0 行。
  **行为变化（须知）**：单只代码级查询失败此前「记 warning 后跳过」，现在一律中止整轮——探针实测「无数据」回落为 `err=0` + 0 行（非股票代码亦然），不会误杀。
  **code-reviewer 两轮复审**（同日）：一轮改 4 处——`_fetch_and_store_financial` docstring 仍写「失败返回 0」、`bao_stock_parquet.py` 的两处
  `error_code != "0"` 前置判断抢在异常分类之前抛错（会话失效退化成 `AttributeError`）、`update_all_extended` 的 Note 高估提交粒度、`SystemExit(1)` 缺异常链；
  二轮改文档口径 1 处（把「第 4 步日 K」误写成「第 4、7 步」），并把测试改走 `closing` 与落库 fixture，去掉手写 `close()`。两轮均判无高风险项。
  **未做**：`--update-all` 全量重跑（数十分钟起）；用户库中仍有 2000 行 `2026-10-05` 的陈旧行业分类，重跑前须先清（见备注）。

## D.12 网络层加接收超时，修 --update-all 挂死与 Ctrl+C 无效

- 原日期：2026-10-10
- 提交：`96fc0c2`/`6a67a09`

原文：

- **D.12** 2026-10-10：**网络层加接收超时，修掉 `--update-all` 永久挂死与 Ctrl+C 无效**（用户裁定「加吧」）。**现象**：`python bao_stock.py --update-all` 停在「1/7 证券同步」后的「证券同步基准日期: 2026-10-09」，30+ 分钟再无输出。**取证**：`Get-Process python` 显示 CPU 仅 0.5 秒、工作集 2.8 MB，且 3 秒内两次采样完全一致——空转在 I/O 上，不是死循环也不是在算。**根因（第三方库）**：`baostock/util/socketutil.py` 的 `send_msg` 建 socket 时**不设超时**，收包循环 `while True: recv(8192)` 又只在攒到结尾分隔符时才退出；上游 `public-api.baostock.com`（解析到 114.94.20.42:10030）的大响应当时严重降速——裸字节探针实测 `query_all_stock` 整包约 130 KB，30 秒只吐 40 KB（约 1.3 KB/s）后停住。小响应则完全正常：`login` 0.2 s、`query_trade_dates` 0.1 s、`query_history_k_data_plus` 0.5 s。**Ctrl+C 也退不出的原因**：中断确实抛出了，但落在 `finally: bs.logout()` 里，而 `logout()` 内部同样走 `sock.send_msg`、在**同一个** socket 上再收一次包——于是又掉回无超时的收包循环，表现为「按了没反应」。**改动六处**：① 新增常量 `SOCKET_TIMEOUT_SECONDS = 60`；② 新增 `enable_socket_timeout()`——`socket.setdefaulttimeout(...)`，**必须早于首次登录**（baostock 的 socket 在 `login` 时创建，晚了不生效）；③ 新增 `logout_quietly()`——把 `bs.logout()` 包进 `try/except Exception` 只记 warning，保证清理阶段不盖掉真正的失败原因，且 `KeyboardInterrupt` 不属 `Exception`，故第二次 Ctrl+C 能立即中断注销；④ 三处 `bs.logout()`（`_run_update` / `update_all` / `--sync-securities` 分支）与管线 `main()` 那处一并改 `logout_quietly()`；⑤ 两脚本入口各调一次 `enable_socket_timeout()`；⑥ 顺带补 `bao_stock.py` **末尾缺失的换行**（`.editorconfig` 的 `insert_final_newline`）——属既有问题，`git show HEAD:bao_stock.py` 末尾即为右括号无换行，非本次引入。**阈值为何取 60 秒**：这是**每次 `recv`** 的超时、不是整包总时长，只有服务端**连续停顿**超过阈值才判失败；实测正常分块间隔约 6 秒，60 秒约为其 10 倍，故不取小。**验证**：① `probe4` 确认调用后 baostock 在 login 时建的 socket `gettimeout()` 读回 `60.0`（默认原本是 `None`）；② `probe5` 端到端确认对当前降速接口的调用在 **124.8 秒**后返回 `err=10002007 网络接收错误。`（两个超时周期）而非永久挂死，`sync_securities` 会据此记「获取证券列表失败」并返回；③ `python -m pytest -q` → **40 passed**（新增两用例：默认超时被置为常量、`logout_quietly` 吞掉 `OSError`），`py_compile` 通过，三个改动文件 CRLF=0、行长 ≤150。**遗留风险**：阈值只把「挂死」变成「有界失败」，不解决「上游很慢」，最坏一次失败约 2 分钟；Ctrl+C 若正好落在 `logout_quietly()` 内，仍需再按一次才立即退出。**旁记**：管线 `bao_stock_parquet.py backfill` 只走 `query_history_k_data_plus` 与本地库，不碰这三个降速接口，是当时唯一能出数据的路。

## D.11 版本化与发布物（v1.0.0）

- 原日期：2026-10-10
- 提交：`4fd1993`/`36b8ec0`/`82d06ce`/`6067d0d`/`c6143c6`

原文：

- **D.11** 2026-10-10：**版本化与发布物（v1.0.0）**——`36b8ec0` 把 `default_start_date` 由「必填」收窄为「仅 `update` 必填」，`4fd1993` 引入版本单一来源，本条补出发布物并收尾陈旧模块名。**① 配置按需取**：`bao_stock_parquet.py` 原先在 `main()` 顶部无条件要求 `default_start_date`，而该键只有 `update` 用得上；消费方平台的 `backfill --start/--end` 流程因此被逼着在配置里填一个根本用不到的键。守卫下移到 `if arguments.command == "update":` 分支，`backfill` / `merge-*` 现全程不读它。**判定依据**：「必填」只应约束真正读它的命令，否则等于把内部实现细节写进对外契约。**② 版本单一来源**：新增 `version.txt`（`v1.0.0`）与 `read_version()`（取首个非空行，缺失或为空即 `SystemExit`），口径对齐引擎的 `engine-version.txt`；两脚本各加 `--version`（`bao_stock.py` 走 `sys.argv` 判定，管线走 argparse 的 `action="version"`）。**不**在每个源文件里写版本字面量——改版时漏一个就出现两个版本号。**③ 发布物五件**：`requirements.txt`（四包精确 pin：`baostock==0.9.3` / `numpy==2.5.3` / `pandas==3.0.5` / `pyarrow==25.0.1`）、`requirements-dev.txt`（`-r requirements.txt` ＋ `pytest==9.1.1`）、`README.md`、`docs/baostock-component-contract.md`（一句话契约／自足前提／库结构／CLI／退出码／产物布局）、`CHANGELOG.md`（v1.0.0 破坏性改动清单）。**④ 陈旧模块名收尾**：`bao_stock_parquet.py:29`/`:30` 与 `docs/baostock-parquet-pipeline.md:69` 的 `BaoStock.DB_PATH`、`BaoStock.init_database` 改回 `bao_stock.*`，`sql/upgrades/Update_v2.1.0.sql:1` 的 `BaoStockParquet 管线` 改 `bao_stock_parquet 管线`。**搜法教训**：这三处残留在文档与注释里，按 `BaoStock\.py` 这类**带后缀**的模式搜不到，须按**裸模块名**再搜一遍才现形。**验证**：`python -m pytest -q` → **38 passed**；两脚本 `--version` 均输出 `v1.0.0`；`backfill` 在缺 `default_start_date` 时退出码 0、缺 `stocks` 仍 exit 1（临时目录内实测）；五个新文件与三个改动文件**逐字节**核对 CRLF=0，行长全部 ≤150。**未做**：`v1.0.0` 的 git tag 与 Gitee Release 尚未发布——属对外动作，等用户点头。
  **代码审查（`code-reviewer`：0 严重 / 1 高 / 3 中 / 3 低 / 3 可选）后修掉四处事实错误**（含 1 处阻断项）：① **高——契约把 `backfill --end` 写成必填**：实测 `backfill_parser` 里 `--start required=True`、`--end default=None`（缺省今天），与同仓管线文档「`--end` 缺省今天」自相矛盾——消费方据契约认定必传、据 README 认定可省，两处打架。已改为「`--start` 必填；`--end` 可省（缺省今天），消费方应显式传」。② **中——「空库时记一条 warning」在 `backfill` 上不可达**：`run_backfill` 先查 `MinuteBars` 里存在的年份，无数据则循环体不执行、根本不调 `export_bars_from_database`，那条 warning 只在 `merge-*` 会打。已改为「既不告警也不报错，只是没有产出文件；仅 `merge-*` 会为此记 warning」。③ **中——`登录失败` 子串并非登录失败的专属标记**：`login_with_retry:101` 每次重试失败都往 stderr 打 `登录失败（尝试 n/m）`，首次失败、第二次成功时进程**以 0 正常退出而 stderr 已含该子串**；而本档记录消费方「只认这个子串」，仅按子串判定会把成功运行误判为失败。已在契约加**注意**段，明写「必须先看退出码，子串只用于给失败分类」。④ **中/低——README 配置表把 `stocks` 写成「每个命令」**：`bao_stock.py` 的 `--sync-securities` / `--update-trade-dates` / `--update-index` / `--update-industry` / `--update-macro` / `--version` 都不读配置，`--codes` 也只存在于管线。命令清单已移到表下正文并逐条列全（原表格单元格塞不下整份清单，且会顶破 150 行宽）。另改三处措辞：契约 §1「输出根一律显式给定」→「应始终显式传，不传回落本仓配置值」（与 `resolve_output_root` 的配置回落对齐）；CHANGELOG 登录失败条注明「本句仅指管线，`bao_stock.py` 是记 error 后 `return`、退出码 0」；`bao_stock_parquet.py:30` 那句易读成「只执行一个升级脚本」的括号改为「加载 `Create_v1.0.0.sql` 后依次执行 upgrades 下全部脚本」。**未采纳（留档）**：表格单元格 ≤40 字符（全仓既存倾向，且 rules §7 那条指单元格文本而非行宽）；`docs/BaoStockPythonAPI.md` 改 kebab-case（既存文件名，改它会连带动 README 引用）；`sql/schema/` 缺 `Create_v2.1.0/2.2.0/2.3.0.sql`（既存结构问题，本批未引入，已立为 ❓）。

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

## Q.11 超时／网络接收错误后不重登录

- 原日期：2026-10-13
- 结论：2026-10-14 用户裁定「那就按 Q.11 做吧」——`ingest_stock` 捕获会话失效与分页中断后换新 socket 重登录并重试该票（上限 3 次），接收超时阈值随之由 300 s 调回 120 s；docstring「附带待定」亦已补上超时污染 socket 的后果。见 D.20。

原文：

- **Q.11** 超时／网络接收错误（10002007）后**不重登录**：baostock 的 `send_msg` 一遇异常就废掉整条 socket（详见 D.19），而本仓只把它当成"分页中断"中止整轮，不换新 socket 续跑——`BaostockSessionLostError`（10001001）的文案里本就写着"重新登录后再跑"，却没人去做。根治方向：`ingest_stock` 捕获后 `logout_quietly()` + `login_with_retry()` 换新 socket 重试该股（每只票的 ingest 本身原子且幂等，整只重试安全），上限 3 次。是否做由用户定。**附带待定**：`enable_socket_timeout` 的 docstring 是否补上"超时即污染 socket、阈值只降概率"的真实后果（加注释属 Harness §4 例外，须用户确认）。

## Q.10 sql/schema 索引名 IX_* 不合 Idx* 规范

- 原日期：2026-10-13
- 结论：2026-10-13 用户裁定「命名问题检查一下，按照规范一起改了」——两个索引改名 `IdxMinuteBarsFrequencyTradingDay` / `IdxMinuteBarTradingDaysTradingDay`，真库既有索引同步重命名后重新打戳。见 D.17。

原文：

- **Q.10** `sql/schema/Create_v1.0.0.sql` 的两个索引名 `IX_MinuteBars_Frequency_TradingDay` 与 `IX_MinuteBarTradingDays_TradingDay` 不合 `rules/database-style.md` §1（应为 `Idx<表><列>`，即 `IdxMinuteBarsFrequencyTradingDay` 与 `IdxMinuteBarTradingDaysTradingDay`）。该命名自旧 `Update_v2.1.0.sql` 原样搬入，本批重写 schema 时按「最小改动」未一并改（同批已把三张分钟线表的匿名主键补成合规的 `CONSTRAINT Pk…`）。**改它得连用户库里的既有索引一起重命名**——结构比对含索引名，只改 schema 会让存量库判为结构不符。是否改由用户定。

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

## Q.01 `sql/schema/` 缺 Create_v2.1.0 / 2.2.0 / 2.3.0 的缺档

- 原日期：2026-10-10
- 结论：2026-10-13 用户裁定——schema **收敛为单一 `Create_v1.0.0.sql`**（全量最终态），删除 `Create_v2.0.0.sql` 与 4 个 `Update_v*.sql`，「缺档」随合并消失。见 D.16。

原文：

- **Q.01** `sql/schema/` 只有 `Create_v1.0.0.sql` 与 `Create_v2.0.0.sql`，缺 `Create_v2.1.0` / `2.2.0` / `2.3.0.sql`；`rules/database-style.md` §6 要求每次发版**同时**更新 schema 与 upgrades（等价性：空库执行 `Create_v2.0.0` ＋ 依次 upgrades 即得最新）。本仓 `init_database` 走的是「`Create_v1.0.0` ＋ 依次 upgrades」，运行上不受影响，但按规范属缺档。是否补齐待定（补齐后 `init_database` 是否改走最新 Create 也需一并决定）。
