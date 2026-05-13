from __future__ import annotations

import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .constants import DB_PATH


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- start_date and end_date all use local date, otherwise streak will break
CREATE TABLE IF NOT EXISTS habits (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  content TEXT DEFAULT '',   -- task prompt
  kind TEXT NOT NULL DEFAULT 'task', -- objective|task,  parent_id is null: onetime tasks, parent_id ot null, objective related task
  category TEXT,      -- fitness|career|general
  parent_id TEXT,     -- null for objective, parent_id for tasks
  target_days INTEGER,
  interval_hours INTEGER,
  start_date TEXT DEFAULT (date('now')),
  end_date TEXT DEFAULT (date('now')),
  timezone TEXT DEFAULT 'America/Los_Angeles',
  --  NEW: daily time window
  start_time TEXT DEFAULT '00:00:00',
  end_time TEXT DEFAULT '23:59:59',  
  agent_model TEXT DEFAULT 'default',
  task_type TEXT DEFAULT 'human', -- human|agent
  status TEXT DEFAULT 'active',
  result TEXT,
  archived INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  UNIQUE(user_id, name, parent_id)
);
CREATE INDEX IF NOT EXISTS idx_habits_user ON habits(user_id);
CREATE INDEX IF NOT EXISTS idx_habits_user_name_parent ON habits(user_id, name, parent_id);

CREATE TABLE IF NOT EXISTS habit_logs (
  id TEXT PRIMARY KEY,
  habit_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  -- Threading (critical for agent replies)
  parent_log_id TEXT DEFAULT NULL,
  -- Classification of log origin
  log_type TEXT NOT NULL DEFAULT 'human',
  -- human | agent
  -- ts/day use pure UTC time  
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  -- day: use user local date, if specify, use UTC
  day TEXT,
  note TEXT CHECK (length(note) <= 1000000),
  image_url TEXT CHECK (length(note) <= 200000),
  status TEXT DEFAULT '',
  -- status: human: completed, agent: starting, running, idle, failure
  value INTEGER NOT NULL DEFAULT 1,
  -- agent task status
  assigned_agent_id TEXT,
  assigned_agent_name TEXT,
  assigned_at TEXT,
  heartbeat_at TEXT,
  -- 🔁 retry
  retry_count INTEGER DEFAULT 0,
  max_retries INTEGER DEFAULT 1,  
  -- Children Task
  expected_children INTEGER DEFAULT 0,
  completed_children INTEGER DEFAULT 0,
  
  --- Multi Agent Task Tables
  execution_id  TEXT,
  root_execution_id TEXT,
  is_root_execution INTEGER DEFAULT 0,
  
  FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(parent_log_id) REFERENCES habit_logs(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_habit_logs_habit_day
ON habit_logs(habit_id, day);
CREATE INDEX IF NOT EXISTS idx_habit_logs_user
ON habit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_habit_logs_parent
ON habit_logs(parent_log_id);
CREATE INDEX IF NOT EXISTS idx_habit_logs_type
ON habit_logs(log_type);


CREATE TABLE IF NOT EXISTS meals (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  meal_text TEXT NOT NULL,
  calories REAL NOT NULL,
  items_json TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_meals_user_ts ON meals(user_id, ts);

CREATE TABLE IF NOT EXISTS coach_settings (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  tab_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  prompt TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(user_id, tab_name),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_coach_settings_user ON coach_settings(user_id);

CREATE TABLE IF NOT EXISTS user_profiles (
  user_id TEXT PRIMARY KEY,
  age INTEGER,
  gender TEXT,
  timezone TEXT DEFAULT 'America/Los_Angeles',
  profile  TEXT DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id
ON user_profiles(user_id);

CREATE TABLE IF NOT EXISTS user_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,               -- 'connected_agents', 'slack', 'discord'
    config_json TEXT NOT NULL,        -- JSON blob for flexibility
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, type)
);

CREATE INDEX IF NOT EXISTS idx_user_configs_user_id
ON user_configs(user_id);

CREATE INDEX IF NOT EXISTS idx_user_configs_type
ON user_configs(type);


CREATE TABLE IF NOT EXISTS agent_execution_logs (
  id TEXT PRIMARY KEY,

  -- identity
  habit_id TEXT NOT NULL,
  execution_id TEXT NOT NULL,
  root_execution_id TEXT,

  agent_id TEXT,
  agent_name TEXT,

  -- event classification
  event_type TEXT NOT NULL, 
  -- stdout | stderr | heartbeat | state | system

  status TEXT,
  -- running | stalled | completed | error | etc

  message TEXT, -- chunk or state message

  ts TEXT NOT NULL DEFAULT (datetime('now')),

  -- optional metadata
  metadata TEXT,

  FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_exec_logs_execution
ON agent_execution_logs(execution_id);

CREATE INDEX IF NOT EXISTS idx_agent_exec_logs_habit
ON agent_execution_logs(habit_id);

CREATE TABLE IF NOT EXISTS agent_execution_state (
  execution_id TEXT PRIMARY KEY,

  habit_id TEXT NOT NULL,
  root_execution_id TEXT,

  agent_id TEXT,
  agent_name TEXT,

  status TEXT NOT NULL,
  -- running | stalled | completed | failed | scheduled
  
  --- days INT,
  --- 1 to 15, the index of days, starting from day1
  scheduled_date TEXT,
  scheduled_start_time TEXT,
  scheduled_end_time TEXT,
  -- scheduled date for selection
  heartbeat_at TEXT,
  started_at TEXT,
  updated_at TEXT,
  
  retry_count INTEGER DEFAULT 0,

  progress REAL DEFAULT 0,
  -- optional: 0.0 → 1.0

  last_message TEXT,

  is_alive INTEGER DEFAULT 1,

  FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE
);

-- Track seen Product Hunt launches per user for dedupe
CREATE TABLE IF NOT EXISTS producthunt_seen (
  user_id TEXT NOT NULL,
  entry_id TEXT NOT NULL,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(user_id, entry_id),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_producthunt_seen_user
ON producthunt_seen(user_id);


"""

def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def ensure_user(conn: sqlite3.Connection, user_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO users(id) VALUES (?)", (user_id,))


def fetchall_dicts(cur: sqlite3.Cursor) -> List[Dict[str, Any]]:
    rows = cur.fetchall()
    return [dict(r) for r in rows]
