"""
服装店 Telegram 机器人  ·  bot.py
顾客:  逛店(分类 → 商品 → 颜色 → 尺码) → 加购物车 → 结算填地址 → 下单锁库存
       → 看收款信息 → 发付款截图 → 等店家确认 → 收发货通知
店家:  新单/截图私聊推送, 按钮「✅确认收款 / ❌驳回」; 发货等在网页后台操作
环境变量: SHOP_BOT_TOKEN(或 TELEGRAM_BOT_TOKEN), SHOP_ADMIN_IDS, PAYMENT_INFO, PAYMENT_QR_URL,
          SHOP_NAME, SHOP_CONTACT, SHOP_ADMIN_URL
"""
import os
import logging
from contextlib import contextmanager
from io import BytesIO

from sqlalchemy import select
from telegram import (Update, InlineKeyboardButton as B, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup)
from telegram.error import BadRequest
from telegram.ext import (Application, MessageHandler, CommandHandler, CallbackQueryHandler,
                          ConversationHandler, filters, ContextTypes)

from .db import get_session, init_db
from .models import Category, Product, Variant, Order
from . import store as S

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("shop-bot")

TOKEN = os.getenv("SHOP_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_IDS = [x.strip() for x in os.getenv("SHOP_ADMIN_IDS", os.getenv("ADMIN_USER_IDS", "")).split(",")
             if x.strip()]
SHOP_NAME = os.getenv("SHOP_NAME", "服装店")
SHOP_CONTACT = os.getenv("SHOP_CONTACT", "")              # 客服, 如 @your_username
PAYMENT_INFO = os.getenv("PAYMENT_INFO", "请联系客服获取收款方式").replace("\\n", "\n")
PAYMENT_QR_URL = os.getenv("PAYMENT_QR_URL", "")          # 收款码图片网址(可选)
ADMIN_URL = os.getenv("SHOP_ADMIN_URL", "")
PAGE = 8                                                  # 商品列表每页几个

BTN_SHOP, BTN_CART, BTN_ORDERS, BTN_HELP = "🛍 逛店", "🛒 购物车", "📦 我的订单", "💬 联系客服"
MENU = ReplyKeyboardMarkup([[BTN_SHOP, BTN_CART], [BTN_ORDERS, BTN_HELP]], resize_keyboard=True)

# 结算对话的状态
ASK_SAVED, ASK_NAME, ASK_PHONE, ASK_ADDR, ASK_NOTE, ASK_CONFIRM = range(6)


@contextmanager
def db():
    s = get_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _is_admin(user) -> bool:
    return str(user.id) in ADMIN_IDS


async def _notify_admins(ctx, text, kb=None, photo=None):
    for aid in ADMIN_IDS:
        try:
            if photo:
                await ctx.bot.send_photo(int(aid), photo, caption=text[:1024], reply_markup=kb)
            else:
                await ctx.bot.send_message(int(aid), text, reply_markup=kb)
        except Exception:
            log.warning("发管理员 %s 失败(他可能没 /start 过机器人)", aid)


async def _show(q, text, kb):
    """回调里展示文字页: 原消息是文字就原地改, 是图片(商品卡)就另发一条。"""
    if q.message and q.message.text is not None:
        try:
            await q.edit_message_text(text, reply_markup=kb)
            return
        except BadRequest as e:
            if "not modified" in str(e):
                return
    await q.message.reply_text(text, reply_markup=kb)


# ─────────────────────── 入口 ───────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db() as s:
        S.get_customer(s, update.effective_user)
    hint = "\n\n你是店家：新订单和付款截图会私聊推给你。" if _is_admin(update.effective_user) else ""
    await update.message.reply_text(
        f"欢迎光临 {SHOP_NAME} 👗\n点下面「🛍 逛店」开始挑选，选好加入购物车再结算。{hint}",
        reply_markup=MENU)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = "有问题随时找我们～"
    if SHOP_CONTACT:
        t += f"\n客服：{SHOP_CONTACT}"
    t += "\n\n付款后在这里直接发付款截图即可。"
    await update.message.reply_text(t, reply_markup=MENU)


