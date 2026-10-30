"""
C组 审核后台 (FastAPI)
  · 看板: 按月看业绩/各方净/艺人应发, 三轨待清(妈咪欠款/代收待发/自留待扣) + 一键结清妈咪
  · 审核队列: 机器解析的报单 看原文 → 改 → 确认入库 / 拒; 财务群消息 → 转财务流水
  · 订单: 搜索/按月/按状态筛, 改单 / 作废 / 恢复, 两轨结款一键切换, 导出 Excel
  · 财务流水 (web/finance.py) · 字典管理 (web/dicts.py) · 操作日志 · 导入旧主文件
登录: HTTP Basic, 用户名 ADMIN_USER(默认 admin), 密码 = 环境变量 ADMIN_PASSWORD
"""
import io
import json
import os
import tempfile
from datetime import date, datetime
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, Depends, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import or_

from ..db.session import get_session, init_db
from ..db.models import (Order, Artist, Mama, Venue, ReviewItem, OperationLog,
                         MamaSettlement)
from ..core import status as ST
from ..core.intake import create_order_from_payload, IntakeError
from ..core.names import find_artist, find_mama, find_venue
from ..core.queries import dashboard_stats, parse_month, month_range, calc
from ..core.settlement import RATIOS, FLOW_CASH_KEPT
from .ui import (auth, esc, fmt, page, card, msg_box, sel, inp, datalist, form_table,
                 month_picker)
from . import dicts, finance

app = FastAPI(title="C组审核后台")
app.include_router(dicts.router)
app.include_router(finance.router)

MODES = list(RATIOS)
FLOWS = [""] + list(FLOW_CASH_KEPT)
PAGE_SIZE = 60


@app.on_event("startup")
def _startup():
    init_db()


def _back(request: Request, default="/orders"):
    """回到来源页 (只认本站路径, 防跳外站)。"""
    ref = request.headers.get("referer") or ""
    u = urlparse(ref)
    if u.path.startswith("/") and (not u.netloc or u.netloc == request.url.netloc):
        return u.path + (f"?{u.query}" if u.query else "")
    return default


def _ym_arg(m):
    """URL 参数 m → (year, month) | None(全部)。默认当月。"""
    if m == "all":
        return None
    today = date.today()
    return parse_month(m, (today.year, today.month)) if m else (today.year, today.month)


def _names(s, model):
    return {x.id: x.name for x in s.query(model).all()}


