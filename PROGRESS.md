# PROGRESS

## ✅ 已完成

- 修复 `pip install baostock` 报 `ResolutionImpossible` 的问题。
  根因是 pypi.org 连接超时（非依赖冲突），已改用清华镜像安装。
- 全局 Python 3.14.5 补齐依赖：baostock 0.9.3、pandas 3.0.5、numpy 2.5.3、pyarrow 25.0.1。
- 写入 pip 永久镜像配置 `C:\Users\15031\pip\pip.ini`（清华源，timeout 60s）。
- 验证：baostock 端到端登录 + K 线取数成功；`BaoStock.py` 与 `BaoStockParquet.py` 均可正常加载（`--help` 退出码 0）。

## 🔄 进行中

- 无。

## ❓ 待讨论 / 待决策

- 依赖目前装在全局 Python 3.14.5 中，仓库内没有 `requirements.txt` 锁定版本。是否需要补一份锁定文件，或改造成 `.venv` 隔离。
- `python BaoStock.py --update-all` 尚未实际执行（全量拉取耗时较长，且会写库）。
- 是否需要把 Python 运行时也锁定为其他 LTS 版本（当前基线为 3.14.5）。

## 备注

- 诊断遗留的临时下载目录（可自行清理）：`%TEMP%\pdtest`、`%TEMP%\pdtest2`、`%TEMP%\bscheck`、`%TEMP%\patest`。
