"""
名字解析  ·  core/names.py
报单里的名字五花八门(别名/助理/带C组后缀/大小写), 这里统一认回字典里的正名。
审核确认(网页+TG)、改单、财务录入共用。
"""
import re

from ..db.models import Artist, Mama, MamaAssistant, Venue

_SPLIT = re.compile(r"[,，、/]+")
_SUFFIX = re.compile(r"\s*[cC]组\s*$")


def _aliases(s):
    return [a.strip() for a in _SPLIT.split(s or "") if a.strip()]


def _norm(name):
    return _SUFFIX.sub("", (name or "").strip()).lower()


def find_artist(session, name):
    """正名 → 别名(大小写不敏感, 去 'C组' 后缀)。找不到返回 None。"""
    key = _norm(name)
    if not key:
        return None
    rows = session.query(Artist).all()
    for a in rows:
        if a.name.lower() == key:
            return a
    for a in rows:
        if key in (x.lower() for x in _aliases(a.aliases)):
            return a
    return None


def find_mama(session, name):
    """主妈咪正名 → 别名 → 助理名(助理认回其主妈咪)。"""
    key = _norm(name)
    if not key:
        return None
    rows = session.query(Mama).all()
    for m in rows:
        if m.name.lower() == key:
            return m
    for m in rows:
        if key in (x.lower() for x in _aliases(m.aliases)):
            return m
    for asst in session.query(MamaAssistant).all():
        if asst.name.lower() == key:
            return asst.mama
    return None


def find_venue(session, name):
    key = (name or "").strip().lower()
    if not key:
        return None
    rows = session.query(Venue).all()
    for v in rows:
        if v.name.lower() == key:
            return v
    for v in rows:
        if key in (x.lower() for x in _aliases(v.aliases)):
            return v
    return None