# ───────────────────────── 看板 ─────────────────────────
@app.get("/", response_class=HTMLResponse)
def dashboard(user=Depends(auth), m: str = ""):
    s = get_session()
    try:
        if not s.query(Order).count():
            return page("看板", card("还没数据。先去 <a href='/upload'>导入数据</a> 上传旧主文件。"), "home")
        ym = _ym_arg(m)
        st = dashboard_stats(s, *(ym or (None, None)))
        art, mam = _names(s, Artist), _names(s, Mama)
        label = f"{ym[0]}年{ym[1]}月" if ym else "全部"

        kpi = ("<div class=kpi>"
               f"<div><div class=n>{st['n']}</div><div class=l>单数</div></div>"
               f"<div><div class=n>{fmt(st['SK'])}</div><div class=l>挂账</div></div>"
               f"<div><div class=n>{fmt(st['SM'])}</div><div class=l>现金</div></div>"
               f"<div><div class=n>{fmt(st['SO'])}</div><div class=l>门票</div></div></div>"
               "<div class=kpi style='margin-top:10px'>"
               f"<div><div class=n>{fmt(st['artist'])}</div><div class=l>艺人净</div></div>"
               f"<div><div class=n>{fmt(st['mama'])}</div><div class=l>妈咪净</div></div>"
               f"<div><div class=n>{fmt(st['company'])}</div><div class=l>公司净</div></div>"
               f"<div><div class=n>{fmt(st['expense'])}</div><div class=l>运营支出</div></div>"
               f"<div><div class='n {'neg' if st['profit'] < 0 else ''}'>{fmt(st['profit'])}</div>"
               f"<div class=l>公司净 − 支出</div></div></div>")
        head = (f"<h2>总览 · {label}</h2>" + month_picker("/", f"{ym[0]}-{ym[1]:02d}" if ym else "")
                + kpi)
        if st["pending"]:
            head += (f"<p class=muted style='margin-top:12px'>🔔 审核队列有 <b>{st['pending']}</b> 条待处理"
                     " → <a href='/review'>去审核</a></p>")

        wrows = "".join(
            f"<tr><td>{esc(art.get(k, k))}</td><td class='r {'neg' if v < 0 else ''}'>{fmt(v)}</td></tr>"
            for k, v in sorted(st["wages"].items(), key=lambda x: -x[1]))
        wtab = (f"<h2>艺人应发 · {label}（月底应结，负=倒扣；未扣预支/罚款）</h2>"
                f"<table><tr><th>艺人</th><th class=r>应结(MYR)</th></tr>"
                f"{wrows or '<tr><td colspan=2 class=muted>这段时间没单</td></tr>'}</table>")

        rrows = ""
        for k, v in sorted(st["due_mama"].items(), key=lambda x: -x[1]):
            if abs(v) <= 0.5:
                continue
            rrows += (f"<tr><td>{esc(mam.get(k, k))}</td><td class=r>{fmt(v)}</td>"
                      f"<td><a class='btn s' href='/orders?{urlencode({'mama': mam.get(k, ''), 'credit': 'due', 'm': 'all'})}'>明细</a> "
                      f"<form method=post action='/mama/{k}/settle_all' style='display:inline'>"
                      f"<button class='btn s g' onclick=\"return confirm('把 {esc(mam.get(k, ''))} 名下全部待结挂账标为已结，并记一笔妈咪结款 {fmt(v)}？')\">全部结清</button>"
                      f"</form></td></tr>")
        rtab = (f"<h2>① 待妈咪结款 · 应收账（全部时间）</h2>"
                f"<table><tr><th>妈咪</th><th class=r>待结C组(MYR)</th><th></th></tr>"
                f"{rrows or '<tr><td colspan=3 class=muted>全部已结</td></tr>'}</table>")

        cash_tracks = (
            "<h2>现金两轨待清（全部时间）</h2><div class=kpi>"
            f"<div><div class=n>{st['col_cnt']}</div><div class=l>② 公司代收待发<br>{fmt(st['col_sum'])} 现金</div></div>"
            f"<div><div class=n>{st['sk_cnt']}</div><div class=l>③ 艺人自留待倒扣<br>{fmt(st['sk_sum'])} 现金</div></div>"
            f"<div><div class=n>{fmt(st['adv_due'])}</div><div class=l>预支/罚款待扣</div></div>"
            "</div><p class=muted style='margin-top:8px'>② 公司已收的现金，待工资发放给艺人/妈咪。"
            "③ 艺人现场已拿，月底从工资倒扣公司+妈咪份。</p>")

        return page("看板", card(head) + card(rtab) + card(cash_tracks) + card(wtab), "home")
    finally:
        s.close()


@app.post("/mama/{mid}/settle_all")
def mama_settle_all(mid: int, request: Request, user=Depends(auth)):
    """妈咪一次结清: 名下所有 待结/部分 挂账 → 已结, 并记一笔妈咪结款流水。"""
    s = get_session()
    try:
        mama = s.get(Mama, mid)
        if mama:
            ref = f"SETTLE-{datetime.now():%Y%m%d%H%M%S}"
            total, n = 0.0, 0
            for o in (s.query(Order).filter(Order.mama_id == mid, Order.status == "已审核",
                                            Order.credit_status.in_([ST.CREDIT_DUE, ST.CREDIT_PART]))):
                total += calc(o).mama_receivable
                o.credit_status = ST.CREDIT_PAID
                o.credit_paid_date = date.today()
                o.credit_ref = ref
                n += 1
            if n:
                s.add(MamaSettlement(mama_id=mid, period="全部待结", amount=round(total, 2),
                                     settled_at=date.today(), notes=f"{ref} · {n}单一键结清"))
                s.add(OperationLog(action="妈咪结清", target=f"mama#{mid}", operator=user,
                                   detail=f"{mama.name} {n}单 {total:.0f} {ref}"))
                s.commit()
    finally:
        s.close()
    return RedirectResponse(_back(request, "/"), status_code=303)


# ───────────────────────── 导入数据 ─────────────────────────
@app.get("/upload", response_class=HTMLResponse)
def upload_form(user=Depends(auth)):
    s = get_session()
    try:
        n = s.query(Order).count()
    finally:
        s.close()
    note = f"<p class=muted>当前库里已有 {n} 单。重复上传只补新内容（已有的跳过）。</p>" if n else ""
    body = card(
        "<h2>导入旧主文件</h2>"
        "<p class=muted>上传 C组数据工作流 .xlsx，自动把字典 + 历史报单导进库。</p>"
        + note +
        "<form action='/upload' method='post' enctype='multipart/form-data' style='margin-top:14px'>"
        "<input type='file' name='file' accept='.xlsx' required> "
        "<button class='btn' type='submit'>上传并导入</button></form>")
    return page("导入数据", body, "upload")


