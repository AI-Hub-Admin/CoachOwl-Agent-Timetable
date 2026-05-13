import base64
import traceback

import httpx
from typing import Any, AsyncGenerator, Dict, List, Optional

from dotenv import load_dotenv
import inspect
from typing import get_type_hints
import sqlite3
from typing import Optional, List, Dict
import sqlite3
import os
import base64
from typing import Optional, List
from urllib.parse import urlparse
import base64
import requests

from .constants import KEY_CONTENT, USER_INTENT_CAREER, USER_INTENT_DEFAULT, USER_INTENT_FITNESS, LONG_TIMEOUT, \
    KEY_SUCCESS, QWEN_MODEL, DEFAULT_FOOD_ANALYZING_RESULT, PROMPT_DIR, LOG_ENABLE, \
    DEFAULT_FOOD_ANALYZING_CHECKIN_PROMPT, MAX_INPUT_TOKEN_LENGTH, KEY_QUERY_LIST, SEARCH_FOOD_ITEM_PER_PAGE, \
    IMAGE_LONG_TIMEOUT, STREAMING_TIMEOUT, DEFAULT_AGENT_BACKGROUND_TASK_CONTENT, \
    AGENT_TASK_STATUS_RUNNING, AGENT_TASK_STATUS_COMPLETED, AGENT_TASK_STATUS_IDLE, \
    AGENT_TASK_STATUS_STALLED, AGENT_TASK_STATUS_FAILURE

from .utils import (call_qwen_tool_calls_model_selection, function_call_result_common_mapper, _parse_json_response,
                    post_process_function_call_qwen_common, llm_qwen_messages_chat, call_qwen_messages_model_selection, read_files, is_valid_function_call_arguments)
from .db import fetchall_dicts
from .tools.email_tools import send_email_with_attachments
from .tools.producthunt_tools import (
    fetch_producthunt_entries,
    filter_entries_by_keywords,
    mark_and_filter_unseen,
)
from .db import connect, ensure_user


import os
import asyncio
import json

load_dotenv()

access_key = os.getenv("DEEPNLP_ONEKEY_ROUTER_ACCESS")
if LOG_ENABLE:
    print (f"DEBUG: Loading OneKey Access Key: {access_key}")

### ----- Use the OneKey Gateway -------
agnets_registry = {}

timeout = httpx.Timeout(
    connect=STREAMING_TIMEOUT,  # time to establish connection
    read=STREAMING_TIMEOUT,  # time waiting for response
    write=STREAMING_TIMEOUT,  # time to send request
    pool=STREAMING_TIMEOUT  # connection pool timeout
)

httpx_client = httpx.AsyncClient(
    timeout=timeout,
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20
    ),
)


### Agent Task Status
###  table: habit_logs -----  /claim by local agent and tasks
###  table: agent_execution_state /post result status and display
###  table: agent_execution_logs /detailed logs for Running | Stalling

def get_agent_task_state(conn: sqlite3.Connection, habit_id: str, user_date: str) -> Dict[str, Any]:
    """
    Aggregate task status + progress

    days: [1, target_days]

    Rule:
    Condition	Final Status
    any failure	failure
    any running or starting	running
    all completed	completed
    no logs	idle
    """
    cur = conn.execute("""
        SELECT * FROM agent_execution_state WHERE habit_id = ? AND scheduled_date = ?
        """, (habit_id, user_date))
    agent_states = fetchall_dicts(cur)
    if len(agent_states) == 0:
        return {
            "habit_id": habit_id,
            "status": AGENT_TASK_STATUS_IDLE,
            "progress": 0.0
        }
    if LOG_ENABLE:
        print (f"DEBUG: get_agent_task_state records returned {len(agent_states)}")
        print (f"DEBUG: get_agent_task_state agent_states returned {agent_states}")

    failure_count, running_count, completed_count = 0,0,0
    total = len(agent_states) ## different execution_id, same date (N agents) for agent_execution_state
    ### get_agent_task_state
    assigned_agent_id_list = []
    for agent_state in agent_states:
        """
        DEBUG: get_agent_task_state agent_states returned [{'execution_id': '5c8aa700-e3c4-4358-9484-17ece380255a:2026-05-12:codex', 'habit_id': '5c8aa700-e3c4-4358-9484-17ece380255a', 'root_execution_id': '5c8aa700-e3c4-4358-9484-17ece380255a:2026-05-12', 'agent_id': 'codex', 'agent_name': 'codex', 'status': 'scheduled', 'scheduled_date': '2026-05-12', 'scheduled_start_time': '2026-05-12 00:00:00', 'scheduled_end_time': '2026-05-12 23:59:59', 'heartbeat_at': None, 'started_at': None, 'updated_at': '2026-05-12 08:45:30', 'retry_count': 0, 'progress': 0.0, 'last_message': None, 'is_alive': 1}]
        """
        execution_id = agent_state.get("execution_id", "")
        root_execution_id = agent_state.get("root_execution_id", "")
        habit_id = agent_state.get("habit_id", "")
        agent_id = agent_state.get("agent_id", "")
        agent_name = agent_state.get("agent_name", "")
        agent_status = agent_state.get("status", "")

        if agent_status == AGENT_TASK_STATUS_RUNNING:
            running_count += 1
        elif agent_status == AGENT_TASK_STATUS_COMPLETED:
            completed_count += 1
        elif agent_status == AGENT_TASK_STATUS_FAILURE:
            failure_count += 1
        ## append agent_id
        assigned_agent_id_list.append(agent_id)

    # ✅ status aggregation
    status = ""
    if failure_count > 0:
        status = AGENT_TASK_STATUS_FAILURE
    elif running_count > 0:
        status = AGENT_TASK_STATUS_RUNNING
    elif completed_count == total:
        status = AGENT_TASK_STATUS_COMPLETED
    else:
        status = AGENT_TASK_STATUS_IDLE

    return {
        "habit_id": habit_id,
        "status": status,
        "total_tasks": total,
        "completed_tasks": completed_count,
        "": ",".join(assigned_agent_id_list)
    }

def get_agent_task_owner_execution_logs(
        conn: sqlite3.Connection,
        habit_id: str,
        scheduled_date: str
) -> Dict[str, Any]:
    """
    Get latest assigned agent (task owner) from agent_execution_state
    for a specific scheduled_date execution timeslot.
    """

    cur = conn.execute("""
        SELECT
            agent_id,
            agent_name,
            execution_id,
            status,
            scheduled_date,
            scheduled_start_time,
            scheduled_end_time,
            started_at,
            updated_at
        FROM agent_execution_state
        WHERE habit_id = ?
          AND scheduled_date = ?
          AND agent_id IS NOT NULL
        ORDER BY datetime(updated_at) DESC
        LIMIT 1
    """, (habit_id, scheduled_date))

    row = cur.fetchone()

    if not row:
        return {
            "habit_id": habit_id,
            "scheduled_date": scheduled_date,
            "assigned_agent_id": None,
            "assigned_agent_name": None
        }

    return {
        "habit_id": habit_id,
        "scheduled_date": row[4],
        "assigned_agent_id": row[0],
        "assigned_agent_name": row[1],
        "execution_id": row[2],
        "status": row[3],
        "scheduled_start_time": row[5],
        "scheduled_end_time": row[6],
        "started_at": row[7],
        "updated_at": row[8]
    }

