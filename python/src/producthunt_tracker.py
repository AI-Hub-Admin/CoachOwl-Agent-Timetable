from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict

from .agents import track_competitor_launches_producthunt
from .db import init_db


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def _main() -> int:
    init_db()

    cfg_path = Path(
        os.getenv("PRODUCTHUNT_TRACKER_CONFIG", "assets/producthunt_tracker_config.json")
    )
    if not cfg_path.exists():
        example = Path("assets/producthunt_tracker_config.example.json")
        raise FileNotFoundError(
            f"Missing config at {cfg_path}. Copy {example} to {cfg_path} and edit."
        )

    cfg = _read_json(cfg_path)
    user_id = str(cfg.get("user_id") or os.getenv("PRODUCTHUNT_TRACKER_USER_ID") or "local-user")
    keywords = cfg.get("keywords") or []
    feed_urls = cfg.get("feed_urls") or []
    days_back = int(cfg.get("days_back") or 7)
    max_entries_per_feed = int(cfg.get("max_entries_per_feed") or 50)

    result = await track_competitor_launches_producthunt(
        user_id=user_id,
        keywords=json.dumps(keywords),
        feed_urls=json.dumps(feed_urls),
        days_back=days_back,
        max_entries_per_feed=max_entries_per_feed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()