@app.post("/upload", response_class=HTMLResponse)
def upload_do(user=Depends(auth), file: UploadFile = File(...)):
    from ..db.migrate import migrate
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file.file.read())
        migrate(tmp)
    except Exception as e:
        return page("导入数据", card(f"<h2>导入出错</h2><p class=muted>{esc(e)}</p>"
                                     "<a class=btn href='/upload'>返回</a>"), "upload")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    s = get_session()
    try:
        n = s.query(Order).count()
        s.add(OperationLog(action="导入主文件", target=(file.filename or "")[:60],
                           detail=f"库内共{n}单", operator=user))
        s.commit()
    finally:
        s.close()
    return page("导入数据", card(f"<h2>✅ 导入完成</h2><p class=muted>库内共 {n} 单。</p>"
                                 "<a class=btn href='/'>去看板</a>"), "upload")


# ───────────────────────── 审核队列 ─────────────────────────
def _payload(it):
    try:
        return json.loads(it.parsed_json or "{}")
    except Exception:
        return {}


@app.get("/review", response_class=HTMLResponse)
def review_list(user=Depends(auth), show: str = "待审"):
    s = get_session()
    try:
        q = s.query(ReviewItem)
        if show in ("待审", "已确认", "已拒"):
            q = q.filter_by(status=show)
        items = q.order_by(ReviewItem.id.desc() if show != "待审" else ReviewItem.id).limit(200).all()
        tabs = " ".join(f"<a class='btn s {'' if show == t else 'x'}' href='/review?show={t}'>{t}</a>"
                        for t in ("待审", "已确认", "已拒"))
        if not items:
            return page("审核队列", card(f"<h2>审核队列</h2>{tabs}<p class=muted>暂无。"
                                         "机器人解析报单后会出现在这里。</p>"), "review")
        rows = ""
        for it in items:
            d = _payload(it)
            warn = f"<span class=warn>{esc(it.parse_warnings)}</span>" if it.parse_warnings else ""
            if it.source_group == "财务群" or not d:
                summary = f"<span class=muted>原文：</span>{esc((it.raw_message or '')[:120])}"
                if it.source_group == "财务群":
                    act = f"<a class='btn g' href='/finance/new?review={it.id}'>录成流水</a> "
                else:
                    act = f"<a class='btn' href='/review/{it.id}'>手工录单</a> "
            else:
                summary = (f"{esc(d.get('日期') or '?')} · {esc(d.get('艺人') or '?')} · "
                           f"{esc(d.get('场所') or '?')} {esc(d.get('包厢') or '')} · "
                           f"妈咪{esc(d.get('妈咪') or '-')} · 挂{esc(d.get('K', 0))}/现{esc(d.get('M', 0))}"
                           f"/票{esc(d.get('O', 0))} · {esc(d.get('客人') or '-')}")
                act = (f"<form style='display:inline' action='/review/{it.id}/confirm' method='post'>"
                       f"<button class='btn g'>确认入库</button></form> "
                       f"<a class='btn' href='/review/{it.id}'>改</a> ")
            if it.status == "待审":
                act += (f"<form style='display:inline' action='/review/{it.id}/reject' method='post'>"
                        f"<button class='btn x'>拒</button></form>")
            else:
                act = f"<span class=muted>{esc(it.status)}</span>"
            rows += (f"<tr><td>#{it.id}<br><span class=muted>{esc(it.source_group)}"
                     f"<br>{esc(it.tg_sender or '')}</span></td>"
                     f"<td>{summary}{warn}</td><td style='white-space:nowrap'>{act}</td></tr>")
        body = (f"<h2>审核队列（{len(items)} 条{esc(show)}）</h2>{tabs}"
                f"<table style='margin-top:10px'><tr><th>来源</th><th>解析结果</th><th>操作</th></tr>{rows}</table>")
        return page("审核队列", card(body), "review")
    finally:
        s.close()


REVIEW_FIELDS = [("日期", "2026-06-20"), ("艺人", ""), ("场所", ""), ("包厢", ""), ("妈咪", "留空=自单"),
                 ("助理", ""), ("客人", ""), ("K", "挂账"), ("M", "现金"), ("O", "门票"),
                 ("工价", "小费单才填"), ("上班", "22:00"), ("下班", "02:00"), ("备注", "")]
