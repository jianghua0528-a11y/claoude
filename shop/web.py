"""
服装店 管理后台 (FastAPI)
  · 看板: 待处理订单 / 本月营业额 / 低库存提醒
  · 订单: 按状态筛选, 确认收款 / 驳回 / 发货填单号 / 完成 / 取消(退库存), 自动通知顾客
  · 商品: 上下架, 图片, 价格, 颜色×尺码 批量生成 SKU, 改库存
  · 分类: 增删改排序
登录: HTTP Basic, 用户名随意, 密码 = 环境变量 SHOP_ADMIN_PASSWORD
"""
import os
import secrets
from urllib.parse import quote
from datetime import datetime, timezone
from html import escape as E

from fastapi import FastAPI, Depends, Form, UploadFile, File, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select, func

from .db import get_session, init_db
from .models import Category, Product, Variant, Order, CartItem, OrderItem
from . import store as S
from . import notify

SHOP_NAME = os.getenv("SHOP_NAME", "服装店")
ADMIN_PW = os.getenv("SHOP_ADMIN_PASSWORD", "")
LOW_STOCK = int(os.getenv("LOW_STOCK", "2"))
MAX_IMAGE = 5 * 1024 * 1024

app = FastAPI(title=f"{SHOP_NAME}后台")
security = HTTPBasic()


@app.on_event("startup")
def _startup():
    if not ADMIN_PW:
        raise RuntimeError("缺 SHOP_ADMIN_PASSWORD")
    init_db()


def auth(cred: HTTPBasicCredentials = Depends(security)):
    if not secrets.compare_digest(cred.password.encode(), ADMIN_PW.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "密码错误",
                            {"WWW-Authenticate": "Basic"})
    return cred.username


def db():
    s = get_session()
    try:
        yield s
    finally:
        s.close()


