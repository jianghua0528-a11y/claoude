"""
切换集成测试  ·  test_switch_integration.py
证明 DB建单 → core.queries 聚合 → web 看板 整条链路已走宪法版 settle 引擎,
且旧 mode(直结/全归艺人) 经适配器正确映射。
"""
import pytest

from cgroup.db.session import init_db, get_session
from cgroup.db.models import Broker, Artist, Mama, Venue, Order
from cgroup.core import queries
from cgroup.core.settle import settle_db

YEAR, MONTH = 2026, 6
DAY = __import__("datetime").date(YEAR, MONTH, 15)


@pytest.fixture(scope="module")
def db():
    init_db()
    s = get_session()
    b = Broker(name="阿星", pct=0.07, is_shareholder=True)
    s.add(b); s.flush()
    art = Artist(name="桃子", broker_id=b.id)
    mama = Mama(name="小宝")
    ven = Venue(name="名门", default_ticket=200)
    s.add_all([art, mama, ven]); s.flush()
    # 四类单, 同艺人; 前三单挂 mama, 自定义单无 mama
    s.add_all([
        Order(biz_date=DAY, artist_id=art.id, venue_id=ven.id, mama_id=mama.id,
              preset="标准", credit_k=3000, ticket_o=200, wp=3000, status="已审核"),
        Order(biz_date=DAY, artist_id=art.id, venue_id=ven.id, mama_id=mama.id,
              preset="标准", cash_m=3000, wp=3000, flow="B", status="已审核"),
        Order(biz_date=DAY, artist_id=art.id, venue_id=ven.id, mama_id=mama.id,
              preset="无水单", credit_k=2000, wp=2000, status="已审核"),   # 表外
        Order(biz_date=DAY, artist_id=art.id, venue_id=ven.id,
              preset="自定义", cust_a=1.0, cust_m=0.0, cust_c=0.0,
              credit_k=1000, wp=1000, status="已审核"),                   # 全归艺人 100/0/0
    ])
    s.commit()
    return dict(session=s, art=art.id, mama=mama.id)


def test_preset_settles_per_constitution(db):
    """DB preset 经 settle_db 出宪法口径。(查询限定本模块 biz_date 防共享库串扰)"""
    s = db["session"]

    def one(**filt):
        return s.query(Order).filter_by(biz_date=DAY, **filt).first()

    # 标准挂账: 应发 0.7*3000+200=2300; 妈咪应结 3200-600=2600; 公司净 300
    r = settle_db(one(preset="标准", credit_k=3000))
    assert r.artist_month_end == pytest.approx(2300)
    assert r.mama_receivable == pytest.approx(2600)
    assert r.company_net == pytest.approx(300)
    # 无水单(表外): 实操全 0, 经济净仍在
    rd = settle_db(one(preset="无水单"))
    assert rd.is_direct_settle is True and rd.on_books is False
    assert rd.artist_month_end == 0 and rd.mama_receivable == 0
    assert rd.artist_net == pytest.approx(0.7 * 2000)
    # 自定义 100/0/0
    rg = settle_db(one(preset="自定义"))
    assert rg.artist_month_end == pytest.approx(1000)
    assert rg.company_net == 0


def test_artist_summary(db):
    """艺人月汇总应发 = 2300(标准挂账) + 0(现金B现场结) + 0(无水单) + 1000(全归艺人) = 3300。"""
    r = queries.artist_summary(db["session"], db["art"], YEAR, MONTH)
    assert r["n"] == 4
    assert r["wage"] == pytest.approx(3300)
    assert r["tickets"] == pytest.approx(200)


def test_mama_summary(db):
    """妈咪应结C组 = 2600(挂账应收) + (-600)(现金反水) + 0(无水单表外) = 2000。"""
    r = queries.mama_summary(db["session"], db["mama"])
    assert r["recv"] == pytest.approx(2000)


def test_day_summary(db):
    r = queries.day_summary(db["session"], DAY)
    assert r["n"] == 4
    assert r["K"] == pytest.approx(6000)   # 3000+2000+1000
    assert r["M"] == pytest.approx(3000)
    assert r["O"] == pytest.approx(200)


def test_web_dashboard_boots(db):
    """web 看板经新引擎渲染, 带鉴权返回 200。"""
    from starlette.testclient import TestClient
    from cgroup.web.app import app
    c = TestClient(app)
    assert c.get("/").status_code == 401             # 无凭据
    r = c.get("/", auth=("admin", "t"))
    assert r.status_code == 200
    assert "总览" in r.text
