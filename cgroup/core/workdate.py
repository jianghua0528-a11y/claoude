"""
C组 日期归属 (宪法 v1.0 · Block D)  ·  workdate.py
工作日窗口 = 中午 12:00 → 次日 12:00 算同一天。按上班时间归属:

  上班 12:00–23:59 → 当天        (自动)
  上班 00:00–05:59 → 前一天      (自动, 夜班延续)
  上班 06:00–11:59 → 待确认灰区  (永远 flag 让业务方逐单拍)

判定基准 = 群消息时间戳的日期, **不信**艺人填的"上班日期"(常笔误)。
业务方可覆盖: 给定真实日期 → 直接采用, 不再 flag。
"""
from datetime import date, timedelta
from typing import Optional, Tuple

GRAY_FLAG = "上班 6–12 点灰区(早班→当天 / 夜班延续→前一天), 归属待业务方确认"


def _parse_hm(t) -> Optional[Tuple[int, int]]:
    try:
        h, m = str(t).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except Exception:
        pass
    return None


def attribute_date(msg_date: date, clock_in,
                   override: Optional[date] = None) -> Tuple[date, Optional[str]]:
    """归属工作日。
    msg_date  : 群消息时间戳的日期 (判定基准)。
    clock_in  : 'HH:MM' 上班时间。
    override  : 业务方指定的真实日期; 给了就以它为准。
    返回 (biz_date, flag); flag 非空表示需人工确认。"""
    if override is not None:
        return override, None

    hm = _parse_hm(clock_in)
    if hm is None:
        return msg_date, "上班时间缺失/无法解析, 归属待确认"
    h, _ = hm

    if 12 <= h <= 23:
        return msg_date, None                       # 当天
    if 0 <= h < 6:
        return msg_date - timedelta(days=1), None    # 前一天 (夜班延续)
    return msg_date, GRAY_FLAG                        # 6–12 灰区: 默认当天 + flag