LABELS = {"K": "挂账 K", "M": "现金 M", "O": "门票 O"}


def _review_form(s, it, d, err=None):
    arts = [a.name for a in s.query(Artist).filter(Artist.active.is_(True)).order_by(Artist.name)]
    mams = [m.name for m in s.query(Mama).order_by(Mama.name)]
    vens = [v.name for v in s.query(Venue).order_by(Venue.name)]
    lists = {"艺人": "dl-art", "妈咪": "dl-mama", "场所": "dl-ven"}
    rows = []
    for key, ph in REVIEW_FIELDS:
        extra = f"list='{lists[key]}'" if key in lists else ""
        rows.append((LABELS.get(key, key), inp(key, d.get(key), ph, extra=extra)))
        if key == "助理":
            rows.append(("分成档", sel("合作模式", [""] + MODES, d.get("合作模式") or "")
                         + " <span class=muted>(留空=有妈咪标准/无妈咪自单)</span>"))
            rows.append(("现金流向", sel("流向", FLOWS, d.get("流向") or "")))
    warn = f"<div class=warn style='margin-bottom:10px'>{esc(it.parse_warnings)}</div>" if it.parse_warnings else ""
    body = (f"<h2>审核 #{it.id} · {esc(it.source_group)} · {esc(it.status)}</h2>" + msg_box(err=err) + warn
            + f"<h3>群原文</h3><div class=raw>{esc(it.raw_message)}</div><h3>解析结果（可改）</h3>"
            + f"<form method=post action='/review/{it.id}/confirm'>"
            + form_table(rows)
            + datalist("dl-art", arts) + datalist("dl-mama", mams) + datalist("dl-ven", vens)
            + "<input type=hidden name=edited value=1><div style='margin-top:14px'>"
            + ("<button class='btn g'>保存并入库</button> " if it.status == "待审" else "")
            + "<a class='btn x' href='/review'>返回</a></div></form>")
    return page("审核", card(body), "review")


@app.get("/review/{item_id}", response_class=HTMLResponse)
def review_detail(item_id: int, user=Depends(auth)):
    s = get_session()
    try:
        it = s.get(ReviewItem, item_id)
        if not it:
            return page("审核", card("没找到这条。<a href='/review'>返回</a>"), "review")
        return _review_form(s, it, _payload(it))
    finally:
        s.close()


@app.post("/review/{item_id}/confirm", response_class=HTMLResponse)
async def review_confirm(item_id: int, request: Request, user=Depends(auth)):
    form = await request.form()
    s = get_session()
    try:
        it = s.get(ReviewItem, item_id)
        if not it or it.status != "待审":            # 防重复点 → 重复入库
            return RedirectResponse("/review", status_code=303)
        d = _payload(it)
        if form.get("edited"):
            d = {k: (str(form.get(k) or "").strip() or None) for k, _ in REVIEW_FIELDS}
            d["合作模式"] = form.get("合作模式") or None
            d["流向"] = form.get("流向") or None
        try:
            o = create_order_from_payload(d, s, source_msg_id=it.tg_msg_id)
        except IntakeError as e:
            s.rollback()
            return _review_form(s, it, d, err=str(e))
        it.parsed_json = json.dumps(d, ensure_ascii=False)
        it.status = "已确认"
        it.reviewed_at = datetime.now()
        s.flush()
        s.add(OperationLog(action="审核确认", target=f"review#{item_id}", operator=user,
                           detail=f"→order#{o.id}" + (" (改过)" if form.get("edited") else "")))
        s.commit()
    finally:
        s.close()
    return RedirectResponse("/review", status_code=303)


@app.post("/review/{item_id}/reject")
def review_reject(item_id: int, user=Depends(auth)):
    s = get_session()
    try:
        it = s.get(ReviewItem, item_id)
        if it and it.status == "待审":
            it.status = "已拒"
            it.reviewed_at = datetime.now()
            s.add(OperationLog(action="审核拒绝", target=f"review#{item_id}", operator=user))
            s.commit()
    finally:
        s.close()
    return RedirectResponse("/review", status_code=303)


@app.get("/health")
def health():
    s = get_session()
    try:
        return {"ok": True, "orders": s.query(Order).count(),
                "pending": s.query(ReviewItem).filter_by(status="待审").count()}
    finally:
        s.close()


