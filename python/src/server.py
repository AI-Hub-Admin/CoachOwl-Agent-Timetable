from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import traceback
import uuid
from datetime import date, datetime
from typing import Any, AsyncGenerator, Dict, List, Optional
from datetime import date, datetime, timedelta
import httpx

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

from .constants import (
    AGENT_NAME,
    CONTENT_TYPE_HTML,
    CONTENT_TYPE_MARKDOWN,
    DB_PATH,
    INTERNAL_API_KEY,
    LOG_ENABLE,
    MESSAGE_TYPE_ASSISTANT,
    OUTPUT_FORMAT_HTML,
    OUTPUT_FORMAT_TEXT,
    STATIC_DIR,
    ASSETS_DIR,
    PROMPT_DIR,
    USER_INTENT_LIST,
    TASK_PROMPT_ACTION_LIST,
    TASK_PROMPT_ACTION_DELETE,
    TASK_PROMPT_ACTION_CHECKIN,
    TASK_PROMPT_ACTION_CREATE,
    TASK_PROMPT_ACTION_DELETE,
    TASK_PROMPT_ACTION_UPDATE,
    TASK_PROMPT_ACTION_SELECT,
    KEY_TARGET_DAYS,
    USER_INTENT_FITNESS,
    USER_INTENT_DEFAULT,
    DEPLOYED_SUBDOMAIN,
    QWEN_MODEL,
    KEY_COOKIE_USER_ID,
    KEY_CONTENT,
    KEY_SUCCESS,
    LOCAL_AGENTS_ENABLE,
    INTERNAL_API_KEY_COACHOWL, CLIENT_ID_ANDROID, CLIENT_ID_IOS,
    DEFAULT_AGENT_BACKGROUND_TASK_CONTENT,
    HABIT_LOG_TYPE_AGENT,
    DEFAULT_USER_TIMEZONE,
    KEY_USER_ID, QWEN_API_KEY,
    KEY_STATUS, KEY_CREDENTIAL,
    CONFIG_TYPE_CONNECTED_AGENTS,
    KEY_AGENT_CLIS_AVAILABLE,
    KEY_AGENT_TASK_DASHBOARD_STATUS,
    KEY_AGENT_TASK_LOGS,
    KEY_AGENT_TASK_RUNNING_LOG,
    AGENT_TASK_LOG_MAX_NUMBER,
    AGENT_META_DICT,
    BACKGROUND_TASK_RUNNING_INTERNAL_SECONDS,
    KEY_TASK_TYPE,
    AGENT_TASK_STATUS_IDLE,
    DEFAULT_ASSIGNED_AGENT_ID,
    DEFAULT_ASSIGNED_AGENT_NAME,
    KEY_AGENT_EXECUTION_STATUS,
    AGENT_TASK_STATUS_RUNNING,
    AGENT_TASK_STATUS_COMPLETED, AGENT_TASK_STATUS_FAILURE,
    AGENT_TASK_STATUS_SCHEDULED,
    DEFAULT_AGENT_ID,
    DEFAULT_AGENT_NAME
)
from .db import connect, init_db
from .fitness import calculate_calories, log_meal, meals_history_html, nutrition_table_html, list_meals
from .nutrition import nutritionist_summary, nutrition_summary_html
from .habits import (
    checkin,
    undo_checkin,
    create_habit,
    get_habit,
    list_habits,
    delete_habit,
    select_user_config,
    parse_habit_command,
    pick_most_recent_habit,
    seed_onboarding_data,
    delete_habit_all
)
from .intent import route_intent
from .career import career_advice_markdown
from .utils import assembly_message, generate_session_id, generate_user_id, get_new_message_id, get_user_last_input, read_files, llm_qwen_messages_chat, is_valid_html, generate_agent_user_id, get_user_day, get_user_profile, is_valid_output, calculate_date_diff, get_today_str, get_utc_date_str, fill_agent_running_task_id, is_valid_result_output, calculate_dates_between, get_user_datetime, get_user_timezone, format_ts
from .agents import agent_workflow_entry, create_agent_execution_plan, get_agent_task_state, get_agent_task_owner_execution_logs, update_agent_execution_plan_status, update_agent_execution_logs, create_execution_logs, select_pending_agent_execution_task, select_pending_agent_execution_task_size
from . import global_variables as gv
from .user_authentification import router as auth_router
from .db import ensure_user, fetchall_dicts

mcp = FastMCP(AGENT_NAME, json_response=True)
mcp_app = mcp.streamable_http_app()


## Call LLM to get the branch
intent_prompt_file_path = str(PROMPT_DIR / "intent_examples.md")
print(f"Loading intent_prompt_file_path from path {intent_prompt_file_path}")
INTENT_SYSTEM_PROMPT = read_files(intent_prompt_file_path)

coach_prompt_file_path = str(PROMPT_DIR / "coach_examples.md")
print(f"Loading coach_prompt_file_path from path {coach_prompt_file_path}")
COACH_SYSTEM_PROMPT = read_files(coach_prompt_file_path)

templates = Jinja2Templates(directory="static")

def _require_internal_key(request: Request) -> None:
    provided = request.headers.get("x-internal-api-key") or ""
    # Support the frontend hardcoded key as fallback
    valid_keys = {INTERNAL_API_KEY_COACHOWL}
    if INTERNAL_API_KEY:
        valid_keys.add(INTERNAL_API_KEY)
        
    if provided not in valid_keys:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API Key")

async def _require_user_authen_onekey(request: Request) -> None:
    """
    """
    user_info = get_temp_user_id_info()
    user_id = user_info.get(KEY_USER_ID) or ""
    return user_id

@mcp.tool()
def calculate_calories_tool(meal_text: str) -> Dict[str, Any]:
    return calculate_calories(meal_text)

@mcp.tool()
async def nutritionist_summary_tool(user_id: str, day: str | None = None, with_llm: bool = True) -> Dict[str, Any]:
    conn = connect()
    try:
        return await nutritionist_summary(conn, user_id=user_id, day=day, with_llm=with_llm)
    finally:
        conn.close()


@mcp.tool()
def create_habit_tool(user_id: str, name: str, target_days: int = 10) -> Dict[str, Any]:
    conn = connect()
    try:
        return create_habit(conn, user_id=user_id, name=name, kind="objective", target_days=target_days)
    finally:
        conn.close()


@mcp.tool()
def habit_checkin_tool(user_id: str, habit_id: str, note: str = "") -> Dict[str, Any]:
    conn = connect()
    try:
        return checkin(conn, user_id=user_id, habit_id=habit_id, note=note)
    finally:
        conn.close()


@mcp.tool()
def career_advice_tool(prompt: str) -> Dict[str, Any]:
    return {"markdown": career_advice_markdown(prompt)}


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "db_path": str(DB_PATH)})

async def api_create_habit(request: Request) -> JSONResponse:
    """
        API Get Start Date From Front End: Use Local Date, Not UTC Date
        habit#agent_model: default local agent loop, codex/claude/openclaw (OneKey Gateway Locally)
    """
    _require_internal_key(request)

    conn = connect()
    try:
        body = await request.json()
        user_id = str(body.get("user_id") or generate_session_id())
        habit_id = body.get("habit_id")
        name = str(body.get("name") or "New Habit")
        kind = str(body.get("kind") or "objective")
        content = str(body.get("content") or "")
        category = body.get("category")
        parent_id = body.get("parent_id")
        target_days = body.get("target_days")
        interval_hours = body.get("interval_hours")
        start_date = body.get("start_date")
        end_date = body.get("end_date")
        agent_model = body.get("agent_model")
        task_type = body.get("task_type")

        ## Optional, format: hh:mm
        start_time = body.get("start_time", None)
        end_time = body.get("end_time", None)

        if LOG_ENABLE:
            print (f"DEBUG /api_create_habit input body {body}")
        if LOG_ENABLE:
            print (f"DEBUG: UPDATE input start_date {start_date} | target_days {target_days} | end_date {end_date} ")

        if habit_id:
            # Fetch existing habit to preserve missing fields
            existing_dict = get_habit(conn, user_id, habit_id)
            if LOG_ENABLE:
                print (f"DEBUG: Habit Id {habit_id} existing_dict {existing_dict}")

            exist_target_days = existing_dict.get("target_days")
            exist_start_date = existing_dict.get("start_date")
            exist_end_date = existing_dict.get("end_date")
            exist_agent_model = existing_dict.get("agent_model")
            exist_task_type = existing_dict.get("task_type")
            exist_name = existing_dict.get("name")
            exist_content = existing_dict.get("content")

            if LOG_ENABLE:
                print (f"DEBUG: UPDATE exist_start_date {exist_start_date} | exist_target_days {exist_target_days} | exist_end_date {exist_end_date} ")

            ## Date First Fix Start Date to avoid null
            start_date = start_date or exist_start_date
            if (target_days is None and end_date is not None):
                target_days = calculate_dates_between(start_date, end_date)
            elif (target_days is not None and end_date is None):
                end_date = calculate_date_diff(start_date, target_days)
            elif (target_days is None and end_date is None):
                target_days = 1
                end_date = start_date
            else:
                ## recalculate new target dats
                target_days = calculate_dates_between(start_date, end_date)

            ## Compare with Existing Fields and update
            ## Start Date
            start_date = start_date if (start_date is not None and start_date != exist_start_date) else exist_start_date
            target_days = target_days if (target_days is not None and target_days != exist_target_days) else exist_target_days
            end_date = end_date if (end_date is not None and end_date != exist_end_date) else exist_end_date
            ## calculate new name
            name = name if (name is not None and name != exist_name) else exist_name
            content = content if (content is not None and content != exist_content) else exist_content
            agent_model = agent_model if (agent_model is not None and agent_model != exist_agent_model) else exist_agent_model
            task_type = task_type if (task_type is not None and task_type != exist_task_type) else exist_task_type

            if LOG_ENABLE:
                print (f"DEBUG: UPDATE final start_date {start_date} | target_days {target_days} | end_date {end_date} ")

            # Update existing habit
            query = "UPDATE habits SET name = ?, content = ?, target_days = ?, start_date = ?, end_date = ?, start_time = ? , end_time = ?, agent_model = ?, task_type = ? WHERE id = ? AND user_id = ?"
            parameters = (name, content, target_days, start_date, end_date, start_time, end_time, agent_model, task_type, habit_id, user_id)
            if LOG_ENABLE:
                print(f"DEBUG: api_create_habit SQL query {query} and Parameters {parameters}")
            conn.execute(query, parameters)
            conn.commit()
            return JSONResponse({"ok": True, "habits": [get_habit(conn, user_id, habit_id)]})
        else:
            habit = create_habit(
                conn,
                user_id=user_id,
                name=name,
                kind=kind,
                category=category,
                parent_id=parent_id,
                target_days=int(target_days) if target_days is not None else 10,
                interval_hours=int(interval_hours) if interval_hours is not None else None,
                start_date=start_date,
                end_date=end_date,
                start_time=start_time,
                end_time=end_time,
                task_type=task_type,
                agent_model=agent_model,
                content=content
            )

            ## Create Human/Agent Execution
            if habit[KEY_TASK_TYPE] == HABIT_LOG_TYPE_AGENT:
                ### Assign execution plan to agents
                agents = [{
                    "agent_id": habit.get("agent_model", DEFAULT_AGENT_ID),
                    "agent_name": habit.get("agent_model", DEFAULT_AGENT_NAME)
                }]
                res_plan = create_agent_execution_plan(
                    conn,
                    habit_id=habit.get("id"),
                    start_date=habit.get("start_date", None),
                    end_date=habit.get("end_date", None),
                    target_days=habit.get("target_days", 1),
                    agents=agents
                )

            return JSONResponse({"ok": True, "user_id": user_id, "habits": [habit] })
    except Exception as e:
        print (f"DEBUG: api_create_habit Exception {e}")
    finally:
        conn.close()

async def api_list_habits(request: Request) -> JSONResponse:
    """
        Query Tasks given input user_id, if no records are found, return dummy
         /api/v1/habits/list?user_id=DerekZZ&category=career&date=2026-04-26&timezone=Asia%2FShanghai
    """
    _require_internal_key(request)
    user_id = request.query_params.get("user_id") or ""
    category = request.query_params.get("category")
    date = request.query_params.get("date") ## format: YYYY-MM-DD
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    conn = connect()
    try:
        ## get all the available user objectives
        ## check if user_id have 0 records, if more than 0, return, if 0 records for user_id, create dummy ones
        seed_onboarding_data(conn, user_id)
        print (f"INFO: api_list_habits user_id {user_id}| querying date {date}")
        date = date or get_user_day(conn, user_id)
        print (f"INFO: api_list_habits user_id {user_id}| final user date {date}")
        # user_objective = filter_current_user_objective(user_id)
        user_objective = filter_current_user_objective_by_date(user_id, date)
        if LOG_ENABLE:
            print(f"DEBUG: api_list_habits Current User Objective user_id {user_id} and date {date}: {user_objective}")
        result = {"ok": True, "habits": list_habits(conn, user_id, category=category, date=date)}
        if LOG_ENABLE:
            print(f"DEBUG: api_list_habits returning result {str(result)[0:200]} ...")
        return JSONResponse(result)
    finally:
        conn.close()

