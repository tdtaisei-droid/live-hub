"""iCalendar (.ics) の VEVENT。会場がカレンダーを公開している場合に使う。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from . import RawItem


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _val(s: str) -> str:
    return s.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").strip()


def _dt(v: str) -> Optional[datetime]:
    v = v.strip().rstrip("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def parse_ics(text: str, base_url: str = "") -> list[RawItem]:
    out: list[RawItem] = []
    cur: Optional[dict] = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            title = cur.get("SUMMARY", "")
            start = _dt(cur.get("DTSTART", ""))
            if title and start:
                end = _dt(cur.get("DTEND", ""))
                end_d: Optional[date] = None
                if end:
                    # 終日予定の DTEND は翌日を指すので1日戻す
                    end_d = (end - timedelta(days=1)).date() if len(cur.get("DTEND", "")) == 8 else end.date()
                    if end_d <= start.date():
                        end_d = None
                out.append(RawItem(
                    title=title,
                    url=cur.get("URL") or base_url,
                    summary=cur.get("DESCRIPTION", "")[:1000],
                    start_date=start.date(),
                    end_date=end_d,
                    venue=cur.get("LOCATION") or None,
                    structured=True,
                ))
            cur = None
        elif cur is not None and ":" in line:
            key, val = line.split(":", 1)
            cur[key.split(";")[0].upper()] = _val(val)
    return out
