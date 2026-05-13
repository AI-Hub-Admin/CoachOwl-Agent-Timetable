from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from datetime import date, datetime, timedelta

from .db import ensure_user, fetchall_dicts
from .utils import get_user_day, habit_log_format_mapper, habit_format_mapper, convert_date_to_str, calculate_date_diff, calculate_dates_between, get_utc_date_str, agent_task_log_format_mapper
from .constants import HABIT_KIND_TASK, HABIT_KIND_OBJECTIVE, HABIT_LOG_TYPE_HUMAN, LOG_ENABLE, HABIT_LOG_TYPE_AGENT, KEY_AGENT_TASK_LOGS, AGENT_TASK_LOG_MAX_NUMBER, HABIT_LOG_MAX_NUMBER

def parse_habit_command(prompt: str) -> Dict[str, Any]:
    """
    Very small NLU for habits/objectives.
    Returns dict with action: create|checkin|list and optional fields.
    """
    p = (prompt or "").strip()
    lower = p.lower()

    if any(k in lower for k in ["list habits", "show habits", "my habits", "objectives"]):
        return {"action": "list"}

    if any(k in lower for k in ["check in", "checkin", "done today", "mark done", "completed today"]):
        return {"action": "checkin"}

    # create objective: "Sugar Free for 10 days", "10-day Coffee Free"
    days = None
    m = re.search(r"(\d+)\s*[- ]?\s*day", lower)
    if m:
        try:
            days = int(m.group(1))
        except Exception:
            days = None

    interval_hours = None
    m2 = re.search(r"every\s*(\d+)\s*hours?", lower)
    if m2:
        try:
            interval_hours = int(m2.group(1))
        except Exception:
            interval_hours = None

    # habit name guess
    name = p
    # strip common framing
    name = re.sub(r"(?i)^(set|create|start|add)\s+(a\s+)?(habit|objective|goal)\s*[:\-]?\s*", "", name).strip()
    name = re.sub(r"(?i)\s*for\s*\d+\s*[- ]?\s*days?\s*$", "", name).strip()
    if not name:
        name = "New Habit"

    kind = "interval" if interval_hours else "objective"
    if days is None and kind == "objective":
        days = 10  # sensible default aligned with PRD examples

    return {
        "action": "create",
        "habit_name": name,
        "kind": kind,
        "target_days": days,
        "interval_hours": interval_hours,
    }


