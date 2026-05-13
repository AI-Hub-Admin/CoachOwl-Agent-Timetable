from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import asyncio
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx

from ..constants import LOG_ENABLE


@dataclass(frozen=True)
class ProductHuntEntry:
    entry_id: str
    title: str
    link: str
    published_at: Optional[datetime]
    summary: str
    source_url: str


_WS_RE = re.compile(r"\\s+")


def _norm_text(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip())


def _parse_datetime(value: str) -> Optional[datetime]:
    """
    Parses common RSS/Atom datetime formats into an aware UTC datetime.
    Returns None if parsing fails.
    """
    value = (value or "").strip()
    if not value:
        return None

    # RFC 3339 / ISO 8601 (Atom)
    # Examples: 2026-05-12T02:34:56Z, 2026-05-12T10:34:56+08:00
    try:
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # RFC 2822 (RSS pubDate), e.g. Tue, 12 May 2026 02:34:56 GMT
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S %z",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue

    return None


def _safe_find_text(el: ET.Element, tags: Sequence[str]) -> str:
    for t in tags:
        node = el.find(t)
        if node is not None and node.text:
            return node.text
    return ""


def _atom_link(el: ET.Element) -> str:
    # Atom: <link href="..."/>
    for link in el.findall("{http://www.w3.org/2005/Atom}link"):
        href = link.attrib.get("href") or ""
        if href:
            return href
    # RSS: <link>...</link>
    return _safe_find_text(el, ("link",))


def parse_producthunt_feed_xml(xml_text: str, source_url: str) -> List[ProductHuntEntry]:
    """
    Best-effort parsing for Product Hunt RSS/Atom feeds.
    """
    xml_text = xml_text or ""
    xml_text = xml_text.strip()
    if not xml_text:
        return []

    root = ET.fromstring(xml_text)

    entries: List[ProductHuntEntry] = []

    # Atom feed
    if root.tag.endswith("feed"):
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.findall(f"{ns}entry"):
            entry_id = _norm_text(_safe_find_text(entry, (f"{ns}id",)))
            title = _norm_text(_safe_find_text(entry, (f"{ns}title",)))
            link = _norm_text(_atom_link(entry))
            published_raw = _safe_find_text(entry, (f"{ns}published", f"{ns}updated"))
            published_at = _parse_datetime(published_raw)
            summary = _norm_text(_safe_find_text(entry, (f"{ns}summary", f"{ns}content")))
            if not entry_id:
                entry_id = link or title
            if not entry_id:
                continue
            entries.append(
                ProductHuntEntry(
                    entry_id=entry_id,
                    title=title,
                    link=link,
                    published_at=published_at,
                    summary=summary,
                    source_url=source_url,
                )
            )
        return entries

    # RSS feed
    channel = root.find("channel")
    if channel is None:
        # Some RSS feeds might be <rss><channel> or namespace variants; fallback scan.
        channel = root.find(".//channel")
    if channel is None:
        return []

    for item in channel.findall("item"):
        entry_id = _norm_text(_safe_find_text(item, ("guid", "id")))
        title = _norm_text(_safe_find_text(item, ("title",)))
        link = _norm_text(_safe_find_text(item, ("link",)))
        published_raw = _safe_find_text(item, ("pubDate", "published", "dc:date"))
        published_at = _parse_datetime(published_raw)
        summary = _norm_text(_safe_find_text(item, ("description", "summary", "content:encoded")))
        if not entry_id:
            entry_id = link or title
        if not entry_id:
            continue
        entries.append(
            ProductHuntEntry(
                entry_id=entry_id,
                title=title,
                link=link,
                published_at=published_at,
                summary=summary,
                source_url=source_url,
            )
        )

    return entries


async def fetch_producthunt_entries(
    feed_urls: Sequence[str],
    *,
    timeout_seconds: float = 10.0,
    max_entries_per_feed: int = 50,
) -> List[ProductHuntEntry]:
    """
    Fetches and parses multiple Product Hunt feed URLs.
    """
    if not feed_urls:
        return []

    timeout = httpx.Timeout(timeout_seconds)
    entries: List[ProductHuntEntry] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async def _fetch_one(url: str) -> None:
            try:
                resp = await client.get(url, headers={"User-Agent": "coachowl/1.0"})
                resp.raise_for_status()
                feed_entries = parse_producthunt_feed_xml(resp.text, source_url=url)
                if max_entries_per_feed > 0:
                    feed_entries = feed_entries[:max_entries_per_feed]
                entries.extend(feed_entries)
            except Exception as e:
                if LOG_ENABLE:
                    print(f"DEBUG: fetch_producthunt_entries failed url={url} err={e}")

        await asyncio.gather(*[_fetch_one(u) for u in feed_urls])
    return entries


async def fetch_producthunt_entries_safe(
    feed_urls: Sequence[str],
    *,
    timeout_seconds: float = 10.0,
    max_entries_per_feed: int = 50,
) -> List[ProductHuntEntry]:
    # Back-compat alias: older callers may use *_safe.
    return await fetch_producthunt_entries(
        feed_urls, timeout_seconds=timeout_seconds, max_entries_per_feed=max_entries_per_feed
    )


def _keywords_match(text: str, keywords: Sequence[str]) -> List[str]:
    text_lc = (text or "").lower()
    matched: List[str] = []
    for kw in keywords or []:
        kw_norm = (kw or "").strip()
        if not kw_norm:
            continue
        if kw_norm.lower() in text_lc:
            matched.append(kw_norm)
    return matched


def filter_entries_by_keywords(
    entries: Sequence[ProductHuntEntry],
    *,
    keywords: Sequence[str],
    days_back: int = 7,
    now_utc: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Returns matched entries as dicts with `matched_keywords`.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=max(days_back, 0))

    results: List[Dict[str, Any]] = []
    for e in entries:
        # If published_at missing, keep it (Product Hunt feeds sometimes omit).
        if e.published_at is not None and e.published_at < cutoff:
            continue

        haystack = f"{e.title}\n{e.summary}\n{e.link}"
        matched = _keywords_match(haystack, keywords)
        if not matched:
            continue

        results.append(
            {
                "entry_id": e.entry_id,
                "title": e.title,
                "link": e.link,
                "published_at": e.published_at.isoformat() if e.published_at else None,
                "summary": e.summary,
                "source_url": e.source_url,
                "matched_keywords": matched,
            }
        )
    return results


def mark_and_filter_unseen(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    matches: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Dedupe matches per user by persisting entry_id into sqlite.
    Returns only previously unseen matches.
    """
    unseen: List[Dict[str, Any]] = []
    if not matches:
        return unseen

    for m in matches:
        entry_id = str(m.get("entry_id") or "")
        if not entry_id:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO producthunt_seen(user_id, entry_id, first_seen_at) VALUES (?, ?, datetime('now'))",
            (user_id, entry_id),
        )
        if cur.rowcount and cur.rowcount > 0:
            unseen.append(dict(m))
    conn.commit()
    return unseen
