from __future__ import annotations

import base64
import json
import re
import uuid
from typing import Any, Dict, List, Tuple
from pathlib import Path
import requests
import logging

import sqlite3
from typing import Any, Dict, List, Optional, Tuple
from .db import ensure_user, fetchall_dicts
from datetime import datetime
from typing import List, Dict, Any
import httpx
from zoneinfo import ZoneInfo

from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException, Depends
from . import global_variables as gv

from .constants import (
    QWEN_API_KEY,
    LOG_ENABLE, LONG_TIMEOUT, DEFAULT_USER_TIMEZONE,
    MCP_ONEKEY_AUTHNTIFICATE_URL, TIMEOUT_GENERAL,
    MCP_ONEKEY_CHECK_BALANCE_ENDPOINT,
    MCP_ONEKEY_RECORD_USAGE_ENDPOINT,
    KEY_STATUS, KEY_MESSAGE, KEY_CREDENTIAL,
    LOGIN_AUTHENTICATE_URL
)

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None

from datetime import datetime
import pytz

import time

### ----- User Local Time -----
USER_TZ_CACHE = {}
CACHE_TTL = 3600  # 1 hour

def get_server_timezone() -> str:
    """
    Returns server timezone as valid IANA timezone.
    """

    tz_abbr = time.tzname[0]

    TZ_MAP = {
        # US
        "PST": "America/Los_Angeles",
        "PDT": "America/Los_Angeles",
        "CDT": "America/Chicago",
        "EST": "America/New_York",
        "EDT": "America/New_York",
        "MST": "America/Denver",
        "MDT": "America/Denver",

        # China
        "CST": "Asia/Shanghai",   # China Standard Time (IMPORTANT: NOT "CST")

        # Europe (you must not use CST here, use real zones)
        "CET": "Europe/Paris",       # Central European Time
        "CEST": "Europe/Paris",      # DST version
        "GMT": "Europe/London",
        "BST": "Europe/London",
    }

    return TZ_MAP.get(tz_abbr, "America/Los_Angeles")

