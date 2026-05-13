#### CoachOwl AI Agent Orchestrator Design

The table describe the design of CoachOwl AI Agent Timetable

#### Table List
| Table Name | Description | Unique Id |
|---- |-------------|  ---- |
| habits | Habit definitions (objectives + tasks; human + agent) | `habits.id` |
| habit_logs | Habit check-ins + agent-task run records (threaded) | `habit_logs.id` |
| agent_execution_state | Latest state for an agent execution (1 row per `execution_id`) | `agent_execution_state.execution_id` |
| agent_execution_logs  | Append-only event stream for an agent execution | `agent_execution_logs.id` |

---

#### Table: `habits`
Defines what should happen over a time window.

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (PK) | Habit id (uuid) |
| `user_id` | TEXT (FK users.id) | Owner |
| `name` | TEXT | Habit/objective name |
| `content` | TEXT | Task prompt (free text) |
| `kind` | TEXT | `objective` or `task` (default `task`) |
| `category` | TEXT | `fitness` \| `career` \| `general` |
| `parent_id` | TEXT | `NULL` for objectives; tasks point to objective id |
| `target_days` | INTEGER | Target days (objective progress) |
| `interval_hours` | INTEGER | Interval scheduling (optional) |
| `start_date` | TEXT | Local date start (YYYY-MM-DD) |
| `end_date` | TEXT | Local date end (YYYY-MM-DD) |
| `timezone` | TEXT | User timezone (default `America/Los_Angeles`) |
| `start_time` | TEXT | Daily window start (default `00:00:00`) |
| `end_time` | TEXT | Daily window end (default `23:59:59`) |
| `agent_model` | TEXT | Model routing key (default `default`) |
| `task_type` | TEXT | `human` or `agent` |
| `status` | TEXT | `active` by default |
| `result` | TEXT | Optional final summary/result |
| `archived` | INTEGER | 0/1 |
| `created_at` | TEXT | Created time |

Notes:
- Uniqueness: `UNIQUE(user_id, name, parent_id)` prevents duplicate tasks under the same objective.

---

#### Table: `habit_logs`
Defines what happened (human check-ins, agent runs, and threaded conversation).

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (PK) | Log id (uuid) |
| `habit_id` | TEXT (FK habits.id) | Which habit/task this log belongs to |
| `user_id` | TEXT (FK users.id) | Owner |
| `parent_log_id` | TEXT (FK habit_logs.id) | Threading for replies (nullable) |
| `log_type` | TEXT | `human` or `agent` (default `human`) |
| `ts` | TEXT | UTC timestamp (default now) |
| `day` | TEXT | Local day (YYYY-MM-DD) for streak/progress |
| `note` | TEXT | User text or agent message |
| `image_url` | TEXT | Optional image URL |
| `status` | TEXT | Human: `completed`; Agent: `starting`/`running`/`idle`/`failure` |
| `value` | INTEGER | Default 1 |
| `assigned_agent_id` | TEXT | Worker identity (optional) |
| `assigned_agent_name` | TEXT | Worker name (optional) |
| `assigned_at` | TEXT | When assigned (optional) |
| `heartbeat_at` | TEXT | Last heartbeat (optional) |
| `retry_count` | INTEGER | Current retry count |
| `max_retries` | INTEGER | Max retries (default 1) |
| `expected_children` | INTEGER | Multi-agent fanout expected |
| `completed_children` | INTEGER | Multi-agent fanout completed |
| `execution_id` | TEXT | Links to `agent_execution_state.execution_id` / `agent_execution_logs.execution_id` |
| `root_execution_id` | TEXT | Root execution for multi-agent trees |
| `is_root_execution` | INTEGER | 1 if this log represents the root execution |

---

#### Table: `agent_execution_state`
Defines the current view of an execution (1 row per `execution_id`).

| Column | Type | Meaning |
|---|---|---|
| `execution_id` | TEXT (PK) | Execution id (design: `${habit_uuid}:${date_slot}:${agent_model}`) |
| `habit_id` | TEXT (FK habits.id) | Which agent habit is running |
| `root_execution_id` | TEXT | Root execution id (design: `${habit_uuid}:${date_slot}`) |
| `agent_id` | TEXT | Worker id |
| `agent_name` | TEXT | Worker name |
| `status` | TEXT | `scheduled` \| `running` \| `stalled` \| `completed` \| `failed` |
| `scheduled_date` | TEXT | Local date chosen to run |
| `scheduled_start_time` | TEXT | Daily window start chosen |
| `scheduled_end_time` | TEXT | Daily window end chosen |
| `heartbeat_at` | TEXT | Last heartbeat time |
| `started_at` | TEXT | Start time |
| `updated_at` | TEXT | Last update time |
| `retry_count` | INTEGER | Retry count |
| `progress` | REAL | 0.0 → 1.0 |
| `last_message` | TEXT | Last human-readable status |
| `is_alive` | INTEGER | 1/0 |