# ───────────────────────── 订单 (列表 + 改单 + 作废 + 导出) ─────────────────────────
def credit_cell(o):
    """挂账态徽章 + 待结↔已结 一键切换。"""
    cs = o.credit_status
    if cs in (None, ST.CREDIT_NONE):  return "<span class=muted>—</span>"
    if cs == ST.CREDIT_DIRECT:        return "<span class='bd dir'>直结</span>"
    f = f"<form method=post action='/orders/{o.id}/credit_toggle' style='display:inline'>"
    if cs == ST.CREDIT_PAID:
        return f"<span class='bd ok'>已结</span> {f}<button class='tg u'>撤</button></form>"
    return f"<span class='bd due'>{esc(cs)}</span> {f}<button class='tg'>标已结</button></form>"


def cash_cell(o):
    """现金态徽章(待/已 + 代收/自留分支) + 一键切换。"""
    ms = o.cash_status
    if ms in (None, ST.CASH_NONE):  return "<span class=muted>—</span>"
    if ms == ST.CASH_DIRECT:        return "<span class='bd dir'>直结</span>"
    br = ST.cash_branch(o.flow)
    f = f"<form method=post action='/orders/{o.id}/cash_toggle' style='display:inline'>"
    if ms == ST.CASH_DONE:
        return f"<span class='bd ok'>已·{br}</span> {f}<button class='tg u'>撤</button></form>"
    return f"<span class='bd due'>待·{br}</span> {f}<button class='tg'>标已</button></form>"


def _orders_query(s, q="", m="", st="有效", credit="", mama=""):
    query = (s.query(Order)
             .outerjoin(Artist, Order.artist_id == Artist.id)
             .outerjoin(Mama, Order.mama_id == Mama.id)
             .outerjoin(Venue, Order.venue_id == Venue.id))
    if st == "有效":
        query = query.filter(Order.status == "已审核")
    elif st == "作废":
        query = query.filter(Order.status != "已审核")
    ym = _ym_arg(m)
    if ym:
        a, b = month_range(*ym)
        query = query.filter(Order.biz_date >= a, Order.biz_date <= b)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Artist.name.ilike(like), Mama.name.ilike(like), Venue.name.ilike(like),
                                 Order.customer.ilike(like), Order.booker.ilike(like),
                                 Order.remark.ilike(like), Order.room.ilike(like)))
    if mama:
        query = query.filter(Mama.name == mama)
    if credit == "due":
        query = query.filter(Order.credit_status.in_([ST.CREDIT_DUE, ST.CREDIT_PART]))
    elif credit == "cash_due":
        query = query.filter(Order.cash_status == ST.CASH_DUE)
    return query.order_by(Order.biz_date.desc().nullslast(), Order.id.desc())