def get_user_timezone(conn, user_id: str) -> str:
    """
    Returns user timezone
    "Asia/Shanghai"
    "America/Los_Angeles"
    "America/New_York"
    "Europe/London"
    """
    try:
        now = time.time()

        # 1. check cache
        if user_id in USER_TZ_CACHE:
            tz, expires_at = USER_TZ_CACHE[user_id]
            if now < expires_at:
                return tz

        # 2. fetch from DB
        row = conn.execute(
            "SELECT timezone FROM user_profiles WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        tz = row[0] if row and row[0] else None
        # 3. fallback chain
        if not tz:
            tz = get_server_timezone() or DEFAULT_USER_TIMEZONE

        # 3. store in cache
        USER_TZ_CACHE[user_id] = (tz, now + CACHE_TTL)

        return tz

    except Exception as e:
        print (f"DEBUG: get_user_timezone failed with error: {e}")
        return get_server_timezone() or DEFAULT_USER_TIMEZONE

def get_user_day(conn, user_id: str) -> str:
    try:
        tz_name = get_user_timezone(conn, user_id) or "UTC"
        tz = pytz.timezone(tz_name)
        if LOG_ENABLE:
            print (f"DEBUG: tz_name {tz_name}|tz {tz}")
    except Exception as e:
        print(f"get_user_day timezone resolve failed: {e}")
        tz = pytz.utc

    return datetime.now(tz).strftime("%Y-%m-%d")

def get_user_datetime(conn, user_id: str):
    try:
        tz_name = get_user_timezone(conn, user_id) or "UTC"
        tz = pytz.timezone(tz_name)
    except Exception as e:
        print(f"get_user_datetime timezone failed: {e}")
        tz = pytz.utc

    return datetime.now(tz).strftime("%H:%M:%S")

def get_user_profile(conn: sqlite3.Connection, user_id: str) -> Dict[str, Any]:
    """
    Get user profile including timezone + core settings.
    Ensures user exists and returns a safe default profile if missing fields.
    """

    # ensure_user(conn, user_id)

    cur = conn.execute(
        """
        SELECT u.id, u.created_at,
               p.age, p.gender, p.timezone, p.updated_at
        FROM users u
        LEFT JOIN user_profiles p ON u.id = p.user_id
        WHERE u.id = ?
        """,
        (user_id,)
    )

    row = cur.fetchone()

    if not row:
        # fallback safety (should not happen if ensure_user works correctly)
        return {
            "user_id": user_id,
            "timezone": "America/Los_Angeles"
        }

    user = {
        "user_id": row[0],
        "created_at": row[1],
        "age": row[2],
        "gender": row[3],
        "timezone": row[4] or "America/Los_Angeles",
        "updated_at": row[5],
    }

    return user


def get_new_message_id() -> str:
    return str(uuid.uuid4())


def assembly_message(msg_type: str, fmt: str, content: Any, **kwargs: Any) -> Dict[str, Any]:
    """
    Minimal message format used by Agent Router UI.

    {
      "type": "assistant",
      "format": "html|text",
      "content": "...",
      "content_type": "text/markdown|text/html|image/*",
      "section": "answer|tool|think|system_msg",
      "template": "...",
      "message_id": "uuid"
    }
    """
    output: Dict[str, Any] = {"type": msg_type, "format": fmt, "content": content}
    for key in ["section", "message_id", "content_type", "template", "done"]:
        if key in kwargs:
            output[key] = kwargs[key]
    return output


def generate_session_id() -> str:
    return f"TEMP_{str(uuid.uuid4())[:8]}"

def generate_user_id() -> str:
    return f"TEMP_{str(uuid.uuid4())[:4]}"

def is_url(s: str | None) -> bool:
    if not s:
        return False
    return s.startswith("http://") or s.startswith("https://")


def _extract_img_urls_from_html(html: str) -> List[str]:
    if not html or "<img" not in html:
        return []
    try:
        if BeautifulSoup is not None:
            soup = BeautifulSoup(html, "html.parser")
            urls: List[str] = []
            for img in soup.find_all("img"):
                data_src = img.get("data-src")
                src = img.get("src")
                if isinstance(data_src, str) and data_src:
                    urls.append(data_src)
                elif isinstance(src, str) and src:
                    urls.append(src)
            return urls

        # regex fallback
        urls: List[str] = []
        for m in re.finditer(r"<img[^>]+>", html, flags=re.IGNORECASE):
            tag = m.group(0)
            m2 = re.search(r'data-src\\s*=\\s*["\\\']([^"\\\']+)["\\\']', tag, flags=re.IGNORECASE)
            if m2:
                urls.append(m2.group(1))
                continue
            m3 = re.search(r'src\\s*=\\s*["\\\']([^"\\\']+)["\\\']', tag, flags=re.IGNORECASE)
            if m3:
                urls.append(m3.group(1))
        return urls
    except Exception:
        return []


def get_user_last_input(messages: List[Dict[str, Any]] | None) -> Tuple[str, Dict[str, Any]]:
    """
    Returns (last_user_text, attachments_dict).
    attachments_dict currently supports {"img": [url_or_data_uri,...]}.
    """
    if not messages:
        return "", {}

    last_user_text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_text = str(msg.get("content", "") or "")
            break

    img_urls: List[str] = []
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = str(msg.get("content", "") or "")
        urls = _extract_img_urls_from_html(content)
        if urls:
            img_urls = urls
            break

    # If the last "text" is actually an HTML attachment wrapper, prefer an empty prompt.
    if "<img" in (last_user_text or "") and len(re.sub(r"<[^>]+>", "", last_user_text).strip()) == 0:
        last_user_text = ""

    return last_user_text, {"img": img_urls} if img_urls else {}


def safe_json_loads(text: str) -> Any:
    if not text:
        return {}
    # remove code fences
    text = re.sub(r"```json|```", "", text, flags=re.IGNORECASE).strip()
    # extract first json object/array
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if m:
        text = m.group(1)
    # remove trailing commas
    text = re.sub(r",\s*([\]}])", r"\1", text)
    try:
        return json.loads(text)
    except Exception:
        return {}


def data_uri_to_b64(data_uri: str) -> str:
    """
    Accepts `data:image/png;base64,...` and returns base64 payload (no header).
    """
    if not data_uri.startswith("data:"):
        return ""
    try:
        header, payload = data_uri.split(",", 1)
        if ";base64" not in header:
            return ""
        # quick validation
        base64.b64decode(payload[:64] + "==", validate=False)
        return payload
    except Exception:
        return ""

def is_valid_function_call_arguments(function_arguments: Dict) -> bool:
    """
        Check if input function_arguments is valid, and not empty valid
    """
    if not function_arguments:
        return False
    if not isinstance(function_arguments, Dict):
        return False
    if len(function_arguments) == 0:
        return False
    return True

def is_valid_output(content: str) -> bool:
    """
        Check if content is valid for output,
    """
    if not content:
        return False
    if content.strip() == "":
        return False
    return True

def is_valid_result_output(content: str, images: List[Any]) -> bool:
    """
        Check if content is valid for output,
    """
    if (not content and content.strip() == "") and len(images) == 0:
        return False
    return True


def is_valid_html(content: str) -> bool:
    """
    Returns True if content looks like valid HTML, False otherwise.
    Lightweight heuristic (fast, no external deps).
    """
    if not content or not isinstance(content, str):
        return False
    try:

        content = content.strip()

        # Must contain at least one HTML tag
        if not re.search(r"<[^>]+>", content):
            return False

        # Check for common root tags
        if re.search(r"<html|<body|<div|<span|<p|<table", content, re.IGNORECASE):
            return True

        # Check for balanced tags (very basic)
        open_tags = re.findall(r"<([a-zA-Z]+)(\s|>)", content)
        close_tags = re.findall(r"</([a-zA-Z]+)>", content)

        if not open_tags or not close_tags:
            return False

        open_tag_names = [t[0].lower() for t in open_tags]
        close_tag_names = [t.lower() for t in close_tags]

        # At least some overlap
        common = set(open_tag_names) & set(close_tag_names)

        return len(common) > 0

    except Exception as e:
        print (f"Failed to check is_valid_html with error {e}")
        return False

### Utils for LLM Calling
def llm_qwen_messages_chat(system_prompt: str, prompt: str,  model: str) -> Any:
    """
        Reference doc: https://help.aliyun.com/zh/model-studio/use-qwen-by-calling-api#b30677f6e9437
        Input:
            messages: List[Dict]
    """
    try:
        messages = []
        messages.append({"role": "user", "content": prompt})
        response = call_qwen_messages_model_selection(system_prompt, messages, model)
        response_text = ""
        if response is None:
            raise RuntimeError("No response from DashScope")
        if response.status_code == 200:
            result = response.json()
            response_text = result["choices"][0]["message"]["content"]
            print("Qwen Response:", response_text)
        else:
            print(f"API Return Failed with Status (Status Code: {response.status_code}): {response.text}")
        try:
            response_text = response_text
            return _parse_json_response(response_text)
        except (KeyError, IndexError):
            print (f"DEBUG:Failed to process intent error")
            return {}
    except Exception as e:
        print(f"ERROR: Failed to process error {e}")
        return {}

def call_qwen_messages_model_selection(sys_prompt: str, messages: List, model: str):
    """
        Reference doc: https://help.aliyun.com/zh/model-studio/use-qwen-by-calling-api#b30677f6e9437
        Input:
            messages: List[Dict]
    """
    try:
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        api_key = QWEN_API_KEY
        if api_key is None or api_key == "":
            raise ValueError(
                "qwen_general_api.py call_qwen_max_user_prompt api_key not found, please check .env file key QWEN_API_KEY")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        messages.append({"role": "system", "content": sys_prompt})
        if LOG_ENABLE:
            print(f"Qwen Request headers {headers} | messages: {messages}")

        data = {
            "model": model,
            "messages": messages,
        }
        # if model in MODEL_SELECTION_QWEN_THINKING_LIST:
        data["enable_thinking"] = False

        data = json.dumps(data).encode("utf-8")
        response = requests.post(url, headers=headers, data=data, timeout=LONG_TIMEOUT)
        if response.status_code == 200:
            result = response.json()
            if LOG_ENABLE:
                print("Qwen Response:", result["choices"][0]["message"]["content"])
        else:
            print(f"API Return Failed with Status (Status Code: {response.status_code}): {response.text}")
        return response
    except Exception as e:
        logging.error(f"Failed to run {e}")
        return None

### Utils for LLM Calling
def call_qwen_tool_calls_model_selection(messages, tools, model):
    """
        Args:
            messages: list of dict
            tools: list of dict
        return:
            {"choices":[{"message":{"content":"","role":"assistant","tool_calls":[{"index":0,"id":"call_f8d9f219ee034156985f6a","type":"function","function":{"name":"get_current_weather","arguments":"{\"location\": \"上海\"}"}}]},"finish_reason":"tool_calls","index":0,"logprobs":null}],"object":"chat.completion","usage":{"prompt_tokens":266,"completion_tokens":20,"total_tokens":286,"prompt_tokens_details":{"cached_tokens":0}},"created":1750987730,"system_fingerprint":null,"model":"qwen-plus","id":"chatcmpl-3bd1954c-8594-98e1-957b-9fda39ac73fc"}
        doc: https://help.aliyun.com/zh/model-studio/qwen-function-calling
    """
    try:
        api_key = QWEN_API_KEY
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {api_key}",
        }
        # messages.append({"role": "system", "content": prompt_function_call_system_message})
        ## add system prompt
        data = {
                "stream": False,
                "model": model,
                "messages": messages,
                "tools": tools
        }
        if LOG_ENABLE:
            print(f"Qwen Request messages: {messages}| tools {tools}")

        ## close thinking for non streaming service call
        # if model in MODEL_SELECTION_QWEN_THINKING_LIST:
        data["enable_thinking"] = False

        data = json.dumps(data).encode("utf-8")
        response = requests.post(url, headers=headers, data=data, timeout=LONG_TIMEOUT)
        if response.status_code == 200:
            result = response.json()
            if LOG_ENABLE:
                print("Qwen Response:", result["choices"][0]["message"]["content"])
        else:
            print(f"API Return Failed with Status (Status Code: {response.status_code}): {response.text}")
        return response
    except Exception as e:
        print (f"call_qwen_tool_calls_model_selection failed with error {e}")
        return None

