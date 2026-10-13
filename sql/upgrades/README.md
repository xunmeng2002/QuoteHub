# 升级脚本目录

本目录存放**正式发布之后**的增量升级脚本 `Update_v<x.y.z>.sql`，由 `init_database.py --upgrade` 按文件名 `sorted()` 顺序执行。

## 约定

- **只在发布时新增**：`v1.0.0` 发布前，全部结构改动都并入 `sql/schema/Create_v1.0.0.sql`，不在此目录留脚本。
- **等价性**：空库执行 `Create_v1.0.0.sql` 后再依次跑本目录全部脚本，结果须与直接执行最新一版 `Create` 完全一致。
- **幂等**：每个脚本都要能重复执行（`IF NOT EXISTS`、先判后改），`--upgrade` 在结构不符时会重跑它们。
- **可重入**：脚本由 `init_database.py` 统一包在事务里执行，中途出错即整体回滚，不会留下半套结构。

## 现状

本目录当前为空——`sql/schema/Create_v1.0.0.sql` 是全量基线，尚无发布后的增量脚本。
本 README 同时充当 git 的目录占位文件（git 不跟踪空目录）。
