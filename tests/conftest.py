"""pytest 全局: 所有 DB 测试共用一个临时 SQLite 库 (隔离, 每轮重建)。
必须在任何 cgroup.db 模块导入前设好 DATABASE_URL —— conftest 先于测试模块导入。"""
import os
import tempfile

_DB = os.path.join(tempfile.gettempdir(), "cgroup_pytest.db")
if os.path.exists(_DB):
    os.remove(_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ.setdefault("ADMIN_PASSWORD", "t")