def post_process_function_call_qwen_common(response):
    """
        response:
            {
                "id": "call_6fcd208b442c4c12b1b419",
                "function": {
                  "arguments": "{\"location\": \"\u4e0a\u6d77\u5e02\"}",
                  "name": "get_current_weather"
                },
                "type": "function",
                "index": 0
            }
        or direct response generation

        {"choices":[{"message":{"content":"The spreadsheet contains records for **3 employees**.","reasoning_content":"","role":"assistant"},"finish_reason":"stop","index":0,"logprobs":null}],"object":"chat.completion","usage":{"prompt_tokens":4698,"completion_tokens":10,"total_tokens":4708},"created":1756731543,"system_fingerprint":null,"model":"qwen3-32b","id":"chatcmpl-41bb6818-f269-9a24-8546-7abf99e81ed1"}

    """
    if response is None:
        print (f"DEBUG: post_process_function_call_qwen_common input response is None...")
        return {}, "", ""

    tools = {}
    completion = ""
    reasoningContent = ""

    res_json = {}
    try:
        content = response.content
        logging.info(f"post_process_function_call_qwen_base content {content}")
        res_json = json.loads(content)

    except json.decoder.JSONDecodeError:
        print("Not Valid Json Format" + content)
        return {}
    try:
        choice = res_json["choices"][0] if len(res_json["choices"]) > 0 else {}
        finish_reason = choice["finish_reason"] if "finish_reason" in choice else "" # tool_calls or "stop"
        message = choice["message"] if "message" in choice else {}

        completion = message["content"] if "content" in message else ""
        reasoningContent = message["reasoning_content"] if "reasoning_content" in message else ""
        tool_calls = message["tool_calls"] if "tool_calls" in message else []
        tool_call = tool_calls[0] if len(tool_calls) > 0 else {}
        return tool_call, completion, reasoningContent
    except Exception as e:
        logging.error(e)
        return {}, completion, reasoningContent

