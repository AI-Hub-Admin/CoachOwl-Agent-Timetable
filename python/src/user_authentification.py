# -*- coding: utf-8 -*-#
# filename:

import sys, os
import urllib
import sqlite3

import uuid
from fastapi import Request, Response
from typing import Optional, Dict, List, Any
from fastapi import APIRouter, HTTPException, Depends

from .constants import KEY_USER_ID, KEY_USER_GROUP, KEY_ACCESS_KEY, DEBUG_ENABLE
from .utils import *

## 30 days session
## Not Used Session Obsolete
SESSION_MAX_AGE = 60 * 60 * 24 * 30
KEY_APP_USER_ID = "mcp_user_id"

KEY_LOGIN_APP_COOKIE_SESSION_ID = "mcp_tool_use_login_session_id"

router = APIRouter()

@router.post("/login_app")
async def login_app(response: Response, user_data: Dict):
    """
    """
    try:
        user_id = user_data["user_id"] if "user_id" in user_data else ""
        user_group = user_data["user_group"] if "user_group" in user_data else ""
        access_key = user_data["access_key"] if "access_key" in user_data else ""
        session_id = str(uuid.uuid4())
        print (f"INFO: /login_app Login to App user_data {user_data}")

        response.set_cookie(
            key=KEY_LOGIN_APP_COOKIE_SESSION_ID,
            value=session_id,
            httponly=True,
            secure=False if DEBUG_ENABLE else True,
            samesite="lax",
            max_age=86400
        )
        ## update global session data
        gv._global_user_session_store[session_id] = user_data
        print (f"DEBUG: gv._global_user_session_store for session_id {session_id} is {gv._global_user_session_store[session_id] if session_id in gv._global_user_session_store else ""}")
        return {"status": "success"}

    except Exception as e:
        print (f"login_app failed with error {e}")
        return {"status": "fail"}
