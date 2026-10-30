"""服装店: 业务逻辑 + 后台 + 机器人界面 的冒烟测试。  运行: pytest tests/test_shop.py"""
import os
import tempfile
from types import SimpleNamespace

_tmp = tempfile.mkdtemp()
os.environ["SHOP_DATABASE_URL"] = f"sqlite:///{_tmp}/shop_test.db"
os.environ["SHOP_ADMIN_PASSWORD"] = "pw"
os.environ["SHIPPING_FEE"] = "8"
os.environ["FREE_SHIPPING_OVER"] = "150"
os.environ.setdefault("SHOP_BOT_TOKEN", "123:test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import close_all_sessions

from shop.db import init_db, get_session, engine
from shop.models import Base, Category, Product, Variant, Order
from shop import store as S


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    init_db()
    yield
    close_all_sessions()


def _user(uid=1, name="小美"):
    return SimpleNamespace(id=uid, full_name=name, username="mei")


def _product(s, stock=3):
    cat = Category(name="上衣")
    p = Product(name="针织开衫", price=S.parse_money("59.90"), category=cat)
    for i, (c, z) in enumerate([("黑色", "S"), ("黑色", "M"), ("白色", "M")]):
        p.variants.append(Variant(color=c, size=z, stock=stock, sort=i))
    s.add(p)
    s.commit()
    return p


def test_money():
    assert S.parse_money("59.9") == 5990
    assert S.parse_money("RM 1,200") == 120000
    assert S.money(5990) == "RM 59.90"
    assert S.shipping_fee(5990) == 800
    assert S.shipping_fee(15000) == 0
    with pytest.raises(S.ShopError):
        S.parse_money("abc")


def test_cart_respects_stock():
    s = get_session()
    p = _product(s, stock=2)
    c = S.get_customer(s, _user())
    v = p.variants[0]
    S.add_to_cart(s, c, v.id)
    S.add_to_cart(s, c, v.id)
    with pytest.raises(S.ShopError):
        S.add_to_cart(s, c, v.id)          # 第 3 件超库存
    item = S.cart_items(s, c)[0]
    assert item.qty == 2
    with pytest.raises(S.ShopError):
        S.change_cart_qty(s, c, item.id, 1)
    S.change_cart_qty(s, c, item.id, -2)  # 减到 0 = 删除
    assert S.cart_items(s, c) == []


def test_order_lifecycle_and_restock():
    s = get_session()
    p = _product(s, stock=3)
    vid = p.variants[1].id
    c = S.get_customer(s, _user())
    S.add_to_cart(s, c, vid, 2)
    o = S.place_order(s, c, "小美", "0123456789", "吉隆坡 xx 路 1 号")
    s.commit()
    assert o.status == S.PENDING
    assert o.subtotal == 2 * 5990 and o.shipping == 800 and o.total == 2 * 5990 + 800
    assert s.get(Variant, vid).stock == 1          # 下单即扣
    assert S.cart_items(s, c) == []                # 购物车清空
    assert c.address == "吉隆坡 xx 路 1 号"         # 记住地址

    S.attach_payment(o, "file123")
    assert o.status == S.REVIEW
    S.reject_payment(o)
    assert o.status == S.PENDING and o.payment_file_id is None
    S.attach_payment(o, "file456")
    S.confirm_payment(o)
    assert o.status == S.PAID
    with pytest.raises(S.ShopError):
        S.complete(o)                              # 没发货不能完成
    S.cancel(s, o)
    s.commit()
    assert s.get(Variant, vid).stock == 3          # 取消退库存
    with pytest.raises(S.ShopError):
        S.cancel(s, o)


def test_order_fails_atomically_when_stock_gone():
    s = get_session()
    p = _product(s, stock=1)
    a, b = p.variants[0].id, p.variants[2].id
    c1 = S.get_customer(s, _user(1))
    c2 = S.get_customer(s, _user(2))
    S.add_to_cart(s, c1, a)
    S.add_to_cart(s, c1, b)
    S.add_to_cart(s, c2, b)
    s.commit()
    S.place_order(s, c2, "B", "0123456789", "地址地址地址")   # c2 先买走白色M
    s.commit()
    with pytest.raises(S.ShopError):
        S.place_order(s, c1, "A", "0123456789", "地址地址地址")
    s.rollback()
    assert s.get(Variant, a).stock == 1            # 黑色S 没被扣 (整单回滚)
    assert len(S.cart_items(s, c1)) == 2


def test_expire_unpaid():
    s = get_session()
    p = _product(s, stock=1)
    c = S.get_customer(s, _user())
    S.add_to_cart(s, c, p.variants[0].id)
    o = S.place_order(s, c, "A", "0123456789", "地址地址地址")
    s.commit()
    assert S.expire_unpaid(s, hours=1) == []
    o.created_at = o.created_at.replace(year=o.created_at.year - 1)
    s.commit()
    assert [x.id for x in S.expire_unpaid(s, hours=1)] == [o.id]
    s.commit()
    assert s.get(Order, o.id).status == S.CANCELLED
    assert s.get(Variant, p.variants[0].id).stock == 1


def test_bot_views():
    from shop import bot
    s = get_session()
    p = _product(s, stock=0)
    p.variants[2].stock = 5
    s.commit()
    cap = bot._product_caption(p)
    assert "白色：M" in cap and "黑色" not in cap.split("现货")[1]
    tip, kb = bot._product_kb(p)                     # 两个颜色 → 先选颜色
    assert [b.text for b in kb.inline_keyboard[0]] == ["黑色", "白色"]
    tip, kb = bot._product_kb(p, 0)                  # 黑色: 全售罄
    assert all("售罄" in b.text for b in kb.inline_keyboard[0])
    tip, kb = bot._product_kb(p, 1)
    assert kb.inline_keyboard[0][0].callback_data == f"add:{p.variants[2].id}"
    c = S.get_customer(s, _user())
    S.add_to_cart(s, c, p.variants[2].id, 3)
    text, kb = bot._cart_view(s, c)
    assert "RM 179.70" in text and "运费 RM 0.00" in text
    text, kb = bot._categories_view(s)
    assert kb.inline_keyboard[0][0].callback_data.startswith("cat:")
    assert bot.build_app() is not None               # 所有 handler 能注册


def test_web_admin(monkeypatch):
    from shop import web, notify
    sent = []
    monkeypatch.setattr(notify, "send_message", lambda chat, text: sent.append((chat, text)) or True)
    cl = TestClient(web.app)
    assert cl.get("/").status_code == 401
    cl.auth = ("admin", "pw")
    assert cl.get("/").status_code == 200

    r = cl.post("/categories/new", data={"name": "裙子"})
    assert r.status_code == 200
    s = get_session()
    cid = s.scalar(select(Category.id))
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    r = cl.post("/products/new", data={"name": "碎花裙", "category_id": str(cid), "price": "89",
                                       "description": "雪纺", "active": "1"},
                files={"image": ("a.png", png, "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    pid = int(r.headers["location"].split("/")[2].split("?")[0])
    cl.post(f"/products/{pid}/bulk", data={"colors": "红，蓝", "sizes": "S,M", "stock": "4"})
    s.expire_all()
    p = s.get(Product, pid)
    assert len(p.variants) == 4 and p.price == 8900
    assert cl.get(f"/products/{pid}/image").content == png
    v = p.variants[0]
    cl.post(f"/products/{pid}/variants", data={f"color_{v.id}": "红", f"size_{v.id}": "S",
                                               f"stock_{v.id}": "9", f"price_{v.id}": "99",
                                               f"del_{p.variants[3].id}": "1"})
    s.expire_all()
    p = s.get(Product, pid)
    assert len(p.variants) == 3 and p.variants[0].stock == 9 and p.variants[0].unit_price == 9900
    assert "碎花裙" in cl.get("/products").text

    c = S.get_customer(s, _user(42))
    S.add_to_cart(s, c, p.variants[0].id)
    o = S.place_order(s, c, "小美", "0123456789", "槟城 xx 路")
    S.attach_payment(o, "f1")
    s.commit()
    assert "要处理的订单" in cl.get("/").text
    assert "槟城" in cl.get(f"/orders/{o.id}").text
    cl.post(f"/orders/{o.id}/confirm")
    cl.post(f"/orders/{o.id}/ship", data={"tracking_no": "JNT 123"})
    s.expire_all()
    o = s.get(Order, o.id)
    assert o.status == S.SHIPPED and o.tracking_no == "JNT 123"
    assert sent[-1][0] == "42" and "JNT 123" in sent[-1][1]
    r = cl.post(f"/orders/{o.id}/cancel", follow_redirects=False)  # 已发货不能取消
    assert "msg=" in r.headers["location"]
    assert cl.get("/orders?st=已发货&q=小美").text.count(f">#{o.id}</a>") == 1
    cl.post(f"/products/{pid}/delete")
    s.expire_all()
    assert s.get(Product, pid) is None and s.get(Order, o.id).items[0].variant_id is None