def function_call_result_common_mapper(tool_call):
    """
        This wrapper is a common mapper to wrap the result of OpenAI/Claude Stype function call results, thinking/no thinking models
        Args:
            tool_call:
                {
                    "id": "call_d6f4ed29ce614390b99a05",
                    "function": {
                        "arguments": "{\"url\": \"https://www.stackoverflow.com\", \"browserType\": \"chromium\"}",
                        "name": "playwright_navigate"
                    },
                    "type": "function",
                    "index": 0
                }

        Return:
            tools_choice_response

                {
                    "function_name": "playwright_navigate",
                    "function_arguments": "{\"url\": \"https://www.stackoverflow.com\", \"browserType\": \"chromium\"}",
                    "is_function_call": true,
                    "id": "call_d6f4ed29ce614390b99a05"
                }
            completion: str
            reasoningContent: str
    """
    if tool_call is None or len(tool_call) == 0:
        return {}, "", ""

    tools_choice_response = {
        'function_name': '',
        'function_arguments': '',
        'is_function_call': False,
        'id': ''
    }
    completion = ""
    reasoningContent = ""
    try:
        tool_id = tool_call["id"] if "id" in tool_call else ""
        function = tool_call["function"] if "function" in tool_call else {}
        function_arguments = function["arguments"] if "arguments" in function else {}
        function_name = function["name"] if "name" in function else ""

        tools_choice_response["is_function_call"] = True
        tools_choice_response["function_name"] = function_name
        tools_choice_response["function_arguments"] = function_arguments
        tools_choice_response["id"] = tool_id
    except Exception as e:
        logging.error(f"Failed to run tool_result_to_claude_mapper {e}")
    return tools_choice_response, completion, reasoningContent


