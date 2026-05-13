import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None

AGENT_NAME = "coachowl"

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
SRC_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
ASSETS_DIR = ROOT_DIR / "assets"
DB_DIR = ROOT_DIR / "db"
DB_PATH = DB_DIR / "project.sqlite"

ASSETS_DIR = ROOT_DIR / "assets"
if str(ASSETS_DIR) not in sys.path:
    sys.path.insert(0, str(ASSETS_DIR))  # put at highest priority
    print (f"Appending {ASSETS_DIR} to sys.path")

FILES_WORKING_DIR = ROOT_DIR / "assets" / "files-wd"
if str(FILES_WORKING_DIR) not in sys.path:
    sys.path.insert(0, str(FILES_WORKING_DIR))  # put at highest priority
    print (f"Appending {FILES_WORKING_DIR} to sys.path")

PROMPT_DIR = SRC_DIR / "prompt"
if str(PROMPT_DIR) not in sys.path:
    sys.path.insert(0, str(PROMPT_DIR))  # put at highest priority
    print (f"Appending {PROMPT_DIR} to sys.path")

STATIC_DIR.mkdir(parents=True, exist_ok=True)
DB_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
PROMPT_DIR.mkdir(parents=True, exist_ok=True)
FILES_WORKING_DIR.mkdir(parents=True, exist_ok=True)


dotenv_path = ROOT_DIR / ".env"
print (f"INFO: CoachOwl Initialization dotenv_path | {dotenv_path}")
if load_dotenv is not None and dotenv_path.exists():
    load_dotenv(dotenv_path)

### export LOG_ENABLE=1
DEBUG_ENABLE = False
LOG_ENABLE = False
# LOG_ENABLE = os.getenv("LOG_ENABLE", "0") == "1"

KEY_COOKIE_USER_ID = "deepnlp_user_id"
INTERNAL_API_KEY = os.getenv("DEEPNLP_ONEKEY_ROUTER_INTERNAL_API_KEY", "")
INTERNAL_API_KEY_COACHOWL = "coach-internal-secret-2024"

# LLM routing (optional)
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3-max")
print (f"INFO: CoachOwl Initialization QWEN_API_KEY {QWEN_API_KEY}")
print (f"INFO: CoachOwl Initialization QWEN_MODEL {QWEN_MODEL}")
print (f"INFO: CoachOwl Initialization INTERNAL_API_KEY {INTERNAL_API_KEY}")

if QWEN_API_KEY is None or QWEN_API_KEY == "":
    QWEN_API_KEY = "" ## set to default
    print (f"INFO: CoachOwl Initialization QWEN_API_KEY empty in evn set to default|{QWEN_API_KEY}")

# USDA FoodData Central (optional)
USDA_FDC_API_KEY = os.getenv("USDA_FDC_API_KEY", "")
DEEPNLP_ONEKEY_ROUTER_ACCESS = os.getenv("DEEPNLP_ONEKEY_ROUTER_ACCESS", "")
print (f"INFO: CoachOwl Initialization DEEPNLP_ONEKEY_ROUTER_ACCESS {DEEPNLP_ONEKEY_ROUTER_ACCESS}")
print (f"INFO: CoachOwl Initialization USDA_FDC_API_KEY {USDA_FDC_API_KEY}")


# Basic response assembly formats (compatible with DeepNLP Agent Router UI)
MESSAGE_TYPE_ASSISTANT = "assistant"
OUTPUT_FORMAT_TEXT = "text"
OUTPUT_FORMAT_HTML = "html"
CONTENT_TYPE_MARKDOWN = "text/markdown"
CONTENT_TYPE_HTML = "text/html"
CONTENT_TYPE_IMAGE = "image/*"

### INTENT
USER_INTENT_FITNESS = "fitness"
USER_INTENT_CAREER = "career"
USER_INTENT_DEFAULT = "general"

USER_INTENT_LIST = [USER_INTENT_FITNESS, USER_INTENT_CAREER]

TASK_PROMPT_ACTION_CHECKIN = "checkin"
TASK_PROMPT_ACTION_CREATE = "create"
TASK_PROMPT_ACTION_DELETE = "delete"
TASK_PROMPT_ACTION_UPDATE = "update"
TASK_PROMPT_ACTION_SELECT = "select"

TASK_PROMPT_ACTION_LIST = [TASK_PROMPT_ACTION_CHECKIN, TASK_PROMPT_ACTION_CREATE, TASK_PROMPT_ACTION_DELETE, TASK_PROMPT_ACTION_UPDATE, TASK_PROMPT_ACTION_SELECT]
TASK_PROMPT_ACTION_DEFAULT = TASK_PROMPT_ACTION_CHECKIN


KEY_TARGET_DAYS = "target_days"
KEY_CONTENT = "content"
KEY_SUCCESS = "success"

INTRO_MARKDOWN = f"""### CoachOwl
Your all-in-one AI coach for Career, Fitness, Family and more.

Share your daily activities—what you ate, how you exercised, and what you accomplished.  
I’ll track your progress, keep you accountable, and generate personalized tasks for you (and also myself as AI Agents) to stay on track and improve every day."""