Execution id format notes:
- `execution_id = ${habit_uuid}:${date_slot}:${agent_model}` (example: `9f4936dc-032f-41a5-83d9-202f350ad2ff:2026-05-13:default`)
- `root_execution_id = ${habit_uuid}:${date_slot}` (example: `9f4936dc-032f-41a5-83d9-202f350ad2ff:2026-05-13`)

---

#### Table: `agent_execution_logs`
Append-only event stream (debug/progress/stdout/stderr/state changes).

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (PK) | Log id (uuid) |
| `habit_id` | TEXT (FK habits.id) | Which agent habit |
| `execution_id` | TEXT | Which run |
| `root_execution_id` | TEXT | Root run id for multi-agent trees (design: `${habit_uuid}:${date_slot}`) |
| `agent_id` | TEXT | Worker id |
| `agent_name` | TEXT | Worker name |
| `event_type` | TEXT | `stdout` \| `stderr` \| `heartbeat` \| `state` \| `system` |
| `status` | TEXT | Optional status label for this event |
| `message` | TEXT | Event message/chunk |
| `ts` | TEXT | UTC timestamp |
| `metadata` | TEXT | Optional JSON blob |

#### Example
#### Case 1: Fitness Agent
Objective: user sets a goal to lose weight (30-day window). Coach creates human habits + an agent task to summarize meals.

##### `habits` records
| id | user_id | name | kind | category | parent_id | start_date | end_date | timezone | task_type | agent_model | status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `e25a8dab-ea12-458b-a725-5bb5a6fad56a` | `TEMP_USER_ID` | `Lose Weight (30 days)` | `objective` | `fitness` | `NULL` | `2026-05-13` | `2026-06-10` | `America/Los_Angeles` | `human` | `default` | `active` |
| `h_walk_5000_steps` | `TEMP_USER_ID` | `Walk 5000 steps daily` | `task` | `fitness` | `e25a8dab-ea12-458b-a725-5bb5a6fad56a` | `2026-05-13` | `2026-06-10` | `America/Los_Angeles` | `human` | `default` | `active` |
| `h_sugar_free` | `TEMP_USER_ID` | `Go sugar-free` | `task` | `fitness` | `e25a8dab-ea12-458b-a725-5bb5a6fad56a` | `2026-05-13` | `2026-06-10` | `America/Los_Angeles` | `human` | `default` | `active` |
| `6c426682-b139-411d-a224-b1213ce7195c` | `TEMP_USER_ID` | `AI Nutritionist: Monitor & Summarize Eating` | `task` | `fitness` | `e25a8dab-ea12-458b-a725-5bb5a6fad56a` | `2026-05-13` | `2026-06-10` | `America/Los_Angeles` | `agent` | `default` | `active` |

Notes:
- The last row mirrors the debug output: `habit_id=6c426682-b139-411d-a224-b1213ce7195c`, `parent_id=e25a8dab-ea12-458b-a725-5bb5a6fad56a`, `category=fitness`, date range `2026-05-13` → `2026-06-10`.

##### `habit_logs` records (human check-in + agent run root log)
Scenario: on local day `2026-05-13` the user checks in a meal photo + text. The agent task runs and produces a nutrition summary.

| id | habit_id | user_id | parent_log_id | log_type | day | status | note | execution_id | root_execution_id | is_root_execution |
|---|---|---|---|---|---|---|---|---|---|---|
| `log_human_meal_20260513` | `6c426682-b139-411d-a224-b1213ce7195c` | `TEMP_USER_ID` | `NULL` | `human` | `2026-05-13` | `completed` | `Lunch: Big Mac, Coke, Milk Shake (photo attached)` | `NULL` | `NULL` | `0` |
| `log_agent_root_exec_001` | `6c426682-b139-411d-a224-b1213ce7195c` | `TEMP_USER_ID` | `log_human_meal_20260513` | `agent` | `2026-05-13` | `running` | `Starting nutrition summarization...` | `6c426682-b139-411d-a224-b1213ce7195c:2026-05-13:default` | `6c426682-b139-411d-a224-b1213ce7195c:2026-05-13` | `1` |
| `log_agent_reply_001` | `6c426682-b139-411d-a224-b1213ce7195c` | `TEMP_USER_ID` | `log_agent_root_exec_001` | `agent` | `2026-05-13` | `idle` | `Estimated calories: ...; highlights: ...; suggestions: ...` | `6c426682-b139-411d-a224-b1213ce7195c:2026-05-13:default` | `6c426682-b139-411d-a224-b1213ce7195c:2026-05-13` | `0` |