@app.get("/orders", response_class=HTMLResponse)
def orders_list(user=Depends(auth), q: str = "", m: str = "all", st: str = "有效",
                credit: str = "", mama: str = "", p: int = 1):
    s = get_session()
    try:
        art, mam, ven = _names(s, Artist), _names(s, Mama), _names(s, Venue)
        query = _orders_query(s, q, m, st, credit, mama)
        total = query.count()
        p = max(1, p)
        rows = query.offset((p - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
        trs = ""
        for o in rows:
            style = " style='opacity:.45'" if o.status != "已审核" else ""
            trs += (f"<tr{style}><td>#{o.id}</td>"
                    f"<td>{o.biz_date.strftime('%m/%d') if o.biz_date else '—'}</td>"
                    f"<td>{esc(art.get(o.artist_id, '?'))}</td>"
                    f"<td>{esc(ven.get(o.venue_id, ''))} {esc(o.room or '')}</td>"
                    f"<td>{esc(mam.get(o.mama_id, '自单'))}</td>"
                    f"<td class=r>{fmt(o.credit_k)}</td><td class=r>{fmt(o.cash_m)}</td>"
                    f"<td class=r>{fmt(o.ticket_o)}</td>"
                    f"<td>{credit_cell(o)}</td><td>{cash_cell(o)}</td>"
                    f"<td>{esc(o.mode)}</td><td>{esc(o.customer or '—')}</td>"
                    f"<td><a class='btn s' href='/orders/{o.id}/edit'>改</a></td></tr>")
        args = dict(q=q, m=m, st=st, credit=credit, mama=mama)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        nav = f"<div class=pager><span class=muted>共 {total} 单 · 第 {p}/{pages} 页</span>"
        if p > 1:
            nav += f"<a class='btn s' href='/orders?{urlencode({**args, 'p': p - 1})}'>上一页</a>"
        if p < pages:
            nav += f"<a class='btn s' href='/orders?{urlencode({**args, 'p': p + 1})}'>下一页</a>"
        nav += f"<a class='btn s g' href='/export?{urlencode(args)}'>导出 Excel</a></div>"
        filt = (f"<form method=get class=inline style='margin-bottom:12px;display:flex;gap:6px;flex-wrap:wrap'>"
                f"<input name=q value='{esc(q)}' placeholder='搜艺人/妈咪/客人/场所/备注'>"
                f"<input type=month name=m value='{esc('' if m == 'all' else m)}'>"
                + sel("st", ["有效", "作废", "全部"], st)
                + sel("credit", [("", "结款态不限"), ("due", "挂账待结"), ("cash_due", "现金待清")], credit)
                + f"<input type=hidden name=mama value='{esc(mama)}'>"
                f"<button class='btn s'>筛选</button> <a class='btn s x' href='/orders'>清空</a></form>")
        if mama:
            filt += f"<p class=muted>只看妈咪：<b>{esc(mama)}</b></p>"
        body = (f"<h2>订单（改单 / 作废）</h2>{filt}"
                f"<table><tr><th>#</th><th>日期</th><th>艺人</th><th>场所</th><th>妈咪</th>"
                f"<th class=r>挂账</th><th class=r>现金</th><th class=r>门票</th>"
                f"<th>挂账态</th><th>现金态</th><th>档</th><th>客人</th><th></th></tr>{trs}</table>{nav}")
        return page("订单", card(body), "orders")
    finally:
        s.close()


@app.get("/export")
def orders_export(user=Depends(auth), q: str = "", m: str = "all", st: str = "有效",
                  credit: str = "", mama: str = ""):
    """按订单页当前筛选导出 xlsx (含引擎算出的各方应得)。"""
    from openpyxl import Workbook
    s = get_session()
    try:
        art, mam, ven = _names(s, Artist), _names(s, Mama), _names(s, Venue)
        wb = Workbook()
        ws = wb.active
        ws.title = "订单"
        ws.append(["#", "日期", "艺人", "场所", "包厢", "妈咪", "助理/预约人", "档", "流向", "挂账K", "现金M",
                   "门票O", "工价", "客人", "上班", "下班", "艺人应得", "现场已拿", "月底应结", "妈咪应收",
                   "妈咪反水", "公司抽成", "挂账态", "挂账结款日", "现金态", "状态", "备注"])
        for o in _orders_query(s, q, m, st, credit, mama).all():
            r = calc(o)
            ws.append([o.id, o.biz_date, art.get(o.artist_id), ven.get(o.venue_id), o.room,
                       mam.get(o.mama_id, "自单"), o.booker, o.mode, o.flow, o.credit_k, o.cash_m,
                       o.ticket_o, o.wp, o.customer, o.start_time, o.end_time,
                       round(r.artist_due, 2), round(r.onsite_taken, 2), round(r.artist_month_end, 2),
                       round(r.mama_receivable, 2), round(r.mama_rebate, 2), round(r.company_cut, 2),
                       o.credit_status, o.credit_paid_date, o.cash_status, o.status, o.remark])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
    finally:
        s.close()
    name = f"cgroup_orders_{m if m and m != 'all' else 'all'}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}"})