def _force_task_to_create_json(result_json: List[Dict[str, Any]],
                                user_objective: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parses and enforces correctness of the LLM JSON output.
    Expected format: List of tasks with name, target_days, task_type.

    Generate Tasks Might be aligned with start_date of user_objective
    """
    if not result_json:
        print (f"DEBUG: _force_task_to_create_json input {result_json} empty...")
        return []
    try:
        objective_start_date = user_objective.get("start_date", "")
        objective_end_date = user_objective.get("end_date", "")
        objective_target_days = user_objective.get("target_days", "")
        objective_category = user_objective.get("category", "")

        # Clean JSON string if LLM included markdown blocks
        validated = []
        ## Generate a List of Tasks: task
        for item in result_json:
            ## AI Generate tasks
            if isinstance(item, dict) and "name" in item and "target_days" in item:
                task_start_date = item.get("start_date") or objective_start_date
                task_target_days = int(item.get("target_days", "1"))
                task_end_date = calculate_date_diff(task_start_date, task_target_days)

                validated.append({
                    "name": str(item["name"]),
                    "kind": "task",
                    "category": objective_category,
                    "start_date": task_start_date,
                    "end_date": task_end_date,
                    "target_days": int(item["target_days"]),
                    "task_type": str(item.get("task_type", "human"))
                })
        return validated
    except Exception as e:
        print(f"Error parsing LLM task JSON: {e}")
        return []

async def api_generate_tasks(request: Request) -> JSONResponse:
    """
        Now: OnLy Update, don't remove...

        Logic: Create Execution Plan during Objective/Agent Task Auto-Creation

        DEBUG: api_generate_tasks Current User ID Json Body: {'user_id': 'bde3194e6e52de0da194001e509acac8', 'category': 'career', 'parent_id': 'f04c89d1-c78d-4ca7-8213-d504aa2a99ac'}
    """
    _require_internal_key(request)
    body = await request.json()
    user_id = str(body.get("user_id") or "")
    category = str(body.get("category") or "general")
    parent_id = str(body.get("parent_id") or "")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    print (f"DEBUG: api_generate_tasks Current User ID Json Body: {body}...")

    ## Todo, Get the objective by parent_id from db
    conn = connect()
    parent_obj = {}
    try:
        cur = conn.execute("SELECT id, name, category, start_date, end_date, target_days FROM habits WHERE id = ? AND user_id = ?", (parent_id, user_id))
        row = cur.fetchone()
        if row:
            parent_obj = dict(row)
    finally:
        conn.close()

    ### Objective Start Date
    ### {'name': 'May 13-MAy 30 Prep For Athlethes Example', 'category': 'fitness'}|current_user_objective {"name": "May 13-MAy 30 Prep For Athlethes Example", "category": "fitness"}
    current_user_objective = parent_obj
    print (f"DEBUG: api_generate_tasks parent_obj {parent_obj}|current_user_objective {current_user_objective}")

    mock_enable = False
    tasks_to_create = []
    if mock_enable:
        # Mock task generation logic
        if category == "fitness":
            tasks_to_create = [
                {"name": "Morning HIIT Session", "kind": "task", "target_days": 1, "task_type": "human"},
                {"name": "Protein Intake (120g)", "kind": "task", "target_days": 10, "task_type": "human"},
                {"name": "AI Calorie Consumption Summary", "kind": "task", "target_days": 15, "task_type": "agent"}
            ]
        elif category == "career":
            tasks_to_create = [
                {"name": "Quarterly Review Prep", "kind": "task", "target_days": 1, "task_type": "human"},
                {"name": "Network with Stakeholders", "kind": "task", "target_days": 10, "task_type": "human"},
                {"name": "AI Career Path Optimization", "kind": "task", "target_days": 5, "task_type": "agent"}
            ]
        else:
            tasks_to_create = [
                {"name": f"Practice {category}", "kind": "task", "target_days": 30, "task_type": "human"},
                {"name": f"AI {category} Performance Review", "kind": "task", "target_days": 7,
                 "task_type": "agent"}
            ]
    else:
        ### Call LLM
        conn = connect()
        try:
            # Get current active tasks for context
            active_habits = list_habits(conn, user_id, category=category)

            ## category only activities
            user_activities = []
            for h in active_habits:
                prog = h.get("progress", {})
                user_activities.append(h)

            # User profile/intent info
            coach_system_prompt = COACH_SYSTEM_PROMPT
            user_prompt = """
            ### User Input
            user_input: {USER_INPUT}
            user_objective: {USER_OBJECTIVE}
            user_history_activities: {USER_HISTORY_ACTIVITIES}
            """.format(
                USER_INPUT="",
                USER_OBJECTIVE=json.dumps(current_user_objective),
                USER_HISTORY_ACTIVITIES=json.dumps(user_activities),
            )

            result_json = llm_qwen_messages_chat(coach_system_prompt, user_prompt, QWEN_MODEL)
            tasks_to_create = _force_task_to_create_json(result_json, current_user_objective)

        finally:
            conn.close()

    if LOG_ENABLE:
        print (f"DEBUG: api_generate_tasks objective Id {parent_id} tasks_to_create: {tasks_to_create}")

    conn = connect()
    try:
        # Check if objective parent exists
        cur = conn.execute("SELECT id FROM habits WHERE id = ? AND user_id = ?", (parent_id, user_id))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Objective not found")

        # Instead of deleting, iterate and create or ignore duplicates
        for task in tasks_to_create:
            if LOG_ENABLE:
                print (f"/api_generate_tasks Generating New Task {task}")
            try:
                ## Create Habit
                habit = create_habit(
                    conn,
                    user_id=user_id,
                    name=task.get("name"),
                    kind=task.get("kind"),
                    category=task.get("category"),
                    parent_id=parent_id,
                    target_days=task.get("target_days", 1),
                    task_type=task.get("task_type", "human"), ##  Task Type Assigned Human/Agent
                    agent_model=task.get("agent_model", "default"),
                    interval_hours=None,
                    start_date=task.get("start_date", None),
                    end_date=task.get("end_date", None),
                    start_time=task.get("start_time", None),
                    end_time=task.get("end_time", None)
                )

                ## Create Human/Agent Execution
                if habit[KEY_TASK_TYPE] == HABIT_LOG_TYPE_AGENT:
                    agents = [{
                        "agent_id": habit.get("agent_model", DEFAULT_AGENT_ID),
                        "agent_name": habit.get("agent_model", DEFAULT_AGENT_NAME)
                    }]

                    ret = create_agent_execution_plan(
                        conn,
                        habit_id = habit.get("id"),
                        start_date = habit.get("start_date", None),
                        end_date = habit.get("end_date", None),
                        target_days = habit.get("target_days", 1),
                        agents = agents
                    )

            except Exception as e:
                print (f"/api_generate_tasks Failed to Add Task task with error {e}")

        current_active_habits = list_habits(conn, user_id, category=category)
        if LOG_ENABLE:
            print (f"DEBUG: api_generate_tasks Final Generating Tasks: {current_active_habits}")
        return JSONResponse({"ok": True, "habits": current_active_habits})
    finally:
        conn.close()

async def api_get_habit(request: Request) -> JSONResponse:
    _require_internal_key(request)
    user_id = request.query_params.get("user_id") or ""
    habit_id = request.query_params.get("habit_id") or ""
    if not user_id or not habit_id:
        raise HTTPException(status_code=400, detail="user_id and habit_id are required")
    conn = connect()
    try:
        habit = get_habit(conn, user_id, habit_id)
        if not habit:
            raise HTTPException(status_code=404, detail="habit not found")

        ## fill external information for display
        # habit = fill_agent_task_running_status(habit)
        if habit.get(KEY_TASK_TYPE) == "agent":
            ## fill running task log
            habit = fill_agent_task_running_log_output(habit)
            ## fill agent tasks execution status
            ### agent-status-strip <-> habit.agent_execution_status
            habit = fill_agent_task_execution_status(habit, user_id)

        if LOG_ENABLE:
            print (f"DEBUG: api_get_habit Output habit {habit}|user_id {user_id}|habit_id {habit_id}")

        return JSONResponse({"ok": True, "habits": [habit]})
    finally:
        conn.close()

def fill_agent_task_execution_status(habit, user_id: str):
    """
        fill_agent_task_execution_status
        user_date: YYYY-MM-DD

        $.$status
        $.$assigned_agent_id
        $.$assigned_agent_name
    """
    conn = connect()
    try:
        if LOG_ENABLE:
            print (f"DEBUG: fill_agent_task_execution_status input user_id: {user_id}| habit {habit}")
        habit_id = habit.get("id")
        user_date = get_user_day(conn, user_id)
        ##
        habit_result = get_agent_task_state(conn, habit_id, user_date)
        ## Owner
        # habit_task_owner = get_agent_task_owner(conn, user_id, habit_id)
        habit_task_owner = get_agent_task_owner_execution_logs(conn, habit_id, user_date)
        ## update
        result = {
            "status": habit_result.get("status", AGENT_TASK_STATUS_IDLE),
            "assigned_agent_id": habit_task_owner.get("assigned_agent_id", DEFAULT_ASSIGNED_AGENT_ID),
            "assigned_agent_name": habit_task_owner.get("assigned_agent_name", DEFAULT_ASSIGNED_AGENT_NAME),
        }

        habit[KEY_AGENT_EXECUTION_STATUS] = result

        return habit
    except Exception as e:
        print (f"fill_agent_task_execution_status failed with error {e}")
        return habit

def fill_agent_task_running_log_output(habit):
    """
    """
    try:
        agent_task_logs = habit.get(KEY_AGENT_TASK_LOGS, [])
        if LOG_ENABLE:
            print(f"")

        # Pre-parse once (FAST)
        parsed_logs = []
        for log in agent_task_logs:
            ts_str = log.get("ts", "")
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except:
                ts = datetime.min

            parsed_logs.append((ts, log))

        # Fast sort (no repeated parsing)
        parsed_logs.sort(key=lambda x: x[0])

        # Limit early (reduces work)
        parsed_logs = parsed_logs[:AGENT_TASK_LOG_MAX_NUMBER]

        # Build HTML
        lines = [
            f"<li class='flex items-center gap-3'><span>{format_ts(log.get('ts'))}</span><span>{log.get('message')}</span></li>"
            for _, log in parsed_logs
        ]
        ##
        lines = lines[0:AGENT_TASK_LOG_MAX_NUMBER] if len(lines) > AGENT_TASK_LOG_MAX_NUMBER else lines
        output_content = "".join(lines)
        habit[KEY_AGENT_TASK_RUNNING_LOG] = f'<ul class="flex flex-col gap-[20px]">{output_content}</ul>'
        return habit

    except Exception as e:
        print (f"fill_agent_task_running_log_output failed with error {e}")
        return habit

def fill_agent_task_running_status(habit):
    """
    Adds runtime dashboard status into habit:
    - idle (no logs)
    - or latest log status (agent runtime state)

    DEBUG: Output habit
        {'id': 'f4556797-0002-4900-83f9-7b5910bc4df8',
        'user_id': 'DerekZZ',
        'name': 'AI Nutritionist: Monitor & Summarize Eating',
        'kind': 'task', 'category': 'fitness', 'parent_id': '4d8d922e-0ce3-476e-90e7-e383faedcfd7', 'target_days': 30, 'interval_hours': None, 'start_date': '2026-04-27', 'end_date': '2026-05-25', 'start_time': '00:00:00', 'end_time': '23:59:59', 'agent_model': 'default',
        'task_type': 'agent', 'status': 'active', 'result': None, 'archived': 0, 'created_at': '2026-04-27 12:53:01', 'logs': [],
        'progress': {'kind': 'objective', 'target_days': 30, 'days_done': 0, 'days_done_list': []},
        'ts': '',
        'task_dashboard_status': 'idle', 'task_runtime_seconds': 0, 'task_last_update': None}|user_id DerekZZ|habit_id f4556797-0002-4900-83f9-7b5910bc4df8
    """
    try:
        logs = habit.get("logs", [])

        # 1. No logs → idle
        if not logs or len(logs) == 0:
            habit["task_dashboard_status"] = "idle"
            habit["task_runtime_seconds"] = 0
            habit["task_last_update"] = None
            return habit

        # 2. Sort logs by ts (safe ordering)
        def parse_ts(log):
            try:
                return datetime.strptime(log.get("ts", ""), "%Y-%m-%d %H:%M:%S")
            except:
                return datetime.min

        logs_sorted = sorted(logs, key=parse_ts, reverse=True)

        latest_log = logs_sorted[0]
        print (f"DEBUG: Current Task Latest Log: {latest_log}")

        # 3. Extract status (your key requirement)
        task_status = latest_log.get("status")

        # fallback logic if status is missing
        if not task_status:
            note = (latest_log.get("note") or "").lower()

            if "start" in note:
                task_status = "running"
            elif "done" in note or "completed" in note:
                task_status = "completed"
            else:
                task_status = "active"

        # 4. Compute runtime (optional but useful)
        try:
            start_time = parse_ts(logs_sorted[-1])
            now = datetime.utcnow()
            runtime_seconds = int((now - start_time).total_seconds())
        except:
            runtime_seconds = 0

        # 5. Inject dashboard fields
        habit[KEY_AGENT_TASK_DASHBOARD_STATUS] = task_status
        habit["task_runtime_seconds"] = runtime_seconds
        habit["task_runtime_hours"] = round(runtime_seconds / 3600, 2)
        habit["task_last_update"] = latest_log.get("ts")
        habit["task_last_note"] = latest_log.get("note")

        return habit
    except Exception as e:
        print (f"Failed to fill_agent_task_running_status with error {e}")
        return habit

async def api_delete_habit(request: Request) -> JSONResponse:
    _require_internal_key(request)
    body = await request.json()
    user_id = str(body.get("user_id") or "")
    habit_id = str(body.get("habit_id") or "")
    if not user_id or not habit_id:
        raise HTTPException(status_code=400, detail="user_id and habit_id are required")
    conn = connect()
    try:
        success = delete_habit(conn, user_id, habit_id)
        return JSONResponse({"ok": success})
    finally:
        conn.close()


async def api_delete_habit_all(request: Request) -> JSONResponse:
    _require_internal_key(request)
    body = await request.json()
    user_id = str(body.get("user_id") or "")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id and habit_id are required")
    conn = connect()
    try:
        success = delete_habit_all(conn, user_id)
        return JSONResponse({"ok": success})
    finally:
        conn.close()

import base64
from pathlib import Path

async def api_checkin(request: Request) -> JSONResponse:
    """
        1. Each User Check In or Save Note, Upload Image will Create a Check-In and
        HabitLog

        2. Call Agent Workflow to give feedback to that Log in backend.

        CheckIn Create a New Log to Existing Habit

        Habit
        {
          'id': 'c9b63a3c-ab71-457c-bb60-8afbb971ffb9',
          'user_id': 'TEMP_4727',
          'name': 'Track daily steps (goal: 8,000+)',
          'kind': 'task',
          'category': 'fitness',
          'parent_id': 'a5cd82ad-db35-480f-b162-3b37d6cc28b5',
          'target_days': 7,
          'interval_hours': None,
          'start_date': '2026-04-22',
          'end_date': None,
          'start_time': '00:00:00',
          'end_time': '23:59:59',
          'agent_model': 'default',
          'task_type': 'human',
          'status': 'active',
          'result': None,
          'archived': 0,
          'created_at': '2026-04-22 15:04:25',
          'logs': []
        }

        Log Schema
        {
          'id': '6b7826b6-563d-4c0b-b013-0e54f37bba99',
          'habit_id': 'c9b63a3c-ab71-457c-bb60-8afbb971ffb9',
          'user_id': 'FITNESS Agent',
          'parent_log_id': '9db73a10-14c3-4aa4-b161-16c07103bac1',
          'log_type': 'agent',
          'ts': '2026-04-23T02:59:14.539254',
          'day': '2026-04-23',
          'note': 'Agent Running Tasks Finished...',
          'image_url': '',
          'value': 1
        }
    """
    _require_internal_key(request)
    body = await request.json()
    user_id = str(body.get("user_id") or "")
    habit_id = str(body.get("habit_id") or "")
    note = str(body.get("note") or "")
    image_base64 = body.get("image_base64")
    image_ext = str(body.get("image_ext") or "jpg")
    value = int(body.get("value", 1))

    if not user_id or not habit_id:
        raise HTTPException(status_code=400, detail="user_id and habit_id are required")
    
    image_url = ""
    if image_base64:
        try:
            # Handle data:image/jpeg;base64, prefix if present
            if "," in image_base64:
                image_base64 = image_base64.split(",")[1]
            
            # Check 5MB limit (base64 size * 0.75 gives approx byte size)
            if len(image_base64) * 0.75 > 5 * 1024 * 1024:
                return JSONResponse({"ok": False, "message": "Image exceeds 5MB limit."}, status_code=400)
            
            image_data = base64.b64decode(image_base64)
            file_name = f"{uuid.uuid4().hex}.{image_ext}"
            
            # /static/{user_id}/{habit_id}/{file_name}
            user_dir = STATIC_DIR / user_id / habit_id
            user_dir.mkdir(parents=True, exist_ok=True)
            
            file_path = user_dir / file_name
            with open(file_path, "wb") as f:
                f.write(image_data)

            ## image url must be in absolute url path, otherwise other apps won't find
            image_url = f"{DEPLOYED_SUBDOMAIN}/static/{user_id}/{habit_id}/{file_name}"
        except Exception as e:
            print(f"Error saving image: {e}")

    conn = connect()
    try:
        ## 1. Check In Results
        ### {"ok": True, "log_id": log_id, "habit": get_habit(conn, user_id, habit_id)}
        checkin_results = checkin(conn, user_id=user_id, habit_id=habit_id, note=note, image_url=image_url, value=value)

        ### 2. Logs: Format:
        log_id = checkin_results.get("log_id", "")
        habit= checkin_results.get("habit", {})
        target_habit_log = {}
        if habit is not None and len(habit) > 0:
            habit_logs = habit.get("logs", [])
            progress = habit.get("progress", [])
            for habit_log in habit_logs:
                if habit_log.get("id", "") == log_id:
                    target_habit_log = habit_log

        print (f"DEBUG: api_checkin Checkin log_id {log_id}| habit {habit} | habit_id {habit_id} target_habit_log {target_habit_log}")

        ### 3. Start a background no hanging Task to
        asyncio.create_task(
            agent_background_respond_to_log(
                habit_id=habit_id,
                log_id=log_id,
                habit_log=target_habit_log,
                note=note,
                image_url=image_url,
                category=habit.get("category", "fitness")
            )
        )

        return JSONResponse(checkin_results)
    finally:
        conn.close()

async def agent_background_respond_to_log(
    habit_id: str,
    log_id: str,
    habit_log: Dict,
    note: str,
    image_url: str,
    category: str
) -> Dict:
    """
    Background agent response for a single user log.

    - Creates AI interpretation
    - Writes agent reply as a new habit_log
    - Links reply via parent_log_id

    e.g. INFO: Agent fitness Agent replied to log 61c54ade-1310-43d5-abdb-b892ec26ab90
    """

    conn = connect()

    try:
        ### default agent_result
        agent_result_content = ""

        if (len(habit_log) == 0 or
                (habit_log.get("note", "") == "" and habit_log.get("image_url", "") == "")):
            ## 1. Input Empty -> No Agent Check-In -> No Front End Log Display
            agent_result_content = ""
            print (f"DEBUG: Checkin Empty Input| habit_id {habit_id}|log_id {log_id}|habit_log {habit_log}|agent_result_content {agent_result_content}")
        else:
            # 1. Build prompt
            user_checkin_prompt = f"User Check In With Note: {note} "
            if image_url is not None and image_url.strip() != "":
                user_checkin_prompt += f", Upload image_url: {image_url}"

            # 2. Run agent
            agent_result_json = await agent_workflow_entry(
                user_checkin_prompt,
                category
            )

            if agent_result_json:
                agent_result_content = agent_result_json.get(
                    KEY_CONTENT,
                    DEFAULT_AGENT_BACKGROUND_TASK_CONTENT
                )

        # 3. Create agent identity
        agent_user_id = generate_agent_user_id(category)

        # 4. Save agent reply as a log (IMPORTANT: link to user log)
        if LOG_ENABLE:
            print (f"DEBUG: /checkin user_id {agent_user_id}|habit_id {habit_id}|note {agent_result_content} | value 1 | parent_log_id {log_id} | log_type {HABIT_LOG_TYPE_AGENT}")

        ## user_id: Coach Agent,habit_id: xxxx, note: xxxx, parent_log_id: reply to parent, log_type: HABIT_LOG_TYPE_AGENT

        agent_checkin_result = {}
        if (agent_result_content):
            agent_checkin_result = checkin(
                conn,
                user_id=agent_user_id,
                habit_id=habit_id,
                note=agent_result_content,
                value=1,
                parent_log_id=log_id,
                log_type=HABIT_LOG_TYPE_AGENT
            )
            conn.commit()
            print(f"INFO: Agent {agent_user_id} |success|replied to log {log_id}| agent_checkin_result {agent_checkin_result}")
        else:
            print(f"INFO: Agent {agent_user_id} |passed|replied to log {log_id}| agent_checkin_result {agent_checkin_result}")

        return {
            "ok": True,
            "result": agent_checkin_result
        }

    except Exception as e:
        print(f"ERROR: agent_background_respond_to_log failed: {e}")

        return {
            "ok": False,
            "error": str(e)
        }

    finally:
        conn.close()

async def api_undo_checkin(request: Request) -> JSONResponse:
    _require_internal_key(request)
    body = await request.json()
    user_id = str(body.get("user_id") or "")
    habit_id = str(body.get("habit_id") or "")
    if not user_id or not habit_id:
        raise HTTPException(status_code=400, detail="user_id and habit_id are required")
    conn = connect()
    try:
        return JSONResponse(undo_checkin(conn, user_id, habit_id))
    finally:
        conn.close()

async def api_save_coach_setting(request: Request) -> JSONResponse:
    _require_internal_key(request)
    body = await request.json()
    user_id = body.get("user_id")
    tab_name = body.get("tab_name")
    status = body.get("status", "active")
    prompt = body.get("prompt", "")
    if not user_id or not tab_name:
        raise HTTPException(status_code=400, detail="user_id and tab_name are required")

    conn = connect()
    try:
        from .db import ensure_user
        ensure_user(conn, user_id)
        setting_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO coach_settings (id, user_id, tab_name, status, prompt) 
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, tab_name) DO UPDATE SET 
                status=excluded.status, 
                prompt=excluded.prompt
            """,
            (setting_id, user_id, tab_name, status, prompt)
        )
        conn.commit()
        return JSONResponse({"ok": True})
    finally:
        conn.close()

