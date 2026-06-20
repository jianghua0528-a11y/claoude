"""
报表 PNG 生成 (Pillow, 2× 高清, 自适应宽度)  ·  render.py
  · render_daily_report   日报 (列宽按内容自适应 + 标签/红条/提醒框)
  · render_mama_statement 妈咪对账单 (挂账区 + 现金区 + 应结C组, 自适应)
"""
import glob
import io
import re

from PIL import Image, ImageDraw, ImageFont

SCALE = 2

BG = "#FBEAF0"; DRED = "#72243E"; DARK = "#4B1528"; PINK = "#F4C0D1"
REDLINE = "#993556"; GRAY = "#888780"; WHITE = "#FFFFFF"
K_CLR = "#4B1528"; M_CLR = "#185FA5"; O_CLR = "#BA7517"; BLUE_BG = "#E8F1FA"

_FONT_DIRS = ["/usr/share/fonts/opentype/noto", "/usr/share/fonts/truetype/noto", "/usr/share/fonts"]


def _S(v):
    return int(round(v * SCALE))


def _font_file(bold=False):
    pat = "NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc"
    for base in _FONT_DIRS:
        hits = glob.glob(f"{base}/**/{pat}", recursive=True)
        if hits:
            return hits[0]
    for base in _FONT_DIRS:
        hits = glob.glob(f"{base}/**/NotoSansCJK*.ttc", recursive=True)
        if hits:
            return hits[0]
    raise RuntimeError("找不到 Noto CJK 字体")


def _font(size, bold=False):
    return ImageFont.truetype(_font_file(bold), _S(size), index=0)


def _fmt(n):
    n = n or 0
    return f"{int(round(n)):,}" if n else "—"


def _pngbytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_MEASURE = ImageDraw.Draw(Image.new("RGB", (4, 4)))


def _tw(text, f):
    if text in (None, ""):
        return 0
    return int(_MEASURE.textlength(str(text), font=f)) + 1


def _layout(widths, order, x_start, gap):
    x = x_start
    pos = {}
    for k in order:
        pos[k] = x
        x += widths[k] + gap
    return pos, x - gap


# ───────────────────────────── 日报 ─────────────────────────────
def _badges(r):
    out = []
    note = r.get("备注") or ""
    flow = (r.get("流向") or "").upper()
    K = r.get("K", 0) or 0
    M = r.get("M", 0) or 0
    guest = r.get("客人") or ""
    m = re.search(r"(超时|加时)\s*[:：]?\s*(\d+)", note) or re.search(r"(\d+)\s*(超时|加时)", note)
    if m:
        kw, amt = (m.group(1), m.group(2)) if m.group(1) in ("超时", "加时") else (m.group(2), m.group(1))
        out.append((f"含{kw}{amt}", "pink"))
    mixed = K > 0 and M > 0
    if mixed:
        out.append(("混合·现金代收A", "blue"))
    elif flow == "A" or "代收" in note:
        out.append((f"代收{int(M):,}" if M else "公司代收", "blue"))
    elif flow == "D":
        out.append(("D·自留", "pink"))
    elif flow == "E":
        out.append(("E·自留", "pink"))
    elif flow == "D60":
        out.append(("D60·自留", "pink"))
    if "群" in note and not any("群" in b[0] for b in out):
        out.append(("群", "pink"))
    if re.search(r"直[✌\s]*", note) and not mixed:
        out.append(("直", "pink"))
    if "客找" in note or guest == "客找":
        out.append(("客找", "pink"))
    tip = re.search(r"(\d+)?\s*(小费|打赏)", note)
    if tip:
        out.append((f"含{tip.group(1)}小费" if tip.group(1) else "含小费", "pink"))
    return out


