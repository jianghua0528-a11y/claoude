"""
录单确定性校验/补全层 (宪法 v1.0 · Block D/F/J 接线)  ·  enrich.py
LLM 解析出工单后, 跑确定性引擎做认人 / 日期归属 / 工时反推校验, 把「必问清单」
落到 warnings 上, 供人工审核。设计原则: 系统主动推断, 只在真歧义处问。

输入 payload 用录单中文键: 艺人/场所/包厢/妈咪/助理/合作模式/客人/K/M/O/流向/上班/下班/日期/备注。
"""
from .workdate import attribute_date
from .pricing import work_hours, standard_base, TIER_PRICE
from .directory import resolve_mama

# 适用工时反推校验的分成预设 (标准平单口径; 自单/自定义不反推)
_RECON_MODES = {"标准", "代收无水", "无水单"}
_TIER_PRICES = {v for v in TIER_PRICE.values() if v is not None}


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def enrich_order(payload, *, msg_date=None, entries=None, date_override=None):
    """对单条解析结果跑确定性校验/补全。返回 (enriched_payload, warnings)。"""
    out = dict(payload)
    warnings = []

    # ── Block J: 认人 (妈咪/助理) ──
    if entries is not None and payload.get("妈咪"):
        mr = resolve_mama(entries, payload["妈咪"])
        if mr.mama:
            out["妈咪"] = mr.mama
            if mr.assistant and not out.get("助理"):
                out["助理"] = mr.assistant
        if mr.candidates:
            out["_妈咪候选"] = mr.candidates
        if mr.flag:
            warnings.append(mr.flag)

    # ── Block D: 日期归属 (基准=群消息时间戳) ──
    if msg_date is not None:
        biz, flag = attribute_date(msg_date, payload.get("上班"), override=date_override)
        out["日期"] = biz.isoformat()
        if flag:
            warnings.append(flag)

    # ── Block F: 工时反推校验 ──
    hours = work_hours(payload.get("上班"), payload.get("下班"))
    if hours is not None:
        out["工时"] = round(hours, 2)
        mode = payload.get("合作模式") or ""
        K = _num(payload.get("K"))
        if mode in _RECON_MODES and K > 0:
            base = standard_base(hours)
            # 命中工时底价 或 某档位标价 → 放行; 否则 flag 让业务方核
            if abs(K - base) >= 1 and K not in _TIER_PRICES:
                warnings.append(f"挂账{K:g}与工时应有底价{base:g}不符(非标价档),请核")

    return out, warnings