async def api_get_coach_settings(request: Request) -> JSONResponse:
    _require_internal_key(request)
    user_id = request.query_params.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    conn = connect()
    try:
        cur = conn.execute("SELECT * FROM coach_settings WHERE user_id = ? AND status = 'active'", (user_id,))
        from .db import fetchall_dicts
        return JSONResponse({"ok": True, "coaches": fetchall_dicts(cur)})
    finally:
        conn.close()

async def api_delete_coach_setting(request: Request) -> JSONResponse:
    _require_internal_key(request)
    body = await request.json()
    user_id = body.get("user_id")
    tab_name = body.get("tab_name")
    if not user_id or not tab_name:
        raise HTTPException(status_code=400, detail="user_id and tab_name are required")
    
    conn = connect()
    try:
        conn.execute(
            "DELETE FROM coach_settings WHERE user_id = ? AND tab_name = ?",
            (user_id, tab_name)
        )
        conn.commit()
        return JSONResponse({"ok": True})
    finally:
        conn.close()


## --------- Running Agent Task in Backgrounds --------
async def process_agent_task_job(task: Dict[Any, Any]):
    """
        Process Agent Task Input Dict

        Claim Result:
            ## Claim Result ID:
            INFO: ----------- Step 1 Default Running Claim results {'success': True, 'habit_id': '95c27aa3-cba4-436d-8318-b63035cf6351', 'execution_date': '2026-05-11', 'root_id': 'f1ce0464-fa60-497d-b7b1-fe20c381d6eb', 'child_id': 'a7ca747b-0808-4af7-a9f5-3e000b149eb6', 'agent_id': 'default'}

        Execution Result:
        1. According to Execution Data: 20260511 Get Execution ID
        INFO: ----------- Step 2 Update Execution Plan Status results {'success': True, 'habit_id': '56ab98ac-8208-426a-8c92-7c2798d7a36e', 'execution_id': '56ab98ac-8208-426a-8c92-7c2798d7a36e:2026-05-11:default', 'updated_agents': 0, 'logs_written': 1}


    """
    conn = connect()
    final_result = {}
    try:
        user_id = task["user_id"]
        habit_id = task["id"]
        ### category: fitness/career
        category = task["category"]
        agent_model = task.get("agent_model", DEFAULT_AGENT_ID)

        ## Agent ID: Local or Remote Codex Agent
        agent_id = agent_model

        print(f"INFO: Processing Agent Task|user_id {user_id} | habit_id {habit_id} | category {category} | agent_model {agent_model} | task {task}")

        if agent_id != DEFAULT_AGENT_ID:
            print (f"DEBUG: process_agent_task_job skipped cur Task to assigned third-party agents|codex/openclaw/claude code")
            ### Keep the agent task as scheduled and waiting for onekey gateway to fetch pending tasks locally
            ### npx onekey gateway coachowl/coachowl --foreground
            final_result["success"] = True
            final_result["status"] = AGENT_TASK_STATUS_SCHEDULED
            return final_result

        ### Find Relevant Execution ID
        #### table: habit,
        #### table: habit_logs, assigned_agent_id
        #### table: agent_execution_state,

        #### step 1. claim the task
        execution_date = get_user_day(conn, user_id)
        #### Default Running Claim
        results = claim_agent_task(conn, user_id, habit_id, execution_date, agent_model, agent_model)
        print(f"INFO: ----------- Step 1 Default Running Claim results {results}")
        ####
        # "success": False,
        # "habit_id": habit_id,
        # "execution_date": execution_date,
        # "root_id": None,
        # "child_id": None,
        # "agent_id": agent_id

        #### step 2. update agent_execution_state, agent_execution_logs
        update_result = update_agent_execution_plan_status(conn, habit_id, execution_date,
                                                           status=AGENT_TASK_STATUS_RUNNING)
        execution_id = update_result.get("execution_id", "")
        root_execution_id = execution_id
        print(f"INFO: ----------- Step 2 Update Execution Plan Status Execution ID {execution_id} | results {update_result}")
        #### execution_id: '3aafbb97-f206-498a-aa2c-49089a9ed8f2:2026-05-07:default

        #### step 3. Run
        result = await run_agent_workflow(conn, task)
        result_text = result.get(KEY_CONTENT, "")
        print(f"INFO: ----------- Step 3 Update run_agent_workflow {result}")

        #### step 4. Update Status
        update_result_completed = update_agent_execution_plan_status(conn, habit_id, execution_date,
                                                                     status=AGENT_TASK_STATUS_COMPLETED)
        print(f"INFO: ----------- Step 4 Update Execution Plan Status results {update_result_completed}")

        ### step 5. Update Execution Logs for Dashboard
        execution_logs = {
            "habit_id": habit_id,
            "execution_id": execution_id,
            "root_execution_id": root_execution_id,
            "agent_id": DEFAULT_AGENT_ID,
            "agent_name": DEFAULT_AGENT_NAME,
            "event_type": "stdout",
            "status": AGENT_TASK_STATUS_COMPLETED,
            "message": "",
            "metadata": ""
        }
        update_result_execution_logs = update_agent_execution_logs(conn, execution_logs, user_id)
        print(f"INFO: ----------- Step 5 Update Execution Logs Status results {update_result_execution_logs}")

        # Update habit result and perform checkin
        conn.execute(
            "UPDATE habits SET result = ? WHERE id = ?",
            (result_text, habit_id)
        )

        ### update Checked in
        if is_valid_output(result_text):
            checkin(conn, agent_id, habit_id, note=result_text,
                    log_type = HABIT_LOG_TYPE_AGENT,
                    value=1)
        else:
            ### Check-In Should Display Agent ID, Not User ID
            checkin(conn, agent_id, habit_id, note=f"Auto-processed by {agent_id}...",
                    log_type=HABIT_LOG_TYPE_AGENT,
                    value=1)

        print(f"INFO: Agent task {habit_id} processed for user {user_id} using {agent_id}")

        ## Todo
        final_result["success"] = True
        final_result["status"] = AGENT_TASK_STATUS_COMPLETED
        return final_result
    except Exception as e:
        print (f"Failed to process agent Task Job with error {e}")
        final_result["success"] = False
        final_result["status"] = AGENT_TASK_STATUS_FAILURE
        return final_result
    finally:
        conn.close()


# ------------- Periodic Agent Tasks Runs Daily in Background  --------------
async def process_agent_tasks():
    """
    Background loop to process agent tasks.

    IMPORTANT:
    --------------------------------
    Don't use habit_logs to determine pending tasks.

    Use agent_execution_state.status instead.

    scheduled/pending/failed
        -> should run

    completed/running
        -> skip
    """

    while True:

        await asyncio.sleep(

            BACKGROUND_TASK_RUNNING_INTERNAL_SECONDS
        )

        conn = connect()

        try:
            # Use UTC Date, might be different from each user day tasks
            today = (
                datetime.utcnow()
            ).date().isoformat()

            #
            # Find remaining execution tasks
            #
            # IMPORTANT:
            # --------------------------------
            # Use execution state instead of habit_logs
            #
            # If the task is already in running status, it will not be claimed again

            agent_tasks = select_pending_agent_execution_task(conn, today)
            print(f"INFO: Running agent tasks Job Started For Today {today} Task Size {len(agent_tasks)}...")

            if LOG_ENABLE:
                print(f"DEBUG: /process_agent_tasks Pending Agent Tasks: {agent_tasks}")

            #
            # Run Sequentially (keep your original architecture)
            #

            enable = True
            if enable:
                for task in agent_tasks:
                    print(f"DEBUG: /process_agent_tasks process_agent_task_job Pending Agent Task: {task}")
                    result = await process_agent_task_job(task)
                    print(f"INFO: process_agent_task_job result {result}")

            #
            # After execution
            #
            remaining = select_pending_agent_execution_task_size(conn, today)
            print(f"INFO: Running agent tasks Job Finished Remaining Task Size {remaining}")

        except Exception as e:
            print(f"Error: process_agent_tasks in background worker: {e}")
        finally:

            conn.close()

async def run_agent_workflow(conn, task: Dict) -> Dict:
    """
        Background Tasks Run Agent Loops from Available Tools and MCPs

        task["category"]: Fitness/Career

        DEBUG:
        run_agent_workflow running Task {'id': '77136d0c-b0dd-402d-b0f9-310845f533ef', 'user_id': 'TEMP_a19e', 'name': 'Hydration and Energy Level Monitoring', 'kind': 'objective', 'category': 'fitness', 'parent_id': '50033831-f8c0-4727-8e29-f87f70f1380a', 'target_days': 30, 'interval_hours': None, 'start_date': '2026-04-18', 'end_date': None, 'agent_model': 'default', 'task_type': 'agent', 'status': 'active', 'result': None, 'archived': 0, 'created_at': '2026-04-18 19:40:36'}

        Return:
            result["content"]
    """
    try:
        if LOG_ENABLE:
            print (f"DEBUG: run_agent_workflow running input task {task}")

        result = {}
        result_text = ""
        today = (datetime.utcnow() - timedelta(hours=8)).date().isoformat()

        ## 1. Get Task Description: What assigned by AI Coach
        user_id = task["user_id"]
        habit_id = task["id"]
        ## parent id -> habit objective
        parent_id = task.get("parent_id", "")
        habit_name = task.get("name", "")
        task_content = task.get("content", "")
        category = task["category"]
        ### Push to Remote Claude Code/Local
        agent_model = task.get("agent_model", "default")

        if agent_model == "default":
            ## 2. What user input un process_logs, (category, covered)

            ### habit_id select user_log to show if it's process or chatted
            ### habit log: value: 0/1
            ### habit_id, user_id,
            # Find agent tasks that haven't been processed today
            # Use Pacific Time (UTC-8), Get Last 2 days All log
            last_2_days = (datetime.utcnow() - timedelta(hours=(24*2+8))).date().isoformat()
            cur = conn.execute(
                """
                SELECT h.* FROM habit_logs h
                WHERE h.habit_id = ? AND h.value = 0
                """,
                (last_2_days,)
            )
            from .db import fetchall_dicts
            habit_logs = fetchall_dicts(cur)

            ### Agent Log and User Log



            ## Check if user provide not answered logs
            user_provide_prompt_enable = False
            if not habit_logs or len(habit_logs) == 0:
                print (f"DEBUG: Current Task Habit Id {habit_id} | Task Name {habit_name} | no Remaining Logs to process: {today}")
                user_provide_prompt_enable = True
            else:
                user_provide_prompt_enable = False

            ### Final Prompt
            final_task_prompt = f"### Agent Task Name: {habit_name}, Content: {task_content}"

            if user_provide_prompt_enable:
                ## system: AI Nutritionist: Monitor & Summarize Eating
                ## user: text, image_url to process
                user_prompt = ""
                for habit_log in habit_logs:
                    ### Dinner picture at Macdonald, xxxx
                    note = habit_log.get("note", "")
                    image_url = habit_log.get("image_url", "")
                    user_prompt = f"## text: {note}, ## image: {image_url}"
                final_task_prompt += "," + f"User: {user_prompt}"

            if LOG_ENABLE:
                print (f"[run_agent_workflow] final_task_prompt agent_workflow_entry input|{final_task_prompt} | category {category}")

            if LOCAL_AGENTS_ENABLE:
                print(f"INFO: Running Local Agents.... ")

                agent_result_json = await agent_workflow_entry(final_task_prompt, category)
                if agent_result_json:
                    result[KEY_SUCCESS] = True
                    result[KEY_CONTENT] = agent_result_json.get(KEY_CONTENT, DEFAULT_AGENT_BACKGROUND_TASK_CONTENT)
                else:
                    result[KEY_SUCCESS] = False
                    result[KEY_CONTENT] = DEFAULT_AGENT_BACKGROUND_TASK_CONTENT
            else:
                print(f"DEBUG: Running Remote Agents {result}")

            if LOG_ENABLE:
                print (f"[run_agent_workflow] Result|{result}")
        else:
            print (f"DEBUG: agent_model selection {agent_model}")

        return result
    except Exception as e:
        print (f"[run_agent_workflow] Error: Failed with error: {e}")
        return {KEY_SUCCESS: False, KEY_CONTENT: DEFAULT_AGENT_BACKGROUND_TASK_CONTENT}

async def api_update_profile(request: Request) -> JSONResponse:
    _require_internal_key(request)
    body = await request.json()
    user_id = str(body.get("user_id") or "")
    age = body.get("age")
    gender = body.get("gender")
    timezone = body.get("timezone")
    profile = body.get("profile")

    age_valid = None
    try:
        age_valid = int(age)
    except Exception as e:
        print (f"Failed to convert input to valid age...{age} error {e}")

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    conn = connect()
    try:
        from .db import ensure_user
        ensure_user(conn, user_id)
        conn.execute(
            """
            INSERT INTO user_profiles (user_id, age, gender, timezone, profile) 
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET 
                age=excluded.age, 
                gender=excluded.gender, 
                timezone=excluded.timezone,
                updated_at=datetime('now', '-8 hours')
            """,
            (user_id, age_valid, gender, timezone, profile)
        )
        conn.commit()
        return JSONResponse({"ok": True})
    finally:
        conn.close()

async def api_get_profile(request: Request) -> JSONResponse:
    _require_internal_key(request)
    user_id = request.query_params.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    conn = connect()
    try:
        cur = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
        from .db import fetchall_dicts
        profiles = fetchall_dicts(cur)
        if LOG_ENABLE:
            print (f"Login User {user_id} Profile {profiles}")

        return JSONResponse({"ok": True, "profile": profiles[0] if profiles else None})
    finally:
        conn.close()



