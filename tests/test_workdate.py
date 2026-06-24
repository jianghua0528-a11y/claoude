"""
Block D 日期归属回归测试  ·  test_workdate.py
"""
from datetime import date

import pytest

from cgroup.core.workdate import attribute_date, GRAY_FLAG

REF = date(2026, 6, 23)


# ─────────────────────── 当天段 12:00–23:59 ───────────────────────
@pytest.mark.parametrize("t", ["12:00", "14:30", "20:00", "23:59"])
def test_same_day_auto(t):
    d, flag = attribute_date(REF, t)
    assert d == REF
    assert flag is None


# ─────────────────────── 前一天段 00:00–05:59 ───────────────────────
@pytest.mark.parametrize("t", ["00:00", "00:50", "02:00", "05:59"])
def test_prev_day_auto(t):
    d, flag = attribute_date(REF, t)
    assert d == date(2026, 6, 22)
    assert flag is None


def test_prev_day_crosses_month():
    d, flag = attribute_date(date(2026, 6, 1), "02:00")
    assert d == date(2026, 5, 31)
    assert flag is None


# ─────────────────────── 灰区 06:00–11:59 ───────────────────────
@pytest.mark.parametrize("t", ["06:00", "08:45", "11:59"])
def test_gray_zone_flags(t):
    d, flag = attribute_date(REF, t)
    assert d == REF          # 默认当天
    assert flag == GRAY_FLAG


def test_gray_zone_xiaoyuer_example():
    # 宪法例: 6/23 小渔儿 上班 8:45 早班 → 灰区 flag, 默认当天
    d, flag = attribute_date(date(2026, 6, 23), "08:45")
    assert d == date(2026, 6, 23)
    assert flag is not None


# ─────────────────────── 业务方覆盖 ───────────────────────
def test_override_wins():
    # 灰区时间但业务方指定真实日期 → 用 override, 不 flag
    d, flag = attribute_date(REF, "08:45", override=date(2026, 6, 23))
    assert d == date(2026, 6, 23)
    assert flag is None


def test_override_beats_auto_segment():
    d, flag = attribute_date(REF, "02:00", override=date(2026, 6, 23))
    assert d == date(2026, 6, 23)   # 覆盖前一天的自动判定
    assert flag is None


# ─────────────────────── 边界/异常 ───────────────────────
def test_bad_clock_in_flags():
    d, flag = attribute_date(REF, "")
    assert d == REF
    assert flag is not None and "待确认" in flag


def test_boundary_noon_is_same_day():
    assert attribute_date(REF, "12:00")[1] is None     # 12:00 = 当天
    assert attribute_date(REF, "11:59")[1] == GRAY_FLAG  # 11:59 = 灰区
