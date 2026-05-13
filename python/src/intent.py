from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Optional, List

import requests

from .constants import LOG_ENABLE, QWEN_API_KEY, QWEN_MODEL
from .utils import safe_json_loads, llm_qwen_messages_chat


@dataclass
class IntentResult:
    intent: str  # fitness|career
    response: str = ""
    objectives: Dict[str, Any] | None = None
    actions: List[Any] | None = None
    kwargs: Dict[str, Any] | None = None

"""
User Prompt: I have diet coke for dinner
{
    "intent": "fitness",
    "objectives": {
        "Lose Weight by 5 pounds(2.3kg)": [
            {"habit_name": "Eat vegetable and Fruits"}
        ]
    },
    "action": ["checkin", "create"]
}
"""

def _heuristic_intent(prompt: str) -> IntentResult:
    p = (prompt or "").lower()
    if any(k in p for k in ["calorie", "calories", "kcal", "meal", "breakfast", "lunch", "dinner", "food", "ate", "burger", "fries"]):
        return IntentResult(intent="fitness")
    if any(k in p for k in ["habit", "objective", "streak", "tracker", "coffee free", "sugar free", "check in", "checkin", "milk"]):
        return IntentResult(intent="habit")
    if any(k in p for k in ["promotion", "boss", "raise", "salary", "network", "networking", "workplace", "conflict", "coworker", "manager"]):
        return IntentResult(intent="career")
    # default
    return IntentResult(intent="career")

def route_intent(user_prompt: str, system_prompt: str) -> IntentResult:
    """
    Uses Qwen (if configured) to do intent routing; otherwise uses a deterministic heuristic.
    """
    if not QWEN_API_KEY:
        return _heuristic_intent(user_prompt)

    try:
        if LOG_ENABLE:
            print (f"DEBUG: route_intent user_prompt {user_prompt}| system_prompt {system_prompt}|")
        data = llm_qwen_messages_chat(system_prompt, user_prompt, QWEN_MODEL)
        
        if not data:
            return _heuristic_intent(user_prompt)
            
        intent = (data.get("intent") or "").strip().lower()
        if intent not in {"fitness", "habit", "career"}:
            return _heuristic_intent(user_prompt)
            
        return IntentResult(
            intent=intent, 
            response=str(data.get("response") or ""), 
            objectives=data.get("objectives") or {},
            actions=data.get("action") or data.get("actions") or []
        )
    except Exception as e:
        if LOG_ENABLE:
            print(f"[intent] error: {e}")
        return _heuristic_intent(user_prompt)