async def api_update_config(request: Request) -> JSONResponse:
    _require_internal_key(request)

    body = await request.json()
    user_id = body.get("user_id")
    config_type = body.get("type")
    config_data = body  # store full payload

    print (f"DEBUG: api_update_config input user_id {user_id} type {config_type} config_data {config_data}")

    if not user_id or not config_type:
        raise HTTPException(status_code=400, detail="user_id and type required")

    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO user_configs (user_id, type, config_json, updated_at)
            VALUES (?, ?, ?, datetime('now', '-8 hours'))
            ON CONFLICT(user_id, type) DO UPDATE SET
                config_json=excluded.config_json,
                updated_at=datetime('now', '-8 hours')
            """,
            (user_id, config_type, json.dumps(config_data))
        )
        conn.commit()

        return JSONResponse({"ok": True})
    finally:
        conn.close()

async def api_get_config(request: Request) -> JSONResponse:
    _require_internal_key(request)

    user_id = request.query_params.get("user_id")
    config_type = request.query_params.get("type")  # optional
    timezone = request.query_params.get("timezone") # optional
    print (f"DEBUG: api_get_config input user_id {user_id} type {config_type}")

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    if not config_type:
        raise HTTPException(status_code=400, detail="config field type is required")
    conn = connect()
    try:
        if config_type:
            cur = conn.execute(
                "SELECT type, config_json FROM user_configs WHERE user_id = ? AND type = ?",
                (user_id, config_type)
            )
        else:
            cur = conn.execute(
                "SELECT type, config_json FROM user_configs WHERE user_id = ?",
                (user_id,)
            )

        rows = cur.fetchall()

        result = []
        for r in rows:
            result.append({
                "type": r[0],
                "config": json.loads(r[1])
            })
        if LOG_ENABLE:
            print (f"DEBUG: api_get_config returning config {result}")
        return JSONResponse({"ok": True, "configs": result})
    finally:
        conn.close()

async def api_poll_habits(request: Request) -> JSONResponse:
    _require_internal_key(request)
    user_id = request.query_params.get("user_id") or ""
    if LOG_ENABLE:
        print (f"DEBUG: /habits/poll New Polling Request| user_id {user_id}")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    conn = connect()
    try:
        ## Polling All the Habits
        habits = list_habits(conn, user_id)
        # if LOG_ENABLE:
            # print (f"DEBUG: /api/v1/habits/poll all habits {habits}")
        return JSONResponse({"ok": True, "habits": habits})
    finally:
        conn.close()

async def api_poll_habits_by_habit_id(request: Request) -> JSONResponse:
    _require_internal_key(request)
    user_id = request.query_params.get("user_id") or ""
    habit_id = request.query_params.get("habit_id") or ""
    if LOG_ENABLE:
        print (f"DEBUG: /habits/poll/habit New Polling Request| habit_id {habit_id}")
    if not user_id or not habit_id:
        raise HTTPException(status_code=400, detail="user_id and habit_id is required")
    conn = connect()
    try:
        ## Polling All the Habits
        # habits = list_habits(conn, user_id)
        habit = get_habit(conn, user_id, habit_id)
        if habit["task_type"] == "agent":
            ## Check if task

            habit["claimed"] = xxxx
            habit["claimed"] = xxxx


        ## Fetch Agent Running Status
        # if habit.ge
        ## Update the Status Bar of Each Agen Task

        # if LOG_ENABLE:
            # print (f"DEBUG: /api/v1/habits/poll all habits {habits}")
        return JSONResponse({"ok": True, "habits": [habit]})
    finally:
        conn.close()

async def api_calculate_calories(request: Request) -> JSONResponse:
    _require_internal_key(request)
    body = await request.json()
    meal_text = str(body.get("meal_text") or body.get("prompt") or "").strip()
    images = body.get("images") or []
    if not meal_text and images:
        return JSONResponse(
            {
                "ok": False,
                "message": "Image received, but image-to-food parsing is not enabled on this server. Please add a short description, e.g. 'Big Mac and fries'.",
            },
            status_code=400,
        )
    if not meal_text:
        raise HTTPException(status_code=400, detail="meal_text is required")
    return JSONResponse({"ok": True, "result": calculate_calories(meal_text)})

async def api_nutrition_summary(request: Request) -> JSONResponse:
    _require_internal_key(request)
    user_id = (request.query_params.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    day = request.query_params.get("day")
    with_llm = (request.query_params.get("with_llm") or "1").lower() not in {"0", "false", "no"}

    conn = connect()
    try:
        summary = await nutritionist_summary(conn, user_id=user_id, day=day, with_llm=with_llm)
        summary["html"] = nutrition_summary_html(summary)
        return JSONResponse(summary)
    finally:
        conn.close()


def filter_current_user_objective_by_date(user_id: str, datestr: str):
    """
    Search the db to get the user's objectives.
    Return the format:
    key: objective_name, value: list of dict, dict key is "habit_name", value is habit name

    datestr: YYYY-MM-DD
    """
    conn = connect()
    try:
        from .db import fetchall_dicts

        query = """
                    SELECT * FROM habits 
                    WHERE user_id = ? 
                    AND archived = 0 
                    AND status = 'active'
                    AND date(start_date) <= date(?)
                    AND (end_date IS NULL OR date(end_date) >= date(?))
                """

        cur = conn.execute(query, (user_id, datestr, datestr))
        habits = fetchall_dicts(cur)

        # 2. Objectives (Top level - parent_id is null)
        # Note: We filter objectives by date too, so we don't show goals
        # that haven't started yet or are already finished.
        objectives = {h["id"]: h for h in habits if not h["parent_id"]}

        # 3. Tasks (Children - parent_id is not null)
        # Filter for human tasks that are active on this day
        tasks = [h for h in habits if h["parent_id"] and h["task_type"] == 'human']

        # 4. Map Tasks to Objectives
        result = {}
        for obj_id, obj in objectives.items():
            obj_name = obj["name"]
            # Build list of tasks associated with this objective
            related_tasks = [{"habit_name": t["name"]} for t in tasks if t["parent_id"] == obj_id]

            # Only add the objective to the result if it has tasks or if you want to show empty goals
            if related_tasks:
                result[obj_name] = related_tasks

        return result

    except Exception as e:
        print(f"Error filtering objectives: {e}")
        return {}
    finally:
        conn.close()


def filter_current_user_objective(user_id: str):
    """
    Search the db to get the user's objectives.
    Return the format: 
    key: objective_name, value: list of dict, dict key is "habit_name", value is habit name
    """
    conn = connect()
    try:
        from .db import fetchall_dicts
        # Get all non-archived habits for the user
        cur = conn.execute(
            "SELECT * FROM habits WHERE user_id = ? AND archived = 0", 
            (user_id,)
        )
        habits = fetchall_dicts(cur)
        
        # Objectives are habits with no parent_id
        objectives = {h["id"]: h for h in habits if not h["parent_id"]}
        
        # Tasks are habits with a parent_id, task_type='human', and status='active'
        tasks = [h for h in habits if h["parent_id"] and h["task_type"] == 'human' and h["status"] == 'active']
        
        result = {}
        for obj_id, obj in objectives.items():
            obj_name = obj["name"]
            result[obj_name] = [{"habit_name": t["name"]} for t in tasks if t["parent_id"] == obj_id]
            
        return result
    except Exception as e:
        print(f"Error filtering objectives: {e}")
        return {}
    finally:
        conn.close()


async def stream_generator(messages: List[Dict[str, Any]], kwargs: Dict[Any, Any]) -> AsyncGenerator[str, None]:
    sep = "\n"
    message_id = get_new_message_id()

    session_id = kwargs.get("session_id", generate_session_id())
    user_id = kwargs.get("user_id", generate_user_id())
    ## android/ios don't return the iframe, h5 return iframe
    client_id = kwargs.get ("client_id", "")
    if_client_web = True if client_id not in [CLIENT_ID_ANDROID, CLIENT_ID_IOS] else False
    output_content_type = CONTENT_TYPE_MARKDOWN if client_id not in [CLIENT_ID_ANDROID, CLIENT_ID_IOS] else CONTENT_TYPE_HTML

    # intro
    if len(messages) <= 1: # Only send intro if it's the first message
        yield json.dumps(
            assembly_message(
                MESSAGE_TYPE_ASSISTANT,
                OUTPUT_FORMAT_TEXT,
                f"Hello Human mate, Tell me what you do today? Set an objective, or ask a career question, upload a picture...",
                content_type=output_content_type,
                section="system_msg",
                message_id=message_id,
            )
        ) + sep
        await asyncio.sleep(0)

    user_prompt, attachments = get_user_last_input(messages)
    
    # Handle confirmation postbacks
    if user_prompt.startswith("CONFIRM_DELETE_"):
        habit_id_to_delete = user_prompt.replace("CONFIRM_DELETE_", "").strip()
        conn = connect()
        try:
            delete_habit(conn, user_id, habit_id_to_delete)
        finally:
            conn.close()
        
        yield json.dumps(
            assembly_message(
                MESSAGE_TYPE_ASSISTANT,
                OUTPUT_FORMAT_TEXT,
                "Task/Objective deleted successfully.",
                content_type=output_content_type,
                section="answer",
                message_id=get_new_message_id(),
            )
        ) + sep
        
        # Trigger UI refresh
        yield json.dumps(
            assembly_message(
                MESSAGE_TYPE_ASSISTANT,
                OUTPUT_FORMAT_TEXT,
                "Tracker updated!",
                content_type=output_content_type,
                section="answer",
                message_id=get_new_message_id(),
            )
        ) + sep
        return

    ## get all the available user objectives
    user_objective = filter_current_user_objective(user_id)
    if LOG_ENABLE:
        print (f"DEBUG: Current User Id {user_id} | User Objective: {user_objective}")

    intent_system_prompt = INTENT_SYSTEM_PROMPT.format(
        INTENT_LIST=json.dumps(USER_INTENT_LIST), 
        USER_INPUT=user_prompt, 
        USER_OBJECTIVE=json.dumps(user_objective), 
        ACTION_LIST=json.dumps(TASK_PROMPT_ACTION_LIST)
    )
    intent = route_intent(user_prompt, intent_system_prompt)

    if LOG_ENABLE:
        print(f"[chat] user_id={user_id} intent={intent} prompt={user_prompt}")

    # Yield LLM Response first
    if intent.response:
        yield json.dumps(
            assembly_message(
                MESSAGE_TYPE_ASSISTANT,
                OUTPUT_FORMAT_TEXT,
                intent.response,
                content_type=output_content_type,
                section="answer",
                message_id=get_new_message_id(),
            )
        ) + sep
        await asyncio.sleep(0)

    actions = intent.actions or []
    objectives = intent.objectives or {}

    ### post process objective routing
    existing_obj = False
    matched_objectives = {}
    new_objectives ={}
    for obj_name in objectives.keys():
        task_list = objectives.get(obj_name, [])
        if obj_name in user_objective:
            matched_objectives[obj_name] = task_list
        else:
            new_objectives[obj_name] = task_list
    print (f"DEBUG: user_prompt {user_prompt} | LLM Output Routed Objectives: {matched_objectives} | new_objectives {new_objectives}")

    conn = connect()
    try:
        from .db import fetchall_dicts
        db_has_changes = False

        if TASK_PROMPT_ACTION_DELETE in actions:
            # Need confirmation for deletion
            # We assume the user wants to delete the objectives mentioned in intent.objectives
            for obj_name in objectives.keys():
                cur = conn.execute("SELECT id FROM habits WHERE user_id = ? AND name = ? AND archived = 0", (user_id, obj_name))
                row = cur.fetchone()
                if row:
                    del_id = row["id"]
                    confirm_html = f"""
                    <div class="mt-2 p-3 bg-red-50 border border-red-200 rounded-xl text-red-800 text-sm">
                        <p class="font-bold mb-2">Confirm Deletion</p>
                        <p>Are you sure you want to delete the objective: <b>{obj_name}</b>?</p>
                        <div class="flex gap-2 mt-3">
                            <button onclick="document.getElementById('q').value='CONFIRM_DELETE_{del_id}'; document.getElementById('send').click();" class="bg-red-600 text-white px-3 py-1.5 rounded-lg font-bold">Confirm Delete</button>
                            <button onclick="document.getElementById('q').value='Nevermind, do not delete.'; document.getElementById('send').click();" class="bg-gray-200 text-gray-800 px-3 py-1.5 rounded-lg font-bold">Cancel</button>
                        </div>
                    </div>
                    """
                    yield json.dumps(
                        assembly_message(
                            MESSAGE_TYPE_ASSISTANT,
                            OUTPUT_FORMAT_HTML,
                            confirm_html,
                            content_type=CONTENT_TYPE_HTML,
                            section="answer",
                            message_id=get_new_message_id(),
                        )
                    ) + sep
            # Stop processing further actions if waiting for delete confirmation
            return

        if TASK_PROMPT_ACTION_CREATE in actions or TASK_PROMPT_ACTION_CHECKIN in actions:
            if matched_objectives:
                for obj_name, tasks in matched_objectives.items():
                    # Find or create objective
                    cur = conn.execute("SELECT id FROM habits WHERE user_id = ? AND name = ? AND parent_id IS NULL AND archived = 0", (user_id, obj_name))
                    row = cur.fetchone()

                    # Update: Habit - Objective
                    if row:
                        obj_id = row["id"]
                    elif "create" in actions:
                        ## Fetch target days: kwargs
                        kwargs = intent.kwargs if intent.kwargs else {}
                        target_days = kwargs.get(KEY_TARGET_DAYS, 0)
                        obj = create_habit(conn, user_id=user_id, name=obj_name, kind="objective", category=intent.intent, target_days=target_days)
                        obj_id = obj["id"]
                        db_has_changes = True
                        if LOG_ENABLE:
                            print(
                                f"DEBUG: Creating habit create_habit user_id {user_id}| intent {intent} | name {obj_name} | kind objective| obj_id {obj_id}")
                    else:
                        continue # Skip if it doesn't exist and we aren't creating

                    # Update: Habit-Task Process tasks under this objective
                    for task in tasks:
                        task_name = task.get("habit_name") if isinstance(task, dict) else task
                        cur = conn.execute("SELECT id FROM habits WHERE user_id = ? AND name = ? AND parent_id = ? AND archived = 0", (user_id, task_name, obj_id))
                        t_row = cur.fetchone()

                        t_id = None
                        if t_row:
                            t_id = t_row["id"]
                        elif "create" in actions:
                            t_obj = create_habit(conn, user_id=user_id, name=task_name, kind="objective", category=intent.intent, parent_id=obj_id, target_days=1)
                            t_id = t_obj["id"]
                            db_has_changes = True
                            if LOG_ENABLE:
                                print (f"DEBUG: Creating habit user_id {user_id}| intent {intent} | task_name {task_name} with id {t_id}| obj_id {obj_id}")

                        if t_id and "checkin" in actions:
                            checkin(conn, user_id=user_id, habit_id=t_id, note=intent.response or "Checked in via chat")
                            db_has_changes = True
                            if LOG_ENABLE:
                                print (f"ACTION: Check In habit user_id {user_id}| intent {intent} | task_name {task_name} with id {t_id}| obj_id {obj_id}")
            else:
                ## general default check in
                print (f"matched_objectives {matched_objectives} is empty| new_objectives {new_objectives}")

        if not db_has_changes:
            ## General Default
            success = False
            if intent.intent == USER_INTENT_FITNESS:
                # Fallback for old fitness logic if LLM didn't return structured objective updates
                meal_text = (intent.entities or {}).get("meal_text") if hasattr(intent, 'entities') and intent.entities else None
                meal_text = str(meal_text or user_prompt or "").strip()
                if meal_text:
                    result = calculate_calories(meal_text)
                    log_meal(conn, user_id=user_id, meal_text=meal_text, calories=float(result.get("total_kcal") or 0), items=result.get("items") or [])
                    db_has_changes = True
            elif intent.intent == USER_INTENT_DEFAULT:

                success = handle_general(user_id, intent)

            else:
                success = handle_general(user_id, intent)
            print (f"DEBUG: Handle Intent Event user_id {user_id} intent {intent}|success {success}")
    finally:
        conn.close()

    # Check if tracker already loaded in this chat
    # Check if tracker already loaded in this chat
    already_has_tracker = any("tracker" in str(m.get("content", "")).lower() for m in messages if m.get("role") == "assistant")

    if LOG_ENABLE:
        print (f"DEBUG: The Tracker have already been shown to the front already_has_tracker {already_has_tracker}")
    if if_client_web and not already_has_tracker:
        html = _habits_list_iframe(user_id)
        yield json.dumps(
                assembly_message(
                    MESSAGE_TYPE_ASSISTANT,
                    OUTPUT_FORMAT_HTML,
                    html,
                    content_type=CONTENT_TYPE_HTML,
                    section="answer",
                    message_id=get_new_message_id(),
                )
            ) + sep
    else:
        yield json.dumps(
                assembly_message(
                    MESSAGE_TYPE_ASSISTANT,
                    OUTPUT_FORMAT_TEXT,
                    "CoachOwl updated!",
                    content_type=output_content_type,
                    section="answer",
                    message_id=get_new_message_id(),
                )
            ) + sep

def handle_general(user_id: str, intent):
    """

    """
    success = False





    return success


def _habit_iframe(user_id: str, habit_id: str) -> str:
    src = f"{DEPLOYED_SUBDOMAIN}/static/ai_coach_tracker.html?user_id={user_id}&habit_id={habit_id}"
    return f"""
<iframe
  src="{src}"
  style="width:100%; height:420px; border:0; border-radius:14px; overflow:hidden; background:#fff;"
  loading="lazy"
