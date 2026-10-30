"""
财务流水  ·  web/finance.py
预支/罚款 (月报自动扣) · 运营支出 · 妈咪结款 · 汇率 —— 录入 / 按月看 / 标已扣 / 删除。
财务群的消息进审核队列后, 在这里「录成流水」, 原文和录入一页对照。
"""
from datetime import date, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db.session import get_session
from ..db.models import (Advance, Expense, MamaSettlement, ExchangeRate, Artist, Broker, Mama,
                         ReviewItem, OperationLog)
from ..core.names import find_artist, find_mama
from ..core.queries import parse_month, month_range, month_key
from .ui import auth, esc, fmt, page, card, msg_box, sel, inp, datalist, form_table, month_picker

router = APIRouter(prefix="/finance")

TYPES = {"advance": "预支/罚款", "expense": "运营支出", "settle": "妈咪结款", "rate": "汇率"}
MODELS = {"advance": Advance, "expense": Expense, "settle": MamaSettlement, "rate": ExchangeRate}
CATEGORIES = ["场地", "伙食", "日用品", "广告", "交通", "工资", "其他"]
CURRENCIES = ["MYR", "RMB", "USDT"]


def _ym(m):
    today = date.today()
    return parse_month(m, (today.year, today.month))


def _guess_type(text):
    t = text or ""
    if "预支" in t or "罚" in t:
        return "advance"
    if "结款" in t or "收款" in t or "回款" in t:
        return "settle"
    if "汇率" in t:
        return "rate"
    return "expense"


