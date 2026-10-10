# 变更记录

非破坏性变更随提交记录即可；**破坏性变更**（重命名、配置键增删、CLI 参数与错误语义变化、schema 漂移）必须记在这里并升版本号。

## v1.0.0（2026-10-10）

首个版本化发布。下列条目相对发布前的开发状态而言是**破坏性**的，按旧版对接的消费方需逐条核对。

### 重命名（对外入口名变更）

- `BaoStock.py` → `bao_stock.py`
- `BaoStockParquet.py` → `bao_stock_parquet.py`

消费方若写死了旧文件名（例如以文件名判断"组件是否在位"，或据此调起下载），必须同步更新。

### 配置

- 两份按脚本命名的配置（`bao_stock.json` / `bao_stock_parquet.json`）**合并回单一 `config.json`**，两脚本共用一份清单。
- `parquet_output_root` 由代码兜底改为**必填**。
- `default_start_date` 由可省（代码兜底）改为**必填**，且只在 `update` 子命令上要求。
- 三个键一律不兜底：缺了即报错退出，不会回退到任何写死的默认值。

### 错误语义

- 配置项缺失与配置文件缺失统一走 `require_config_value` / `load_config_file`，信息形如 `配置缺少必填项 <键>（<配置文件>）` 与 `配置文件不存在: <路径>`，退出码 1。
- 管线 `bao_stock_parquet.py` 的登录失败仍为 `raise SystemExit("BaoStock 登录失败")`，退出码 1——
  该句含 `登录失败` 子串，消费方的失败分类依据不变。
- **注**：`bao_stock.py` 的登录失败一直是「记 error 后 `return`」、退出码 0，与管线不同。

### 数据库

- schema 累计到 v2.3.0：`MinuteBars` 自 v2.1.0、`QueriedBarSpans` 自 v2.3.0，由 `init_database` 在首次连接时自动应用，无需手工执行 SQL。

### 新增

- `version.txt`：版本号的唯一来源，`python bao_stock.py --version` 打印它。
- `tests/`：pytest 用例，只覆盖纯函数。