></iframe>
""".strip()


def _habits_list_iframe(user_id: str) -> str:
    src = f"{DEPLOYED_SUBDOMAIN}/static/ai_coach_tracker.html?user_id={user_id}"
    # src = f"/static/ai_coach_tracker.html?user_id={user_id}"

    return f"""
<iframe
  src="{src}"
  style="width:100%; height:520px; border:0; border-radius:14px; overflow:hidden; background:#fff;"
  loading="lazy"
></iframe>
""".strip()


def _fitness_advice(result: Dict[str, Any]) -> str:
    total = float(result.get("total_kcal") or 0)
    if total <= 400:
        tone = "Nice—light meal."
    elif total <= 800:
        tone = "Solid meal."
    else:
        tone = "Heavier meal—let’s balance the rest of today."
    return f"""{tone}

- If you want weight control: aim for **protein + fiber** next (e.g., Greek yogurt + fruit, or chicken + salad).
- Want me to set a daily calorie target? Tell me your height/weight/goal and activity level."""


async def chat_endpoint(request: Request) -> StreamingResponse:
    """
        payload
        {
            "messages": []
            "kwargs": {}
        }
    """
    _require_internal_key(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    messages = body.get("messages", []) or []
    kwargs = body.get("kwargs", {}) or {}
    session_id = kwargs.get("session_id", generate_session_id())
    client_id = kwargs.get ("client_id", "")

    if LOG_ENABLE:
        print (f"DEBUG: Streaming Input {messages}|kwargs {kwargs}|client_id {client_id}")

    # init user in db quickly
    conn = connect()
    try:
        conn.execute("INSERT OR IGNORE INTO users(id) VALUES (?)", (session_id,))
        conn.commit()
    finally:
        conn.close()

    return StreamingResponse(
        stream_generator(messages, kwargs),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "x-session-id": session_id,
        },
    )


async def index(_: Request) -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text("utf-8"))


### Main Index of the website
async def main_index(request: Request) -> HTMLResponse:
    user_info = {}
    user_id = user_info.get(KEY_USER_ID, "") if user_info is not None else ""

    ### 2. Anonomous Not Loggedin Before, Keep use the cookie_user_id for unlogged in user, don't change per
    if user_id == "":
        user_id = request.cookies.get(KEY_COOKIE_USER_ID)
        print (f"INFO: /main_entry user not logged in check client cookie user_id: {user_id}")

    if not user_id:
        user_id = generate_user_id()
        print(f"INFO: /main_entry user_id not in cookie or logged in before session_id not in server state, generation new|{user_id}")
    else:
        print (f"INFO: /main_entry user_id exist in cookie|{user_id}")
    try:
        conn = connect()
        # user_profile_dict = get_user_profile(conn, user_id)
        # user_timezone = user_profile_dict.get("timezone", DEFAULT_USER_TIMEZONE)
        ## Get Local Date and Datetime
        user_timezone = get_user_timezone(conn, user_id)
        local_date = get_user_day(conn, user_id)
        local_datetime = get_user_datetime(conn, user_id)

        # html = (STATIC_DIR / "ai_coach_tracker.html").read_text("utf-8")
        # response = HTMLResponse(html, media_type="text/html")

        response = templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "user_id": user_id,
                "user_timezone": user_timezone,
                "local_date": local_date,
                "local_datetime": local_datetime
            }
        )

        # put both logged in and unlogged in user_id in the cooki and update
        response.set_cookie(
            key=KEY_COOKIE_USER_ID,
            value=user_id,
            max_age=60 * 60 * 24 * 30
        )

        return response

    except Exception as e:
        print (f"DEBUG: main_entry failed with error {e}")
        user_id = generate_user_id()
        response = templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "user_id": user_id,
                "user_timezone": DEFAULT_USER_TIMEZONE
            }
        )
        return response

def get_temp_user_id_info():
    """
        Use For Open Source Project
    """
    return {"user_id": "TEMP_USER_ID"}

async def main_entry(request: Request) -> HTMLResponse:
    """
        Website Main Page
        Same as:
        http://0.0.0.0:7115/static/ai_coach_tracker.html?user_id=345&tab=todo
        response.set_cookie:
    """
    # user_id = request.cookies.get(KEY_COOKIE_USER_ID)
    ## 1. Check if it's logged in: session_id -> user_info -> user_id
    user_info = get_temp_user_id_info()
    user_id = user_info.get(KEY_USER_ID, "") if user_info is not None else ""

    ### 2. Anonomous Not Loggedin Before, Keep use the cookie_user_id for unlogged in user, don't change per
    if user_id == "":
        user_id = request.cookies.get(KEY_COOKIE_USER_ID)
        print (f"INFO: /main_entry user not logged in check client cookie user_id: {user_id}")

    if not user_id:
        user_id = generate_user_id()
        print(f"INFO: /main_entry user_id not in cookie or logged in before session_id not in server state, generation new|{user_id}")
    else:
        print (f"INFO: /main_entry user_id exist in cookie|{user_id}")
    try:
        conn = connect()
        # user_profile_dict = get_user_profile(conn, user_id)
        # user_timezone = user_profile_dict.get("timezone", DEFAULT_USER_TIMEZONE)
        ## Get Local Date and Datetime
        user_timezone = get_user_timezone(conn, user_id)
        local_date = get_user_day(conn, user_id)
        local_datetime = get_user_datetime(conn, user_id)

        # html = (STATIC_DIR / "ai_coach_tracker.html").read_text("utf-8")
        # response = HTMLResponse(html, media_type="text/html")

        response = templates.TemplateResponse(
            "ai_coach_tracker.html",
            {
                "request": request,
                "user_id": user_id,
                "user_timezone": user_timezone,
                "local_date": local_date,
                "local_datetime": local_datetime
            }
        )

        # put both logged in and unlogged in user_id in the cooki and update
        response.set_cookie(
            key=KEY_COOKIE_USER_ID,
            value=user_id,
            max_age=60 * 60 * 24 * 30
        )

        return response

    except Exception as e:
        print (f"DEBUG: main_entry failed with error {e}")
        user_id = generate_user_id()
        response = templates.TemplateResponse(
            "ai_coach_tracker.html",
            {
                "request": request,
                "user_id": user_id,
                "user_timezone": DEFAULT_USER_TIMEZONE
            }
        )
        return response

async def asr_endpoint(request: Request):
    """
    """
    try:
        form = await request.form()
        audio_file = form.get("audio")

        if not audio_file:
            return JSONResponse(
                {"error": "No audio file provided"},
                status_code=400
            )

        audio_content = await audio_file.read()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription",
                headers={
                    "Authorization": f"Bearer {QWEN_API_KEY}",
                    "Content-Type": "audio/wav"
                },
                data=audio_content,
                timeout=30.0
            )

            if response.status_code == 200:
                result = response.json()
                transcript = result.get("text", result.get("transcript", ""))
                return JSONResponse({"text": transcript})
            else:
                return JSONResponse(
                    {"error": f"ASR service error: {response.text}"},
                    status_code=response.status_code
                )

    except Exception as e:
        return JSONResponse(
            {"error": f"Server error: {str(e)}"},
            status_code=500
        )

async def api_get_agent_dashboard(request: Request) -> JSONResponse:
    """
        front end
    """
    # user_id = await _require_user_authen_onekey(request)
    #if not user_id:
    #    raise HTTPException(status_code=403, detail="Forbidden: Invalid OneKey Key No User Id Matched...")
    conn = connect()
    try:
        qp = request.query_params
        user_id = qp.get("user_id", "")
        date = get_user_day(conn, user_id)

        # fetch agent-related habits
        agent_tasks_finished = list_habits(
            conn,
            user_id=user_id,
            date=date,
            task_type=HABIT_LOG_TYPE_AGENT,
            archived=1,
            fill_agent_task_log_enable=True
        )
        ##
        agent_tasks_finished_mapped = []
        for agent_task in agent_tasks_finished:
            agent_task["archived"] = 1
            agent_tasks_finished_mapped.append(agent_task)

        agent_tasks_pending = list_habits(
            conn,
            user_id=user_id,
            date=date,
            task_type=HABIT_LOG_TYPE_AGENT,
            archived=0,
            fill_agent_task_log_enable=True
        )
        agent_tasks_pending_mapped = []
        for agent_task in agent_tasks_pending:
            agent_task["archived"] = 0
            agent_tasks_pending_mapped.append(agent_task)

        agent_tasks = agent_tasks_finished + agent_tasks_pending
        if LOG_ENABLE:
            print (f"DEBUG: agent_tasks_finished {agent_tasks_finished} | agent_tasks_pending {agent_tasks_pending} | agent_tasks {agent_tasks}")
        # ---------------------------
        # GROUP BY AGENT
        # ---------------------------
        ## current user monitored Agents
        monitored_agents = {}

        ## Iteration 1: /claim endpoint
        ## Why after claim, assigned_agent_id missing?
        for task in agent_tasks:

            ## Go Over Final Logs Field of Start and Completed
            task_logs = task.get(KEY_AGENT_TASK_LOGS, [])
            if LOG_ENABLE:
                print (f"DEBUG: Task Logs Example: task_logs {task_logs}")

            for log in task_logs:
                """
                e.g.
                {
                  'id': 'a1f30908-7f89-4a81-92da-f494abb08530',
                  'habit_id': 'f04fb93e-dd68-48e1-a142-9b364758a2c4',
                  'execution_id': '6e227e65-9ec9-405c-be5b-e22552ad8257',
                  'root_execution_id': 'f34412c4-3d41-4ffb-bb5c-b9b8c066ea59',
                  'agent_id': 'codex_localhost_90866',
                  'agent_name': 'codex',
                  'event_type': 'stderr',
                  'status': None,
                }
                """
                agent_id = log.get("agent_id") or DEFAULT_AGENT_ID
                agent_name = log.get("agent_name") or DEFAULT_AGENT_NAME
                status = log.get("status", "")
                ts = log.get("ts", "")
                event_type = log.get("event_type", "")

                agent_meta = AGENT_META_DICT.get(agent_name, {})
                image_thumbnail = agent_meta.get("image_thumbnail", "")
                ### check
                if agent_id not in monitored_agents:
                    monitored_agents[agent_id] = {
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "image_thumbnail": image_thumbnail,
                        "active_executions": 0,
                        "error_count": 0,
                        "last_ts": "",
                        "status": "running",
                        "habit_id": task.get("habit_id"),
                    }

                # update last activity
                if not monitored_agents[agent_id]["last_ts"] or ts > monitored_agents[agent_id]["last_ts"]:
                    monitored_agents[agent_id]["last_ts"] = ts

                # status logic
                if status == "error":
                    monitored_agents[agent_id]["error_count"] += 1
                    monitored_agents[agent_id]["status"] = "error"

                elif status == "running":
                    monitored_agents[agent_id]["status"] = "running"

                elif status == "completed":
                    monitored_agents[agent_id]["status"] = "completed"

                # count active executions
                if status in ("running", "stalled"):
                    monitored_agents[agent_id]["active_executions"] += 1

            ## Go Over Running Logs Field
        if LOG_ENABLE:
            print (f"DEBUG: Returning agents dict {monitored_agents}")

        # ---------------------------
        # BUILD DASHBOARD LINKS
        # ---------------------------
        base_url = ""

        result = []

        for agent in monitored_agents.values():
            agent_id = agent.get("agent_id") or DEFAULT_AGENT_ID
            if agent_id:
                agent["task_url"] = (
                    f"{base_url}/?user_id={user_id}"
                    f"&agent_id={agent_id}"
                )
            result.append(agent)

        if LOG_ENABLE:
            print (f"DEBUG: api_get_agent_dashboard Returning agents result {result}")

        return JSONResponse({
            "success": True,
            "date": date,
            "agents": result
        })

    except Exception as e:
        print (f"api_get_agent_dashboard Failed with error {e}")
        print(f"api_get_agent_dashboard Failed: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)

async def api_get_agent_task(request: Request) -> JSONResponse:
    """
        Get Available Tasks
        ## Request
            curl -X GET "http://0.0.0.0:7115/api/v1/agent/tasks/get" \
              -H "X-OneKey: $DEEPNLP_ONEKEY_ROUTER_ACCESS"

        ## Output
        {"success":true,"tasks":[{"id":"2e345abe-4219-4530-917a-46f9c7cc09d4","user_id":"DerekZZ","name":"AI-Powered Concept Explanation & Flashcards","kind":"task","category":"career","parent_id":"e0c64b6d-d8d0-46d5-acea-b6fb0af23819","target_days":30,"interval_hours":null,"start_date":"2026-04-26","end_date":"2026-05-25","start_time":null,"end_time":null,"agent_model":"default","task_type":"agent","status":"active","result":null,"archived":0,"created_at":"2026-04-27 02:26:17","logs":[],"progress":{"kind":"objective","target_days":30,"days_done":0,"days_done_list":[]},"ts":""},{"id":"df0f1b4d-4f11-4f5a-bfa1-7a6d31d56d80","user_id":"DerekZZ","name":"Performance Tracking & Study Plan Adjustment","kind":"task","category":"career","parent_id":"e0c64b6d-d8d0-46d5-acea-b6fb0af23819","target_days":15,"interval_hours":null,"start_date":"2026-04-26","end_date":"2026-05-10","start_time":"00:00:00","end_time":"23:59:59","agent_model":"default","task_type":"agent","status":"active","result":null,"archived":0,"created_at":"2026-04-27 02:26:17","logs":[],"progress":{"kind":"objective","target_days":15,"days_done":0,"days_done_list":[]},"ts":""},{"id":"a8317db6-faa2-4c46-8403-344a49646e74","user_id":"DerekZZ","name":"AI-Powered Industry Trend Analysis","kind":"task","category":"career","parent_id":"e0c64b6d-d8d0-46d5-acea-b6fb0af23819","target_days":10,"interval_hours":null,"start_date":"2026-04-26","end_date":"2026-05-05","start_time":null,"end_time":null,"agent_model":"default","task_type":"agent","status":"active","result":null,"archived":0,"created_at":"2026-04-26 14:53:08","logs":[],"progress":{"kind":"objective","target_days":10,"days_done":0,"days_done_list":[]},"ts":""},{"id":"06c8adda-8ec7-45bb-a447-daac7a80b8f0","user_id":"DerekZZ","name":"Cross-Reference Report with Internal Performance Metrics","kind":"task","category":"career","parent_id":"e0c64b6d-d8d0-46d5-acea-b6fb0af23819","target_days":3,"interval_hours":null,"start_date":"2026-04-26","end_date":"2026-04-28","start_time":null,"end_time":null,"agent_model":"default","task_type":"agent","status":"active","result":null,"archived":0,"created_at":"2026-04-26 14:53:08","logs":[],"progress":{"kind":"objective","target_days":3,"days_done":0,"days_done_list":[]},"ts":""}],"connected_agents":[{"id":"","name":"default","description":"Agent for default","cli":"default"},{"id":"","name":"claude","description":"Agent for claude","cli":"claude"},{"id":"","name":"codex","description":"Agent for codex","cli":"codex"},{"id":"","name":"gemini","description":"Agent for gemini","cli":"gemini"},{"id":"","name":"openclaw","description":"Agent for openclaw","cli":"openclaw"}]}
    """
    user_id = await _require_user_authen_onekey(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid OneKey Key No User Id Matched...")

    conn = connect()
    try:
        body = {}
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.json()
            except Exception:
                body = {}
        if LOG_ENABLE:
            print (f"DEBUG: api_get_agent_task input body {body}")
        ## Server Today Format YYYY-MM-DD
        # user_profile_dict = get_user_profile(conn, user_id=user_id)
        # user_timezone = user_profile_dict.get("timezone", DEFAULT_USER_TIMEZONE)
        # date = get_today_str(time_zone=user_timezone)
        ## Internally Select All use UTC Time, only for external display
        date = get_utc_date_str()
        agent_tasks = list_habits(conn, user_id=user_id, date=date, task_type=HABIT_LOG_TYPE_AGENT)
        print (f"INFO: /api_get_agent_task date {date} | user_id {user_id} | task_type {HABIT_LOG_TYPE_AGENT}| Result Size {len(agent_tasks)}")
        if LOG_ENABLE:
            print (f"DEBUG: agent_tasks {agent_tasks}")

        ### Get All the Pending Tasks Schedule For Today
        configs = select_user_config(conn, user_id=user_id, config_type=CONFIG_TYPE_CONNECTED_AGENTS)
        if LOG_ENABLE:
            print (f"DEBUG: user_id {user_id} configs {configs}")
        connected_agents_config = []
        if len(configs) > 0:
            config_dict = configs[0]
            config_json_str = config_dict.get('config_json', "")
            config_json = {}
            if isinstance(config_json_str, str):
                config_json = json.loads(config_json_str)
            else:
                config_json = {}
            agent_clis = config_json.get(KEY_AGENT_CLIS_AVAILABLE, [])

            connected_agents_config = [{"id":"", "name": cli, "description": f"Agent for {cli}", "cli": cli} for cli in agent_clis]

            print (f"DEBUG: config_json_str type {type(config_json_str)} config_json_str is {config_json_str}")
            print (f"DEBUG: config_json type {type(config_json)} config_json is {config_json}")

        ###
        # Agent Config
        # "connected_agents": ["codex", "claude", "gemini"]
        # ”connected_ims“: ["", ""]
        results = {
            "success": True,
            "tasks": agent_tasks,
            "connected_agents": connected_agents_config
        }
        if LOG_ENABLE:
            print (f"DEBUG: api_get_agent_task results {results}")
        return JSONResponse(results)
    except Exception as e:
        print (f"Failed to get agent task results: {e}")
        traceback.print_exc()
        results = {
            "success": False,
            "tasks": [],
            "connected_agents": []
        }
        return JSONResponse(results)
    finally:
        conn.close()

def claim_agent_task(conn: sqlite3.Connection, user_id: str, habit_id: str,
                     execution_date: str, agent_id: str, agent_name: str):
    """
        habit_log:
        execution_log:

        Claim 1 Agent N Records
        record1: root_id: habit_log#id
        record2: child_record, parent_log_id=root_id

        N个Agent
        record1: root_id: habit_log#id
        record2-N: child_record, parent_log_id=root_id, unique_key: child_id
        Use OneKey Gateway Pull Task and Claim Task
    """
    try:
        # STEP 1: ensure root execution exists
        root_id = get_or_create_root_execution(
            conn,
            habit_id,
            user_id,
            execution_date
        )

        ## execution_id
        child_id = str(uuid.uuid4())

        # STEP 2: claim under root directly，
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO habit_logs (
            id,
            habit_id,
            user_id,
            parent_log_id,
            log_type,
            status,
            assigned_agent_id,
            assigned_agent_name,
            assigned_at,
            heartbeat_at,
            retry_count,
            max_retries
        )
        SELECT
            ?,
            root.habit_id,
            root.user_id,
            root.id,
            'agent',
            'running',
            ?,
            ?,
            datetime('now'),
            datetime('now'),
            0,
            root.max_retries
        FROM habit_logs root
        WHERE root.id = ?
          AND root.is_root_execution = 1

          -- parallel safety
          AND (
            SELECT COUNT(*)
            FROM habit_logs child
            WHERE child.parent_log_id = root.id
          ) < root.expected_children

          -- prevent duplicate agent claim
          AND NOT EXISTS (
            SELECT 1
            FROM habit_logs child
            WHERE child.parent_log_id = root.id
              AND child.assigned_agent_id = ?
          )
        """, (
            child_id,
            agent_id,
            agent_name,
            root_id,
            agent_id
        ))

        conn.commit()

        if cursor.rowcount == 0:
            empty_result = {
                "success": False,
                "message": "No available execution slot or already claimed"
            }
            return empty_result

        results = {
            "success": True,
            "habit_id": habit_id,
            "execution_date": execution_date,
            "root_id": root_id,
            "child_id": child_id,
            "agent_id": agent_id
        }
        return results

    except Exception as e:
        print (f"claim_agent_task failed with error {e}")
        results = {
            "success": False,
            "habit_id": habit_id,
            "execution_date": execution_date,
            "root_id": None,
            "child_id": None,
            "agent_id": agent_id
        }
        return results