@app.get("/orders/{oid}/edit", response_class=HTMLResponse)
def order_edit_form(oid: int, user=Depends(auth), err: str = ""):
    s = get_session()
    try:
        o = s.get(Order, oid)
        if not o:
            return page("改单", card("没找到这单。<a href='/orders'>返回</a>"), "orders")
        art, mam, ven = _names(s, Artist), _names(s, Mama), _names(s, Venue)
        void = o.status != "已审核"
        form = (
            f"<h2>改 #{o.id}{' · 已作废' if void else ''}</h2>" + msg_box(err=err)
            + f"<form method='post' action='/orders/{oid}/edit'>"
            + form_table([
                ("日期", inp("biz_date", o.biz_date.strftime('%Y-%m-%d') if o.biz_date else '', typ="date")),
                ("艺人", inp("artist", art.get(o.artist_id, ''), extra="list='dl-art'")),
                ("场所", inp("venue", ven.get(o.venue_id, ''), extra="list='dl-ven'")),
                ("包厢", inp("room", o.room)),
                ("妈咪", inp("mama", mam.get(o.mama_id, ''), extra="list='dl-mama'")
                 + " <span class=muted>(留空=自单)</span>"),
                ("助理/预约人", inp("booker", o.booker)),
                ("分成档", sel("mode", MODES, o.mode)),
                ("现金流向", sel("flow", FLOWS, o.flow or '')),
                ("挂账 K", inp("credit_k", o.credit_k or 0, typ="number", extra="step=any")),
                ("现金 M", inp("cash_m", o.cash_m or 0, typ="number", extra="step=any")),
                ("门票 O", inp("ticket_o", o.ticket_o or 0, typ="number", extra="step=any")),
                ("工价", inp("wp", o.wp if o.wp is not None else '', "留空=K+M", typ="number", extra="step=any")),
                ("客人", inp("customer", o.customer)),
                ("上班", inp("start_time", o.start_time)),
                ("下班", inp("end_time", o.end_time)),
                ("备注", inp("remark", o.remark))])
            + datalist("dl-art", sorted(art.values())) + datalist("dl-mama", sorted(mam.values()))
            + datalist("dl-ven", sorted(ven.values()))
            + "<div style='margin-top:14px'><button class='btn g' type='submit'>保存</button> "
            + "<a class='btn' href='/orders'>取消</a></div></form>")
        if void:
            form += (f"<form method='post' action='/orders/{oid}/restore' style='margin-top:16px'>"
                     f"<button class='btn'>↩️ 恢复这单</button></form>")
        else:
            form += (f"<form method='post' action='/orders/{oid}/void' style='margin-top:16px'>"
                     f"<button class='btn x' onclick=\"return confirm('确认作废 #{o.id}? 作废后不计入任何报表')\">"
                     f"⚠️ 作废这单</button></form>")
        return page("改单", card(form), "orders")
    finally:
        s.close()


@app.post("/orders/{oid}/edit")
def order_edit_save(oid: int, user=Depends(auth),
                    biz_date: str = Form(""), artist: str = Form(""), venue: str = Form(""),
                    room: str = Form(""), mama: str = Form(""), booker: str = Form(""),
                    mode: str = Form("标准"), flow: str = Form(""), credit_k: float = Form(0),
                    cash_m: float = Form(0), ticket_o: float = Form(0), wp: str = Form(""),
                    customer: str = Form(""), start_time: str = Form(""), end_time: str = Form(""),
                    remark: str = Form("")):
    s = get_session()
    try:
        o = s.get(Order, oid)
        if not o:
            return RedirectResponse("/orders", status_code=303)

        def fail(msg):
            return RedirectResponse(f"/orders/{oid}/edit?{urlencode({'err': msg})}", status_code=303)

        a = find_artist(s, artist)
        if not a:
            return fail(f"艺人「{artist}」不在字典")
        v = find_venue(s, venue) if venue.strip() else None
        if venue.strip() and not v:
            return fail(f"场所「{venue}」不在字典")
        m = find_mama(s, mama) if mama.strip() else None
        if mama.strip() and not m:
            return fail(f"妈咪「{mama}」不在字典")
        if mode not in MODES:
            return fail(f"未知分成档「{mode}」")
        if mode in ("标准", "直结") and not m:
            return fail(f"分成档「{mode}」必须有妈咪；没有妈咪请选 自单")
        try:
            o.biz_date = datetime.strptime(biz_date, "%Y-%m-%d").date() if biz_date else o.biz_date
            o.wp = float(wp) if wp.strip() else None
        except ValueError:
            return fail("日期或工价格式不对")
        o.artist_id = a.id
        o.venue_id = v.id if v else None
        o.mama_id = m.id if m else None
        o.room = room.strip() or None
        o.booker = booker.strip() or None
        o.mode = mode
        o.flow = flow.strip().upper() or None
        o.credit_k = credit_k or 0
        o.cash_m = cash_m or 0
        o.ticket_o = ticket_o or 0
        o.customer = customer.strip()[:20] or None
        o.start_time = start_time.strip()[:8]
        o.end_time = end_time.strip()[:8]
        o.remark = remark.strip() or None
        # 金额/档/流向改了 → 重派生两轨态, 保留已结/已
        o.credit_status, o.cash_status = ST.derive_status(
            credit_k=o.credit_k, cash_m=o.cash_m, mode=o.mode, flow=o.flow,
            credit_paid=(o.credit_status == ST.CREDIT_PAID),
            cash_settled=(o.cash_status == ST.CASH_DONE),
            void=(o.status != "已审核"))
        s.add(OperationLog(action="改单", target=f"order#{oid}", operator=user,
                           detail=f"挂{int(o.credit_k)}/现{int(o.cash_m)}/票{int(o.ticket_o)}/{o.mode}"))
        s.commit()
    finally:
        s.close()
    return RedirectResponse("/orders", status_code=303)


