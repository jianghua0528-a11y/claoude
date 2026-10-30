"""测试统一用临时 SQLite 库; 每个用例前清空重建。"""
import os
import sys
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="cgroup_test_"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ.setdefault("ADMIN_PASSWORD", "test-pw")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from cgroup.db.models import Base, Broker, Artist, Mama, MamaAssistant, Venue  # noqa: E402
from cgroup.db.session import engine, get_session, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture
def s():
    sess = get_session()
    yield sess
    sess.close()


@pytest.fixture
def seeded(s):
    """最小字典: 经纪人阿星 / 艺人桃子(别名peach) / 妈咪小宝(助理阿美) / 场所88(门票150)。"""
    b = Broker(name="阿星", pct=0.07, is_shareholder=True)
    s.add(b); s.flush()
    s.add(Artist(name="桃子", aliases="peach,桃桃", broker_id=b.id))
    s.add(Artist(name="小白"))
    m = Mama(name="小宝", aliases="宝姐")
    s.add(m); s.flush()
    s.add(MamaAssistant(name="阿美", mama_id=m.id))
    s.add(Venue(name="88", default_ticket=150, aliases="88会所"))
    s.commit()
    return s