### Deployment
#### ------------- Constants ------------
DEPLOYED_SUBDOMAIN = "http://127.0.0.1:7115" if DEBUG_ENABLE else "https://coachowl.aiagenta2z.com/coachowl"

LOCAL_AGENTS_ENABLE = True

KEY_CLIENT_ID = "client_id"
CLIENT_ID_ANDROID = "coachowl_app_android"
CLIENT_ID_IOS = "coachowl_app_ios"

STREAMING_TIMEOUT = 180
LONG_TIMEOUT = 60
IMAGE_LONG_TIMEOUT = 60 * 2

KEY_SUCCESS = "success"

DEFAULT_AGENT_BACKGROUND_TASK_CONTENT = "Agent Running Tasks Finished..."
DEFAULT_FOOD_ANALYZING_RESULT = "Analyzing your checkin information and images..."
DEFAULT_FOOD_ANALYZING_CHECKIN_PROMPT = "Analyze the nutrition and calories in the list of foods, Run Search Calories USDA APIs. If not provided, just respond with please upload a picture of meals or what you eat!"

SEARCH_FOOD_ITEM_PER_PAGE = 3

### page size: 3 item x 3 size -> 13w
MAX_INPUT_TOKEN_LENGTH = 200000
KEY_QUERY_LIST = "query_list"

HABIT_LOG_TYPE_AGENT = "agent"
HABIT_LOG_TYPE_HUMAN = "human"


DEFAULT_AGENT_ID = "default"
DEFAULT_AGENT_NAME = "Default Agent"

KEY_AGENT_TASK_LOGS = "agent_task_logs"
DEFAULT_USER_TIMEZONE = "America/Los_Angeles"

KEY_USER_ID = "user_id"
KEY_USER_GROUP = "user_group"
KEY_ACCESS_KEY = "access_key"
HABIT_KIND_OBJECTIVE = "objective"
HABIT_KIND_TASK = "task"

TIMEOUT_LONG_API = 180.0
TIMEOUT_GENERAL = 5.0
TIMEOUT_QUICK = 2.0


LOGIN_AUTHENTICATE_URL = "https://www.deepnlp.org/login_third_party"
MCP_ONEKEY_AUTHNTIFICATE_URL = "https://www.deepnlp.org/mcp/auth"
MCP_API_CENTER_URL = "https://agent.deepnlp.org/agent/mcp_tool_use"
MCP_API_CENTER_URL_QUERY = "https://agent.deepnlp.org/api/query?data_return_type=json"
MCP_ONEKEY_CHECK_BALANCE_ENDPOINT = "https://www.deepnlp.org/api/billing/check-credits"
MCP_ONEKEY_RECORD_USAGE_ENDPOINT = "https://www.deepnlp.org/api/billing/record-usage-detailed"

KEY_STATUS = "status"
KEY_MESSAGE = "message"
KEY_CREDENTIAL = "credential"

### CONFIG TYPE

CONFIG_TYPE_CONNECTED_AGENTS = "connected_agents"
KEY_AGENT_CLIS_AVAILABLE = "agent_clis_available"

KEY_AGENT_TASK_DASHBOARD_STATUS = "task_dashboard_status"
KEY_AGENT_TASK_RUNNING_LOG = "task_running_log"
AGENT_TASK_LOG_MAX_NUMBER = 20
HABIT_LOG_MAX_NUMBER = 20

## interval seconds
BACKGROUND_TASK_RUNNING_INTERNAL_SECONDS = 30


AGENT_META_DICT = {
    "codex": {
        "image_thumbnail": "https://avatars.githubusercontent.com/u/14957082?s=48&v=4",
        "cli": "codex"
    },
    "gemini": {
        "image_thumbnail": "https://avatars.githubusercontent.com/u/161781182?s=48&v=4",
        "cli": "gemini"
    },
    "claude": {
        "image_thumbnail": "https://avatars.githubusercontent.com/u/76263028?s=48&v=4",
        "cli": "claude"
    },
    "openclaw": {
        "image_thumbnail": "https://avatars.githubusercontent.com/u/252820863?s=48&v=4",
        "cli": "openclaw"
    }
}

### Status
AGENT_TASK_STATUS_RUNNING = "running"
AGENT_TASK_STATUS_COMPLETED = "completed"
AGENT_TASK_STATUS_IDLE = "idle" ## hanged
AGENT_TASK_STATUS_STALLED = "stalled" ## hanged
AGENT_TASK_STATUS_FAILURE = "failure"
AGENT_TASK_STATUS_SCHEDULED = "scheduled"

KEY_TASK_TYPE = "task_type"

DEFAULT_ASSIGNED_AGENT_ID = "Agent"
DEFAULT_ASSIGNED_AGENT_NAME = "Agent"

KEY_AGENT_EXECUTION_STATUS = "agent_execution_status"