async def api_run_agent_task(request: Request) -> JSONResponse:
    """
        API Run Agent Task, The same as periodically run
    """
    conn = connect()
    try:
        body = await request.json()
        if LOG_ENABLE:
            print (f"DEBUG: api_run_agent_task input body {body}")

        user_id = body.get("user_id")
        task_id = body.get("task_id")
        agent_run_task = get_habit(conn, user_id, task_id)
        if LOG_ENABLE:
            print (f"DEBUG: api_run_agent_task agent_run_task {agent_run_task}")

        task = {}
        results = await process_agent_task_job(agent_run_task)
        return JSONResponse(results)

    except Exception as e:
        traceback.print_exc()
        results = {}
        results["success"] = False
        results["message"] = "Failed to api_run_agent_task..."
        print (f"DEBUG: api_run_agent_task failed with error {e}")
        return JSONResponse(results)
    finally:
        conn.close()

async def api_claim_agent_task(request: Request) -> JSONResponse:
    """
        api_claim_agent_task:  Task Lazy Load Mode,
        If agent claim a task_id, and execution_date e.g. 2026-08-28, it will create a
        root parent record for multi-agent task

        task_id: task_id ## habits.id

        {
          "habit_id": "H1",/api/v1/agent/tasks/log/update
          "execution_date": "2026-04-28",
          "parent_log_id": NULL,
          "is_root_execution": 1,
          "expected_children": 2
        }

        {
          "parent_log_id": "root_execution_id",
          "assigned_agent_id": "codex",
          "status": "running"
        }

    """
    user_id = await _require_user_authen_onekey(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = connect()
    try:
        body = await request.json()
        if LOG_ENABLE:
            print (f"DEBUG: api_claim_agent_task input body {body}")

        agent_id = body.get("agent_id")
        agent_name = body.get("agent_name", "unknown")
        habit_id = body.get("habit_id")
        execution_date = body.get("execution_date")

        if not agent_id or not habit_id or not execution_date:
            raise HTTPException(
                status_code=400,
                detail="Missing agent_id, habit_id or execution_date"
            )

        results = claim_agent_task(conn, user_id, habit_id, execution_date, agent_id, agent_name)

        if LOG_ENABLE:
            print (f"DEBUG: api_claim_agent_task results {results}")

        return JSONResponse(results)

    except Exception as e:
        print(f"ERROR: api_claim_agent_task failed: {e}")
        conn.rollback()
        return JSONResponse({"success": False, "error": str(e)})

    finally:
        conn.close()

async def api_get_detailed_agent_task_status(request: Request) -> JSONResponse:
    """
        Get Status Status of Each Task
    """
    user_id = await _require_user_authen_onekey(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    conn = connect()
    result = {}
    try:
        body = await request.json()
        if LOG_ENABLE:
            print(f"DEBUG: api_post_health_agent_task input body {body}")

        habit_id = body.get("habit_id")
        user_date = get_user_day(conn, user_id)

        ##
        habit_result = get_agent_task_state(conn, habit_id, user_date)

        ## Owner
        # habit_task_owner = get_agent_task_owner(conn, user_id, habit_id)
        habit_task_owner = get_agent_task_owner_execution_logs(conn, habit_id, user_date)

        result = {
            "status": habit_result.get("status", AGENT_TASK_STATUS_IDLE),
            "assigned_agent_id": habit_task_owner.get("assigned_agent_id", DEFAULT_ASSIGNED_AGENT_ID),
            "assigned_agent_name": habit_task_owner.get("assigned_agent_name", DEFAULT_ASSIGNED_AGENT_NAME),
        }

        return JSONResponse({"success": True, "result": result})

    except Exception as e:
        print (f"Failed to api_get_detailed_agent_task_status with error {e}")
        return JSONResponse({"success": False, "result": {}, "error": str(e)})

async def api_get_health_agent_task(request: Request) -> JSONResponse:
    user_id = await _require_user_authen_onekey(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = connect()
    try:
        qp = request.query_params
        habit_id = qp.get("habit_id")
        execution_id = qp.get("execution_id")
        root_execution_id = qp.get("root_execution_id")
        agent_id = qp.get("agent_id")
        limit = int(qp.get("limit") or 100)

        sql = """
        SELECT s.*
        FROM agent_execution_state s
        JOIN habits h ON h.id = s.habit_id
        WHERE h.user_id = ?
        """
        params: List[Any] = [user_id]

        if habit_id:
            sql += " AND s.habit_id = ?"
            params.append(habit_id)
        if execution_id:
            sql += " AND s.execution_id = ?"
            params.append(execution_id)
        if root_execution_id:
            sql += " AND s.root_execution_id = ?"
            params.append(root_execution_id)
        if agent_id:
            sql += " AND s.agent_id = ?"
            params.append(agent_id)

        sql += " ORDER BY COALESCE(s.updated_at, s.heartbeat_at, s.started_at) DESC LIMIT ?"
        params.append(limit)

        cur = conn.execute(sql, tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
        return JSONResponse({"success": True, "result": rows})
    except Exception as e:
        print(f"ERROR: api_get_health_agent_task failed: {e}")
        traceback.print_exc()
        return JSONResponse({"success": False, "error": str(e)})
    finally:
        conn.close()

async def api_post_health_agent_task(request: Request) -> JSONResponse:
    user_id = await _require_user_authen_onekey(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = connect()
    try:
        body = await request.json()
        if LOG_ENABLE:
            print(f"DEBUG: api_post_health_agent_task input body {body}")

        execution_id = body.get("execution_id")
        habit_id = body.get("habit_id")
        root_execution_id = body.get("root_execution_id")
        agent_id = body.get("agent_id")
        agent_name = body.get("agent_name")
        status = body.get("status") or "running"
        progress = body.get("progress")
        last_message = body.get("last_message")
        retry_count = body.get("retry_count")
        is_alive = body.get("is_alive")
        started_at = body.get("started_at")
        heartbeat_at = body.get("heartbeat_at")

        if not execution_id or not habit_id:
            raise HTTPException(status_code=400, detail="Missing execution_id or habit_id")

        # Authorization: habit must belong to this user
        row = conn.execute(
            "SELECT 1 FROM habits WHERE id = ? AND user_id = ? LIMIT 1",
            (habit_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Habit not found")

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        heartbeat_at = heartbeat_at or now_str
        started_at = started_at or now_str

        # Upsert. Keep started_at if the row already exists.
        conn.execute(
            """
            INSERT INTO agent_execution_state (
              execution_id,
              habit_id,
              root_execution_id,
              agent_id,
              agent_name,
              status,
              heartbeat_at,
              started_at,
              updated_at,
              retry_count,
              progress,
              last_message,
              is_alive
            )
            VALUES (
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?
            )
            ON CONFLICT(execution_id) DO UPDATE SET
              habit_id = excluded.habit_id,
              root_execution_id = excluded.root_execution_id,
              agent_id = excluded.agent_id,
              agent_name = excluded.agent_name,
              status = excluded.status,
              heartbeat_at = excluded.heartbeat_at,
              updated_at = excluded.updated_at,
              retry_count = COALESCE(excluded.retry_count, agent_execution_state.retry_count),
              progress = COALESCE(excluded.progress, agent_execution_state.progress),
              last_message = COALESCE(excluded.last_message, agent_execution_state.last_message),
              is_alive = COALESCE(excluded.is_alive, agent_execution_state.is_alive),
              started_at = COALESCE(agent_execution_state.started_at, excluded.started_at)
            """,
            (
                str(execution_id),
                str(habit_id),
                root_execution_id,
                agent_id,
                agent_name,
                status,
                heartbeat_at,
                started_at,
                now_str,
                retry_count,
                progress,
                last_message,
                is_alive,
            ),
        )
        conn.commit()
        return JSONResponse({"success": True})
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR: api_post_health_agent_task failed: {e}")
        traceback.print_exc()
        conn.rollback()
        return JSONResponse({"success": False, "error": str(e)})
    finally:
        conn.close()


async def api_get_log_agent_task(request: Request) -> JSONResponse:
    """
        Todo: Change Credential to Authenticate and OneKey Access Key
    """
    # user_id = await _require_user_authen_onekey(request)
    # if not user_id:
    #     raise HTTPException(status_code=403, detail="Forbidden")
    conn = connect()
    try:
        qp = request.query_params
        user_id = qp.get("user_id")
        if not user_id:
            raise HTTPException(status_code=400, detail="/api_get_log_agent_task Missing user_id")

        habit_id = qp.get("habit_id")
        execution_id = qp.get("execution_id")
        root_execution_id = qp.get("root_execution_id")
        agent_id = qp.get("agent_id")
        event_type = qp.get("event_type")
        since_ts = qp.get("since_ts")
        limit = int(qp.get("limit") or 200)

        sql = """
        SELECT l.*
        FROM agent_execution_logs l
        JOIN habits h ON h.id = l.habit_id
        WHERE h.user_id = ?
        """
        params: List[Any] = [user_id]

        if habit_id:
            sql += " AND l.habit_id = ?"
            params.append(habit_id)
        if execution_id:
            sql += " AND l.execution_id = ?"
            params.append(execution_id)
        if root_execution_id:
            sql += " AND l.root_execution_id = ?"
            params.append(root_execution_id)
        if agent_id:
            sql += " AND l.agent_id = ?"
            params.append(agent_id)
        if event_type:
            sql += " AND l.event_type = ?"
            params.append(event_type)
        if since_ts:
            sql += " AND l.ts >= ?"
            params.append(since_ts)

        sql += " ORDER BY l.ts DESC LIMIT ?"
        params.append(limit)

        cur = conn.execute(sql, tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
        if LOG_ENABLE:
            print (f"DEBUG: api_get_log_agent_task rows: {rows}")
        return JSONResponse({"success": True, "results": rows})
    except Exception as e:
        print(f"ERROR: api_get_log_agent_task failed with error {e}")
        traceback.print_exc()
        return JSONResponse({"success": False, "error": str(e)})
    finally:
        conn.close()


async def api_post_log_agent_task(request: Request) -> JSONResponse:
    user_id = await _require_user_authen_onekey(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = connect()
    try:
        body = await request.json()
        if LOG_ENABLE:
            print(f"DEBUG: api_post_log_agent_task input body {body}")

        habit_id = body.get("habit_id")
        execution_id = body.get("execution_id")
        root_execution_id = body.get("root_execution_id")
        agent_id = body.get("agent_id")
        agent_name = body.get("agent_name")
        event_type = body.get("event_type")
        status = body.get("status")
        message = body.get("message")
        metadata = body.get("metadata")

        if not habit_id or not execution_id or not event_type:
            raise HTTPException(status_code=400, detail="Missing habit_id, execution_id, or event_type")

        result = create_execution_logs(conn, body, user_id)

        if result["success"]:
            return JSONResponse({"success": True, "id": log_id})
        else:
            return JSONResponse({"success": False, "id": log_id})

    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR: api_post_log_agent_task failed: {e}")
        traceback.print_exc()
        conn.rollback()
        return JSONResponse({"success": False, "error": str(e)})
    finally:
        conn.close()

def select_agent_execution_by_date(conn, habit_id, execution_date):
    """
    """
    cursor = conn.cursor()
    # 1. Try to find existing root execution
    cursor.execute("""
        SELECT *
        FROM habit_logs
        WHERE habit_id = ?
          AND execution_date = ?
          AND is_root_execution = 1
        LIMIT 1
    """, (habit_id, execution_date))
    dict_rows = fetchall_dicts(cursor)
    return dict_rows

def get_or_create_root_execution(conn, habit_id, user_id, execution_date):
    """
        root_id: 一条 habit_log的id: root_id
    """
    cursor = conn.cursor()

    # 1. Try to find existing root execution
    cursor.execute("""
        SELECT id
        FROM habit_logs
        WHERE habit_id = ?
          AND user_id = ?
          AND execution_date = ?
          AND is_root_execution = 1
        LIMIT 1
    """, (habit_id, user_id, execution_date))

    row = cursor.fetchone()
    if row:
        return row[0]

    # 2. Create if not exists
    root_id = str(uuid.uuid4())

    expected_children = 5
    cursor.execute("""
        INSERT INTO habit_logs (
            id,
            habit_id,
            user_id,
            parent_log_id,
            log_type,
            status,
            is_root_execution,
            execution_date,
            expected_children,
            retry_count,
            max_retries,
            assigned_at,
            heartbeat_at
        )
        VALUES (
            ?, ?, ?, NULL,
            'agent',
            'pending',
            1,
            ?,
            ?,
            0,
            1,
            datetime('now'),
            datetime('now')
        )
    """, (root_id, habit_id, user_id, execution_date, expected_children))
    return root_id

async def api_post_agent_result(request: Request) -> JSONResponse:
    """
        #task_id:  habit table habit_id, type="agent"
        ## execution_id: The Actual UniqueId Of Each Task Running Log
        ## root_execution_id: Parallel Task, Parent Task have a root_execution_id and parallel task each have a execution_id.

        N Agent Task
        Request:
        {
            user_id: user_id
            results: [
                {
                    task_id: "## the HabitId of Agent Task",
                    content: str
                    images: List[Any]
                    agent_id: xxxx?
                    agent_name: codex,
                },
                {
                    task_id: "## the HabitId of Agent Task",
                    result_text: str
                    result_images: List[Any]
                    agent_id: xxxx?
                    agent_name: codex,
                },
            ]
        }
            task_id:
            result_text: str
            agent_id: xxxx?
            agent_name: codex

        {
            "success": True,
            "task_status": [
                {"task_id": task_id, "success": True, "updated": agent_checkin_result},
                {"task_id": task_id, "success": False, "updated": agent_checkin_result}
            ]
        }
    """
    user_id = await _require_user_authen_onekey(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid OneKey Key No User Id Matched...")
    ### Result
    conn = connect()
    update_result = []
    try:
        body = await request.json()
        if LOG_ENABLE:
            print (f"DEBUG: api_post_agent_result input body {body}")

        results = body.get("results", [])

        if not isinstance(results, list):
            raise HTTPException(status_code=400, detail="Invalid Results Type $.result should be a list of agent running result json")
        if len(results) == 0:
            results = JSONResponse({"ok": True, "logs": []})

        for result in results:

            ### Get Execution Record: task_id, execution_id, root_execution_id
            task_id = result.get("task_id", "")
            if task_id == "":
                print (f"WARNING: Input Result Missing Task Id Skipped {result}")
                continue
            execution_id = result.get("execution_id", "")
            root_execution_id = result.get("root_execution_id", "")

            content = result.get("content", "")
            images = result.get("images", [])

            agent_id = result.get("agent_id", "")
            agent_name = result.get("agent_name", "")
            if not agent_id:
                agent_id = fill_agent_running_task_id(agent_name)
            image_urls = ",".join(images)
            status = result.get("status", "")

            valid = is_valid_result_output(content, images)
            agent_checkin_result = {}
            if valid:
                ## user_id: Coach Agent,habit_id: xxxx, note: xxxx, parent_log_id: reply to parent, log_type: HABIT_LOG_TYPE_AGENT
                agent_checkin_result = checkin(
                    conn,
                    user_id=agent_id,
                    habit_id=task_id,
                    note=content,
                    image_url=image_urls,
                    value=1,
                    parent_log_id=None,
                    log_type=HABIT_LOG_TYPE_AGENT,
                    execution_id=execution_id,
                    root_execution_id=root_execution_id,
                    status=status
                )
                conn.commit()
            else:
                print (f"WARNING: Input Result Invalid Skipped Content or Images are Empty...|{result}")
                continue
            print(f"INFO: Agent {agent_id} |Task ID {task_id}|agent_checkin_result {agent_checkin_result}")

            if len(agent_checkin_result) > 0:
                update_result.append({"task_id": task_id, "success": True, "updated": agent_checkin_result})
            else:
                update_result.append({"task_id": task_id, "success": False, "updated": agent_checkin_result})

        if LOG_ENABLE:
            print (f"DEBUG: api_post_agent_result results {update_result}")

        ### update agent results
        return JSONResponse({"success": True, "task_status": update_result})
    except Exception as e:
        print (f"ERROR: Failed to api_post_agent_result with {e}")
        return JSONResponse({"success": False, "task_status": update_result})
    finally:
        conn.close()

async def lifespan(app: Starlette):
    init_db()
    # Start background worker for agent tasks
    asyncio.create_task(process_agent_tasks())
    yield

app = Starlette(
    debug=False,
    routes=[
        Route("/", main_entry, methods=["GET"]),
        Route("/index", main_index, methods=["GET"]),
        Route("/chat_admin", index, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Mount("/auth", app=auth_router),
        # Mount auth router under /auth to avoid root conflicts
        # Habits API routes
        Route("/api/chat/", chat_endpoint, methods=["POST"]),
        Route("/api/v1/asr", asr_endpoint, methods=["POST"]),
        Route("/api/v1/habits/create", api_create_habit, methods=["POST"]),
        Route("/api/v1/habits/list", api_list_habits, methods=["GET"]),
        Route("/api/v1/habits/poll", api_poll_habits, methods=["GET"]),
        Route("/api/v1/habits/get", api_get_habit, methods=["GET"]),
        Route("/api/v1/habits/delete", api_delete_habit, methods=["POST"]),
        Route("/api/v1/habits/delete/all", api_delete_habit_all, methods=["POST"]),
        Route("/api/v1/habits/generate_tasks", api_generate_tasks, methods=["POST"]),
        Route("/api/v1/habits/checkin", api_checkin, methods=["POST"]),
        Route("/api/v1/habits/undo_checkin", api_undo_checkin, methods=["POST"]),
        ### Agent
        Route("/api/v1/agent/dashboard", api_get_agent_dashboard, methods=["GET"]),
        Route("/api/v1/agent/tasks/get", api_get_agent_task, methods=["GET"]),
        Route("/api/v1/agent/tasks/post", api_post_agent_result, methods=["POST"]),
        Route("/api/v1/agent/tasks/claim", api_claim_agent_task, methods=["POST"]),
        Route("/api/v1/agent/tasks/run", api_run_agent_task, methods=["POST"]),
        Route("/api/v1/agent/tasks/detail/get", api_get_detailed_agent_task_status, methods=["GET"]),
        Route("/api/v1/agent/tasks/health/post", api_post_health_agent_task, methods=["POST"]),
        Route("/api/v1/agent/tasks/health/get", api_get_health_agent_task, methods=["GET"]),
        Route("/api/v1/agent/tasks/log/post", api_post_log_agent_task, methods=["POST"]),
        Route("/api/v1/agent/tasks/log/get", api_get_log_agent_task, methods=["GET"]),
        Route("/api/v1/coaches/save", api_save_coach_setting, methods=["POST"]),
        Route("/api/v1/coaches/list", api_get_coach_settings, methods=["GET"]),
        Route("/api/v1/coaches/delete", api_delete_coach_setting, methods=["POST"]),
        Route("/api/v1/profile/update", api_update_profile, methods=["POST"]),
        Route("/api/v1/profile/get", api_get_profile, methods=["GET"]),
        Route("/api/v1/config/update", api_update_config, methods=["POST"]),
        Route("/api/v1/config/get", api_get_config, methods=["GET"]),
        Route("/api/v1/update_habit_streak", api_checkin, methods=["POST"]),
        Route("/api/v1/calculate_calories", api_calculate_calories, methods=["POST"]),
        Route("/api/v1/nutrition/summary", api_nutrition_summary, methods=["GET"]),
        Mount("/static", app=StaticFiles(directory=str(STATIC_DIR.resolve()), html=True), name="static"),
        Mount("/assets", app=StaticFiles(directory=str(ASSETS_DIR.resolve()), html=True), name="assets"),
        Mount("/", app=mcp_app),
    ],
    lifespan=lifespan,
)

def main() -> None:
    # Run the full Starlette app (includes /chat, /api/v1, /static, and mounted /mcp).
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    os.environ["MCP_SERVER_URL"] = f"http://{host}:{port}/mcp"
    uvicorn.run(app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    main()