@app.post("/orders/{oid}/void")
def order_void(oid: int, user=Depends(auth)):
    s = get_session()
    try:
        o = s.get(Order, oid)
        if o and o.status == "已审核":
            o.status = "作废"
            o.credit_status, o.cash_status = None, None
            s.add(OperationLog(action="作废单", target=f"order#{oid}", operator=user))
            s.commit()
    finally:
        s.close()
    return RedirectResponse("/orders", status_code=303)


@app.post("/orders/{oid}/restore")
def order_restore(oid: int, user=Depends(auth)):
    s = get_session()
    try:
        o = s.get(Order, oid)
        if o and o.status != "已审核":
            o.status = "已审核"
            o.credit_status, o.cash_status = ST.derive_status(
                credit_k=o.credit_k, cash_m=o.cash_m, mode=o.mode, flow=o.flow,
                credit_paid=bool(o.credit_paid_date), cash_settled=bool(o.cash_settle_date))
            s.add(OperationLog(action="恢复单", target=f"order#{oid}", operator=user))
            s.commit()
    finally:
        s.close()
    return RedirectResponse(f"/orders/{oid}/edit", status_code=303)


@app.post("/orders/{oid}/credit_toggle")
def credit_toggle(oid: int, request: Request, user=Depends(auth)):
    """挂账态 待结/部分 ↔ 已结 切换 (直结/无K 不可切)。"""
    s = get_session()
    try:
        o = s.get(Order, oid)
        if o and o.credit_status in (ST.CREDIT_DUE, ST.CREDIT_PART, ST.CREDIT_PAID):
            if o.credit_status == ST.CREDIT_PAID:
                o.credit_status = ST.CREDIT_DUE; o.credit_paid_date = None
            else:
                o.credit_status = ST.CREDIT_PAID; o.credit_paid_date = date.today()
            s.add(OperationLog(action="挂账结款", target=f"order#{oid}", detail=o.credit_status,
                               operator=user))
            s.commit()
    finally:
        s.close()
    return RedirectResponse(_back(request), status_code=303)


@app.post("/orders/{oid}/cash_toggle")
def cash_toggle(oid: int, request: Request, user=Depends(auth)):
    """现金态 待 ↔ 已 切换 (直结/无M 不可切)。"""
    s = get_session()
    try:
        o = s.get(Order, oid)
        if o and o.cash_status in (ST.CASH_DUE, ST.CASH_DONE):
            if o.cash_status == ST.CASH_DONE:
                o.cash_status = ST.CASH_DUE; o.cash_settle_date = None
            else:
                o.cash_status = ST.CASH_DONE; o.cash_settle_date = date.today()
            s.add(OperationLog(action="现金结算", target=f"order#{oid}", detail=o.cash_status,
                               operator=user))
            s.commit()
    finally:
        s.close()
    return RedirectResponse(_back(request), status_code=303)


# ───────────────────────── 操作日志 ─────────────────────────
@app.get("/logs", response_class=HTMLResponse)
def logs(user=Depends(auth), q: str = "", n: int = 200):
    s = get_session()
    try:
        query = s.query(OperationLog)
        if q:
            like = f"%{q}%"
            query = query.filter(or_(OperationLog.action.ilike(like), OperationLog.target.ilike(like),
                                     OperationLog.detail.ilike(like), OperationLog.operator.ilike(like)))
        rows = query.order_by(OperationLog.id.desc()).limit(min(max(n, 1), 1000)).all()
        trs = "".join(f"<tr><td>{esc(r.ts.strftime('%m/%d %H:%M') if r.ts else '')}</td>"
                      f"<td>{esc(r.operator)}</td><td>{esc(r.action)}</td><td>{esc(r.target)}</td>"
                      f"<td>{esc(r.detail)}</td></tr>" for r in rows)
        body = (f"<h2>操作日志（最近 {len(rows)} 条）</h2>"
                f"<form method=get class=inline style='margin-bottom:12px'>"
                f"<input name=q value='{esc(q)}' placeholder='搜动作/对象/内容'> <button class='btn s'>搜</button></form>"
                f"<table><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>内容</th></tr>"
                f"{trs or '<tr><td colspan=5 class=muted>暂无</td></tr>'}</table>")
        return page("日志", card(body), "logs")
    finally:
        s.close()