@router.get("", response_class=HTMLResponse)
def finance_home(user=Depends(auth), tab: str = "advance", m: str = "", ok: str = ""):
    tab = tab if tab in TYPES else "advance"
    y, mo = _ym(m)
    ym = month_key(y, mo)
    start, end = month_range(y, mo)
    s = get_session()
    try:
        mam = {x.id: x.name for x in s.query(Mama).all()}
        if tab == "advance":
            rows = (s.query(Advance).filter(Advance.related_month == ym)
                    .order_by(Advance.subject_name, Advance.id).all())
            due = sum(a.amount for a in rows if a.status == "待扣")
            trs = "".join(
                f"<tr><td>{esc(a.subject_type)}</td><td>{esc(a.subject_name)}</td><td>{esc(a.kind)}</td>"
                f"<td class=r>{fmt(a.amount)} {esc(a.currency)}</td>"
                f"<td><span class='bd {'due' if a.status == '待扣' else 'ok'}'>{esc(a.status)}</span> "
                f"<form method=post action='/finance/advance/{a.id}/toggle' style='display:inline'>"
                f"<button class='tg {'' if a.status == '待扣' else 'u'}'>{'标已扣' if a.status == '待扣' else '撤'}</button></form></td>"
                f"<td>{esc(a.notes)}</td><td>{_del('advance', a.id)}</td></tr>" for a in rows)
            table = ("<table><tr><th>对象</th><th>名字</th><th>类型</th><th class=r>金额</th><th>状态</th>"
                     f"<th>备注</th><th></th></tr>{trs or _empty(7)}</table>"
                     f"<p class=muted>本月待扣合计 <b>{fmt(due)}</b>。发月报时艺人工资单会自动扣掉本月「待扣」项；"
                     "工资发完后点下面按钮全部标已扣。</p>"
                     f"<form method=post action='/finance/advance/settle_month?m={ym}'>"
                     f"<button class='btn s' onclick=\"return confirm('把 {ym} 所有待扣标为已扣？')\">本月全部标已扣</button></form>")
        elif tab == "expense":
            rows = (s.query(Expense).filter(Expense.spend_date >= start, Expense.spend_date <= end)
                    .order_by(Expense.spend_date, Expense.id).all())
            bycat = {}
            for e in rows:
                bycat[e.category] = bycat.get(e.category, 0) + e.amount
            trs = "".join(
                f"<tr><td>{e.spend_date:%m/%d}</td><td>{esc(e.category)}</td>"
                f"<td class=r>{fmt(e.amount)} {esc(e.currency)}</td><td>{esc(e.notes)}</td>"
                f"<td>{_del('expense', e.id)}</td></tr>" for e in rows)
            summary = " · ".join(f"{esc(k)} {fmt(v)}" for k, v in sorted(bycat.items(), key=lambda x: -x[1]))
            table = (f"<table><tr><th>日期</th><th>类别</th><th class=r>金额</th><th>备注</th><th></th></tr>"
                     f"{trs or _empty(5)}</table><p class=muted>合计 <b>{fmt(sum(bycat.values()))}</b>"
                     f"{' ： ' + summary if summary else ''}</p>")
        elif tab == "settle":
            rows = (s.query(MamaSettlement)
                    .filter(MamaSettlement.settled_at >= start, MamaSettlement.settled_at <= end)
                    .order_by(MamaSettlement.settled_at, MamaSettlement.id).all())
            trs = "".join(
                f"<tr><td>{x.settled_at:%m/%d}</td><td>{esc(mam.get(x.mama_id, '?'))}</td>"
                f"<td class=r>{fmt(x.amount)} {esc(x.currency)}</td><td>{esc(x.period)}</td>"
                f"<td>{esc(x.notes)}</td><td>{_del('settle', x.id)}</td></tr>" for x in rows)
            table = (f"<table><tr><th>日期</th><th>妈咪</th><th class=r>金额</th><th>周期</th><th>备注</th><th></th></tr>"
                     f"{trs or _empty(6)}</table><p class=muted>合计 <b>{fmt(sum(x.amount for x in rows))}</b>。"
                     "看板「全部结清」会自动记一笔在这里；单张挂账的结款态在订单页切换。</p>")
        else:
            rows = s.query(ExchangeRate).order_by(ExchangeRate.rate_date.desc(), ExchangeRate.id.desc()).limit(60).all()
            trs = "".join(
                f"<tr><td>{x.rate_date}</td><td>{esc(x.currency)}</td><td class=r>{x.real_rate:g}</td>"
                f"<td class=r>{x.settle_rate:g}</td><td>{_del('rate', x.id)}</td></tr>" for x in rows)
            table = (f"<table><tr><th>日期</th><th>币种</th><th class=r>市价</th><th class=r>结算价</th><th></th></tr>"
                     f"{trs or _empty(5)}</table><p class=muted>汇率 = 1 MYR 兑多少该币种。"
                     "艺人工资单的 RMB 折算取最新一条 RMB 结算价。</p>")
        tabs = " ".join(f"<a class='btn s {'' if k == tab else 'x'}' href='/finance?{urlencode({'tab': k, 'm': ym})}'>{v}</a>"
                        for k, v in TYPES.items())
        picker = "" if tab == "rate" else month_picker("/finance", ym, f"<input type=hidden name=tab value='{tab}'>")
        body = (f"<h2>财务流水 · {TYPES[tab]}{'' if tab == 'rate' else f' · {y}年{mo}月'}</h2>{tabs} "
                f"<a class='btn s g' href='/finance/new?type={tab}'>＋ 录一笔</a>" + msg_box(ok=ok)
                + f"<div style='margin-top:12px'>{picker}{table}</div>")
        return page("财务流水", card(body), "finance")
    finally:
        s.close()


def _del(typ, oid):
    return (f"<form method=post action='/finance/{typ}/{oid}/delete' style='display:inline'>"
            f"<button class='tg u' onclick=\"return confirm('删除这笔？')\">删</button></form>")


def _empty(cols):
    return f"<tr><td colspan={cols} class=muted>这个月还没有</td></tr>"


