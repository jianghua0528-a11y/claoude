"""
字典管理  ·  web/dicts.py
艺人 / 妈咪(+助理) / 场所 / 经纪人 的 列表·新增·修改。改名前先确认没有重名。
解析器和审核认人都读这里, 新妈咪/新场所在这加上后, 下一条报单就能认出来。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from urllib.parse import urlencode

from ..db.session import get_session
from ..db.models import Artist, Mama, MamaAssistant, Venue, Broker, Order, OperationLog
from .ui import auth, esc, fmt, page, card, msg_box, sel, inp, form_table

router = APIRouter(prefix="/dicts")

# kind → (模型, 中文名, 字段[(属性, 标签, 类型)])
#   类型: text / float / pct / bool / broker / vtype / cycle / assistants
SPECS = {
    "artist": (Artist, "艺人", [("name", "艺名", "text"), ("aliases", "别名(逗号分隔)", "text"),
                              ("broker_id", "经纪人", "broker"), ("active", "在职", "bool"),
                              ("tg_user_id", "TG绑定ID(清空=解绑)", "text")]),
    "mama": (Mama, "妈咪", [("name", "主妈咪名", "text"), ("aliases", "别名(逗号分隔)", "text"),
                          ("settlement_cycle", "结款周期", "cycle"), ("assistants", "助理/带房(逗号分隔)", "assistants"),
                          ("notes", "备注", "text")]),
    "venue": (Venue, "场所", [("name", "场所名", "text"), ("aliases", "别名(逗号分隔)", "text"),
                            ("vtype", "类型", "vtype"), ("default_ticket", "默认门票", "float"),
                            ("rooms", "包厢(逗号分隔)", "text")]),
    "broker": (Broker, "经纪人", [("name", "名字", "text"), ("pct", "提成%", "pct"),
                               ("is_shareholder", "股东", "bool")]),
}
CYCLES = ["半月结", "月结", "周结", "日结"]
VTYPES = ["标准", "酒店公寓"]


def _tabs(kind):
    return " ".join(f"<a class='btn s {'' if k == kind else 'x'}' href='/dicts?kind={k}'>{v[1]}</a>"
                    for k, v in SPECS.items())


def _show(s, obj, attr, typ):
    if typ == "broker":
        return esc(obj.broker.name) if obj.broker else "<span class=muted>未分配</span>"
    if typ == "assistants":
        return esc("、".join(a.name for a in obj.assistants))
    if typ == "bool":
        return "✅" if getattr(obj, attr) else "—"
    if typ == "pct":
        return f"{(getattr(obj, attr) or 0) * 100:g}%"
    if typ == "float":
        return fmt(getattr(obj, attr))
    if attr == "tg_user_id":
        return "已绑定" if getattr(obj, attr) else "<span class=muted>未绑定</span>"
    return esc(getattr(obj, attr) or "")


@router.get("", response_class=HTMLResponse)
def dict_list(user=Depends(auth), kind: str = "artist", ok: str = ""):
    if kind not in SPECS:
        kind = "artist"
    model, title, fields = SPECS[kind]
    s = get_session()
    try:
        rows = s.query(model).order_by(model.name).all()
        counts = {}
        if kind in ("artist", "mama", "venue"):
            col = {"artist": Order.artist_id, "mama": Order.mama_id, "venue": Order.venue_id}[kind]
            for (k,) in s.query(col).filter(Order.status == "已审核"):
                counts[k] = counts.get(k, 0) + 1
        head = "".join(f"<th>{esc(l)}</th>" for _, l, _ in fields) + ("<th class=r>单数</th>" if kind != "broker" else "") + "<th></th>"
        trs = ""
        for o in rows:
            dim = " style='opacity:.45'" if kind == "artist" and not o.active else ""
            tds = "".join(f"<td>{_show(s, o, a, t)}</td>" for a, _, t in fields)
            if kind != "broker":
                tds += f"<td class=r>{counts.get(o.id, 0)}</td>"
            trs += f"<tr{dim}>{tds}<td><a class='btn s' href='/dicts/{kind}/{o.id}'>改</a></td></tr>"
        body = (f"<h2>字典 · {title}（{len(rows)}）</h2>{_tabs(kind)} "
                f"<a class='btn s g' href='/dicts/{kind}/new'>＋ 新增{title}</a>" + msg_box(ok=ok)
                + f"<table style='margin-top:12px'><tr>{head}</tr>{trs}</table>")
        return page("字典", card(body), "dicts")
    finally:
        s.close()


def _form(s, kind, obj, err=""):
    model, title, fields = SPECS[kind]
    brokers = [("", "未分配")] + [(b.id, b.name) for b in s.query(Broker).order_by(Broker.name)]
    rows = []
    for attr, label, typ in fields:
        cur = getattr(obj, attr, None) if obj and typ != "assistants" else None
        if typ == "broker":
            ctl = sel(attr, brokers, cur or "")
        elif typ == "bool":
            checked = "checked" if (cur if obj else attr == "active") else ""
            ctl = f"<input type=checkbox name='{attr}' value=1 {checked}>"
        elif typ == "cycle":
            ctl = sel(attr, CYCLES, cur or "半月结")
        elif typ == "vtype":
            ctl = sel(attr, VTYPES, cur or "标准")
        elif typ == "assistants":
            ctl = inp(attr, ",".join(a.name for a in obj.assistants) if obj else "")
        elif typ == "pct":
            ctl = inp(attr, f"{(cur or 0) * 100:g}" if obj else "7", "7 = 7%", typ="number", extra="step=any")
        elif typ == "float":
            ctl = inp(attr, cur if obj else 0, typ="number", extra="step=any")
        else:
            ctl = inp(attr, cur, extra="required" if attr == "name" else "")
        rows.append((label, ctl))
    action = f"/dicts/{kind}/{obj.id}" if obj else f"/dicts/{kind}/new"
    body = (f"<h2>{'改' if obj else '新增'}{title}{' · ' + esc(obj.name) if obj else ''}</h2>" + msg_box(err=err)
            + f"<form method=post action='{action}'>" + form_table(rows)
            + "<div style='margin-top:14px'><button class='btn g'>保存</button> "
            + f"<a class='btn x' href='/dicts?kind={kind}'>返回</a></div></form>")
    return page("字典", card(body), "dicts")


@router.get("/{kind}/new", response_class=HTMLResponse)
def dict_new(kind: str, user=Depends(auth)):
    if kind not in SPECS:
        return RedirectResponse("/dicts", status_code=303)
    s = get_session()
    try:
        return _form(s, kind, None)
    finally:
        s.close()


@router.get("/{kind}/{oid}", response_class=HTMLResponse)
def dict_edit(kind: str, oid: int, user=Depends(auth)):
    if kind not in SPECS:
        return RedirectResponse("/dicts", status_code=303)
    s = get_session()
    try:
        obj = s.get(SPECS[kind][0], oid)
        if not obj:
            return RedirectResponse(f"/dicts?kind={kind}", status_code=303)
        return _form(s, kind, obj)
    finally:
        s.close()


@router.post("/{kind}/{oid}", response_class=HTMLResponse)
async def dict_save(kind: str, oid: str, request: Request, user=Depends(auth)):
    if kind not in SPECS:
        return RedirectResponse("/dicts", status_code=303)
    model, title, fields = SPECS[kind]
    form = await request.form()
    s = get_session()
    try:
        obj = None if oid == "new" else s.get(model, int(oid)) if oid.isdigit() else None
        if oid != "new" and not obj:
            return RedirectResponse(f"/dicts?kind={kind}", status_code=303)
        name = str(form.get("name") or "").strip()
        if not name:
            return _form(s, kind, obj, "名字不能空")
        dup = s.query(model).filter(model.name == name).first()
        if dup and (not obj or dup.id != obj.id):
            return _form(s, kind, obj, f"已经有叫「{name}」的{title}了")
        is_new = obj is None
        if is_new:
            obj = model(name=name)
            s.add(obj)
        for attr, label, typ in fields:
            raw = str(form.get(attr) or "").strip()
            if typ == "bool":
                setattr(obj, attr, bool(form.get(attr)))
            elif typ == "broker":
                setattr(obj, attr, int(raw) if raw.isdigit() else None)
            elif typ in ("float", "pct"):
                try:
                    v = float(raw or 0)
                except ValueError:
                    s.rollback()
                    return _form(s, kind, None if is_new else obj, f"{label} 要填数字")
                setattr(obj, attr, v / 100 if typ == "pct" else v)
            elif typ == "assistants":
                continue
            else:
                setattr(obj, attr, raw or None)
        obj.name = name
        s.flush()
        if kind == "mama":
            want = [x.strip() for x in str(form.get("assistants") or "").replace("，", ",").split(",") if x.strip()]
            have = {a.name: a for a in obj.assistants}
            for n, a in have.items():
                if n not in want:
                    s.delete(a)
            for n in want:
                if n not in have:
                    s.add(MamaAssistant(name=n, mama_id=obj.id))
        s.add(OperationLog(action=f"{'新增' if is_new else '修改'}{title}", target=f"{kind}#{obj.id}",
                           detail=name, operator=user))
        s.commit()
    finally:
        s.close()
    return RedirectResponse(f"/dicts?{urlencode({'kind': kind, 'ok': f'已保存 {name}'})}", status_code=303)