def _parse_json_response(text: str) -> Any:
    """
    Remove ```json fences and parse JSON.
    Retry once with simple repair for common LLM JSON errors.
    """

    def _clean(t: str) -> str:
        t = re.sub(r"```json|```", "", t, flags=re.IGNORECASE)
        return t.strip()

    def _repair(t: str) -> str:
        # remove duplicate closing brackets like ]\n]
        t = re.sub(r"\]\s*\]\s*$", "]", t)

        # remove trailing commas before ] or }
        t = re.sub(r",\s*([\]}])", r"\1", t)

        return t

    cleaned = _clean(text)

    try:
        return json.loads(cleaned)

    except json.JSONDecodeError:
        try:
            repaired = _repair(cleaned)
            return json.loads(repaired)
        except Exception:
            print(f"Model response was not valid JSON: {cleaned[:200]}...")
            return {"success": False,
                    "content": cleaned,
                    "raw_response": text,
                    }

    except Exception as e:
        print (f"DEBUG: _parse_json_response failed with error {e}")
        return {"success": False,
                "content": cleaned,
                "raw_response": text,
        }

def read_files(file_paths: str | list) -> str:
    """

    Args:
        file_paths (str or list):  Markdown

    Returns:
        str:  Markdown Concatenation

    Raises:
        FileNotFoundError:
        ValueError:
    """
    if isinstance(file_paths, str):
        paths_to_read = [Path(file_paths)]
    elif isinstance(file_paths, list):
        paths_to_read = [Path(fp) for fp in file_paths]
    else:
        raise ValueError("file_paths must be a str or list")

    all_contents = []

    for path in paths_to_read:
        if not path.exists():
            raise FileNotFoundError(f"Not Found File: {path}")

        with path.open('r', encoding='utf-8') as f:
            content = f.read()
            all_contents.append(content)

    return "\n\n".join(all_contents)


def read_json_file(file_paths: str | list) -> List[Any]:
    """

    Args:
        file_paths (str or list):

    Returns:
        List[Any]: List of Json Dict
    """
    try:
        if isinstance(file_paths, str):
            paths_to_read = [Path(file_paths)]
        elif isinstance(file_paths, (list, tuple)):
            paths_to_read = [Path(fp) for fp in file_paths]
        else:
            print("[WARN] file_paths must be a str or list")
            return []

        all_contents: List[Any] = []

        for path in paths_to_read:
            try:
                if not path.exists():
                    print(f"[WARN] File not found: {path}")
                    continue

                with path.open('r', encoding='utf-8') as f:
                    try:
                        data = json.load(f)
                        all_contents.append(data)
                    except json.JSONDecodeError as e:
                        print(f"[WARN] JSON decode error in {path}: {e}")

            except Exception as e:
                print(f"[WARN] Unexpected error reading {path}: {e}")

        return all_contents

    except Exception as e:
        print (f"DEBUG: Failed with error {e}")
        return []