def _fields(s, typ, d):
    today = date.today()
    if typ == "advance":
        names = ([a.name for a in s.query(Artist).order_by(Artist.name)]
                 + [b.name for b in s.query(Broker).order_by(Broker.name)])
        return [("对象", sel("subject_type", ["艺人", "经纪人"], d.get("subject_type") or "艺人")),
                ("名字", inp("subject_name", d.get("subject_name"), extra="list='dl-names' required")
                 + datalist("dl-names", names)),
                ("类型", sel("kind", ["预支", "罚款"], d.get("kind") or "预支")),
                ("金额", inp("amount", d.get("amount"), typ="number", extra="step=any required")),
                ("币种", sel("currency", CURRENCIES, d.get("currency") or "MYR")),
                ("扣款月份", inp("related_month", d.get("related_month") or month_key(today.year, today.month),
                               typ="month")),
                ("备注", inp("notes", d.get("notes")))]
    if typ == "expense":
        return [("日期", inp("spend_date", d.get("spend_date") or today.isoformat(), typ="date")),
                ("类别", inp("category", d.get("category"), extra="list='dl-cat' required")
                 + datalist("dl-cat", CATEGORIES)),
                ("金额", inp("amount", d.get("amount"), typ="number", extra="step=any required")),
                ("币种", sel("currency", CURRENCIES, d.get("currency") or "MYR")),
                ("备注", inp("notes", d.get("notes")))]
    if typ == "settle":
        return [("妈咪", inp("mama", d.get("mama"), extra="list='dl-mama' required")
                 + datalist("dl-mama", [m.name for m in s.query(Mama).order_by(Mama.name)])),
                ("金额", inp("amount", d.get("amount"), typ="number", extra="step=any required")),
                ("币种", sel("currency", CURRENCIES, d.get("currency") or "MYR")),
                ("结款日", inp("settled_at", d.get("settled_at") or today.isoformat(), typ="date")),
                ("关联周期", inp("period", d.get("period"), "6.1-6.15")),
                ("备注", inp("notes", d.get("notes")))]
    return [("日期", inp("rate_date", d.get("rate_date") or today.isoformat(), typ="date")),
            ("币种", sel("currency", ["RMB", "USDT"], d.get("currency") or "RMB")),
            ("市价", inp("real_rate", d.get("real_rate"), "1 MYR = ?", typ="number", extra="step=any required")),
            ("结算价", inp("settle_rate", d.get("settle_rate"), "给艺人的价", typ="number", extra="step=any required"))]


def _new_form(s, typ, d, review=None, err=""):
    tabs = " ".join(f"<a class='btn s {'' if k == typ else 'x'}' "
                    f"href='/finance/new?{urlencode({'type': k, **({'review': review.id} if review else {})})}'>{v}</a>"
                    for k, v in TYPES.items())
    raw = ""
    if review:
        raw = (f"<h3>财务群原文 · 审核#{review.id} · {esc(review.tg_sender or '')}</h3>"
               f"<div class=raw>{esc(review.raw_message)}</div>")
    body = (f"<h2>录一笔 · {TYPES[typ]}</h2>{tabs}" + msg_box(err=err) + raw
            + "<form method=post action='/finance/new' style='margin-top:12px'>"
            + f"<input type=hidden name=type value='{typ}'>"
            + (f"<input type=hidden name=review value='{review.id}'>" if review else "")
            + form_table(_fields(s, typ, d))
            + "<div style='margin-top:14px'><button class='btn g'>保存</button> "
            + f"<a class='btn x' href='{'/review' if review else '/finance?tab=' + typ}'>返回</a></div></form>")
    return page("财务流水", card(body), "finance")


@router.get("/new", response_class=HTMLResponse)
def finance_new(user=Depends(auth), type: str = "", review: int = 0):
    s = get_session()
    try:
        it = s.get(ReviewItem, review) if review else None
        typ = type if type in TYPES else _guess_type(it.raw_message if it else "")
        return _new_form(s, typ, {}, it)
    finally:
        s.close()


def _date(v):
    return datetime.strptime(v, "%Y-%m-%d").date()