def _badge(d, x, ym, text, kind, f):
    bw = _tw(text, f) + _S(12)
    h = _S(18)
    bg, brd = (BLUE_BG, M_CLR) if kind == "blue" else (BG, REDLINE)
    d.rounded_rectangle([x, ym - h // 2, x + bw, ym + h // 2], radius=_S(4), fill=bg, outline=brd, width=max(1, _S(1)))
    d.text((x + _S(6), ym), text, font=f, fill=brd, anchor="lm")
    return bw + _S(6)


def _badges_w(r, f):
    w = sum(_tw(t, f) + _S(12) + _S(6) for t, _ in _badges(r))
    return w


def _dashed(d, box, color, dash=6, gap=4, width=1):
    x0, y0, x1, y1 = box
    dash, gap, width = _S(dash), _S(gap), max(1, _S(width))
    p = x0
    while p < x1:
        e = min(p + dash, x1)
        d.line([p, y0, e, y0], fill=color, width=width); d.line([p, y1, e, y1], fill=color, width=width)
        p = e + gap
    p = y0
    while p < y1:
        e = min(p + dash, y1)
        d.line([x0, p, x0, e], fill=color, width=width); d.line([x1, p, x1, e], fill=color, width=width)
        p = e + gap


def render_daily_report(day_label, weekday, rows, year="2026年", currency="MYR"):
    pad = _S(24); gap = _S(22)
    row_h = _S(44); th_h = _S(38); head_h = _S(96)
    f_title = _font(23, bold=True); f_sub = _font(13); f_th = _font(13, bold=True)
    f_cell = _font(13); f_cellb = _font(13, bold=True); f_small = _font(11)
    f_logo = _font(34); f_sumlbl = _font(14, bold=True); f_sumnum = _font(18, bold=True); f_badge = _font(11)

    def mx(fn):
        return max((fn(r) for r in rows), default=0)

    cw = {
        "#": _S(24),
        "artist": max(_tw("艺人", f_th), mx(lambda r: _tw(r.get("艺人"), f_cellb))),
        "venue": max(_tw("场所·包厢", f_th), mx(lambda r: max(_tw(r.get("场所"), f_cell), _tw(r.get("包厢"), f_small)))),
        "mama": max(_tw("妈咪", f_th), mx(lambda r: max(_tw(r.get("妈咪"), f_cellb), _tw((f"{r.get('约')}预约" if r.get("约") else ""), f_small)))),
        "K": max(_tw("挂账", f_th), mx(lambda r: _tw(_fmt(r.get("K")), f_cellb))),
        "M": max(_tw("现金", f_th), mx(lambda r: _tw(_fmt(r.get("M")), f_cellb))),
        "O": max(_tw("门票", f_th), mx(lambda r: _tw(_fmt(r.get("O")), f_cellb))),
        "guest": max(_tw("客人", f_th), mx(lambda r: _tw(r.get("客人") or "—", f_cell))),
        "time": max(_tw("上下班", f_th), mx(lambda r: _tw(f"{r.get('上班') or '?'}→{r.get('下班') or '?'}", f_small)
                    + (_S(10) + _badges_w(r, f_badge) if _badges(r) else 0))),
    }
    order = ["#", "artist", "venue", "mama", "K", "M", "O", "guest", "time"]
    right = {"K", "M", "O"}
    L = pad
    xL, x_end = _layout(cw, order, L + _S(12), gap)
    W = x_end + _S(12) + pad
    W = max(W, _S(560))
    R = W - pad

    body_h = max(len(rows), 1) * row_h
    sum_h = _S(92); remind_h = _S(64)
    table_top = pad + head_h
    H = table_top + th_h + body_h + _S(22) + sum_h + _S(16) + remind_h + _S(52)

    runs = []
    i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1].get("艺人") == rows[i].get("艺人"):
            j += 1
        if j > i:
            runs.append((i, j - i + 1, rows[i].get("艺人")))
        i = j + 1

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    cx, cy, cr = pad + _S(35), pad + _S(35), _S(35)
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], outline=DRED, width=max(1, _S(3)))
    d.text((cx, cy), "C", font=f_logo, fill=DRED, anchor="mm")
    d.text((pad + _S(86), pad + _S(22)), f"{day_label} 报单对账", font=f_title, fill=DARK)
    d.text((pad + _S(86), pad + _S(54)), f"{year} · {weekday}", font=f_sub, fill=GRAY)

    d.rounded_rectangle([L, table_top, R, table_top + th_h], radius=_S(6), fill=DRED)
    ty = table_top + th_h // 2
    labels = {"#": "#", "artist": "艺人", "venue": "场所·包厢", "mama": "妈咪", "K": "挂账",
              "M": "现金", "O": "门票", "guest": "客人", "time": "上下班"}
    for k in order:
        if k in right:
            d.text((xL[k] + cw[k], ty), labels[k], font=f_th, fill=WHITE, anchor="rm")
        else:
            d.text((xL[k], ty), labels[k], font=f_th, fill=WHITE, anchor="lm")

    by = table_top + th_h
    d.rectangle([L, by, R, by + body_h], fill=WHITE)
    bar_x = xL["artist"] - _S(13)
    for idx, r in enumerate(rows):
        y0 = by + idx * row_h; ym = y0 + row_h // 2
        if idx:
            d.line([L + _S(16), y0, R, y0], fill=PINK, width=max(1, _S(1)))
        cxn = xL["#"] + _S(9)
        d.ellipse([cxn - _S(11), ym - _S(11), cxn + _S(11), ym + _S(11)], fill=DRED)
        d.text((cxn, ym), str(idx + 1), font=f_small, fill=WHITE, anchor="mm")
        d.text((xL["artist"], ym), str(r.get("艺人", "")), font=f_cellb, fill=DARK, anchor="lm")
        d.text((xL["venue"], ym - _S(8)), str(r.get("场所", "")), font=f_cell, fill=DARK, anchor="lm")
        if r.get("包厢"):
            d.text((xL["venue"], ym + _S(9)), str(r.get("包厢")), font=f_small, fill=GRAY, anchor="lm")
        d.text((xL["mama"], ym - _S(8) if r.get("约") else ym), str(r.get("妈咪", "")), font=f_cellb, fill=DARK, anchor="lm")
        if r.get("约"):
            d.text((xL["mama"], ym + _S(9)), f"{r.get('约')}预约", font=f_small, fill=GRAY, anchor="lm")
        for k, clr in [("K", K_CLR), ("M", M_CLR), ("O", O_CLR)]:
            d.text((xL[k] + cw[k], ym), _fmt(r.get(k)), font=f_cellb, fill=clr if r.get(k) else GRAY, anchor="rm")
        d.text((xL["guest"], ym), str(r.get("客人") or "—"), font=f_cell, fill=DARK, anchor="lm")
        tm = f"{r.get('上班') or '?'}→{r.get('下班') or '?'}"
        d.text((xL["time"], ym), tm, font=f_small, fill="#5F5E5A", anchor="lm")
        tx = xL["time"] + _tw(tm, f_small) + _S(10)
        for text, kind in _badges(r):
            tx += _badge(d, tx, ym, text, kind, f_badge)

    # 红条 (多班次) 最后画, 盖在行线上保持连续
    for start, length, _n in runs:
        d.rounded_rectangle([bar_x, by + start * row_h + _S(10), bar_x + _S(4), by + (start + length) * row_h - _S(10)],
                            radius=_S(2), fill=REDLINE)

    sy = by + body_h + _S(22)
    d.rounded_rectangle([L, sy, R, sy + sum_h], radius=_S(8), fill=PINK)
    SK = sum(r.get("K", 0) or 0 for r in rows); SM = sum(r.get("M", 0) or 0 for r in rows)
    SO = sum(r.get("O", 0) or 0 for r in rows); sisters = len({r.get("艺人") for r in rows})
    daishou = [(r.get("艺人"), r.get("M", 0)) for r in rows if (r.get("流向") or "").upper() == "A" and r.get("M")]
    cxm = (L + R) // 2
    d.text((cxm, sy + _S(22)), f"{day_label} 合计 · {len(rows)} 单 · {sisters} 位姐妹", font=f_sumlbl, fill=DARK, anchor="mm")
    seg = f"挂账  {_fmt(SK)}      ·      现金  {_fmt(SM)}      ·      门票  {_fmt(SO)}    {currency}"
    d.text((cxm, sy + (_S(48) if daishou else _S(54))), seg, font=f_sumnum, fill=DARK, anchor="mm")
    if daishou:
        ds = "  ".join(f"代收 {int(a):,}({nm})" for nm, a in daishou[:3])
        d.text((cxm, sy + _S(74)), f"· {ds} ·", font=f_small, fill=M_CLR, anchor="mm")

    ry = sy + sum_h + _S(16)
    _dashed(d, [L, ry, R, ry + remind_h], REDLINE)
    d.text((L + _S(18), ry + _S(20)), "请姐妹们核对自己的单", font=f_cellb, fill=DARK, anchor="lm")
    note2 = "如有错漏或误报，请于今日 24:00 前联系经纪人订正，超时不予调整。"
    if daishou:
        note2 = "现金列显标准工价，公司代收实情见蓝标。" + note2
    d.text((L + _S(18), ry + _S(42)), note2, font=f_small, fill=GRAY, anchor="lm")

    if runs:
        multi = " · ".join(f"{nm} {ln}单" for _, ln, nm in runs)
        d.text((cxm, H - _S(36)), f"红条 = 同一姐妹多班次（{multi}）", font=f_small, fill=GRAY, anchor="mm")
    d.text((cxm, H - _S(18)), f"C组运营 · {year[:4]}/06", font=f_small, fill=GRAY, anchor="mm")
    return _pngbytes(img)