# ─────────────────────── 逛店 ───────────────────────
def _categories_view(s):
    cats = list(s.scalars(select(Category).order_by(Category.sort, Category.id)))
    rows = []
    for c in cats:
        n = sum(1 for p in c.products if p.active)
        if n:
            rows.append([B(f"{c.name} ({n})", callback_data=f"cat:{c.id}:0")])
    loose = s.scalar(select(Product.id).where(Product.category_id.is_(None), Product.active).limit(1))
    if loose:
        rows.append([B("其他", callback_data="cat:0:0")])
    if not rows:
        return "店里还没上架商品，晚点再来看看～", None
    rows.append([B("🛒 购物车", callback_data="cart")])
    return "请选择分类：", InlineKeyboardMarkup(rows)


async def on_shop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db() as s:
        text, kb = _categories_view(s)
    if update.callback_query:
        await update.callback_query.answer()
        await _show(update.callback_query, text, kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)


async def on_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, cid, page = q.data.split(":")
    cid, page = int(cid), int(page)
    with db() as s:
        cond = Product.category_id.is_(None) if cid == 0 else Product.category_id == cid
        prods = list(s.scalars(select(Product).where(cond, Product.active)
                               .order_by(Product.id.desc())))
        title = "其他" if cid == 0 else (s.get(Category, cid).name if s.get(Category, cid) else "")
        chunk = prods[page * PAGE:(page + 1) * PAGE]
        rows = []
        for p in chunk:
            stock = sum(v.stock for v in p.variants)
            tag = "" if stock else " · 售罄"
            rows.append([B(f"{p.name} · {S.money(p.price)}{tag}", callback_data=f"p:{p.id}")])
    nav = []
    if page > 0:
        nav.append(B("⬅ 上一页", callback_data=f"cat:{cid}:{page - 1}"))
    if (page + 1) * PAGE < len(prods):
        nav.append(B("下一页 ➡", callback_data=f"cat:{cid}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([B("↩ 分类", callback_data="cats"), B("🛒 购物车", callback_data="cart")])
    text = f"【{title}】共 {len(prods)} 款" if prods else f"【{title}】暂时没有商品"
    await _show(q, text, InlineKeyboardMarkup(rows))


def _colors(p):
    seen = []
    for v in p.variants:
        if v.color not in seen:
            seen.append(v.color)
    return seen


def _product_caption(p):
    prices = {v.unit_price for v in p.variants} or {p.price}
    lo, hi = min(prices), max(prices)
    price = S.money(lo) if lo == hi else f"{S.money(lo)} ~ {S.money(hi)}"
    lines = [f"👗 {p.name}", f"💰 {price}"]
    if p.description:
        lines.append(f"\n{p.description}")
    stock = {}
    for v in p.variants:
        if v.stock > 0:
            stock.setdefault(v.color or "均色", []).append(v.size or "均码")
    if stock:
        lines.append("\n现货：")
        for color, sizes in stock.items():
            lines.append(f"  {color}：{' '.join(sizes)}")
    else:
        lines.append("\n😢 全部售罄")
    return "\n".join(lines)[:1024]


def _product_kb(p, color_idx=None):
    """多个颜色: 先选颜色; 只有一个颜色(或已选颜色): 直接出尺码。"""
    colors = _colors(p)
    rows = []
    if len(colors) > 1 and color_idx is None:
        btns = [B(c or "默认", callback_data=f"col:{p.id}:{i}") for i, c in enumerate(colors)]
        rows += [btns[i:i + 3] for i in range(0, len(btns), 3)]
        tip = "选颜色 👇"
    else:
        color = colors[color_idx or 0] if colors else ""
        btns = []
        for v in p.variants:
            if v.color != color:
                continue
            name = v.size or v.color or "均码"
            if v.stock <= 0:
                btns.append(B(f"{name} 售罄", callback_data="none"))
            else:
                extra = f" {S.money(v.unit_price)}" if v.price is not None else ""
                btns.append(B(f"{name}{extra} ➕", callback_data=f"add:{v.id}"))
        rows += [btns[i:i + 3] for i in range(0, len(btns), 3)]
        if len(colors) > 1:
            rows.append([B(f"↩ 换颜色（当前 {color}）", callback_data=f"col:{p.id}:-1")])
        tip = "选尺码加入购物车 👇"
    rows.append([B("🛒 购物车", callback_data="cart"),
                 B("↩ 继续逛", callback_data=f"cat:{p.category_id or 0}:0")])
    return tip, InlineKeyboardMarkup(rows)


async def on_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split(":")[1])
    with db() as s:
        p = s.get(Product, pid)
        if not p or not p.active:
            await q.message.reply_text("这个商品已下架了")
            return
        caption = _product_caption(p)
        tip, kb = _product_kb(p)
        caption = f"{caption}\n\n{tip}"[:1024]
        photo = p.image_file_id or (BytesIO(p.image) if p.image else None)
        if not photo:
            await q.message.reply_text(caption, reply_markup=kb)
            return
        try:
            msg = await q.message.reply_photo(photo, caption=caption, reply_markup=kb)
        except BadRequest:                      # file_id 失效(换了机器人等) → 重传原图
            if not p.image:
                await q.message.reply_text(caption, reply_markup=kb)
                return
            msg = await q.message.reply_photo(BytesIO(p.image), caption=caption, reply_markup=kb)
        if msg.photo and msg.photo[-1].file_id != p.image_file_id:
            p.image_file_id = msg.photo[-1].file_id   # 缓存, 下次秒发


async def on_color(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, pid, idx = q.data.split(":")
    with db() as s:
        p = s.get(Product, int(pid))
        if not p:
            return
        idx = int(idx)
        tip, kb = _product_kb(p, None if idx < 0 else idx)
    try:
        await q.edit_message_reply_markup(kb)
    except BadRequest:
        pass


async def on_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    vid = int(q.data.split(":")[1])
    try:
        with db() as s:
            c = S.get_customer(s, q.from_user)
            item = S.add_to_cart(s, c, vid)
            label = f"{item.variant.product.name} {item.variant.label}"
            count = sum(i.qty for i in S.cart_items(s, c))
    except S.ShopError as e:
        await q.answer(str(e), show_alert=True)
        return
    await q.answer(f"✅ 已加入：{label}\n购物车共 {count} 件", show_alert=False)


async def on_none(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("这个尺码卖完了 😢")


# ─────────────────────── 购物车 ───────────────────────
def _cart_view(s, customer):
    items = S.cart_items(s, customer)
    if not items:
        return "🛒 购物车是空的", InlineKeyboardMarkup([[B("🛍 去逛逛", callback_data="cats")]])
    lines, rows = ["🛒 购物车"], []
    for n, it in enumerate(items, 1):
        v = it.variant
        warn = "" if it.qty <= v.stock else f" ⚠️只剩{v.stock}件"
        lines.append(f"{n}. {v.product.name} ({v.label}) × {it.qty}  {S.money(v.unit_price * it.qty)}{warn}")
        rows.append([B(f"{n}. ➖", callback_data=f"ci:{it.id}:-1"),
                     B(f"{it.qty}", callback_data="noop"),
                     B("➕", callback_data=f"ci:{it.id}:1"),
                     B("🗑", callback_data=f"ci:{it.id}:-999")])
    sub = S.cart_subtotal(items)
    ship = S.shipping_fee(sub)
    lines.append(f"\n小计 {S.money(sub)}  运费 {S.money(ship)}")
    free_over = S.parse_money(S.FREE_SHIPPING_OVER)
    if ship and free_over:
        lines.append(f"再买 {S.money(free_over - sub)} 包邮")
    lines.append(f"合计 {S.money(sub + ship)}")
    rows.append([B("🗑 清空", callback_data="cclear"), B("🛍 继续逛", callback_data="cats")])
    rows.append([B("✅ 去结算", callback_data="checkout")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def on_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db() as s:
        c = S.get_customer(s, update.effective_user)
        text, kb = _cart_view(s, c)
    if update.callback_query:
        await update.callback_query.answer()
        await _show(update.callback_query, text, kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)


async def on_cart_change(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        with db() as s:
            c = S.get_customer(s, q.from_user)
            if q.data == "cclear":
                S.clear_cart(s, c)
            else:
                _, iid, delta = q.data.split(":")
                S.change_cart_qty(s, c, int(iid), int(delta))
            text, kb = _cart_view(s, c)
    except S.ShopError as e:
        await q.answer(str(e), show_alert=True)
        return
    await q.answer()
    await _show(q, text, kb)


async def on_noop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# ─────────────────────── 结算(对话) ───────────────────────
async def co_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    with db() as s:
        c = S.get_customer(s, q.from_user)
        items = S.cart_items(s, c)
        if not items:
            await q.message.reply_text("购物车是空的～")
            return ConversationHandler.END
        bad = [i for i in items if i.qty > i.variant.stock]
        if bad:
            await q.message.reply_text("有商品库存不够了，请先在购物车里调整数量。")
            return ConversationHandler.END
        saved = (c.name, c.phone, c.address)
    ctx.user_data["co"] = {}
    if all(saved):
        kb = InlineKeyboardMarkup([[B("✅ 用这个地址", callback_data="co:saved")],
                                   [B("✏️ 填新地址", callback_data="co:new")]])
        await q.message.reply_text(f"收货信息：\n{saved[0]}  {saved[1]}\n{saved[2]}", reply_markup=kb)
        return ASK_SAVED
    await q.message.reply_text("请输入收货人姓名：（随时发 /cancel 取消）")
    return ASK_NAME


async def co_saved(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "co:new":
        await q.message.reply_text("请输入收货人姓名：")
        return ASK_NAME
    with db() as s:
        c = S.get_customer(s, q.from_user)
        ctx.user_data["co"] = {"name": c.name, "phone": c.phone, "address": c.address}
    return await _ask_note(q.message)


async def co_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.setdefault("co", {})["name"] = update.message.text.strip()[:100]
    await update.message.reply_text("请输入手机号：")
    return ASK_PHONE


async def co_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if sum(ch.isdigit() for ch in phone) < 7:
        await update.message.reply_text("手机号好像不对，请重新输入：")
        return ASK_PHONE
    ctx.user_data.setdefault("co", {})["phone"] = phone[:40]
    await update.message.reply_text("请输入详细收货地址（含邮编）：")
    return ASK_ADDR


async def co_addr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    addr = update.message.text.strip()
    if len(addr) < 6:
        await update.message.reply_text("地址太短了，请写详细一点：")
        return ASK_ADDR
    ctx.user_data.setdefault("co", {})["address"] = addr[:500]
    return await _ask_note(update.message)


async def _ask_note(message):
    kb = InlineKeyboardMarkup([[B("跳过", callback_data="co:nonote")]])
    await message.reply_text("有备注吗？（例如：想要的发货时间）没有就点跳过。", reply_markup=kb)
    return ASK_NOTE


async def co_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    co = ctx.user_data.setdefault("co", {})
    if update.callback_query:
        await update.callback_query.answer()
        co["note"] = None
        message = update.callback_query.message
    else:
        co["note"] = update.message.text.strip()[:500]
        message = update.message
    with db() as s:
        c = S.get_customer(s, update.effective_user)
        items = S.cart_items(s, c)
        sub = S.cart_subtotal(items)
        ship = S.shipping_fee(sub)
        lines = ["请确认订单："]
        lines += [f"• {i.variant.product.name} ({i.variant.label}) × {i.qty}  "
                  f"{S.money(i.variant.unit_price * i.qty)}" for i in items]
    lines.append(f"小计 {S.money(sub)}  运费 {S.money(ship)}\n合计 {S.money(sub + ship)}")
    lines.append(f"\n收货人 {co.get('name')}  {co.get('phone')}\n地址 {co.get('address')}")
    if co.get("note"):
        lines.append(f"备注 {co['note']}")
    kb = InlineKeyboardMarkup([[B("✅ 提交订单", callback_data="co:submit"),
                                B("❌ 取消", callback_data="co:abort")]])
    await message.reply_text("\n".join(lines), reply_markup=kb)
    return ASK_CONFIRM


async def co_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "co:abort":
        await q.edit_message_text("已取消结算，购物车还在。")
        ctx.user_data.pop("co", None)
        return ConversationHandler.END
    co = ctx.user_data.pop("co", {})
    try:
        with db() as s:
            c = S.get_customer(s, q.from_user)
            o = S.place_order(s, c, co.get("name", ""), co.get("phone", ""),
                              co.get("address", ""), co.get("note"))
            s.flush()
            oid, summary = o.id, S.order_text(o)
            who = f"{c.tg_name or ''} @{c.tg_username}" if c.tg_username else (c.tg_name or c.tg_user_id)
    except S.ShopError as e:
        await q.edit_message_text(f"⚠️ 下单失败：{e}\n请回购物车调整后再试。")
        return ConversationHandler.END
    ctx.user_data["pay_order"] = oid
    await q.edit_message_text(f"🎉 下单成功！\n\n{summary}")
    pay = (f"💳 付款方式：\n{PAYMENT_INFO}\n\n"
           f"付款后请直接在这里发送【付款截图】，我们确认后尽快发货。\n"
           f"⏰ {S.UNPAID_EXPIRE_HOURS} 小时内未付款订单会自动取消。")
    if PAYMENT_QR_URL:
        await q.message.reply_photo(PAYMENT_QR_URL, caption=pay, reply_markup=MENU)
    else:
        await q.message.reply_text(pay, reply_markup=MENU)
    await _notify_admins(ctx, f"🆕 新订单（待付款）· 顾客 {who}\n\n{summary}")
    return ConversationHandler.END


async def co_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("co", None)
    await update.message.reply_text("已取消结算，购物车还在。", reply_markup=MENU)
    return ConversationHandler.END


# ─────────────────────── 我的订单 ───────────────────────
def _order_kb(o):
    rows = []
    if o.status == S.PENDING:
        rows.append([B("💳 付款方式", callback_data=f"pay:{o.id}"),
                     B("❌ 取消订单", callback_data=f"ocancel:{o.id}")])
    if o.status == S.REVIEW:
        rows.append([B("🔁 重新上传截图", callback_data=f"pay:{o.id}")])
    rows.append([B("↩ 我的订单", callback_data="orders")])
    return InlineKeyboardMarkup(rows)


async def on_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db() as s:
        c = S.get_customer(s, update.effective_user)
        orders = list(s.scalars(select(Order).where(Order.customer_id == c.id)
                                .order_by(Order.id.desc()).limit(10)))
        rows = [[B(f"{S.STATUS_ICON[o.status]} #{o.id} · {o.status} · {S.money(o.total)}",
                   callback_data=f"ord:{o.id}")] for o in orders]
    text = "📦 最近的订单：" if orders else "还没有订单哦～"
    kb = InlineKeyboardMarkup(rows) if rows else None
    if update.callback_query:
        await update.callback_query.answer()
        await _show(update.callback_query, text, kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)


async def on_order_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = int(q.data.split(":")[1])
    with db() as s:
        c = S.get_customer(s, q.from_user)
        o = s.get(Order, oid)
        if not o or o.customer_id != c.id:
            return
        text, kb = S.order_text(o), _order_kb(o)
    await _show(q, text, kb)


async def on_order_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    oid = int(q.data.split(":")[1])
    try:
        with db() as s:
            c = S.get_customer(s, q.from_user)
            o = s.get(Order, oid)
            if not o or o.customer_id != c.id or o.status != S.PENDING:
                raise S.ShopError("这个订单现在不能取消了，请联系客服")
            S.cancel(s, o)
            text, kb = S.order_text(o), _order_kb(o)
    except S.ShopError as e:
        await q.answer(str(e), show_alert=True)
        return
    await q.answer("订单已取消")
    await _show(q, text, kb)
    await _notify_admins(ctx, f"顾客取消了订单 #{oid}（库存已退回）")


async def on_pay_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = int(q.data.split(":")[1])
    ctx.user_data["pay_order"] = oid
    pay = f"💳 订单 #{oid} 付款方式：\n{PAYMENT_INFO}\n\n付款后直接在这里发送付款截图。"
    if PAYMENT_QR_URL:
        await q.message.reply_photo(PAYMENT_QR_URL, caption=pay)
    else:
        await q.message.reply_text(pay)


async def on_payment_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """顾客私聊发图片 = 付款截图。挂到指定的/最近的待付款单上。"""
    msg = update.message
    file_id = msg.photo[-1].file_id if msg.photo else msg.document.file_id
    with db() as s:
        c = S.get_customer(s, update.effective_user)
        o = None
        want = ctx.user_data.get("pay_order")
        if want:
            o = s.get(Order, want)
            if not o or o.customer_id != c.id or o.status not in (S.PENDING, S.REVIEW):
                o = None
        if not o:
            o = s.scalar(select(Order).where(Order.customer_id == c.id,
                                             Order.status.in_([S.PENDING, S.REVIEW]))
                         .order_by(Order.id.desc()).limit(1))
        if not o:
            await msg.reply_text("没找到待付款的订单哦。有问题请点「💬 联系客服」。")
            return
        S.attach_payment(o, file_id)
        oid, summary = o.id, S.order_text(o)
        who = f"{c.tg_name or ''} @{c.tg_username}" if c.tg_username else (c.tg_name or c.tg_user_id)
    ctx.user_data.pop("pay_order", None)
    await msg.reply_text(f"✅ 已收到订单 #{oid} 的付款截图，店家确认后会通知你。", reply_markup=MENU)
    kb = InlineKeyboardMarkup([[B("✅ 确认收款", callback_data=f"aok:{oid}"),
                                B("❌ 驳回", callback_data=f"ano:{oid}")]])
    cap = f"🧾 付款截图 · 顾客 {who}\n\n{summary}"
    for aid in ADMIN_IDS:
        try:
            if msg.photo:
                await ctx.bot.send_photo(int(aid), file_id, caption=cap[:1024], reply_markup=kb)
            else:
                await ctx.bot.send_document(int(aid), file_id, caption=cap[:1024], reply_markup=kb)
        except Exception:
            log.warning("发管理员 %s 失败", aid)


# ─────────────────────── 店家操作 ───────────────────────
async def on_admin_pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _is_admin(q.from_user):
        await q.answer("只有店家能操作", show_alert=True)
        return
    action, oid = q.data.split(":")
    oid = int(oid)
    try:
        with db() as s:
            o = s.get(Order, oid)
            if not o:
                raise S.ShopError("订单不存在")
            if action == "aok":
                S.confirm_payment(o)
                tell = f"💰 订单 #{oid} 已确认收款，我们会尽快发货！"
                mark = "✅ 已确认收款"
            else:
                S.reject_payment(o)
                tell = (f"⚠️ 订单 #{oid} 的付款截图没有核对上，请检查后重新发送截图，"
                        f"或点「💬 联系客服」。")
                mark = "❌ 已驳回，等顾客重传"
            cust = int(o.customer.tg_user_id)
    except S.ShopError as e:
        await q.answer(str(e), show_alert=True)
        return
    await q.answer(mark)
    try:
        await q.edit_message_caption((q.message.caption or "") + f"\n\n{mark}")
    except BadRequest:
        pass
    try:
        await ctx.bot.send_message(cust, tell)
    except Exception:
        log.warning("通知顾客 %s 失败", cust)


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user):
        await update.message.reply_text(f"你的 Telegram ID：{update.effective_user.id}")
        return
    with db() as s:
        counts = {st: s.query(Order).filter(Order.status == st).count()
                  for st in (S.PENDING, S.REVIEW, S.PAID)}
        low = s.query(Variant).join(Product).filter(Product.active, Variant.stock <= 2).count()
    t = (f"🏪 {SHOP_NAME} 店家面板\n"
         f"⏳ 待付款 {counts[S.PENDING]}\n🧾 待确认收款 {counts[S.REVIEW]}\n"
         f"💰 已付款待发货 {counts[S.PAID]}\n⚠️ 库存≤2 的规格 {low}")
    if ADMIN_URL:
        t += f"\n\n后台：{ADMIN_URL}"
    await update.message.reply_text(t)


async def expire_job(ctx: ContextTypes.DEFAULT_TYPE):
    """定时: 超时未付款的单自动取消, 通知顾客。"""
    with db() as s:
        olds = S.expire_unpaid(s)
        notes = [(int(o.customer.tg_user_id), o.id) for o in olds]
    for uid, oid in notes:
        try:
            await ctx.bot.send_message(uid, f"⏰ 订单 #{oid} 超时未付款，已自动取消。需要的话可以重新下单～")
        except Exception:
            pass
    if notes:
        log.info("自动取消超时订单: %s", [o for _, o in notes])


def build_app() -> Application:
    app = Application.builder().token(TOKEN).build()
    private = filters.ChatType.PRIVATE
    typed = private & filters.TEXT & ~filters.COMMAND & ~filters.Text([BTN_SHOP, BTN_CART, BTN_ORDERS, BTN_HELP])
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(co_start, pattern=r"^checkout$")],
        states={
            ASK_SAVED: [CallbackQueryHandler(co_saved, pattern=r"^co:(saved|new)$")],
            ASK_NAME: [MessageHandler(typed, co_name)],
            ASK_PHONE: [MessageHandler(typed, co_phone)],
            ASK_ADDR: [MessageHandler(typed, co_addr)],
            ASK_NOTE: [CallbackQueryHandler(co_note, pattern=r"^co:nonote$"),
                       MessageHandler(typed, co_note)],
            ASK_CONFIRM: [CallbackQueryHandler(co_confirm, pattern=r"^co:(submit|abort)$")],
        },
        fallbacks=[CommandHandler("cancel", co_cancel)],
        conversation_timeout=1800,
    ))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("shop", on_shop))
    app.add_handler(CommandHandler("cart", on_cart))
    app.add_handler(CommandHandler("orders", on_orders))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(MessageHandler(private & filters.Text([BTN_SHOP]), on_shop))
    app.add_handler(MessageHandler(private & filters.Text([BTN_CART]), on_cart))
    app.add_handler(MessageHandler(private & filters.Text([BTN_ORDERS]), on_orders))
    app.add_handler(MessageHandler(private & filters.Text([BTN_HELP]), cmd_help))
    app.add_handler(MessageHandler(private & (filters.PHOTO | filters.Document.IMAGE), on_payment_photo))
    app.add_handler(CallbackQueryHandler(on_shop, pattern=r"^cats$"))
    app.add_handler(CallbackQueryHandler(on_category, pattern=r"^cat:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(on_product, pattern=r"^p:\d+$"))
    app.add_handler(CallbackQueryHandler(on_color, pattern=r"^col:\d+:-?\d+$"))
    app.add_handler(CallbackQueryHandler(on_add, pattern=r"^add:\d+$"))
    app.add_handler(CallbackQueryHandler(on_none, pattern=r"^none$"))
    app.add_handler(CallbackQueryHandler(on_noop, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(on_cart, pattern=r"^cart$"))
    app.add_handler(CallbackQueryHandler(on_cart_change, pattern=r"^(ci:\d+:-?\d+|cclear)$"))
    app.add_handler(CallbackQueryHandler(on_orders, pattern=r"^orders$"))
    app.add_handler(CallbackQueryHandler(on_order_detail, pattern=r"^ord:\d+$"))
    app.add_handler(CallbackQueryHandler(on_order_cancel, pattern=r"^ocancel:\d+$"))
    app.add_handler(CallbackQueryHandler(on_pay_info, pattern=r"^pay:\d+$"))
    app.add_handler(CallbackQueryHandler(on_admin_pay, pattern=r"^(aok|ano):\d+$"))
    if app.job_queue:
        app.job_queue.run_repeating(expire_job, interval=1800, first=60)
    return app


def main():
    if not TOKEN:
        raise SystemExit("缺 SHOP_BOT_TOKEN")
    init_db()
    app = build_app()
    log.info("服装店机器人启动: 店名=%s 管理员=%s", SHOP_NAME, ADMIN_IDS or "未设")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