def create_habit(
    conn: sqlite3.Connection,
    user_id: str,
    name: str,
    kind: str = "objective",
    category: Optional[str] = None,
    parent_id: Optional[str] = None,
    target_days: Optional[int] = 10,
    interval_hours: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    task_type: str = "human",
    agent_model: str = "default",
    status: str = "active",
    content:  str = "",
    result: Optional[str] = None,
    ):
    try:
        """
            Two ways of creation
            start_date + target_date
            start_date + end_date
        """

        habit_id = str(uuid.uuid4())
        if LOG_ENABLE:
            print (f"DEBUG: Habit_id {habit_id} Before Normalizing start_date {start_date} | end_date {end_date} | target_days {target_days}")

        # Use Pacific Time (UTC-8)
        start_date = start_date or get_utc_date_str()
        ## end_date = start_date + target_days - 1
        if (end_date is None and target_days is not None):
            ## target_days,end_date=None
            target_days = target_days or 1  ## at least 1 day period,
            ## target_days: 1, start and end date are the same
            days_diff = 0 if target_days is None else (target_days - 1 if target_days >= 1 else 0)
            ### Default Filling to Make Sture End Date is larger than Start Date
            end_date = end_date or calculate_date_diff(start_date, days_diff)
        elif (end_date is not None and target_days is None):
            ## target_days=None,end_date
            target_days = calculate_dates_between(start_date, end_date)
        elif (end_date is None and target_days is None):
            ## target_days=None,end_date=None
            target_days = 1
            end_date = start_date
        else:
            ## target_days,end_date
            target_days = calculate_dates_between(start_date, end_date)
        if LOG_ENABLE:
            print (f"DEBUG: Habit_id {habit_id} After Normalizing start_date {start_date} | end_date {end_date} | target_days {target_days}")

        start_time = start_time or "00:00:00"
        end_time = end_time or "23:59:59"

        conn.execute(
            """
            INSERT INTO habits(id, user_id, name, content, kind, category, parent_id, target_days, interval_hours, start_date, end_date, start_time, end_time, task_type, agent_model, status, result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (habit_id, user_id, name, content, kind, category, parent_id, target_days, interval_hours, start_date, end_date, start_time, end_time, task_type, agent_model, status,
             result),
        )
        conn.commit()
        return get_habit(conn, user_id, habit_id)

    except Exception as e:
        print (f"DEBUG: create_habit failed with error: {e}")
        return {}

def get_habit(conn: sqlite3.Connection, user_id: str, habit_id: str) -> Dict[str, Any]:
    """
        Get the Habit and Habit Logs Below to the habit_id, user_id and agent respond to user
    """
    cur = conn.execute(
        "SELECT * FROM habits WHERE id = ? AND user_id = ? AND archived = 0",
        (habit_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        return {}
    habit = dict(row)
    habit["logs"] = list_habit_logs(conn, habit_id, limit=HABIT_LOG_MAX_NUMBER)
    habit["progress"] = compute_progress(habit)
    if habit.get("task_type") == HABIT_LOG_TYPE_AGENT:
        ## logs are two stage, first get recent 100, then choose latest 20 display in increasing order
        habit[KEY_AGENT_TASK_LOGS] = list_agent_task_logs(conn, habit_id, limit=AGENT_TASK_LOG_MAX_NUMBER)

    if LOG_ENABLE:
        print (f"DEBUG: /get_habit Habit Agent Task Logs {habit.get(KEY_AGENT_TASK_LOGS, [])}")

    habit_mapped = habit_format_mapper(habit)
    return habit_mapped

def list_habits(conn: sqlite3.Connection, user_id: str,
                category: Optional[str] = None,
                date: Optional[str] = None,
                task_type: Optional[str] = None,
                archived: int = 0,
                fill_agent_task_log_enable: bool = False,
                ) -> List[Dict[str, Any]]:
    ensure_user(conn, user_id)

    ## default to select available not archived tasks
    query = f"SELECT * FROM habits WHERE user_id = ? AND archived = {archived}"
    params = [user_id]

    # category filter
    if category:
        query += " AND category = ?"
        params.append(category)

    if date:
        query += " AND start_date <= ? AND end_date >= ?"
        params.extend([date, date])

    if task_type:
        query += " AND task_type = ?"
        params.extend([task_type])

    query += " ORDER BY created_at DESC"

    cur = conn.execute(query, tuple(params))
    habits = fetchall_dicts(cur)

    habits_mapped = []
    for h in habits:
        h["logs"] = list_habit_logs(conn, h["id"], limit=60)
        h["progress"] = compute_progress(h)
        ## fill agent_task_logs
        if fill_agent_task_log_enable and h.get("task_type") == HABIT_LOG_TYPE_AGENT:
            h[KEY_AGENT_TASK_LOGS] = list_agent_task_logs(conn, h["id"], limit=AGENT_TASK_LOG_MAX_NUMBER)
        habits_mapped.append(habit_format_mapper(h))

    return habits_mapped

def claim_habit_task(conn: sqlite3.Connection, user_id: str,):
    """
        Task: Habit: user_id is used to save each agent it assigsn to? Now Default to "Agent"
    """
    ensure_user(conn, user_id)

def checkin(conn: sqlite3.Connection, user_id: str, habit_id: str, note: str = "", image_url: str = "", value: int = 1, log_type: str = HABIT_LOG_TYPE_HUMAN,
            parent_log_id: str = None,
            execution_id: str = None,
            root_execution_id: str = None,
            status: str = "") -> Dict[str, Any]:
    """
        habit db, value:  0: upcoming, 1: Check-in Success, 2: Missing Checked In
        image_url: "," separated

        status: starting, idle, running, completed, (agent)
    """
    ensure_user(conn, user_id)
    log_id = str(uuid.uuid4())
    # UTC timestamp (source of truth)
    ts = datetime.utcnow().isoformat()

    # USER LOCAL DAY (critical fix)
    day = get_user_day(conn, user_id)
    conn.execute(
        "INSERT INTO habit_logs(id, habit_id, user_id, ts, day, note, image_url, value, log_type, parent_log_id, execution_id, root_execution_id, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (log_id, habit_id, user_id, ts, day, note, image_url, value, log_type, parent_log_id, execution_id, root_execution_id, status),
    )
    conn.commit()
    return {"ok": True, "log_id": log_id, "habit": get_habit(conn, user_id, habit_id)}

def undo_checkin(conn: sqlite3.Connection, user_id: str, habit_id: str) -> Dict[str, Any]:
    """Reverts the most recent checkin (value > 0) for a habit to uncheck it."""
    ensure_user(conn, user_id)
    cur = conn.execute(
        "SELECT id FROM habit_logs WHERE habit_id = ? AND user_id = ? AND value > 0 ORDER BY ts DESC LIMIT 1",
        (habit_id, user_id)
    )
    row = cur.fetchone()
    if row:
        conn.execute(
            "UPDATE habit_logs SET value = 0 WHERE id = ?",
            (row["id"],)
        )
        conn.commit()
    return {"ok": True, "habit": get_habit(conn, user_id, habit_id)}


def delete_habit(conn: sqlite3.Connection, user_id: str, habit_id: str) -> bool:
    """Archives a habit (objective or task) and its children (if it's an objective)."""
    # Archive the habit itself
    cur = conn.execute(
        "UPDATE habits SET archived = 1 WHERE id = ? AND user_id = ?",
        (habit_id, user_id),
    )
    # Archive its children if any
    conn.execute(
        "UPDATE habits SET archived = 1 WHERE parent_id = ? AND user_id = ?",
        (habit_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0

def delete_habit_all(conn: sqlite3.Connection, user_id: str) -> bool:
    """Archives a habit (objective or task) and its children (if it's an objective)."""
    # Archive the habit itself
    cur = conn.execute(
        "UPDATE habits SET archived = 1 WHERE user_id = ?",
        (user_id,),
    )
    # Archive its children if any
    conn.execute(
        "UPDATE habits SET archived = 1 WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()
    return cur.rowcount > 0

# def list_habit_logs(conn: sqlite3.Connection, user_id: str, habit_id: str, limit: int = 60) -> List[Dict[str, Any]]:
#     cur = conn.execute(
#         "SELECT * FROM habit_logs WHERE user_id = ? AND habit_id = ? ORDER BY ts DESC LIMIT ?",
#         (user_id, habit_id, int(limit)),
#     )
#     return fetchall_dicts(cur)

def list_habit_logs(conn: sqlite3.Connection, habit_id: str, limit: int = 60) -> List[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM habit_logs WHERE habit_id = ? ORDER BY ts DESC LIMIT ?",
        ( habit_id, int(limit)),
    )
    rows = fetchall_dicts(cur)
    return [habit_log_format_mapper(r) for r in rows]


def list_agent_task_logs(conn: sqlite3.Connection, habit_id: str, limit: int = 60) -> List[Dict[str, Any]]:
    """
        Select Columns From table: agent_execution_logs
    """
    cur = conn.execute(
        "SELECT * FROM agent_execution_logs WHERE habit_id = ? ORDER BY ts DESC LIMIT ?",
        ( habit_id, int(limit)),
    )
    rows = fetchall_dicts(cur)
    return [agent_task_log_format_mapper(r) for r in rows]


def list_habit_logs_with_replies(
    conn: sqlite3.Connection,
    user_id: str,
    habit_id: str,
    limit: int = 60
) -> List[Dict[str, Any]]:
    """
        Suitable for Timeline Generation
    """
    cur = conn.execute("""
        SELECT 
            l.*,
            r.id   AS reply_id,
            r.note AS agent_reply,
            r.ts   AS reply_ts
        FROM habit_logs l
        LEFT JOIN habit_logs r
          ON r.parent_log_id = l.id
         AND r.log_type = 'agent'
        WHERE l.user_id = ?
          AND l.habit_id = ?
          AND l.log_type = 'user'
        ORDER BY l.ts DESC
        LIMIT ?
    """, (user_id, habit_id, int(limit)))

    rows = fetchall_dicts(cur)
    return [habit_log_format_mapper(r) for r in rows]

def compute_progress(habit: Dict[str, Any]) -> Dict[str, Any]:
    """
        {"kind": "interval", "last_checkin": last_ts, "interval_hours": habit.get("interval_hours")}

        {"kind": "objective", "target_days": target_days, "days_done": done_count, "days_done_list": sorted(days_done)}
    """
    kind = habit.get("kind") or "objective"
    logs = habit.get("logs") or []
    if kind == "interval":
        last_ts = logs[0]["ts"] if logs else None
        return {"kind": "interval", "last_checkin": last_ts, "interval_hours": habit.get("interval_hours")}

    target_days = int(habit.get("target_days") or 0)
    # completion by day (only count valid checkins, not just notes)
    days_done = set()
    for log in logs:
        # value > 0 means it's a checkin tick
        if log.get("value", 1) > 0:
            d = log.get("day")
            if d:
                days_done.add(d)
    done_count = len(days_done)
    return {"kind": "objective", "target_days": target_days, "days_done": done_count, "days_done_list": sorted(days_done)}


def seed_onboarding_data(conn: sqlite3.Connection, user_id: str):
    """Seed initial data for a new user if they have no habits yet."""
    try:

        # Ensure user exists in users table
        ensure_user(conn, user_id)

        # start_date
        start_date = get_user_day(conn, user_id)

        # Check if user has any habits already, calculate all the non-archived not deleted tasks
        cur = conn.execute("SELECT count(*) as cnt FROM habits WHERE user_id = ? AND archived = 0", (user_id,))
        cur_records = cur.fetchone()
        current_records_cnt = cur_records["cnt"]
        if LOG_ENABLE:
            print (f"DEBUG: Add OnBoarding data for user_id {user_id}| cur_records {dict(cur_records)}| data {start_date} seed_onboarding_data")

        if current_records_cnt > 0:
            return

        # 1. Fitness Onboarding
        fit_obj = create_habit(
            conn, user_id,
            name="Lose Weight by 5 pounds(2.3kg)",
            kind=HABIT_KIND_OBJECTIVE,
            category="fitness",
            start_date=start_date,
            target_days=30
        )
        if fit_obj:
            create_habit(
                conn, user_id,
                name="Eat vegetable and Fruits",
                kind=HABIT_KIND_TASK,
                category="fitness",
                start_date=start_date,
                parent_id=fit_obj.get("id"),
                target_days=30
            )
            create_habit(
                conn, user_id,
                name="Sugar Free for 3 days",
                kind=HABIT_KIND_TASK,
                category="fitness",
                parent_id=fit_obj.get("id"),
                start_date=start_date,
                target_days=3)

            # 1c. Agent Task for Fitness
            create_habit(
                conn, user_id,
                name="AI Nutritionist: Monitor & Summarize Eating",
                kind=HABIT_KIND_TASK,
                category="fitness",
                parent_id=fit_obj.get("id"),
                target_days=30,
                start_date=start_date,
                task_type="agent"
            )

        # 2. Career Onboarding
        car_obj = create_habit(
            conn, user_id,
            name="Got promoted to manager role",
            kind=HABIT_KIND_OBJECTIVE,
            category="career",
            start_date=start_date,
            target_days=30
        )
        if car_obj:
            create_habit(
                conn, user_id,
                name="1/1 talk and career path with your manager now",
                kind=HABIT_KIND_TASK,
                category="career",
                parent_id=car_obj.get("id"),
                start_date=start_date,
                target_days=1
            )
            create_habit(
                conn, user_id,
                name="Coffee Chat with excellent performance worker at least twice a week",
                kind=HABIT_KIND_TASK,
                category="career",
                parent_id=car_obj.get("id"),
                start_date=start_date,
                target_days=30
            )

    except Exception as e:
        print (f"seed_onboarding_data Failed with exception: {e}")

def pick_most_recent_habit(conn: sqlite3.Connection, user_id: str) -> Optional[str]:
    cur = conn.execute(
        "SELECT id FROM habits WHERE user_id = ? AND archived = 0 ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    return str(row["id"]) if row else None

def select_user_config(conn: sqlite3.Connection, user_id: str, config_type: str) -> List[Dict[str, Any]]:
    """
        Return: List of Dict
    """
    cur = conn.execute(
        "SELECT user_id, type, config_json FROM user_configs WHERE user_id = ? AND type = ?",
        (user_id, config_type)
    )
    ### type, config_json
    rows = fetchall_dicts(cur)
    return rows

