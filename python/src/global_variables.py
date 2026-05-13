
from typing import Dict, List, Any

import httpx
from .constants import TIMEOUT_LONG_API

## User Session Store: Key: Session ID, Value: Dict
_global_user_session_store: Dict[str, Any] = {}

## User Access Key StoreL: Key: OneKey, Value: Credential
_global_access_key_credential_dict : Dict[str, Any] = {}


timeout = httpx.Timeout(TIMEOUT_LONG_API, read=TIMEOUT_LONG_API, connect=TIMEOUT_LONG_API)

## Global Http Requests to Reuse
_httpx_client = httpx.AsyncClient(
    timeout=timeout,
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=30
    )
)