# ───────────────────────────── 妈咪对账单 ─────────────────────────────
def render_mama_statement(mama_name, period, k_rows, m_rows, totals, currency="MYR", rate_note=""):
    pad = _S(24); gap = _S(22)
    row_h = _S(34); th_h = _S(34); head_h = _S(90); sec_gap = _S(20)
    f_title = _font(22, bold=True); f_sub = _font(13); f_th = _font(12, bold=True)
    f_cell = _font(12); f_cellb = _font(12, bold=True); f_small = _font(10)
    f_logo = _font(32); f_seclbl = _font(14, bold=True); f_sumlbl = _font(14, bold=True); f_sumnum = _font(20, bold=True)

    # 两个区共用列宽测量, 取并集让两个表对齐
    def venue_txt(r):
        return f"{r.get('场所','')} {r.get('包厢') or ''}".strip()

    k_spec = [("date", "日期", "l", f_cell, lambda r: r.get("日期", "")),
              ("artist", "艺人", "l", f_cellb, lambda r: r.get("艺人", "")),
              ("venue", "场所·包厢", "l", f_cell, venue_txt),
              ("guest", "客人", "l", f_cell, lambda r: r.get("客人") or "—"),
              ("K", "挂账", "r", f_cellb, lambda r: _fmt(r.get("K"))),
              ("O", "门票", "r", f_cell, lambda r: _fmt(r.get("O"))),
              ("recv", "应收", "r", f_cellb, lambda r: _fmt(r.get("应收")))]
    m_spec = [("date", "日期", "l", f_cell, lambda r: r.get("日期", "")),
              ("artist", "艺人", "l", f_cellb, lambda r: r.get("艺人", "")),
              ("venue", "场所·包厢", "l", f_cell, venue_txt),
              ("guest", "客人", "l", f_cell, lambda r: r.get("客人") or "—"),
              ("wp", "现金工价", "r", f_cellb, lambda r: _fmt(r.get("wp"))),
              ("flow", "流向", "l", f_cell, lambda r: str(r.get("流向") or "—")),
              ("rebate", "反水", "r", f_cellb, lambda r: _fmt(r.get("反水")) if r.get("反水") else "水已扣")]

    # 测量两区, 共享同名列取最大宽
    cw = {}
    for spec, rws in ((k_spec, k_rows), (m_spec, m_rows)):
        for key, hdr, al, f, fn in spec:
            w = max(_tw(hdr, f_th), max((_tw(fn(r), f) for r in rws), default=0))
            cw[key] = max(cw.get(key, 0), w)

    def layout(spec):
        order = [s[0] for s in spec]
        xL, xend = _layout({k: cw[k] for k in order}, order, pad + _S(12), gap)
        return xL, xend
    kxL, kend = layout(k_spec)
    mxL, mend = layout(m_spec)
    inner = max(kend, mend)
    W = inner + _S(12) + pad
    L, R = pad, W - pad

    # 计算高度
    def sec_h(rws):
        return _S(26) + th_h + (max(len(rws), 1)) * row_h
    H = head_h + pad + sec_h(k_rows) + sec_gap + sec_h(m_rows) + _S(18) + _S(96) + _S(50)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    cx, cy, cr = pad + _S(32), pad + _S(32), _S(32)
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], outline=DRED, width=max(1, _S(3)))
    d.text((cx, cy), "C", font=f_logo, fill=DRED, anchor="mm")
    d.text((pad + _S(80), pad + _S(20)), f"{mama_name} · 对账单", font=f_title, fill=DARK)
    d.text((pad + _S(80), pad + _S(50)), f"{period}", font=f_sub, fill=GRAY)

    y = pad + head_h

    def draw_section(title, spec, xL, rws, valfn):
        nonlocal y
        d.text((L, y), title, font=f_seclbl, fill=REDLINE, anchor="lm")
        y += _S(26)
        d.rounded_rectangle([L, y, R, y + th_h], radius=_S(5), fill=DRED)
        ty = y + th_h // 2
        for key, hdr, al, f, fn in spec:
            if al == "r":
                d.text((xL[key] + cw[key], ty), hdr, font=f_th, fill=WHITE, anchor="rm")
            else:
                d.text((xL[key], ty), hdr, font=f_th, fill=WHITE, anchor="lm")
        y += th_h
        if not rws:
            d.rectangle([L, y, R, y + row_h], fill=WHITE)
            d.text(((L + R) // 2, y + row_h // 2), "（无）", font=f_small, fill=GRAY, anchor="mm")
            y += row_h
            return
        d.rectangle([L, y, R, y + len(rws) * row_h], fill=WHITE)
        for i, r in enumerate(rws):
            ym = y + i * row_h + row_h // 2
            if i:
                d.line([L, y + i * row_h, R, y + i * row_h], fill=PINK, width=max(1, _S(1)))
            for key, hdr, al, f, fn in spec:
                v, clr = valfn(r, key)
                if al == "r":
                    d.text((xL[key] + cw[key], ym), v, font=f, fill=clr, anchor="rm")
                else:
                    d.text((xL[key], ym), v, font=f, fill=clr, anchor="lm")
        y += len(rws) * row_h

    def kval(r, key):
        return {"date": (r.get("日期", ""), DARK), "artist": (r.get("艺人", ""), DARK),
                "venue": (venue_txt(r), DARK), "guest": (r.get("客人") or "—", DARK),
                "K": (_fmt(r.get("K")), K_CLR), "O": (_fmt(r.get("O")), O_CLR),
                "recv": (_fmt(r.get("应收")), DARK)}[key]

    def mval(r, key):
        return {"date": (r.get("日期", ""), DARK), "artist": (r.get("艺人", ""), DARK),
                "venue": (venue_txt(r), DARK), "guest": (r.get("客人") or "—", DARK),
                "wp": (_fmt(r.get("wp")), M_CLR), "flow": (str(r.get("流向") or "—"), GRAY),
                "rebate": (_fmt(r.get("反水")) if r.get("反水") else "水已扣", REDLINE)}[key]

    draw_section("📋 挂账单", k_spec, kxL, k_rows, kval)
    y += sec_gap
    draw_section("💵 现金单", m_spec, mxL, m_rows, mval)
    y += _S(18)

    sum_h = _S(96)
    d.rounded_rectangle([L, y, R, y + sum_h], radius=_S(8), fill=PINK)
    cxm = (L + R) // 2
    d.text((cxm, y + _S(22)), "应结 C组", font=f_sumlbl, fill=DARK, anchor="mm")
    d.text((cxm, y + _S(52)), f"{_fmt(totals.get('应结'))} {currency}", font=f_sumnum, fill=DARK, anchor="mm")
    detail = f"挂账×80% {_fmt(totals.get('挂账',0)*0.8)} + 门票 {_fmt(totals.get('门票'))} − 现金反水 {_fmt(totals.get('反水'))}"
    d.text((cxm, y + _S(78)), detail, font=f_small, fill=GRAY, anchor="mm")
    y += sum_h + _S(14)

    foot = "请核对后结款"
    if rate_note:
        foot = rate_note + " · " + foot
    d.text((cxm, H - _S(20)), foot, font=f_small, fill=GRAY, anchor="mm")
    return _pngbytes(img)


# ───────────────────────────── 艺人月报 (工资单) ─────────────────────────────
def render_artist_payslip(artist_name, period, rows, total_wage_myr, rate=1.65,
                          direct_rows=None, currency="MYR"):
    """
    rows: [{'序','日期','场所','包厢','妈咪','客人','K','wp','O','分成'}]  分成=月底应结(可负)
    direct_rows: 直结单(不计工资) [{'日期','场所','妈咪','K'}]
    total_wage_myr: 实发(Σ月底应结, MYR)
    """
    direct_rows = direct_rows or []
    NEG = "#C0392B"
    pad = _S(24); gap = _S(20); row_h = _S(34); th_h = _S(34); head_h = _S(90)
    f_title = _font(22, bold=True); f_sub = _font(13); f_th = _font(12, bold=True)
    f_cell = _font(12); f_cellb = _font(12, bold=True); f_small = _font(10)
    f_logo = _font(32); f_sumlbl = _font(14, bold=True); f_sumnum = _font(22, bold=True)

    def venue_txt(r):
        return f"{r.get('场所','')} {r.get('包厢') or ''}".strip()

    def share_txt(r):
        v = r.get("分成", 0) or 0
        return f"{int(round(v)):+,}" if v else "—"

    spec = [("seq", "序", "l", f_cell, lambda r: str(r.get("序", ""))),
            ("date", "日期", "l", f_cellb, lambda r: r.get("日期", "")),
            ("venue", "场所·包厢", "l", f_cell, venue_txt),
            ("mama", "妈咪", "l", f_cell, lambda r: r.get("妈咪") or "自单"),
            ("guest", "客人", "l", f_cell, lambda r: r.get("客人") or "—"),
            ("K", "挂账", "r", f_cellb, lambda r: _fmt(r.get("K"))),
            ("wp", "现金", "r", f_cellb, lambda r: _fmt(r.get("wp"))),
            ("O", "门票", "r", f_cell, lambda r: _fmt(r.get("O"))),
            ("share", "分成", "r", f_cellb, share_txt)]
    cw = {}
    for key, hdr, al, f, fn in spec:
        cw[key] = max(_tw(hdr, f_th), max((_tw(fn(r), f) for r in rows), default=0))
    order = [s[0] for s in spec]
    xL, xend = _layout(cw, order, pad + _S(12), gap)
    W = xend + _S(12) + pad
    L, R = pad, W - pad

    sum_h = _S(86)
    dblock_h = (_S(30) + _S(28) + max(len(direct_rows), 1) * _S(28)) if direct_rows else 0
    H = head_h + pad + (th_h + max(len(rows), 1) * row_h) + _S(18) + sum_h + (_S(16) + dblock_h if direct_rows else 0) + _S(46)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    cx, cy, cr = pad + _S(32), pad + _S(32), _S(32)
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], outline=DRED, width=max(1, _S(3)))
    d.text((cx, cy), "C", font=f_logo, fill=DRED, anchor="mm")
    d.text((pad + _S(80), pad + _S(20)), f"{artist_name} · 工资单", font=f_title, fill=DARK)
    d.text((pad + _S(80), pad + _S(50)), f"{period}", font=f_sub, fill=GRAY)

    y = pad + head_h
    d.rounded_rectangle([L, y, R, y + th_h], radius=_S(6), fill=DRED)
    ty = y + th_h // 2
    for key, hdr, al, f, fn in spec:
        if al == "r":
            d.text((xL[key] + cw[key], ty), hdr, font=f_th, fill=WHITE, anchor="rm")
        else:
            d.text((xL[key], ty), hdr, font=f_th, fill=WHITE, anchor="lm")
    y += th_h
    d.rectangle([L, y, R, y + max(len(rows), 1) * row_h], fill=WHITE)
    for i, r in enumerate(rows):
        ym = y + i * row_h + row_h // 2
        if i:
            d.line([L, y + i * row_h, R, y + i * row_h], fill=PINK, width=max(1, _S(1)))
        share_v = r.get("分成", 0) or 0
        colors = {"seq": GRAY, "date": DRED, "venue": DARK, "mama": DARK, "guest": DARK,
                  "K": K_CLR, "wp": M_CLR, "O": O_CLR, "share": (NEG if share_v < 0 else DARK)}
        for key, hdr, al, f, fn in spec:
            if al == "r":
                d.text((xL[key] + cw[key], ym), fn(r), font=f, fill=colors[key], anchor="rm")
            else:
                d.text((xL[key], ym), fn(r), font=f, fill=colors[key], anchor="lm")
    y += max(len(rows), 1) * row_h + _S(18)

    # 实发
    d.rounded_rectangle([L, y, R, y + sum_h], radius=_S(8), fill=PINK)
    cxm = (L + R) // 2
    d.text((cxm, y + _S(22)), "实发工资", font=f_sumlbl, fill=DARK, anchor="mm")
    rmb = total_wage_myr * rate
    d.text((cxm, y + _S(52)), f"{_fmt(total_wage_myr)} {currency}  ≈  {_fmt(rmb)} RMB", font=f_sumnum, fill=DARK, anchor="mm")
    y += sum_h

    # 直结单(不计工资)
    if direct_rows:
        y += _S(16)
        d.rounded_rectangle([L, y, R, y + dblock_h], radius=_S(8), fill=WHITE, outline=REDLINE, width=max(1, _S(1)))
        d.text((L + _S(16), y + _S(18)), "⚠️ 直结单 · 妈咪直结（不计入工资）", font=f_cellb, fill=REDLINE, anchor="lm")
        yy = y + _S(40)
        d.text((L + _S(16), yy + _S(14)), "日期", font=f_small, fill=GRAY, anchor="lm")
        d.text((L + _S(90), yy + _S(14)), "场所", font=f_small, fill=GRAY, anchor="lm")
        d.text((L + _S(260), yy + _S(14)), "妈咪", font=f_small, fill=GRAY, anchor="lm")
        d.text((R - _S(16), yy + _S(14)), "挂账", font=f_small, fill=GRAY, anchor="rm")
        yy += _S(28)
        for r in direct_rows:
            d.text((L + _S(16), yy + _S(14)), r.get("日期", ""), font=f_cell, fill=DARK, anchor="lm")
            d.text((L + _S(90), yy + _S(14)), f"{r.get('场所','')} {r.get('包厢') or ''}".strip(), font=f_cell, fill=DARK, anchor="lm")
            d.text((L + _S(260), yy + _S(14)), r.get("妈咪", ""), font=f_cell, fill=DARK, anchor="lm")
            d.text((R - _S(16), yy + _S(14)), _fmt(r.get("K")), font=f_cellb, fill=K_CLR, anchor="rm")
            yy += _S(28)

    d.text((cxm, H - _S(20)), f"现金列显标准工价 · 自留单分成为倒扣（负） · 1MYR≈{rate}RMB", font=f_small, fill=GRAY, anchor="mm")
    return _pngbytes(img)