@router.post("/new", response_class=HTMLResponse)
async def finance_create(request: Request, user=Depends(auth)):
    f = {k: str(v).strip() for k, v in (await request.form()).items()}
    typ = f.get("type") if f.get("type") in TYPES else "expense"
    s = get_session()
    try:
        it = s.get(ReviewItem, int(f["review"])) if f.get("review", "").isdigit() else None
        try:
            if typ == "advance":
                name = f.get("subject_name", "")
                if f.get("subject_type") == "艺人":
                    a = find_artist(s, name)
                    if not a:
                        raise ValueError(f"艺人「{name}」不在字典")
                    name = a.name
                elif not s.query(Broker).filter_by(name=name).first():
                    raise ValueError(f"经纪人「{name}」不在字典")
                rm = parse_month(f.get("related_month"))
                if not rm:
                    raise ValueError("扣款月份不对")
                obj = Advance(subject_type=f.get("subject_type") or "艺人", subject_name=name,
                              kind=f.get("kind") or "预支", amount=float(f["amount"]),
                              currency=f.get("currency") or "MYR", status="待扣",
                              related_month=month_key(*rm), notes=f.get("notes") or None)
                label = f"{obj.kind} {name} {obj.amount:g}"
            elif typ == "expense":
                if not f.get("category"):
                    raise ValueError("类别不能空")
                obj = Expense(spend_date=_date(f.get("spend_date")), category=f["category"][:30],
                              amount=float(f["amount"]), currency=f.get("currency") or "MYR",
                              notes=f.get("notes") or None)
                label = f"{obj.category} {obj.amount:g}"
            elif typ == "settle":
                m = find_mama(s, f.get("mama"))
                if not m:
                    raise ValueError(f"妈咪「{f.get('mama')}」不在字典")
                obj = MamaSettlement(mama_id=m.id, amount=float(f["amount"]),
                                     currency=f.get("currency") or "MYR",
                                     settled_at=_date(f.get("settled_at")),
                                     period=f.get("period") or None, notes=f.get("notes") or None)
                label = f"{m.name} {obj.amount:g}"
            else:
                obj = ExchangeRate(rate_date=_date(f.get("rate_date")), currency=f.get("currency") or "RMB",
                                   real_rate=float(f["real_rate"]), settle_rate=float(f["settle_rate"]))
                label = f"{obj.currency} {obj.real_rate:g}/{obj.settle_rate:g}"
        except (ValueError, KeyError) as e:
            msg = str(e) if isinstance(e, ValueError) and "could not convert" not in str(e) \
                and "does not match" not in str(e) else "金额/日期格式不对"
            return _new_form(s, typ, f, it, msg)
        s.add(obj)
        s.flush()
        if it and it.status == "待审":
            it.status = "已确认"
            it.reviewed_at = datetime.now()
        s.add(OperationLog(action=f"录{TYPES[typ]}", target=f"{typ}#{obj.id}", operator=user,
                           detail=label + (f" ←review#{it.id}" if it else "")))
        s.commit()
        ym = (obj.related_month if typ == "advance" else "")
        if typ == "expense":
            ym = month_key(obj.spend_date.year, obj.spend_date.month)
        elif typ == "settle":
            ym = month_key(obj.settled_at.year, obj.settled_at.month)
    finally:
        s.close()
    if it:
        return RedirectResponse("/review", status_code=303)
    return RedirectResponse(f"/finance?{urlencode({'tab': typ, 'm': ym, 'ok': '已保存 ' + label})}", status_code=303)


@router.post("/advance/{aid}/toggle")
def advance_toggle(aid: int, user=Depends(auth)):
    s = get_session()
    try:
        a = s.get(Advance, aid)
        ym = ""
        if a:
            a.status = "已扣" if a.status == "待扣" else "待扣"
            a.repay_date = date.today() if a.status == "已扣" else None
            ym = a.related_month or ""
            s.add(OperationLog(action="预支扣款", target=f"advance#{aid}", detail=a.status, operator=user))
            s.commit()
    finally:
        s.close()
    return RedirectResponse(f"/finance?{urlencode({'tab': 'advance', 'm': ym})}", status_code=303)


@router.post("/advance/settle_month")
def advance_settle_month(user=Depends(auth), m: str = ""):
    rm = parse_month(m)
    if not rm:
        return RedirectResponse("/finance", status_code=303)
    ym = month_key(*rm)
    s = get_session()
    try:
        n = 0
        for a in s.query(Advance).filter(Advance.related_month == ym, Advance.status == "待扣"):
            a.status = "已扣"
            a.repay_date = date.today()
            n += 1
        if n:
            s.add(OperationLog(action="预支月结", target=ym, detail=f"{n}笔标已扣", operator=user))
            s.commit()
    finally:
        s.close()
    return RedirectResponse(f"/finance?{urlencode({'tab': 'advance', 'm': ym, 'ok': f'{n} 笔已标已扣'})}",
                            status_code=303)


@router.post("/{typ}/{oid}/delete")
def finance_delete(typ: str, oid: int, user=Depends(auth)):
    if typ not in MODELS:
        return RedirectResponse("/finance", status_code=303)
    s = get_session()
    try:
        obj = s.get(MODELS[typ], oid)
        if obj:
            s.delete(obj)
            s.add(OperationLog(action=f"删{TYPES[typ]}", target=f"{typ}#{oid}", operator=user,
                               detail=str(getattr(obj, "amount", "") or getattr(obj, "settle_rate", ""))))
            s.commit()
    finally:
        s.close()
    return RedirectResponse(f"/finance?tab={typ}", status_code=303)