def generate_agent_user_id(category):
    """
        Add Log
    """
    if not category:
        return "Agent"
    return f"{category.upper()} Agent"

def fill_agent_running_task_id(agent_name: Optional[str]):
    if agent_name:
        return agent_name
    uid = uuid.uuid4().hex[:6]  # real UUID, first 6 hex chars
    return f"AGENT_{uid}"

def format_ts(ts: str) -> str:
    if not ts:
        return ""
    try:
        # Handle ISO like 2026-04-23T03:12:15.142671
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        try:
            # Fallback: strip microseconds manually
            dt = datetime.strptime(ts.split('.')[0], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return ts  # return raw if parsing fails

    return dt.strftime("%Y-%m-%d %H:%M:%S")

def habit_log_format_mapper(log: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **log,
        "ts": format_ts(log.get("ts"))
    }

def agent_task_log_format_mapper(log: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **log,
        "ts": format_ts(log.get("ts"))
    }

def habit_format_mapper(h: Dict[str, Any]) -> Dict[str, Any]:
    logs = h.get("logs") or []

    return {
        **h,
        "ts": format_ts(h.get("ts")),
        "logs": [habit_log_format_mapper(l) for l in logs]
    }

from datetime import datetime, timedelta, date

def get_utc_date_str() -> str:
    """
        Get UTC date string
    """
    now = datetime.utcnow()
    today_str = now.strftime("%Y-%m-%d")
    return today_str

def get_today_str(time_zone: str = None) -> str:
    """
    Get current date as YYYY-MM-DD.

    :param time_zone: e.g. "Asia/Shanghai"
    :return: date string in YYYY-MM-DD
    """
    if not time_zone:
        # UTC time
        now = datetime.utcnow()
    else:
        try:
            now = datetime.now(ZoneInfo(time_zone))
        except Exception:
            # fallback to UTC if timezone invalid
            now = datetime.utcnow()

    return now.strftime("%Y-%m-%d")

def convert_str_to_date(start_date):
    start_date_obj = None
    if isinstance(start_date, str):
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
    elif isinstance(start_date, date):
        start_date_obj = start_date
    else:
        start_date_obj = datetime.utcnow().date()
    return start_date_obj

def convert_date_to_str(date_obj):
    """
    Convert a date/datetime/str to YYYY-MM-DD string
    """
    if isinstance(date_obj, str):
        # assume already correct format
        return date_obj

    if isinstance(date_obj, datetime):
        return date_obj.date().isoformat()

    if isinstance(date_obj, date):
        return date_obj.isoformat()

    # fallback (None or invalid)
    return datetime.utcnow().date().isoformat()

def calculate_dates_between(start_date: str, end_date: str) -> int:
    """
    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD

    Returns:
        int: inclusive number of days
             e.g. May 1 → May 2 = 2 days
             same day = 1
             negative if start > end
    """
    if start_date is None or end_date is None:
        return None
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        diff = (end - start).days
        return diff + 1 if diff >= 0 else diff - 1

    except Exception as e:
        print(f"calculate_dates_between start_date {start_date} and end_date {end_date} failed with error {e}")
        return 0

def calculate_date_diff(start_date: str, days: int) -> str:
    """
    Calculate end date from start_date + (days - 1)

    Args:
        start_date: YYYY-MM-DD
        days: number of days (inclusive)

    Returns:
        end_date: YYYY-MM-DD
    """
    if not start_date:
        raise ValueError("start_date is required")

    if days is None or days < 1:
        raise ValueError("days must be >= 1")

    start = datetime.strptime(start_date, "%Y-%m-%d").date()

    # inclusive range
    end = start + timedelta(days=days - 1)

    return end.strftime("%Y-%m-%d")

### Authentification


# Shared HTTP client for efficiency
async def get_http_client():
    async with httpx.AsyncClient() as client:
        yield client