### Local Agent
from datetime import datetime, timedelta
import uuid
from typing import Dict, Any, List
import sqlite3

def _generate_execution_id(habit_id: str, scheduled_date: str, agent_id: str) -> str:
    return f"{habit_id}:{scheduled_date}:{agent_id}"


def _generate_root_execution_id(habit_id: str, scheduled_date: str) -> str:
    return f"{habit_id}:{scheduled_date}"


def _build_schedule_slots(
    start_date: str,
    end_date: str,
    target_days: int
):
    """
    Returns structured schedule slots instead of raw dates
    """

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    if target_days:
        end = min(end, start + timedelta(days=target_days - 1))

    slots = []

    current = start
    while current <= end:
        day = current.strftime("%Y-%m-%d")

        slots.append({
            "scheduled_date": day,
            "scheduled_start_time": f"{day} 00:00:00",
            "scheduled_end_time": f"{day} 23:59:59"
        })

        current += timedelta(days=1)

    return slots

def _date_range(start_date: str, end_date: str, max_days: int = None):
    """
    Generate YYYY-MM-DD range
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    if max_days:
        end = min(end, start + timedelta(days=max_days - 1))

    current = start
    while current <= end:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)

def create_agent_execution_plan(
    conn: sqlite3.Connection,
    habit_id: str,
    start_date: str,
    end_date: str,
    target_days: int = 0,
    agents: List[Dict[str, str]] = None
) -> Dict[str, Any]:

    if not agents:
        agents = [{
            "agent_id": "default",
            "agent_name": "default"
        }]

    cursor = conn.cursor()

    insert_sql = """
        INSERT OR IGNORE INTO agent_execution_state (
            execution_id,
            habit_id,
            root_execution_id,
            agent_id,
            agent_name,
            status,

            scheduled_date,
            scheduled_start_time,
            scheduled_end_time,

            heartbeat_at,
            started_at,
            updated_at,

            retry_count,
            progress,
            last_message,
            is_alive
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            NULL, NULL, datetime('now'),
            0, 0, NULL, 1
        )
    """

    slots = _build_schedule_slots(start_date, end_date, target_days)

    created = []

    for slot in slots:

        scheduled_date = slot["scheduled_date"]
        root_execution_id = _generate_root_execution_id(habit_id, scheduled_date)

        for agent in agents:

            execution_id = _generate_execution_id(
                habit_id,
                scheduled_date,
                agent["agent_id"]
            )

            row = (
                execution_id,
                habit_id,
                root_execution_id,
                agent["agent_id"],
                agent["agent_name"],
                "scheduled",

                slot["scheduled_date"],
                slot["scheduled_start_time"],
                slot["scheduled_end_time"]
            )

            cursor.execute(insert_sql, row)

            created.append({
                "execution_id": execution_id,
                "root_execution_id": root_execution_id,
                "scheduled_date": scheduled_date,
                "agent_id": agent["agent_id"],
                "status": "scheduled"
            })

    conn.commit()

    return {
        "status": "ok",
        "execution_plans_created": len(created),
        "execution_plans": created
    }

def create_execution_logs(conn: sqlite3.Connection, body: Dict[str, Any], user_id: str) -> Dict:
    """
    """
    if LOG_ENABLE:
        print(f"DEBUG: create_execution_logs input body {body}")
    try:
        log_id = body.get("id") or str(uuid.uuid4())

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
            return {"success": False}

        # Authorization: habit must belong to this user
        row = conn.execute(
            "SELECT 1 FROM habits WHERE id = ? AND user_id = ? LIMIT 1",
            (habit_id, user_id),
        ).fetchone()
        if not row:
            return {"success": False}

        if metadata is not None and not isinstance(metadata, str):
            try:
                metadata = json.dumps(metadata, ensure_ascii=False)
            except Exception:
                metadata = str(metadata)

        conn.execute(
            """
            INSERT INTO agent_execution_logs (
              id,
              habit_id,
              execution_id,
              root_execution_id,
              agent_id,
              agent_name,
              event_type,
              status,
              message,
              metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                habit_id,
                execution_id,
                root_execution_id,
                agent_id,
                agent_name,
                event_type,
                status,
                message,
                metadata,
            ),
        )
        conn.commit()

        return {"success": True}
    except Exception as e:
        print (f"DEBUG: create_execution_logs Failed with error {e}")
        return {"success": False}


def update_agent_execution_logs(conn, execution_logs: Dict, user_id: str) -> Dict[str, Any]:
    """
        update_agent_execution_logs Add Logs to Avoid
    """
    result = create_execution_logs(conn, execution_logs, user_id)
    return result

def update_agent_execution_plan_status(
    conn: sqlite3.Connection,
    habit_id: str,
    execution_date: Optional[str] = None,
    status: Optional[str] = None,
    agents: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """
    Update agent execution plan status + write execution logs.

    agents example:
    [
        {
            "agent_id": "a1",
            "agent_name": "agent-A",
            "status": "running",
            "message": "started"
        }
    ]
    """
    try:
        cursor = conn.cursor()

        ### Select or Create
        plan_results = select_agent_execution_plan(conn, habit_id, execution_date, agents)
        print (f"INFO: Agent Execution Plans plan_results {plan_results}")
        execution_plans = plan_results["execution_plans"]
        if LOG_ENABLE:
            print (f"DEBUG: execution_plans results | {execution_plans}")
        if len(execution_plans) == 0:
            result = {
                "success": False,
                "habit_id": habit_id,
                "execution_id": None,
                "updated_agents": len(agents) if agents else 0,
                "logs_written": None
            }
            return result

        ## task, date, first execution
        execution_plan = execution_plans[0]
        execution_id = execution_plan.get("execution_id", "")

        if LOG_ENABLE:
            print(f"INFO: Updating agent execution plan status "
                  f"habit_id={habit_id} execution_id={execution_id} status={status} agents={agents}")

        # -----------------------------
        # STEP 1: Update main execution status (optional)
        # -----------------------------
        if execution_id and status:
            cursor.execute("""
                UPDATE agent_execution_state
                SET status = ?,
                    heartbeat_at = datetime('now')
                WHERE execution_id = ?
                  AND habit_id = ?
            """, (status, execution_id, habit_id))

        # -----------------------------
        # STEP 2: Insert logs (core part)
        # -----------------------------
        log_entries = []

        # global execution-level log
        if status:
            log_entries.append({
                "id": str(uuid.uuid4()),
                "habit_id": habit_id,
                "execution_id": execution_id,
                "root_execution_id": execution_id,
                "agent_id": None,
                "agent_name": None,
                "event_type": "state",
                "status": status,
                "message": f"Execution status updated to {status}",
                "metadata": None
            })

        # agent-level logs
        if agents:
            for agent in agents:
                log_entries.append({
                    "id": str(uuid.uuid4()),
                    "habit_id": habit_id,
                    "execution_id": execution_id,
                    "root_execution_id": execution_id,
                    "agent_id": agent.get("agent_id"),
                    "agent_name": agent.get("agent_name"),
                    "event_type": "state",
                    "status": agent.get("status"),
                    "message": agent.get("message"),
                    "metadata": json.dumps(agent) if agent else None
                })

        # batch insert
        if log_entries:
            cursor.executemany("""
                INSERT INTO agent_execution_logs (
                    id,
                    habit_id,
                    execution_id,
                    root_execution_id,
                    agent_id,
                    agent_name,
                    event_type,
                    status,
                    message,
                    metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    log["id"],
                    log["habit_id"],
                    log["execution_id"],
                    log["root_execution_id"],
                    log["agent_id"],
                    log["agent_name"],
                    log["event_type"],
                    log["status"],
                    log["message"],
                    log["metadata"]
                )
                for log in log_entries
            ])

        conn.commit()

        return {
            "success": True,
            "habit_id": habit_id,
            "execution_id": execution_id,
            "updated_agents": len(agents) if agents else 0,
            "logs_written": len(log_entries)
        }

    except Exception as e:
        conn.rollback()

        print(f"ERROR: update_agent_execution_plan_status failed: {e}")

        return {
            "success": False,
            "habit_id": habit_id,
            "execution_id": None,
            "error": str(e)
        }

def select_agent_execution_plan(
    conn: sqlite3.Connection,
    habit_id: str,
    scheduled_date: Optional[str] = None,
    agents: List[Dict[str, str]] = None
):
    """
    agent_execution_state

    Get the execution plan by habit_id + scheduled_date.

    Returns all existing execution records for the date.
    If an agent does not yet have a record for the date,
    generate a virtual scheduled record.

    Args:
        conn:
            sqlite3 connection

        habit_id:
            habit id

        scheduled_date:
            YYYY-MM-DD

        agents:
            [
                {
                    "agent_id": "...",
                    "agent_name": "...",
                    "scheduled_start_time": "09:00:00",
                    "scheduled_end_time": "10:00:00"
                }
            ]
    """

    try:

        results = {
            "habit_id": habit_id,
            "scheduled_date": scheduled_date,
            "execution_plans": []
        }

        if not scheduled_date:
            return results

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                execution_id,
                root_execution_id,
                agent_id,
                agent_name,
                status,
                scheduled_date,
                scheduled_start_time,
                scheduled_end_time,
                heartbeat_at,
                started_at,
                updated_at,
                retry_count,
                progress,
                last_message,
                is_alive
            FROM agent_execution_state
            WHERE habit_id = ?
              AND scheduled_date = ?
            ORDER BY scheduled_start_time ASC
            """,
            (
                habit_id,
                scheduled_date
            )
        )

        rows = cursor.fetchall()

        existing_agent_ids = set()

        # Existing execution records
        for row in rows:

            execution = {
                "execution_id": row[0],
                "root_execution_id": row[1],
                "agent_id": row[2],
                "agent_name": row[3],
                "status": row[4],
                "scheduled_date": row[5],
                "scheduled_start_time": row[6],
                "scheduled_end_time": row[7],
                "heartbeat_at": row[8],
                "started_at": row[9],
                "updated_at": row[10],
                "retry_count": row[11],
                "progress": row[12],
                "last_message": row[13],
                "is_alive": row[14]
            }

            existing_agent_ids.add(row[2])

            results["execution_plans"].append(execution)

        # Final sort
        results["execution_plans"].sort(
            key=lambda x: (
                x.get("scheduled_start_time") or ""
            )
        )

        return results

    except Exception as e:
        print(
            f"select_agent_execution_plan failed with error: {e}"
        )

        return {}

def select_pending_agent_execution_task(
    conn: sqlite3.Connection,
    scheduled_date: str
):
    """
    SELECT all the pending agent tasks, not in running/completed status, and runs in backgrounds
    agent_execution_state,
    """
    try:

        cur = conn.execute(
            """
            SELECT
                h.*,

                aes.execution_id,
                aes.root_execution_id,
                aes.agent_id,
                aes.agent_name,
                aes.status,
                aes.scheduled_date,
                aes.scheduled_start_time,
                aes.scheduled_end_time

            FROM habits h

            INNER JOIN agent_execution_state aes
                ON aes.habit_id = h.id

            WHERE
                h.task_type = 'agent'
                AND h.archived = 0

                AND aes.scheduled_date = ?

                AND aes.status IN (
                    'scheduled',
                    'pending',
                    'idle',
                    'stalled',
                    'failure'
                )

            ORDER BY
                aes.scheduled_start_time ASC,
                aes.updated_at ASC
            """,
            (scheduled_date,)
        )

        agent_tasks = fetchall_dicts(cur)
        return agent_tasks

    except Exception as e:
        print (f"ERROR: select_pending_agent_execution_task failed with error: {e}")
        return []


def select_pending_agent_execution_task_size(
    conn: sqlite3.Connection,
    scheduled_date: str
) -> int:
    """
    SELECT all the pending agent tasks, not in running/completed status, and runs in backgrounds
    agent_execution_state,
    """
    try:
        cur_after = conn.execute(
            """
            SELECT
                COUNT(*) as remaining_tasks

            FROM habits h

            INNER JOIN agent_execution_state aes
                ON aes.habit_id = h.id

            WHERE
                h.task_type = 'agent'
                AND h.archived = 0

                AND aes.scheduled_date = ?

                AND aes.status IN (
                    'scheduled',
                    'pending',
                    'idle',
                    'stalled',
                    'failure'
                )
            """,
            (scheduled_date,)
        )

        remaining = cur_after.fetchone()[0]
        return remaining

    except Exception as e:
        print (f"ERROR: select_pending_agent_execution_task_size failed with error: {e}")
        return 0

### prompt
## Call LLM to get the branch
analyze_food_prompt_file_path = str(PROMPT_DIR / "analyze_food_examples.md")
print(f"Loading intent_prompt_file_path from path {analyze_food_prompt_file_path}")
ANALYZE_FOOD_SYSTEM_PROMPT = read_files(analyze_food_prompt_file_path)

### Generation Prompt
prompt_generation_system_prompt = str(PROMPT_DIR / "agent_run_summarization.md")


## key1: unique_id, key2: api_id
fitness_tools_dict = {
    "gemini/gemini": {
        "ocr_extract_text_from_image": "",
        "list_items_from_image": ""
    },
    "fdcnal/usda-fooddata-central-agent": {
        "search_foods": ""
    }
}

## send email, search_xxx, search_jobs, send_message
## playwright
career_tools_dict = {
    "google-search/google-search": {
        "google-search": {}
    },
    "search_tools": {
        "search_jobs": {
        }
    },
    "omni-doc-agent": {
        "send_emails": {}
    }
}

# -----------------------------
# Async API Call
# -----------------------------
async def call_onekey_gateway_api(
    unique_id: str,
    api_id: str,
    data: Dict[Any, Any]
):
    """
    Call OneKey Gateway API using shared async httpx client.
    """
    result = {}
    try:
        import os

        url = "https://agent.deepnlp.org/agent_router"
        if not access_key:
            raise ValueError("DEEPNLP_ONEKEY_ROUTER_ACCESS is not set")

        payload = {
            "unique_id": unique_id,
            "api_id": api_id,
            "data": data
        }

        headers = {
            "Content-Type": "application/json",
            "X-OneKey": access_key
        }

        if LOG_ENABLE:
            print(f"DEBUG: call_onekey_gateway_api url {url}|payload {payload}|headers {headers}")

        response = await httpx_client.post(
            url,
            json=payload,
            headers=headers
        )

        response.raise_for_status()
        result = response.json()
        if LOG_ENABLE:
            print("DEBUG: call_onekey_gateway_api success: result length", len(result))
        result[KEY_SUCCESS] = True
        return result

    except httpx.TimeoutException as e:
        print(f"ERROR: call_onekey_gateway_api timeout error: {e}")
        result[KEY_SUCCESS] = False
        return result
    except httpx.HTTPStatusError as e:
        print(f"ERROR: call_onekey_gateway_api HTTP error: {e.response.status_code} - {e.response.text}")
        result[KEY_SUCCESS] = False
        return result
    except Exception as e:
        print(f"ERROR: call_onekey_gateway_api failed with error: {e}")
        result[KEY_SUCCESS] = False
        return result

def search_food_result_content_wrapper(result: Dict):
    """
        "nutrientId": 1003,
          "nutrientName": "Protein",
        "nutrientId": 1004,
          "nutrientName": "Total lipid (fat)",
        "nutrientId": 1005,
          "nutrientName": "Carbohydrate, by difference",
        "nutrientId": 1008,
          "nutrientName": "Energy",
        "nutrientId": 1057,
          "nutrientName": "Caffeine",
          "nutrientId": 2000,
          "nutrientName": "Total Sugars",
        "nutrientId": 1093,
          "nutrientName": "Sodium, Na",

        Keep Important Value: Energy, Protein,
        clean the nutritions and limit to variable keep_nutrition_number =10
        result.
        {
          "success": true,
          "totalHits": 4433,
          "currentPage": 1,
          "totalPages": 887,
          "pageList": [
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10
          ],
          "foodSearchCriteria": {
            "query": "large steak",
            "generalSearchInput": "large steak",
            "pageNumber": 1,
            "numberOfResultsPerPage": 50,
            "pageSize": 5,
            "requireAllWords": false
          },
          "foods": []
        }

        Clean: "foods"
        「"fdcId": 2706747,
          "description": "Pepper steak",
          "commonNames": "",
          "additionalDescriptions": "",
          "dataType": "Survey (FNDDS)",
          "foodCode": 27416150,
          "publishedDate": "2024-10-31",
          "foodCategory": "Meat mixed dishes",
          "foodCategoryId": 3301042,
          "allHighlightFields": "",
          "score": 369.10315,
          "microbes": [],
          "foodNutrients": [
            ]


        filter by nutrientId
    """
    keep_nutrition_number = 10

    # whitelist nutrient IDs
    IMPORTANT_NUTRIENTS = {
        1003,  # Protein
        1004,  # Fat
        1005,  # Carbs
        1008,  # Energy
        1057,  # Caffeine
        2000,  # Sugar
        1093   # Sodium
    }

    PRIORITY_NUTRIENTS = {1008, 1003}  # Energy, Protein

    result_cleaner = {}

    try:
        foods_list = result.get("foods", [])
        foods_list_clean = []

        for food in foods_list:
            nutrients = food.get("foodNutrients", [])

            # 1. Filter by allowed nutrient IDs
            filtered_nutrients = [
                n for n in nutrients
                if n.get("nutrientId") in IMPORTANT_NUTRIENTS
            ]

            # 2. Sort → priority nutrients first
            def nutrient_priority(n):
                nid = n.get("nutrientId")
                if nid in PRIORITY_NUTRIENTS:
                    return 0
                return 1

            filtered_nutrients.sort(key=nutrient_priority)

            # 3. Deduplicate by nutrientId (important for USDA data)
            seen = set()
            unique_nutrients = []
            for n in filtered_nutrients:
                nid = n.get("nutrientId")
                if nid not in seen:
                    seen.add(nid)
                    unique_nutrients.append({
                        "nutrientId": nid,
                        "nutrientName": n.get("nutrientName"),
                        "value": n.get("value"),
                        "unitName": n.get("unitName")
                    })

            # 4. Limit to top N
            unique_nutrients = unique_nutrients[:keep_nutrition_number]

            # 5. Build cleaned food object
            fdc_id = food.get("fdcId", "")
            food_clean = {
                "fdcId": fdc_id,
                "fdc_url": f"https://fdc.nal.usda.gov/food-details/{fdc_id}/nutrients",
                "description": food.get("description"),
                "foodCategory": food.get("foodCategory"),
                "foodNutrients": unique_nutrients
            }

            foods_list_clean.append(food_clean)

        # 6. Build final result
        result_cleaner = {
            "success": result.get("success", False),
            "totalHits": result.get("totalHits", 0),
            "foods": foods_list_clean
        }

    except Exception as e:
        print(f"[ERROR] search_food_result_content_wrapper: {e}")
        result_cleaner = {
            "success": False,
            "foods": []
        }

    return result_cleaner

async def search_foods(query: str, page_size: int=0, page_number: int=SEARCH_FOOD_ITEM_PER_PAGE):
    """
        "fdcnal/usda-fooddata-central-agent": {
            "search_foods": {}
        }
        Doc: https://deepnlp.org/doc/onekey_gateway
    """
    unique_id = "fdcnal/usda-fooddata-central-agent"
    api_id = "search_foods"
    payload = {
        "query": query,
        "pageSize": page_size,
        "pageNumber": page_number
    }
    result = await call_onekey_gateway_api(unique_id, api_id, payload)
    return search_food_result_content_wrapper(result)

def clean_food_summarization_report(result: Dict):
    """
        class: div_food_coach_report

    """
    if result is None or len(result) == 0:
        return result
    try:
        content = result.get(KEY_CONTENT, "")
        ### html
        content_clean = content.replace("\n", " ")
        ## output
        result_clean = {}
        result_clean[KEY_CONTENT] =  f'<div class="div_food_coach_report">{content_clean}</div>'
        return result_clean
    except Exception as e:
        print (f"clean_food_summarization_report failed with error: {e}")
        return result

async def summarize_nutrition_calories(messages: List[Any]):
    """
        Output a list of text indicating the calories and nutritions
        INFO: summarize_nutrition_calories Context Input Token Length: 3867109
        INFO: summarize_nutrition_calories Context Input Token Length After chunk: 2000000

        API Return Failed with Status (Status Code: 400): {"error":{"message":"<400> InternalError.Algo.InvalidParameter: Range of input length should be [1, 258048]","type":"invalid_request_error","param":null,"code":"invalid_parameter_error"},"id":"chatcmpl-c116c32f-f8c6-9b49-82d0-070fc90adf1c","request_id":"c116c32f-f8c6-9b49-82d0-070fc90adf1c"}
        API Return Failed with Status (Status Code: 400): {"error":{"message":"<400> InternalError.Algo.InvalidParameter: Range of input length should be [1, 258048]","type":"invalid_request_error","param":null,"code":"invalid_parameter_error"},"id":"chatcmpl-c116c32f-f8c6-9b49-82d0-070fc90adf1c","request_id":"c116c32f-f8c6-9b49-82d0-070fc90adf1c"}
    """
    result = {}
    try:
        prompt = json.dumps(messages)
        print (f"INFO: summarize_nutrition_calories Context Input Token Length: {len(prompt)}")

        ## after chunking
        prompt_chunk = prompt[0:MAX_INPUT_TOKEN_LENGTH] if len(prompt) > MAX_INPUT_TOKEN_LENGTH else prompt
        print (f"INFO: summarize_nutrition_calories Context Input Token Length After chunk: {len(prompt_chunk)}")

        ## output json, key: content
        result_raw = llm_qwen_messages_chat(ANALYZE_FOOD_SYSTEM_PROMPT, prompt_chunk, QWEN_MODEL)
        result_clean = clean_food_summarization_report(result_raw)
        return result_clean
    except Exception as e:
        print (f"summarize_nutrition_calories failed with error: {e}")
        result[KEY_CONTENT] = f'<div class="div_food_coach_report">{DEFAULT_FOOD_ANALYZING_RESULT}</div>'
        return result

async def search_foods_batch(query_list: List[str], page_size: int=0, page_number: int=SEARCH_FOOD_ITEM_PER_PAGE) -> Dict[str, Any]:
    """
    Batch search foods from USDA US Department of Agriculture API,
    Args:
        query_list: List[str]
        page_size: int=0
        page_number: int=3
    Returns:
        dict: {query: result_dict}
    """
    try:
        max_query_batch_size = 10
        print (f"INFO: search_foods_batch Input Query List Size Before: {len(query_list)}")
        if len(query_list) > max_query_batch_size:
            query_list = query_list[0:max_query_batch_size]
        print (f"INFO: search_foods_batch Input Query List Siz After: {len(query_list)}")
        tasks = [search_foods(q, page_size, page_number) for q in query_list]
        print (f"INFO: search_foods_batch Task List Size: {len(tasks)}")
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: Dict[str, Any] = {}

        for query, result in zip(query_list, results):
            if isinstance(result, Exception):
                output[query] = {
                    "error": str(result)
                }
            else:
                output[query] = result

        return output

    except Exception as e:
        print(f"search_foods_batch failed with error: {e}")
        return {}

def python_type_to_json_type(py_type):
    """Map Python types to JSON schema types."""
    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    return mapping.get(py_type, "string")

def function_to_tools_mapper(funcs):
    tools = []

    for func in funcs:
        sig = inspect.signature(func)
        type_hints = get_type_hints(func)

        properties = {}
        required = []

        for name, param in sig.parameters.items():
            if name == "self":
                continue

            param_type = type_hints.get(name, str)
            json_type = python_type_to_json_type(param_type)

            properties[name] = {
                "type": json_type,
                "description": f"{name} parameter"
            }

            if param.default == inspect.Parameter.empty:
                required.append(name)

        tool = {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": func.__doc__ or "",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }

        tools.append(tool)

    return tools

async def ocr_extract_text_from_image(images: List[str], model: str = "gemini-3-flash-preview"):
    unique_id = "gemini-nano-banana/gemini-nano-banana"
    api_id = "ocr_extract_text_from_image"
    payload = {
        "images": images,
        "model": model
    }
    return await call_onekey_gateway_api(unique_id, api_id, payload)

async def list_items_from_image(images: List[str], model: str = "gemini-3-flash-preview"):
    unique_id = "gemini-nano-banana/gemini-nano-banana"
    api_id = "list_items_from_image"
    payload = {
        "images": images,
        "model": model
    }
    return await call_onekey_gateway_api(unique_id, api_id, payload)

"""
        ocr_extract_text_from_image
        list_items_from_image, output:  ```json\\n[\\n  {\\"box_2d\\": [278, 417, 355, 582], \\"text_content\\": \\"Coca-Cola\\"}\\n]\\n```
        timeout:
            1. Image Processing Gemini API: LONG_TIMEOUT
"""

def preprocess_images_url_data(images: Optional[List[str]] = None):
    """
    Process images for AI APIs.

    Rules:
    - Public URLs -> keep original URL
    - localhost / 127.0.0.1 / 0.0.0.0 URLs
      -> convert to base64 data URL
    - existing base64 data URLs
      -> keep as-is
    - local file path
      -> convert to base64 data URL

    Returns:
        List[str]
    """

    if images is None:
        return []

    try:
        images_processed = []
        for image in images:
            if not image:
                continue
            image = str(image).strip()
            #
            # HTTP / HTTPS URL
            #
            if image.startswith("http://") or image.startswith("https://"):
                #
                # localhost image
                #
                if (
                    "127.0.0.1" in image
                    or "0.0.0.0" in image
                    or "localhost" in image
                ):

                    try:
                        print(f"DEBUG: Reading localhost image via requests {image}")

                        response = requests.get(image, timeout=20)

                        if response.status_code != 200:
                            print(f"WARNING: Failed to fetch image {image}")
                            continue

                        image_bytes = response.content

                        #
                        # Detect mime type
                        #
                        mime_type = response.headers.get(
                            "Content-Type",
                            "image/png"
                        )

                        base64_data = base64.b64encode(image_bytes).decode("utf-8")
                        data_url = f"data:{mime_type};base64,{base64_data}"
                        images_processed.append(data_url)

                    except Exception as e:
                        print(f"DEBUG: Failed localhost image processing {e}")

                #
                # public URL
                #
                else:
                    images_processed.append(image)

            #
            # already base64
            #
            elif image.startswith("data:image"):
                images_processed.append(image)

            #
            # local filesystem path
            #
            elif os.path.exists(image):

                try:
                    ext = os.path.splitext(image)[1].lower()

                    mime_type = "image/png"

                    if ext in [".jpg", ".jpeg"]:
                        mime_type = "image/jpeg"
                    elif ext == ".webp":
                        mime_type = "image/webp"

                    with open(image, "rb") as f:
                        image_bytes = f.read()

                    base64_data = base64.b64encode(image_bytes).decode("utf-8")

                    data_url = f"data:{mime_type};base64,{base64_data}"

                    images_processed.append(data_url)

                except Exception as e:
                    print(f"DEBUG: Failed local image processing {e}")

            else:
                print(f"DEBUG: Unsupported image input {image}")

        return images_processed

    except Exception as e:
        print(f"DEBUG: Failed preprocess_images_url_data {e}")
        return []


async def analyze_foods_nutrition_workflow(prompt: str, images: Optional[List[str]] = None) -> Dict:
    """
        prompt: str, e.g.  Big Mac, Analyze
        images: List[str], e.g. list of image url
        result:
            result[KEY_CONTENT]: Will be displayed on the final Agent Run Result, output in html or plain text,
            no markdown
    """
    result = {}
    if not prompt and not images:
        print("DEBUG: analyze_foods_nutrition_workflow input is empty, return default...")
        result[KEY_CONTENT] = fill_default_foods_nutrition(prompt)
        return result
    try:
        ## History
        messages = []
        ## 1. Parse Food/Receipt From Calories
        ### Text List[sstr]
        image_results = {}
        if images is not None and len(images) > 0:
            print (f"DEBUG: Starting to Analyze Images: {images}")
            ## If Images is URL with local access, which is not available to remote server,
            ## send Base64 image data directly
            images = preprocess_images_url_data(images)

            # Map api_id -> coroutine
            task_map = {
                "ocr_extract_text_from_image": ocr_extract_text_from_image(images),
                "list_items_from_image": list_items_from_image(images),
            }
            try:
                # Create tasks
                tasks = {
                    api_id: asyncio.create_task(coro)
                    for api_id, coro in task_map.items()
                }

                # Await all with timeout
                parallel_results = await asyncio.wait_for(
                    asyncio.gather(*tasks.values(), return_exceptions=True),
                    timeout=IMAGE_LONG_TIMEOUT
                )

                # Map results back to api_id
                for api_id, api_result in zip(tasks.keys(), parallel_results):
                    if isinstance(api_result, Exception):
                        image_results[api_id] = {"error": str(api_result)}
                    else:
                        image_results[api_id] = api_result

            except asyncio.TimeoutError:
                print("Image analysis timed out (30s)")
                # mark all unfinished as timeout
                for api_id in task_map.keys():
                    image_results[api_id] = {"error": "timeout"}

            except Exception as e:
                print(f"Image analysis failed: {e}")
                for api_id in task_map.keys():
                    image_results[api_id] = {"error": str(e)}
            ## image optional, can be none
            messages.extend([{"role": "assistant", "content": json.dumps(image_results)}])

        ### Step 2. Parse the required Nutrition tool: search_foods_batch
        if prompt:
            messages.extend([{"role": "user", "content": prompt}])
        else:
            messages.extend([{"role": "user", "content": DEFAULT_FOOD_ANALYZING_CHECKIN_PROMPT}])

        tools = function_to_tools_mapper([search_foods_batch])
        ### Function Call
        if LOG_ENABLE:
            print(f"DEBUG: Function Call Input messages {messages}|tools {tools}|QWEN_MODEL {QWEN_MODEL}")
        response = call_qwen_tool_calls_model_selection(messages, tools, QWEN_MODEL)
        tool_call, completion, reasoningContent = post_process_function_call_qwen_common(response)
        if LOG_ENABLE:
            print(f"DEBUG: Function Call Output tool_call {tool_call}|completion {completion}|reasoningContent {reasoningContent}")

        function = tool_call["function"] if "function" in tool_call else {}
        function_arguments_str = function["arguments"] if "arguments" in function else ""
        function_name = function["name"] if "name" in function else ""

        function_arguments = {}
        if function_arguments_str:
            try:
                function_arguments = json.loads(function_arguments_str)
            except Exception as e:
                print (f"Error: Failed to parse function arguments: {function_arguments_str}")
                function_arguments = {}

        ## Run Function
        if function_arguments and len(function_arguments) > 0:
            if LOG_ENABLE:
                print (f"DEBUG: search_foods_batch batch function_arguments {function_arguments}| size length {len(function_arguments)}")

            ### 2. Post Process Variable Call Search Food, [receipt, item_name]
            query_list_raw = function_arguments.get(KEY_QUERY_LIST) if isinstance(function_arguments, dict) else []
            query_list = []
            if isinstance(query_list_raw, str):
                try:
                    query_list = json.loads(query_list_raw)
                except Exception as e:
                    print (f"Processing unquoted query list: {query_list_raw} with error: {e}")
            elif isinstance(query_list_raw, list):
                query_list = query_list_raw
            else:
                query_list = []

            if LOG_ENABLE:
                print (f"DEBUG: search_foods_batch batch query_list {query_list}| size length {len(query_list)}")
            result_dict = await search_foods_batch(query_list, page_size=0, page_number=SEARCH_FOOD_ITEM_PER_PAGE)
            if LOG_ENABLE:
                print (f"DEBUG: search_foods_batch batch result_dict length {len(result_dict)}")
            messages.append({"role": "assistant", "content": json.dumps(result_dict)})
        else:
            ### 3. Don't run function call
            print (f"DEBUG: Assistants Return empty function call, a lisf ot completion")
            if completion:
                messages.append({"role": "assistant", "content": completion})

        ### 3. summarize_nutrition_calories
        summary_result_dict = await summarize_nutrition_calories(messages)

        if LOG_ENABLE:
            print (f"DEBUG: analyze_foods_nutrition_workflow final summary_result_dict {summary_result_dict}")
        content = summary_result_dict.get(KEY_CONTENT, "")
        ### Fill Default
        if not content:
            print (f"Returning Empty Result, Filling Default Value")
            content = fill_default_foods_nutrition(prompt)

        result[KEY_CONTENT] = content
        return result

    except Exception as e:
        traceback.print_exc()
        print (f"DEBUG: analyze_foods_nutrition failed with error {e}")
        result = {KEY_CONTENT: DEFAULT_FOOD_ANALYZING_RESULT}
        return result

def fill_default_foods_nutrition(prompt: str) -> str:
    """
        prompt: Milkshake, Hamburger
    """
    return DEFAULT_FOOD_ANALYZING_RESULT


async def base_search(query: str, num: int = 10, start: int = 0) -> dict:
    """
        OneKey Gateway API Search
    """
    unique_id = "google-search/google-search"
    api_id = "google_search"
    payload = {
        "query": query,
        "num": num,
        "start": start
    }
    return await call_onekey_gateway_api(unique_id, api_id, payload)


async def tavily_search(query: str, max_results: int = 10) -> dict:
    """
        OneKey Gateway API Search
    """
    unique_id = "tavily-remote-mcp/tavily-remote-mcp"
    api_id = "tavily_search"
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced"
    }
    return await call_onekey_gateway_api(unique_id, api_id, payload)

def _parse_listish(value: str) -> List[str]:
    """
    Accepts JSON list string (e.g. '["a","b"]') or comma/newline-separated text.
    """
    value = (value or "").strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            data = json.loads(value)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    # Fallback: comma/newline
    parts = []
    for chunk in value.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


async def track_competitor_launches_producthunt(
    user_id: str,
    keywords: str,
    feed_urls: str = "",
    days_back: int = 7,
    max_entries_per_feed: int = 50,
) -> dict:
    """
    Track competitor launches on Product Hunt via RSS/Atom feeds with per-user dedupe.

    Args:
      user_id: the CoachOwl user id for dedupe storage
      keywords: JSON list string or comma-separated keywords (e.g. 'replit, cursor, lovable')
      feed_urls: optional JSON list string or comma-separated feed urls; defaults to Product Hunt main feed
      days_back: only consider entries published in the last N days (best-effort if feed omits dates)
      max_entries_per_feed: max items parsed per feed URL
    """
    kw_list = _parse_listish(keywords)
    url_list = _parse_listish(feed_urls) or ["https://www.producthunt.com/feed"]

    conn = connect()
    try:
        ensure_user(conn, user_id)
        entries = await fetch_producthunt_entries(url_list, max_entries_per_feed=max_entries_per_feed)
        matches = filter_entries_by_keywords(entries, keywords=kw_list, days_back=days_back)
        new_matches = mark_and_filter_unseen(conn, user_id=user_id, matches=matches)
        return {
            "success": True,
            "feed_urls": url_list,
            "keywords": kw_list,
            "days_back": days_back,
            "total_matches": len(matches),
            "new_matches": new_matches,
        }
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}
    finally:
        conn.close()

