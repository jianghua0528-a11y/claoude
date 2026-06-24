"""从解析/审核的 payload 建 Order —— 网页后台确认 和 TG 按钮确认 共用。"""
from datetime import date, datetime

from ..db.models import Order, Artist, Mama, Venue


def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if s else date.today()
    except Exception:
        return date.today()


# 旧分成档 → 宪法预设 (兼容 LLM/历史 payload)
_PRESET_ALIAS = {"直结": "无水单", "全归艺人": "自定义"}


def _resolve_preset(d, mama):
    """payload 合作模式 → (preset, cust_a, cust_m, cust_c)。"""
    raw = d.get("合作模式") or ("标准" if mama else "自单")
    preset = _PRESET_ALIAS.get(raw, raw)
    if raw == "全归艺人":                       # 旧"全归艺人"(100/0/0) → 自定义
        return "自定义", 1.0, 0.0, 0.0
    if preset == "自定义":
        return preset, d.get("cust_a"), d.get("cust_m"), d.get("cust_c")
    return preset, None, None, None


def create_order_from_payload(d, session, source_msg_id=None):
    """d: 审核 payload(中文键)。建 Order(已审核)并加入 session, 返回该 Order。"""
    art = session.query(Artist).filter_by(name=(d.get("艺人") or "")).first()
    mama = session.query(Mama).filter_by(name=(d.get("妈咪") or "")).first()
    ven = session.query(Venue).filter_by(name=(d.get("场所") or "")).first()
    preset, ca, cm, cc = _resolve_preset(d, mama)
    o = Order(
        biz_date=_parse_date(d.get("日期")),
        artist_id=art.id if art else None,
        venue_id=ven.id if ven else None,
        room=d.get("包厢"), booker=d.get("助理"),
        mama_id=mama.id if mama else None,
        preset=preset, cust_a=ca, cust_m=cm, cust_c=cc,
        flow=d.get("流向"),
        credit_k=float(d.get("K", 0) or 0),
        cash_m=float(d.get("M", 0) or 0),
        ticket_o=float(d.get("O", 0) or 0),
        customer=d.get("客人"),
        start_time=str(d.get("上班") or "")[:8],
        end_time=str(d.get("下班") or "")[:8],
        remark=d.get("备注"), status="已审核", source_msg_id=source_msg_id)
    session.add(o)
    return o