_BASE = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'><title>__TITLE__ · __SHOP__</title>
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#F6F3EE;color:#2B2622}
.top{background:#2B2622;color:#fff;padding:13px 20px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.top b{font-size:17px;margin-right:6px}
.top a{color:#D9CFC1;text-decoration:none;font-size:14px}
.top a.on{color:#fff;font-weight:700}
.wrap{max-width:980px;margin:18px auto;padding:0 14px}
.card{background:#fff;border-radius:10px;padding:18px;margin-bottom:16px;overflow-x:auto}
h2{font-size:15px;margin:0 0 12px}
.kpi{display:flex;gap:10px;flex-wrap:wrap}
.kpi>a{flex:1;min-width:110px;background:#F6F3EE;border-radius:8px;padding:12px 8px;text-align:center;text-decoration:none;color:inherit}
.kpi .n{font-size:21px;font-weight:700}
.kpi .l{font-size:12px;color:#888;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#2B2622;color:#fff;padding:8px;text-align:left;font-weight:600}
td{padding:7px 8px;border-bottom:1px solid #EAE3D9;vertical-align:middle}
.r{text-align:right}
.btn{display:inline-block;padding:7px 14px;background:#2B2622;color:#fff;border:none;border-radius:6px;text-decoration:none;font-size:13px;cursor:pointer}
.btn.g{background:#2E7D52}.btn.x{background:#9c9a92}.btn.red{background:#B03A2E}
input,select,textarea{font:inherit;padding:6px 8px;border:1px solid #D9CFC1;border-radius:6px;box-sizing:border-box}
textarea{width:100%;min-height:80px}
label{display:block;font-size:12px;color:#777;margin:10px 0 4px}
.muted{color:#888;font-size:13px}
.bd{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;background:#EEE;white-space:nowrap}
.bd.待付款{background:#FFF4D6;color:#8A6200}.bd.待确认{background:#FDECEA;color:#B03A2E}
.bd.已付款{background:#E3F0FB;color:#1F5F99}.bd.已发货{background:#E8F6EF;color:#2E7D52}
.bd.已取消{color:#999}
.low{color:#B03A2E;font-weight:700}
.tabs a{margin-right:10px;font-size:13px;color:#555}.tabs a.on{font-weight:700;color:#000}
.thumb{width:44px;height:44px;object-fit:cover;border-radius:6px;background:#EEE}
.flash{background:#FFF4D6;border-radius:8px;padding:10px 14px;margin-bottom:14px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:end}
.row>div{flex:1;min-width:140px}
.row input,.row select{width:100%}
.row input[type=checkbox]{width:auto}
.vt input:not([type=checkbox]){width:90px}
</style></head><body>
<div class=top><b>__SHOP__ 后台</b>__NAV__</div><div class=wrap>__FLASH____BODY__</div></body></html>"""


def page(title, body, active="", flash=""):
    def lk(href, label, key):
        return f"<a class='{'on' if key == active else ''}' href='{href}'>{label}</a>"
    nav = (lk("/", "看板", "home") + lk("/orders", "订单", "orders")
           + lk("/products", "商品", "products") + lk("/categories", "分类", "cats"))
    fl = f"<div class=flash>{E(flash)}</div>" if flash else ""
    return HTMLResponse(_BASE.replace("__TITLE__", E(title)).replace("__SHOP__", E(SHOP_NAME))
                        .replace("__NAV__", nav).replace("__FLASH__", fl).replace("__BODY__", body))


def back(url, msg=""):
    if msg:
        url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
    return RedirectResponse(url, status_code=303)


def _badge(st):
    return f"<span class='bd {E(st)}'>{S.STATUS_ICON.get(st, '')} {E(st)}</span>"


def _dt(d):
    return d.strftime("%m-%d %H:%M") if d else ""


# ─────────────────────── 看板 ───────────────────────
@app.get("/", response_class=HTMLResponse)
def home(request: Request, s=Depends(db), _=Depends(auth)):
    cnt = dict(s.execute(select(Order.status, func.count()).group_by(Order.status)).all())
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    month0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sold = [S.PAID, S.SHIPPED, S.DONE]
    rev = s.scalar(select(func.coalesce(func.sum(Order.total), 0))
                   .where(Order.status.in_(sold), Order.created_at >= month0)) or 0
    n_month = s.scalar(select(func.count()).select_from(Order)
                       .where(Order.status.in_(sold), Order.created_at >= month0)) or 0
    kpi = "".join(
        f"<a href='{h}'><div class=n>{v}</div><div class=l>{l}</div></a>" for v, l, h in [
            (cnt.get(S.REVIEW, 0), "🧾 待确认收款", f"/orders?st={S.REVIEW}"),
            (cnt.get(S.PAID, 0), "💰 待发货", f"/orders?st={S.PAID}"),
            (cnt.get(S.PENDING, 0), "⏳ 待付款", f"/orders?st={S.PENDING}"),
            (n_month, "本月成交单", "/orders"),
            (S.money(rev), "本月营业额", "/orders"),
        ])
    lows = list(s.scalars(select(Variant).join(Product)
                          .where(Product.active, Variant.stock <= LOW_STOCK)
                          .order_by(Variant.stock, Product.id).limit(30)))
    low_rows = "".join(
        f"<tr><td><a href='/products/{v.product_id}'>{E(v.product.name)}</a></td><td>{E(v.label)}</td>"
        f"<td class='r low'>{v.stock}</td></tr>" for v in lows) or "<tr><td colspan=3 class=muted>库存都充足</td></tr>"
    todo = list(s.scalars(select(Order).where(Order.status.in_([S.REVIEW, S.PAID]))
                          .order_by(Order.id).limit(20)))
    body = (f"<div class=card><div class=kpi>{kpi}</div></div>"
            f"<div class=card><h2>要处理的订单</h2>{_orders_table(todo)}</div>"
            f"<div class=card><h2>低库存（≤{LOW_STOCK}）</h2><table><tr><th>商品</th><th>规格</th>"
            f"<th class=r>库存</th></tr>{low_rows}</table></div>")
    return page("看板", body, "home", request.query_params.get("msg", ""))


# ─────────────────────── 订单 ───────────────────────
def _orders_table(orders):
    if not orders:
        return "<p class=muted>没有订单</p>"
    rows = "".join(
        f"<tr><td><a href='/orders/{o.id}'>#{o.id}</a></td><td>{_dt(o.created_at)}</td>"
        f"<td>{E(o.name)}<div class=muted>{E(o.phone)}</div></td>"
        f"<td>{'<br>'.join(E(f'{i.product_name} {i.variant_label} ×{i.qty}') for i in o.items)}</td>"
        f"<td class=r>{S.money(o.total)}</td><td>{_badge(o.status)}</td></tr>" for o in orders)
    return (f"<table><tr><th>单号</th><th>时间(UTC)</th><th>收货人</th><th>商品</th>"
            f"<th class=r>金额</th><th>状态</th></tr>{rows}</table>")


@app.get("/orders", response_class=HTMLResponse)
def orders(request: Request, st: str = "", q: str = "", s=Depends(db), _=Depends(auth)):
    stmt = select(Order).order_by(Order.id.desc()).limit(200)
    if st in S.STATUSES:
        stmt = stmt.where(Order.status == st)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where((Order.name.ilike(like)) | (Order.phone.ilike(like))
                          | (Order.tracking_no.ilike(like))
                          | (Order.id == (int(q) if q.strip().isdigit() else -1)))
    tabs = "".join(f"<a class='{'on' if x == st else ''}' href='/orders?st={E(x)}'>{E(x or '全部')}</a>"
                   for x in [""] + S.STATUSES)
    search = (f"<form method=get class=row style='margin:12px 0'><input type=hidden name=st value='{E(st)}'>"
              f"<div><input name=q value='{E(q)}' placeholder='单号 / 姓名 / 电话 / 快递单号'></div>"
              f"<div style='flex:0'><button class=btn>搜索</button></div></form>")
    body = f"<div class=card><div class=tabs>{tabs}</div>{search}{_orders_table(list(s.scalars(stmt)))}</div>"
    return page("订单", body, "orders", request.query_params.get("msg", ""))


@app.get("/orders/{oid}", response_class=HTMLResponse)
def order_detail(oid: int, request: Request, s=Depends(db), _=Depends(auth)):
    o = s.get(Order, oid)
    if not o:
        raise HTTPException(404)
    c = o.customer
    tg = f"@{E(c.tg_username)}" if c.tg_username else E(c.tg_name or "")
    items = "".join(f"<tr><td>{E(i.product_name)}</td><td>{E(i.variant_label)}</td><td class=r>{S.money(i.unit_price)}</td>"
                    f"<td class=r>{i.qty}</td><td class=r>{S.money(i.unit_price * i.qty)}</td></tr>" for i in o.items)
    acts = []

    def btn(action, label, cls="", confirm=""):
        js = f" onclick=\"return confirm('{confirm}')\"" if confirm else ""
        return (f"<form method=post action='/orders/{o.id}/{action}' style='display:inline'>"
                f"<button class='btn {cls}'{js}>{label}</button></form> ")
    if o.status == S.REVIEW:
        acts += [btn("confirm", "✅ 确认收款", "g"), btn("reject", "❌ 驳回截图", "x")]
    if o.status == S.PENDING:
        acts.append(btn("confirm", "✅ 已收到款（无截图直接确认）", "g", "确定已收到款？"))
    if o.status == S.PAID:
        acts.append(f"<form method=post action='/orders/{o.id}/ship' class=row style='margin:8px 0'>"
                    f"<div><input name=tracking_no placeholder='快递公司 + 单号'></div>"
                    f"<div style='flex:0'><button class='btn g'>🚚 发货并通知顾客</button></div></form>")
    if o.status == S.SHIPPED:
        acts.append(btn("complete", "✅ 标记完成"))
    if o.status in (S.PENDING, S.REVIEW, S.PAID):
        acts.append(btn("cancel", "取消订单（退库存）", "red", "确定取消？库存会退回，已付款需线下退款。"))
    proof = ""
    if o.payment_file_id:
        proof = (f"<div class=card><h2>付款截图</h2><a href='/orders/{o.id}/proof' target=_blank>"
                 f"<img src='/orders/{o.id}/proof' style='max-width:320px;border-radius:8px'></a></div>")
    body = (f"<div class=card><h2>订单 #{o.id} {_badge(o.status)}</h2>"
            f"<p class=muted>下单 {_dt(o.created_at)} · 更新 {_dt(o.updated_at)} (UTC) · 顾客 TG {tg}</p>"
            f"<table><tr><th>商品</th><th>规格</th><th class=r>单价</th><th class=r>数量</th><th class=r>小计</th></tr>{items}"
            f"<tr><td colspan=4 class=r>运费</td><td class=r>{S.money(o.shipping)}</td></tr>"
            f"<tr><td colspan=4 class=r><b>合计</b></td><td class=r><b>{S.money(o.total)}</b></td></tr></table>"
            f"<p><b>{E(o.name)}</b> {E(o.phone)}<br>{E(o.address)}</p>"
            + (f"<p>顾客备注：{E(o.note)}</p>" if o.note else "")
            + (f"<p>快递单号：{E(o.tracking_no)}</p>" if o.tracking_no else "")
            + f"<div>{''.join(acts)}</div></div>{proof}"
            f"<div class=card><h2>店内备注</h2><form method=post action='/orders/{o.id}/note'>"
            f"<textarea name=admin_note>{E(o.admin_note or '')}</textarea>"
            f"<p><button class=btn>保存备注</button></p></form></div>")
    return page(f"订单 #{o.id}", body, "orders", request.query_params.get("msg", ""))


@app.get("/orders/{oid}/proof")
def order_proof(oid: int, s=Depends(db), _=Depends(auth)):
    o = s.get(Order, oid)
    data = notify.download_file(o.payment_file_id) if o else None
    if not data:
        raise HTTPException(404, "截图取不到（检查 SHOP_BOT_TOKEN）")
    return Response(data, media_type="image/jpeg")


@app.post("/orders/{oid}/{action}")
def order_action(oid: int, action: str, tracking_no: str = Form(""), admin_note: str = Form(""),
                 s=Depends(db), _=Depends(auth)):
    o = s.get(Order, oid)
    if not o:
        raise HTTPException(404)
    tell = None
    try:
        if action == "confirm":
            if o.status == S.PENDING:          # 线下收款, 没截图
                S.attach_payment(o, None)
            S.confirm_payment(o)
            tell = f"💰 订单 #{o.id} 已确认收款，我们会尽快发货！"
        elif action == "reject":
            S.reject_payment(o)
            tell = f"⚠️ 订单 #{o.id} 的付款截图没有核对上，请检查后重新发送截图，或联系客服。"
        elif action == "ship":
            S.ship(o, tracking_no)
            tell = f"🚚 订单 #{o.id} 已发货！" + (f"\n快递单号：{o.tracking_no}" if o.tracking_no else "")
        elif action == "complete":
            S.complete(o)
        elif action == "cancel":
            S.cancel(s, o)
            tell = f"订单 #{o.id} 已取消。如有疑问请联系客服。"
        elif action == "note":
            o.admin_note = admin_note.strip() or None
        else:
            raise HTTPException(404)
        s.commit()
    except S.ShopError as e:
        s.rollback()
        return back(f"/orders/{oid}", str(e))
    msg = "已保存"
    if tell:
        ok = notify.send_message(o.customer.tg_user_id, tell)
        msg = "已保存，已通知顾客" if ok else "已保存（通知顾客失败）"
    return back(f"/orders/{oid}", msg)


# ─────────────────────── 商品 ───────────────────────
@app.get("/products", response_class=HTMLResponse)
def products(request: Request, s=Depends(db), _=Depends(auth)):
    prods = list(s.scalars(select(Product).order_by(Product.active.desc(), Product.id.desc())))
    rows = "".join(
        f"<tr><td>{'<img class=thumb src=/products/%d/image>' % p.id if p.image else '<div class=thumb></div>'}</td>"
        f"<td><a href='/products/{p.id}'>{E(p.name)}</a>{'' if p.active else ' <span class=bd>已下架</span>'}</td>"
        f"<td>{E(p.category.name) if p.category else '—'}</td><td class=r>{S.money(p.price)}</td>"
        f"<td class=r>{len(p.variants)}</td>"
        f"<td class='r {'low' if sum(v.stock for v in p.variants) <= LOW_STOCK else ''}'>{sum(v.stock for v in p.variants)}</td></tr>"
        for p in prods)
    body = (f"<div class=card><p><a class='btn g' href='/products/new'>＋ 新增商品</a></p>"
            f"<table><tr><th></th><th>商品</th><th>分类</th><th class=r>价格</th><th class=r>规格数</th>"
            f"<th class=r>总库存</th></tr>{rows or '<tr><td colspan=6 class=muted>还没有商品</td></tr>'}</table></div>")
    return page("商品", body, "products", request.query_params.get("msg", ""))


def _cat_options(s, selected=None):
    cats = list(s.scalars(select(Category).order_by(Category.sort, Category.id)))
    return "<option value=''>（不分类）</option>" + "".join(
        f"<option value={c.id} {'selected' if c.id == selected else ''}>{E(c.name)}</option>" for c in cats)


def _product_form(s, p=None):
    p_price = f"{p.price / 100:.2f}" if p else ""
    return (f"<form method=post enctype=multipart/form-data action='{'/products/%d' % p.id if p else '/products/new'}'>"
            f"<div class=row><div><label>名称</label><input name=name required value='{E(p.name) if p else ''}'></div>"
            f"<div><label>分类</label><select name=category_id>{_cat_options(s, p.category_id if p else None)}</select></div>"
            f"<div><label>价格（{E(S.CURRENCY)}）</label><input name=price required value='{p_price}'></div></div>"
            f"<label>介绍（面料、尺码建议等）</label><textarea name=description>{E(p.description or '') if p else ''}</textarea>"
            f"<div class=row><div><label>主图（jpg/png，≤5MB{'，不选则不改' if p else ''}）</label>"
            f"<input type=file name=image accept='image/*'></div>"
            f"<div><label><input type=checkbox name=active value=1 {'checked' if (p is None or p.active) else ''}> 上架</label></div></div>"
            f"<p><button class='btn g'>保存</button></p></form>")


@app.get("/products/new", response_class=HTMLResponse)
def product_new(s=Depends(db), _=Depends(auth)):
    return page("新增商品", f"<div class=card><h2>新增商品</h2>{_product_form(s)}"
                          f"<p class=muted>保存后再添加颜色/尺码和库存。</p></div>", "products")


async def _save_product(s, p, name, category_id, price, description, active, image):
    p.name = name.strip()[:100]
    p.category_id = int(category_id) if category_id else None
    p.price = S.parse_money(price)
    p.description = description.strip() or None
    p.active = bool(active)
    if image is not None and image.filename:
        data = await image.read()
        if len(data) > MAX_IMAGE:
            raise S.ShopError("图片超过 5MB")
        if not (image.content_type or "").startswith("image/"):
            raise S.ShopError("只能传图片")
        p.image, p.image_type, p.image_file_id = data, image.content_type, None


@app.post("/products/new")
async def product_create(name: str = Form(...), category_id: str = Form(""), price: str = Form(...),
                         description: str = Form(""), active: str = Form(""),
                         image: UploadFile | None = File(None), s=Depends(db), _=Depends(auth)):
    p = Product()
    try:
        await _save_product(s, p, name, category_id, price, description, active, image)
    except S.ShopError as e:
        return back("/products/new", str(e))
    s.add(p)
    s.commit()
    return back(f"/products/{p.id}", "已创建，接着添加颜色/尺码")


@app.get("/products/{pid}", response_class=HTMLResponse)
def product_edit(pid: int, request: Request, s=Depends(db), _=Depends(auth)):
    p = s.get(Product, pid)
    if not p:
        raise HTTPException(404)
    img = f"<img src='/products/{p.id}/image' style='max-width:200px;border-radius:8px'>" if p.image else ""
    vrows = "".join(
        f"<tr><td><input name=color_{v.id} value='{E(v.color)}' size=8></td>"
        f"<td><input name=size_{v.id} value='{E(v.size)}' size=6></td>"
        f"<td><input name=stock_{v.id} value='{v.stock}' size=5 type=number min=0 class='{'low' if v.stock <= LOW_STOCK else ''}'></td>"
        f"<td><input name=price_{v.id} value='{f'{v.price / 100:.2f}' if v.price is not None else ''}' size=7 placeholder='同款式价'></td>"
        f"<td><label style='margin:0'><input type=checkbox name=del_{v.id} value=1> 删</label></td></tr>"
        for v in p.variants)
    variants = (f"<form method=post action='/products/{p.id}/variants'>"
                f"<table class=vt><tr><th>颜色</th><th>尺码</th><th>库存</th><th>单独定价</th><th></th></tr>"
                f"{vrows or '<tr><td colspan=5 class=muted>还没有规格，用下面批量生成</td></tr>'}</table>"
                f"<p><button class='btn g'>保存规格/库存</button></p></form>")
    bulk = (f"<form method=post action='/products/{p.id}/bulk' class=row>"
            f"<div><label>颜色（逗号分隔）</label><input name=colors placeholder='黑色, 白色, 杏色'></div>"
            f"<div><label>尺码（逗号分隔）</label><input name=sizes placeholder='S, M, L, XL'></div>"
            f"<div><label>每个规格初始库存</label><input name=stock type=number min=0 value=10></div>"
            f"<div style='flex:0'><button class=btn>生成</button></div></form>"
            f"<p class=muted>颜色或尺码可以只填一个。已存在的组合会跳过。</p>")
    body = (f"<div class=card><h2>{E(p.name)}</h2>{img}{_product_form(s, p)}</div>"
            f"<div class=card><h2>颜色 × 尺码 · 库存</h2>{variants}</div>"
            f"<div class=card><h2>批量添加规格</h2>{bulk}</div>"
            f"<div class=card><form method=post action='/products/{p.id}/delete' "
            f"onsubmit=\"return confirm('删除后不可恢复（历史订单不受影响）。只想暂停售卖请取消「上架」。确定删除？')\">"
            f"<button class='btn red'>删除商品</button></form></div>")
    return page(p.name, body, "products", request.query_params.get("msg", ""))


@app.post("/products/{pid}")
async def product_update(pid: int, name: str = Form(...), category_id: str = Form(""), price: str = Form(...),
                         description: str = Form(""), active: str = Form(""),
                         image: UploadFile | None = File(None), s=Depends(db), _=Depends(auth)):
    p = s.get(Product, pid)
    if not p:
        raise HTTPException(404)
    try:
        await _save_product(s, p, name, category_id, price, description, active, image)
    except S.ShopError as e:
        s.rollback()
        return back(f"/products/{pid}", str(e))
    s.commit()
    return back(f"/products/{pid}", "已保存")


@app.get("/products/{pid}/image")
def product_image(pid: int, s=Depends(db), _=Depends(auth)):
    p = s.get(Product, pid)
    if not p or not p.image:
        raise HTTPException(404)
    return Response(p.image, media_type=p.image_type or "image/jpeg")


@app.post("/products/{pid}/variants")
async def variants_save(pid: int, request: Request, s=Depends(db), _=Depends(auth)):
    p = s.get(Product, pid)
    if not p:
        raise HTTPException(404)
    f = await request.form()
    try:
        for v in list(p.variants):
            if f.get(f"del_{v.id}"):
                s.query(CartItem).filter_by(variant_id=v.id).delete()
                s.query(OrderItem).filter_by(variant_id=v.id).update({OrderItem.variant_id: None})
                p.variants.remove(v)
                continue
            v.color = str(f.get(f"color_{v.id}", v.color)).strip()[:30]
            v.size = str(f.get(f"size_{v.id}", v.size)).strip()[:20]
            stock = str(f.get(f"stock_{v.id}", v.stock)).strip()
            if not stock.lstrip("-").isdigit() or int(stock) < 0:
                raise S.ShopError(f"库存要填 ≥0 的整数：{v.label}")
            v.stock = int(stock)
            price = str(f.get(f"price_{v.id}", "")).strip()
            v.price = S.parse_money(price) if price else None
        s.commit()
    except S.ShopError as e:
        s.rollback()
        return back(f"/products/{pid}", str(e))
    except Exception:
        s.rollback()
        return back(f"/products/{pid}", "保存失败：同一商品里 颜色+尺码 不能重复")
    return back(f"/products/{pid}", "规格已保存")


def _split(text):
    return [x.strip() for x in text.replace("，", ",").replace("、", ",").split(",") if x.strip()]


@app.post("/products/{pid}/bulk")
def variants_bulk(pid: int, colors: str = Form(""), sizes: str = Form(""), stock: int = Form(0),
                  s=Depends(db), _=Depends(auth)):
    p = s.get(Product, pid)
    if not p:
        raise HTTPException(404)
    cs, zs = _split(colors) or [""], _split(sizes) or [""]
    if cs == [""] and zs == [""]:
        return back(f"/products/{pid}", "颜色和尺码至少填一个")
    have = {(v.color, v.size) for v in p.variants}
    n, base = 0, max((v.sort for v in p.variants), default=0)
    for c in cs:
        for z in zs:
            if (c, z) in have:
                continue
            n += 1
            p.variants.append(Variant(color=c[:30], size=z[:20], stock=max(stock, 0), sort=base + n))
    s.commit()
    return back(f"/products/{pid}", f"新增 {n} 个规格")


@app.post("/products/{pid}/delete")
def product_delete(pid: int, s=Depends(db), _=Depends(auth)):
    p = s.get(Product, pid)
    if not p:
        raise HTTPException(404)
    vids = [v.id for v in p.variants]
    if vids:
        s.query(CartItem).filter(CartItem.variant_id.in_(vids)).delete(synchronize_session=False)
        s.query(OrderItem).filter(OrderItem.variant_id.in_(vids)) \
            .update({OrderItem.variant_id: None}, synchronize_session=False)
    s.delete(p)
    s.commit()
    return back("/products", "已删除")


# ─────────────────────── 分类 ───────────────────────
@app.get("/categories", response_class=HTMLResponse)
def categories(request: Request, s=Depends(db), _=Depends(auth)):
    cats = list(s.scalars(select(Category).order_by(Category.sort, Category.id)))
    rows = "".join(
        f"<tr><form method=post action='/categories/{c.id}'><td><input name=name value='{E(c.name)}'></td>"
        f"<td><input name=sort type=number value={c.sort} style='width:70px'></td><td class=r>{len(c.products)}</td>"
        f"<td><button class=btn>保存</button> <button class='btn red' name=delete value=1 "
        f"onclick=\"return confirm('删除分类？里面的商品会变成「不分类」')\">删除</button></td></form></tr>"
        for c in cats)
    body = (f"<div class=card><h2>分类</h2><table><tr><th>名称</th><th>排序（小的在前）</th><th class=r>商品数</th><th></th></tr>"
            f"{rows or '<tr><td colspan=4 class=muted>还没有分类</td></tr>'}</table></div>"
            f"<div class=card><h2>新增分类</h2><form method=post action='/categories/new' class=row>"
            f"<div><input name=name required placeholder='例如：上衣'></div>"
            f"<div style='flex:0'><button class='btn g'>添加</button></div></form></div>")
    return page("分类", body, "cats", request.query_params.get("msg", ""))


@app.post("/categories/new")
def category_create(name: str = Form(...), s=Depends(db), _=Depends(auth)):
    name = name.strip()[:50]
    if not name or s.scalar(select(Category).where(Category.name == name)):
        return back("/categories", "名称为空或已存在")
    top = s.scalar(select(func.max(Category.sort))) or 0
    s.add(Category(name=name, sort=top + 1))
    s.commit()
    return back("/categories", "已添加")


@app.post("/categories/{cid}")
def category_update(cid: int, name: str = Form(""), sort: int = Form(0), delete: str = Form(""),
                    s=Depends(db), _=Depends(auth)):
    c = s.get(Category, cid)
    if not c:
        raise HTTPException(404)
    if delete:
        for p in c.products:
            p.category_id = None
        s.delete(c)
        s.commit()
        return back("/categories", "已删除")
    name = name.strip()[:50]
    dup = s.scalar(select(Category).where(Category.name == name, Category.id != cid))
    if not name or dup:
        return back("/categories", "名称为空或已存在")
    c.name, c.sort = name, sort
    s.commit()
    return back("/categories", "已保存")