### Tools Definition, input: funcs
default_tools = [base_search, send_email_with_attachments]
fitness_available_tools = [analyze_foods_nutrition_workflow]
career_available_tools = [track_competitor_launches_producthunt]


##
coach_available_tools = {
    USER_INTENT_DEFAULT: default_tools,
    USER_INTENT_FITNESS: fitness_available_tools,
    USER_INTENT_CAREER: career_available_tools
}

def assemble_tool_call_result_message(tool_id: str,
                                      tool_name: str,
                                      tool_arguments: Dict,
                                      tool_results: Dict) -> List[Any]:
    """
    """
    message_tool_result = []
    try:
        tool_calls = []
        tool_calls.append({
            "id": tool_id,
            "type": "tool_use",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(tool_arguments)
            }
        })

        tool_results_text = json.dumps(tool_results)
        ## add tool result to chat history
        message_tool_result.extend([
            {
                "role": "assistant",
                "content": "",
                "tool_calls": tool_calls
            },
            {
                "role": "tool",
                "tool_call_id": tool_id,
                "name": tool_name,
                "content": tool_results_text
            }
        ])
    except Exception as e:
        print (f"DEBUG: assemble_tool_call_result_message failed with error {e}")
    return message_tool_result

async def agent_workflow_entry(prompt: str, category: str, **kwargs):
    """

        Optimization:
            batch logs into ONE structured payload

        tool_call:
            DEBUG: agent_workflow_entry tool_call {'function': {'arguments': '{"query": "Product Hunt AI Agents release last 7 days", "num": 10, "start": 0}', 'name': 'base_search'}, 'id': 'call_719ca121f1594cd4ab9f8c42', 'index': 0, 'type': 'function'}| completion  | reasoningContent

        prompt: str, e.g. monitor food calories,
        category: fitness, career

        Return:
            result: Dict, Key: KEY_CONTENT, type: html
    """
    result = {}
    if not prompt:
        print (f"DEBUG: agent_workflow_entry prompt empty|{prompt}")
        result[KEY_CONTENT] = ""
        return result
    try:
        ### get available_tools =
        default_tools = coach_available_tools.get(USER_INTENT_DEFAULT, [])
        category_tools = coach_available_tools.get(category, [])
        available_functions = default_tools + category_tools
        if LOG_ENABLE:
            print (f"DEBUG: /agent_workflow_entry default_tools size {len(default_tools)} | category_tools size {len(category_tools)} | available_functions {len(available_functions)}")
        # if not available_functions or len(available_functions) == 0:
        #     available_functions = coach_available_tools.get(USER_INTENT_DEFAULT)
        available_tools = function_to_tools_mapper(available_functions)
        if LOG_ENABLE:
            print (f"DEBUG: agent_workflow_entry input size {len(available_tools)}")

        available_functions_dict = {}
        for func in available_functions:
            available_functions_dict[func.__name__] = func

        ### Function Call
        messages = [{"role":"user", "content": prompt}]

        ####
        iterations = 0
        MAX_ITERATION_IN_EACH_LOOP = 3
        workflow_running_results = {}
        # for _ in range(max_agent_loop_run):
        while (iterations < MAX_ITERATION_IN_EACH_LOOP):
            iterations += 1
            if LOG_ENABLE:
                 print (f"### tooluse_loop_autorun Agent Loop {iterations}, input messages size {len(messages)} and available tools size {len(available_tools)}...")

            response = call_qwen_tool_calls_model_selection(messages, available_tools, QWEN_MODEL)
            tool_call, completion, reasoningContent = post_process_function_call_qwen_common(response)

            if LOG_ENABLE:
                print (f"DEBUG: agent_workflow_entry tool_call {tool_call}| completion {completion} | reasoningContent {reasoningContent}")

            if not tool_call or len(tool_call) == 0:
                if completion:
                    messages.append({
                        "role": "assistant",
                        "content": completion
                    })
                break

            tool_call_id = tool_call.get("id", "")
            function = tool_call["function"] if "function" in tool_call else {}
            function_arguments_raw = function["arguments"] if "arguments" in function else {}
            function_name = function["name"] if "name" in function else ""

            function_arguments = {}
            if function_arguments_raw:
                if isinstance(function_arguments_raw, dict):
                    function_arguments = function_arguments_raw
                elif isinstance(function_arguments_raw, str):
                    try:
                        function_arguments = json.loads(function_arguments_raw)
                    except Exception as e:
                        print (f"Error: Failed to parse function arguments as json: {function_arguments_raw} error {e}")
                else:
                    print (f"DEBUG: function_arguments_raw type not supported: {type(function_arguments_raw)} ")

            ## Adding Break Conditon, Invalid Function Name or arguments
            if not function_name:
                break

            if not is_valid_function_call_arguments(function_arguments):
                break

            ### 2. Call Search Food, [receipt, item_name]
            func = available_functions_dict.get(function_name, None)
            tool_call_result = {}
            if func:
                try:
                    tool_call_result = await func(**function_arguments)
                except Exception as e:
                    print (f"DEBUG: Failed with error {e}")
            else:
                print (f"DEBUG: function_name failed with error {function_name}")
            print (f"DEBUG: function_name {function_name}| function_arguments {function_arguments}| tool_call_result {str(tool_call_result)[:200]} ")
            # result[KEY_CONTENT] = tool_call_result.get(KEY_CONTENT, DEFAULT_AGENT_BACKGROUND_TASK_CONTENT)
            message_tool_result_list = assemble_tool_call_result_message(tool_call_id, function_name, function_arguments, tool_call_result)
            messages.extend(message_tool_result_list)

        ## Append Tool Call Result, prompt co-erse the Dict results...
        final_results = summarize_final_agent_results(messages)
        ## Dict with key: content
        result = final_results

    except Exception as e:
        print (f"DEBUG: agent_workflow_entry Failed with error {e}")
        result[KEY_CONTENT] = ""

    if LOG_ENABLE:
        print (f"DEBUG: agent_workflow_entry returning final result {result}")
    return result


def summarize_final_agent_results(messages: List[Any]) -> Dict:
    """
        Generate a concise Summary Report of Agent Running Results,
        For Example, if the agent runs a deep research of productHunt releases and also finished sending the Emails,
        Just summarize what's completed and shows in summary message

        Return: Dict, prompt co-erse
            key: {"content": ""}
    """
    result = {}
    try:
        response = call_qwen_messages_model_selection(prompt_generation_system_prompt, messages, QWEN_MODEL)
        response_text = ""
        if response is None:
            raise RuntimeError("No response from DashScope")
        if response.status_code == 200:
            result = response.json()
            response_text = result["choices"][0]["message"]["content"]
            print("Qwen Response:", response_text)
        else:
            print(f"API Return Failed with Status (Status Code: {response.status_code}): {response.text}")

        ### special handling post llm call json results
        result = _parse_json_response(response_text)
        return result

    except Exception as e:
        print (f"DEBUG: summarize_final_agent_results failed with error {e}")
        return result

async def test_google_search():
    """
    """
    query = "Product Hunt AI Agents releases last 7 days"
    result = await base_search(query, num=10, start=0)
    print (f"DEBUG: Google Search result {result}")

async def main():
    """
        call_onekey_gateway_api success: {'success': False, 'message': "Internal API error Server error '503 Service Unavailable' for url 'https://api.nal.usda.gov/fdc/v1/foods/search'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503"}
    """
    ### Search Food
    # query = "large steak"
    # search_result = await search_foods(query, page_size = 0, page_number = 5)
    # print (f"DEBUG: Search Result for query list: {query} search_result {search_result} ")
    """
        empty: {'success': True, 'totalHits': 0, 'currentPage': 1, 'totalPages': 0, 'pageList': [], 'foodSearchCriteria': {'query': 'large_steak', 'generalSearchInput': 'large_steak', 'pageNumber': 5, 'numberOfResultsPerPage': 50, 'pageSize': 50, 'requireAllWords': False}, 'foods': [], 'aggregations': {'dataType': {}, 'nutrients': {}}} 
        4 seconds 
    """

    """
        ocr results: 
        DEBUG: Function Call Input messages 
        [{'role': 'assistant', 'content': '{"ocr_extract_text_from_image": {"success": true, "data": [{"success": true, "raw_text": "Based on the images provided, here is the transcription of all visible text, maintaining the layout as closely as possible.\\n\\n**Image 1:**\\nCoca-Cola\\nM\\n\\n**Image 2:**\\nLucca Steakhouse\\n     Methqal Al Fayez St\\n        Building #27\\n         064633355\\n      VAT #:129246034\\n           TABLE\\n      13-08-2017 22:39\\nCHECK # 119374    TABLE # 222\\n-------------------------------\\n1 BIG WATER                      2.250\\n1 MEATBALLS & CHEESE             5.750\\n1 LUCCA FAMOUS SALAD             6.500\\n1 LUCCA GOURMET BURG             9.750\\n1 LOKUM BERGER                   9.750\\n1 RIB - EYE 350 GM              26.000\\n1 KAFES                         45.000\\n1 BREAD BASKET                   0.000\\n1 DONATION                       1.000\\n  SUB-TOTAL:                   106.000\\n  SERVICE:                      10.500\\n  TAX 16 %                      18.480\\n-------------------------------\\nTOTAL JD:  134.980\\n\\n    Clerk #22 Customer # 3", "model": "gemini-3-flash-preview"}], "error": null}, "list_items_from_image": {"success": true, "data": [], "error": null}}'}, {'role': 'user', 'content': 'Today I have two meals together with 3 people'}]|tools [{'type': 'function', 'function': {'name': 'search_foods_batch', 'description': '\n    Batch search foods concurrently.\n\n    Returns:\n        dict: {query: result_dict}\n    ', 'parameters': {'type': 'object', 'properties': {'query_list': {'type': 'string', 'description': 'query_list parameter'}, 'page_size': {'type': 'integer', 'description': 'page_size parameter'}, 'page_number': {'type': 'integer', 'description': 'page_number parameter'}}, 'required': ['query_list']}}}]|QWEN_MODEL qwen3-max-2025-09-23
        
        ## List Item Result 
        DEBUG: Function Call Input messages [{'role': 'assistant', 'content': '{"ocr_extract_text_from_image": {"success": true, "data": [], "error": null}, "list_items_from_image": {"success": true, "data": [{"success": true, "raw_text": "```json\\n[\\n  {\\"box_2d\\": [278, 417, 355, 582], \\"text_content\\": \\"Coca-Cola\\"}\\n]\\n```", "model": "gemini-3-flash-preview"}], "error": null}}'}, {'role': 'user', 'content': 'Today I have two meals together with 3 people'}]|tools [{'type': 'function', 'function': {'name': 'search_foods_batch', 'description': '\n    Batch search foods concurrently.\n        query_list: 最多 5个\n    Returns:\n        dict: {query: result_dict}\n    ', 'parameters': {'type': 'object', 'properties': {'query_list': {'type': 'string', 'description': 'query_list parameter'}, 'page_size': {'type': 'integer', 'description': 'page_size parameter'}, 'page_number': {'type': 'integer', 'description': 'page_number parameter'}}, 'required': ['query_list']}}}]|QWEN_MODEL qwen3-max-2025-09-23
    """
    ### Test Google Search
    await test_google_search()

if __name__ == "__main__":
    # main()
    asyncio.run(main())
