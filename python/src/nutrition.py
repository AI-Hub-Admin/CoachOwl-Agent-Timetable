from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import sqlite3
from zoneinfo import ZoneInfo

from .db import ensure_user, fetchall_dicts
from .utils import get_user_profile, get_user_timezone, llm_qwen_messages_chat
from .constants import QWEN_MODEL


def _parse_day(day_str: Optional[str]) -> date:
    if not day_str:
        return date.today()
    return date.fromisoformat(day_str)


def _local_day_bounds_utc(day: date, tz_name: str) -> Tuple[str, str]:
    """
    Convert a local day [00:00, next 00:00) in tz_name to UTC timestamps compatible
    with `meals.ts` (SQLite datetime('now') format: YYYY-MM-DD HH:MM:SS).
    """
    tz = ZoneInfo(tz_name)
    start_local = datetime.combine(day, time.min).replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return (
        start_utc.strftime("%Y-%m-%d %H:%M:%S"),
        end_utc.strftime("%Y-%m-%d %H:%M:%S"),
    )


def list_meals_for_day(conn: sqlite3.Connection, user_id: str, day: str | None = None) -> Dict[str, Any]:
    ensure_user(conn, user_id)
    tz_name = get_user_timezone(conn, user_id)
    d = _parse_day(day)
    start_utc, end_utc = _local_day_bounds_utc(d, tz_name)

    cur = conn.execute(
        """
        SELECT id, ts, meal_text, calories, items_json
        FROM meals
        WHERE user_id = ?
          AND ts >= ?
          AND ts < ?
        ORDER BY ts ASC
        """,
        (user_id, start_utc, end_utc),
    )
    meals = fetchall_dicts(cur)
    return {
        "user_id": user_id,
        "day": d.isoformat(),
        "timezone": tz_name,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "meals": meals,
    }


def _safe_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def summarize_meals_basic(meals: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_kcal = 0.0
    per_meal: List[Dict[str, Any]] = []
    max_meal: Optional[Dict[str, Any]] = None

    for m in meals:
        kcal = _safe_float(m.get("calories"))
        total_kcal += kcal
        entry = {"ts": m.get("ts"), "meal_text": m.get("meal_text"), "kcal": round(kcal, 1)}
        per_meal.append(entry)
        if max_meal is None or kcal > _safe_float(max_meal.get("kcal")):
            max_meal = {"ts": m.get("ts"), "meal_text": m.get("meal_text"), "kcal": round(kcal, 1)}

    return {
        "meal_count": len(meals),
        "total_kcal": round(total_kcal, 1),
        "avg_kcal_per_meal": round((total_kcal / len(meals)), 1) if meals else 0.0,
        "max_meal": max_meal,
        "meals": per_meal,
    }


async def nutritionist_summary(
    conn: sqlite3.Connection,
    user_id: str,
    day: str | None = None,
    with_llm: bool = True,
) -> Dict[str, Any]:
    """
    Daily eating summary. Uses deterministic aggregation first; optionally adds a short
    LLM "nutritionist note" if available.
    """
    data = list_meals_for_day(conn, user_id=user_id, day=day)
    meals = data.get("meals") or []
    basic = summarize_meals_basic(meals)

    result: Dict[str, Any] = {
        "ok": True,
        "user_id": user_id,
        "day": data.get("day"),
        "timezone": data.get("timezone"),
        "basic": basic,
        "nutritionist_note": "",
        "warnings": [],
    }

    if not meals:
        result["warnings"].append("No meals logged for this day.")
        result["nutritionist_note"] = "No meals logged today. If you ate, add a quick note like: 'breakfast: eggs and toast'."
        return result

    if not with_llm:
        return result

    try:
        profile = get_user_profile(conn, user_id) or {}
    except Exception:
        profile = {}

    # Keep prompt compact; the upstream chat system already has richer context.
    prompt = {
        "date": result["day"],
        "timezone": result["timezone"],
        "profile": profile,
        "meals": basic["meals"],
        "totals": {
            "meal_count": basic["meal_count"],
            "total_kcal": basic["total_kcal"],
        },
    }

    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful nutrition coach. Summarize the day briefly and suggest 2-4 actionable improvements. "
                "Do not diagnose or give medical advice. If data is incomplete, say so."
            ),
        },
        {
            "role": "user",
            "content": (
                "Summarize my eating for the day from this JSON and give practical suggestions:\n"
                + json.dumps(prompt, ensure_ascii=False)
            ),
        },
    ]
    llm = await llm_qwen_messages_chat(messages, model=QWEN_MODEL)
    note = (llm or {}).get("content") if isinstance(llm, dict) else None
    if isinstance(note, str):
        result["nutritionist_note"] = note.strip()
    else:
        result["warnings"].append("LLM did not return a note.")
    return result


def nutrition_summary_html(summary: Dict[str, Any]) -> str:
    basic = summary.get("basic") or {}
    meals = basic.get("meals") or []
    total = basic.get("total_kcal") or 0
    day = summary.get("day") or ""
    tz_name = summary.get("timezone") or ""
    note = (summary.get("nutritionist_note") or "").strip()

    rows = []
    for m in meals:
        rows.append(
            f"<tr><td>{_escape(str(m.get('ts','')))}</td><td>{_escape(str(m.get('meal_text','')))}</td>"
            f"<td style='text-align:right'>{_escape(str(m.get('kcal','')))}</td></tr>"
        )

    note_html = f"<div style='margin-top:10px; padding:10px; background:#f8fafc; border:1px solid #e5e7eb; border-radius:10px;'>{_escape(note)}</div>" if note else ""

    return f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <div style="font-weight:700; font-size:16px;">Nutrition Summary</div>
  <div style="color:#64748b; margin-top:2px; font-size:12px;">{_escape(day)} · {tz_name}</div>
  <div style="margin-top:10px; display:flex; gap:10px; flex-wrap:wrap;">
    <div style="padding:8px 10px; border:1px solid #e5e7eb; border-radius:10px; background:#fff;">
      <div style="font-size:12px; color:#64748b;">Total kcal</div>
      <div style="font-size:18px; font-weight:700;">{float(total):.0f}</div>
    </div>
    <div style="padding:8px 10px; border:1px solid #e5e7eb; border-radius:10px; background:#fff;">
      <div style="font-size:12px; color:#64748b;">Meals</div>
      <div style="font-size:18px; font-weight:700;">{int(basic.get('meal_count') or 0)}</div>
    </div>
  </div>
  <div style="margin-top:12px;">
    <table style="border-collapse:collapse; width:100%; font-size:13px;">
      <thead>
        <tr>
          <th style="text-align:left; border-bottom:1px solid #eee; padding:6px 4px;">Time (UTC)</th>
          <th style="text-align:left; border-bottom:1px solid #eee; padding:6px 4px;">Meal</th>
          <th style="text-align:right; border-bottom:1px solid #eee; padding:6px 4px;">kcal</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
  {note_html}
</div>
""".strip()


def _escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )

