"""pytest 根配置：把仓根加入 sys.path，使 tests/ 下能 import 被测脚本。

仓库没有 `pyproject.toml` / `pytest.ini`，pytest 默认只会把测试文件所在目录
（`tests/`）放进 `sys.path`，仓根不在其中。此处显式插入，避免依赖 pytest
内部的 rootdir 推导。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