##### `agent_execution_state` record
| execution_id | habit_id | agent_name | status | scheduled_date | progress | last_message | is_alive |
|---|---|---|---|---|---:|---|---:|
| `6c426682-b139-411d-a224-b1213ce7195c:2026-05-13:default` | `6c426682-b139-411d-a224-b1213ce7195c` | `default` | `completed` | `2026-05-13` | 1.0 | `Nutrition summary generated` | 1 |

##### `agent_execution_logs` records
| id | habit_id | execution_id | event_type | status | message |
|---|---|---|---|---|---|
| `ael_fitness_001_state_start` | `6c426682-b139-411d-a224-b1213ce7195c` | `6c426682-b139-411d-a224-b1213ce7195c:2026-05-13:default` | `state` | `running` | `run started` |
| `ael_fitness_001_stdout_001` | `6c426682-b139-411d-a224-b1213ce7195c` | `6c426682-b139-411d-a224-b1213ce7195c:2026-05-13:default` | `stdout` | `running` | `parsed meal text + image` |
| `ael_fitness_001_state_done` | `6c426682-b139-411d-a224-b1213ce7195c` | `6c426682-b139-411d-a224-b1213ce7195c:2026-05-13:default` | `state` | `completed` | `run completed` |

#### Case 2: Career Agent
Objective: prepare for a Product Hunt release in 5 days. Coach creates a human outreach habit + an agent competitor analysis task.

##### `habits` records
| id | user_id | name | kind | category | parent_id | start_date | end_date | timezone | task_type | agent_model | status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `obj_ph_launch_5d` | `TEMP_USER_ID` | `ProductHunt Launch (5 days)` | `objective` | `career` | `NULL` | `2026-05-13` | `2026-05-17` | `America/Los_Angeles` | `human` | `default` | `active` |
| `task_reachout_10` | `TEMP_USER_ID` | `Reach out to 10 users/day` | `task` | `career` | `obj_ph_launch_5d` | `2026-05-13` | `2026-05-17` | `America/Los_Angeles` | `human` | `default` | `active` |
| `task_competitor_report` | `TEMP_USER_ID` | `Competitor Analysis (last 7 days)` | `task` | `career` | `obj_ph_launch_5d` | `2026-05-13` | `2026-05-17` | `America/Los_Angeles` | `agent` | `codex` | `active` |

##### `habit_logs` records
| id | habit_id | user_id | parent_log_id | log_type | day | status | note | execution_id | root_execution_id | is_root_execution |
|---|---|---|---|---|---|---|---|---|---|---|
| `log_reachout_20260513` | `task_reachout_10` | `TEMP_USER_ID` | `NULL` | `human` | `2026-05-13` | `completed` | `Reached out to 10 users; 3 replied` | `NULL` | `NULL` | `0` |
| `log_agent_root_exec_career_001` | `task_competitor_report` | `TEMP_USER_ID` | `NULL` | `agent` | `2026-05-13` | `running` | `Starting competitor analysis...` | `task_competitor_report:2026-05-13:codex` | `task_competitor_report:2026-05-13` | `1` |
| `log_agent_reply_career_001` | `task_competitor_report` | `TEMP_USER_ID` | `log_agent_root_exec_career_001` | `agent` | `2026-05-13` | `idle` | `Report ready: key launches, positioning, messaging, and outreach ideas` | `task_competitor_report:2026-05-13:codex` | `task_competitor_report:2026-05-13` | `0` |

##### `agent_execution_state` record
| execution_id | habit_id | agent_name | status | scheduled_date | progress | last_message | is_alive |
|---|---|---|---|---|---:|---|---:|
| `task_competitor_report:2026-05-13:codex` | `task_competitor_report` | `codex` | `completed` | `2026-05-13` | 1.0 | `Competitor analysis generated` | 1 |

##### `agent_execution_logs` records
| id | habit_id | execution_id | event_type | status | message |
|---|---|---|---|---|---|
| `ael_career_001_state_start` | `task_competitor_report` | `task_competitor_report:2026-05-13:codex` | `state` | `running` | `run started` |
| `ael_career_001_stdout_001` | `task_competitor_report` | `task_competitor_report:2026-05-13:codex` | `stdout` | `running` | `fetched candidate launches + summarized themes` |
| `ael_career_001_state_done` | `task_competitor_report` | `task_competitor_report:2026-05-13:codex` | `state` | `completed` | `run completed` |
