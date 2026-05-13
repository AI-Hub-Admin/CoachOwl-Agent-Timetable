from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any, Dict, List, Tuple

import requests

from .constants import USDA_FDC_API_KEY
from .db import ensure_user, fetchall_dicts


_COMMON_CALORIES = {
    "big mac": 563,
    "fries": 365,
    "french fries": 365,
    "coke": 140,
    "cola": 140,
    "pizza slice": 285,
    "chicken breast": 165,
    "rice": 206,
    "banana": 105,
    "apple": 95,
}


def _split_meal_items(meal_text: str) -> List[str]:
    t = (meal_text or "").strip()
    t = re.sub(r"(?i)^(breakfast|lunch|dinner|snack)\s*[:\-]\s*", "", t).strip()
    if not t:
        return []
    parts = re.split(r",| and |\\+|;", t, flags=re.IGNORECASE)
    items = [p.strip() for p in parts if p.strip()]
    return items[:8]


def _fdc_search(query: str) -> Dict[str, Any]:
    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params = {"api_key": USDA_FDC_API_KEY}
    payload = {"query": query, "pageSize": 5}
    resp = requests.post(url, params=params, json=payload, timeout=12)
    resp.raise_for_status()
    return resp.json()


def _extract_kcal(food: Dict[str, Any]) -> float | None:
    for n in food.get("foodNutrients", []) or []:
        name = (n.get("nutrientName") or "").lower()
        unit = (n.get("unitName") or "").lower()
        if "energy" in name and unit in {"kcal", "kcals"}:
            try:
                return float(n.get("value"))
            except Exception:
                return None
    return None


def calculate_calories(meal_text: str) -> Dict[str, Any]:
    items = _split_meal_items(meal_text)
    if not items:
        return {"meal_text": meal_text, "items": [], "total_kcal": 0.0, "source": "none"}

    results: List[Dict[str, Any]] = []
    total = 0.0

    if USDA_FDC_API_KEY:
        for item in items:
            try:
                data = _fdc_search(item)
                foods = data.get("foods") or []
                if not foods:
                    results.append({"item": item, "kcal": None, "note": "No match"})
                    continue
                top = foods[0]
                kcal = _extract_kcal(top)
                if kcal is None:
                    results.append({"item": item, "kcal": None, "note": "No kcal nutrient"})
                    continue
                total += kcal
                results.append(
                    {
                        "item": item,
                        "kcal": kcal,
                        "description": top.get("description"),
                        "brand": top.get("brandName"),
                    }
                )
            except Exception:
                results.append({"item": item, "kcal": None, "note": "Lookup error"})

        return {"meal_text": meal_text, "items": results, "total_kcal": round(total, 1), "source": "usda_fdc"}

    # fallback
    for item in items:
        key = item.lower()
        kcal = None
        for k, v in _COMMON_CALORIES.items():
            if k in key:
                kcal = float(v)
                break
        if kcal is None:
            kcal = 250.0  # generic placeholder for unknown item
            note = "Estimated (no USDA key)"
        else:
            note = "Common estimate"
        total += kcal
        results.append({"item": item, "kcal": kcal, "note": note})

    return {"meal_text": meal_text, "items": results, "total_kcal": round(total, 1), "source": "estimate"}


def log_meal(conn: sqlite3.Connection, user_id: str, meal_text: str, calories: float, items: List[Dict[str, Any]]) -> str:
    ensure_user(conn, user_id)
    meal_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO meals(id, user_id, meal_text, calories, items_json) VALUES (?,?,?,?,?)",
        (meal_id, user_id, meal_text, float(calories), json.dumps(items, ensure_ascii=False)),
    )
    conn.commit()
    return meal_id


def list_meals(conn: sqlite3.Connection, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    ensure_user(conn, user_id)
    cur = conn.execute(
        "SELECT id, ts, meal_text, calories FROM meals WHERE user_id = ? ORDER BY ts DESC LIMIT ?",
        (user_id, int(limit)),
    )
    return fetchall_dicts(cur)

def nutrition_table_html(result: Dict[str, Any]) -> str:
    items = result.get("items") or []
    rows = []
    for it in items:
        kcal = it.get("kcal")
        kcal_str = f"{kcal:.0f}" if isinstance(kcal, (int, float)) else "-"
        note = it.get("note") or it.get("description") or ""
        rows.append(f"<tr><td>{_escape(it.get('item',''))}</td><td style='text-align:right'>{kcal_str}</td><td>{_escape(str(note))}</td></tr>")
    total = result.get("total_kcal", 0)
    source = result.get("source", "")
    return f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <div style="font-weight:600; margin-bottom:6px;">Nutrition (kcal) <span style="color:#666; font-weight:400; font-size:12px;">source: {source}</span></div>
  <table style="border-collapse:collapse; width:100%; font-size:14px;">
    <thead>
      <tr>
        <th style="text-align:left; border-bottom:1px solid #eee; padding:6px 4px;">Item</th>
        <th style="text-align:right; border-bottom:1px solid #eee; padding:6px 4px;">kcal</th>
        <th style="text-align:left; border-bottom:1px solid #eee; padding:6px 4px;">Note</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
      <tr>
        <td style="border-top:1px solid #eee; padding:6px 4px; font-weight:600;">Total</td>
        <td style="border-top:1px solid #eee; padding:6px 4px; text-align:right; font-weight:600;">{float(total):.0f}</td>
        <td style="border-top:1px solid #eee; padding:6px 4px;"></td>
      </tr>
    </tbody>
  </table>
</div>
""".strip()


def meals_history_html(meals: List[Dict[str, Any]]) -> str:
    if not meals:
        return "<div style='color:#666'>No meals logged yet.</div>"
    rows = []
    for m in meals:
        rows.append(
            f"<tr><td>{_escape(str(m.get('ts','')))}</td><td>{_escape(str(m.get('meal_text','')))}</td><td style='text-align:right'>{float(m.get('calories') or 0):.0f}</td></tr>"
        )
    return f"""
<div style="margin-top:10px;">
  <div style="font-weight:600; margin-bottom:6px;">Recent meals</div>
  <table style="border-collapse:collapse; width:100%; font-size:13px;">
    <thead>
      <tr>
        <th style="text-align:left; border-bottom:1px solid #eee; padding:6px 4px;">Time</th>
        <th style="text-align:left; border-bottom:1px solid #eee; padding:6px 4px;">Meal</th>
        <th style="text-align:right; border-bottom:1px solid #eee; padding:6px 4px;">kcal</th>
      </tr>
    </thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
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

