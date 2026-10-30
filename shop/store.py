"""
服装店 业务逻辑  ·  store.py
机器人和后台共用这一份: 购物车 / 下单扣库存 / 订单状态流转 / 金额格式。

订单状态:
  待付款 ──(顾客传截图)──▶ 待确认 ──(管理员确认收款)──▶ 已付款 ──(填单号)──▶ 已发货 ──▶ 已完成
     ▲                      │
     └──(管理员驳回截图)────┘
  已发货之前都可以 → 已取消 (库存退回)
下单即扣库存(锁货), 超时未付款自动取消退回。
"""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, update

from .models import Customer, CartItem, Variant, Order, OrderItem

CURRENCY = os.getenv("SHOP_CURRENCY", "RM")
SHIPPING_FEE = os.getenv("SHIPPING_FEE", "8")            # 运费 (元)
FREE_SHIPPING_OVER = os.getenv("FREE_SHIPPING_OVER", "150")  # 满多少包邮, 0 = 不包邮
UNPAID_EXPIRE_HOURS = int(os.getenv("UNPAID_EXPIRE_HOURS", "24"))

PENDING, REVIEW, PAID, SHIPPED, DONE, CANCELLED = "待付款", "待确认", "已付款", "已发货", "已完成", "已取消"
STATUSES = [PENDING, REVIEW, PAID, SHIPPED, DONE, CANCELLED]
STATUS_ICON = {PENDING: "⏳", REVIEW: "🧾", PAID: "💰", SHIPPED: "🚚", DONE: "✅", CANCELLED: "❌"}
_NEXT = {                                 # 合法流转
    PENDING: {REVIEW, CANCELLED},
    REVIEW: {PAID, PENDING, CANCELLED},
    PAID: {SHIPPED, CANCELLED},
    SHIPPED: {DONE},
    DONE: set(),
    CANCELLED: set(),
}


def _utcnow():                            # 和数据库 server_default now() 一致: 无时区 UTC
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ShopError(Exception):
    """给顾客/管理员看的业务错误 (库存不足、状态不对...)。"""


# ─────────────────────── 金额 ───────────────────────
def parse_money(text) -> int:
    """'59' / '59.9' / 'RM 59.90' → 分。"""
    t = str(text).replace(CURRENCY, "").replace(",", "").strip()
    try:
        d = Decimal(t)
    except InvalidOperation:
        raise ShopError(f"金额格式不对: {text}")
    if d < 0:
        raise ShopError("金额不能为负")
    return int((d * 100).quantize(Decimal("1")))


def money(cents: int) -> str:
    return f"{CURRENCY} {cents / 100:,.2f}"


def shipping_fee(subtotal: int) -> int:
    free_over = parse_money(FREE_SHIPPING_OVER)
    if free_over and subtotal >= free_over:
        return 0
    return parse_money(SHIPPING_FEE)


# ─────────────────────── 顾客 & 购物车 ───────────────────────
def get_customer(s, tg_user) -> Customer:
    """按 Telegram 用户找顾客, 没有就建; 顺手更新昵称。"""
    uid = str(tg_user.id)
    c = s.scalar(select(Customer).where(Customer.tg_user_id == uid))
    if not c:
        c = Customer(tg_user_id=uid)
        s.add(c)
    c.tg_name = tg_user.full_name
    c.tg_username = tg_user.username
    s.flush()
    return c


def cart_items(s, customer) -> list[CartItem]:
    return list(s.scalars(select(CartItem).where(CartItem.customer_id == customer.id)
                          .order_by(CartItem.id)))


def cart_subtotal(items) -> int:
    return sum(i.variant.unit_price * i.qty for i in items)


def add_to_cart(s, customer, variant_id: int, qty: int = 1) -> CartItem:
    v = s.get(Variant, variant_id)
    if not v or not v.product.active:
        raise ShopError("这个商品已下架")
    item = s.scalar(select(CartItem).where(CartItem.customer_id == customer.id,
                                           CartItem.variant_id == variant_id))
    have = item.qty if item else 0
    if have + qty > v.stock:
        raise ShopError(f"库存不足: {v.product.name} {v.label} 只剩 {v.stock} 件"
                        + (f"，购物车里已有 {have} 件" if have else ""))
    if item:
        item.qty += qty
    else:
        item = CartItem(customer_id=customer.id, variant_id=variant_id, qty=qty)
        s.add(item)
    s.flush()
    return item


def change_cart_qty(s, customer, item_id: int, delta: int):
    item = s.get(CartItem, item_id)
    if not item or item.customer_id != customer.id:
        return
    new = item.qty + delta
    if new <= 0:
        s.delete(item)
    elif new > item.variant.stock:
        raise ShopError(f"只剩 {item.variant.stock} 件了")
    else:
        item.qty = new
    s.flush()


def clear_cart(s, customer):
    for i in cart_items(s, customer):
        s.delete(i)
    s.flush()


# ─────────────────────── 下单 ───────────────────────
def place_order(s, customer, name, phone, address, note=None) -> Order:
    """购物车 → 订单。逐个 SKU 条件扣库存, 任何一件不够就整单回滚。
    调用方负责 commit; 抛 ShopError 时调用方应 rollback。"""
    items = cart_items(s, customer)
    if not items:
        raise ShopError("购物车是空的")
    order = Order(customer_id=customer.id, status=PENDING,
                  name=name.strip(), phone=phone.strip(), address=address.strip(),
                  note=(note or "").strip() or None, subtotal=0, total=0)
    subtotal = 0
    for it in items:
        v = it.variant
        if not v.product.active:
            raise ShopError(f"{v.product.name} 已下架，请从购物车删掉")
        # 条件更新: 并发下单也不会超卖
        res = s.execute(update(Variant)
                        .where(Variant.id == v.id, Variant.stock >= it.qty)
                        .values(stock=Variant.stock - it.qty))
        if res.rowcount != 1:
            s.refresh(v)
            raise ShopError(f"库存不足: {v.product.name} {v.label} 只剩 {v.stock} 件")
        price = v.unit_price
        subtotal += price * it.qty
        order.items.append(OrderItem(variant_id=v.id, product_name=v.product.name,
                                     variant_label=v.label, unit_price=price, qty=it.qty))
        s.delete(it)
    order.subtotal = subtotal
    order.shipping = shipping_fee(subtotal)
    order.total = subtotal + order.shipping
    s.add(order)
    # 记住收货信息, 下次一键沿用
    customer.name, customer.phone, customer.address = order.name, order.phone, order.address
    s.flush()
    s.expire_all()                 # 刷掉内存里旧的 stock
    return order


# ─────────────────────── 状态流转 ───────────────────────
def _move(order, to):
    if to not in _NEXT[order.status]:
        raise ShopError(f"订单 #{order.id} 当前「{order.status}」，不能改成「{to}」")
    order.status = to
    order.updated_at = _utcnow()


def _restock(s, order):
    for it in order.items:
        if it.variant_id:
            s.execute(update(Variant).where(Variant.id == it.variant_id)
                      .values(stock=Variant.stock + it.qty))


def attach_payment(order, file_id):
    if order.status == REVIEW:            # 重传截图: 覆盖
        order.payment_file_id = file_id
        return
    _move(order, REVIEW)
    order.payment_file_id = file_id


def confirm_payment(order):
    _move(order, PAID)


def reject_payment(order):
    _move(order, PENDING)
    order.payment_file_id = None


def ship(order, tracking_no):
    _move(order, SHIPPED)
    order.tracking_no = (tracking_no or "").strip() or None


def complete(order):
    _move(order, DONE)


def cancel(s, order):
    _move(order, CANCELLED)
    _restock(s, order)
    s.flush()


def expire_unpaid(s, hours=UNPAID_EXPIRE_HOURS) -> list[Order]:
    """超时未付款(还停在 待付款)的单自动取消, 退库存。返回被取消的单。"""
    cutoff = _utcnow() - timedelta(hours=hours)
    olds = list(s.scalars(select(Order).where(Order.status == PENDING,
                                              Order.created_at < cutoff)))
    for o in olds:
        cancel(s, o)
    return olds


# ─────────────────────── 文本 ───────────────────────
def order_text(order, with_address=True) -> str:
    lines = [f"{STATUS_ICON.get(order.status, '')} 订单 #{order.id} · {order.status}"]
    for it in order.items:
        lines.append(f"• {it.product_name} ({it.variant_label}) × {it.qty}  {money(it.unit_price * it.qty)}")
    lines.append(f"小计 {money(order.subtotal)}  运费 {money(order.shipping)}")
    lines.append(f"合计 {money(order.total)}")
    if with_address:
        lines.append(f"\n收货人 {order.name}  {order.phone}\n地址 {order.address}")
    if order.note:
        lines.append(f"备注 {order.note}")
    if order.tracking_no:
        lines.append(f"快递单号 {order.tracking_no}")
    return "\n".join(lines)
