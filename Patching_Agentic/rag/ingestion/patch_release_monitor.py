# Changelog:
#   2026-09-22T21:06:16+05:30 — Initial quarterly CPU advisory monitor against Oracle's real, public RSS feed — Arvind Regukumar

"""Detects whether a new quarterly Database Critical Patch Update has been
published, using Oracle's own public RSS feed — no MOS login needed for this
part, only the eventual patch *download* is MOS-gated.

Feed URL found by inspecting oracle.com/security-alerts/'s actual page
source (it links an RSS icon), then verified directly — not assumed:

    https://www.oracle.com/ocom/groups/public/@otn/documents/webcontent/rss-otn-sec.xml

Real, well-formed RSS 2.0, confirmed reachable with both `curl` and Python's
default `urllib` User-Agent (no special headers needed, despite an earlier
WebFetch-tool-specific 403 that turned out to be unrelated to this feed).

IMPORTANT — the feed mixes two different advisory types with very similar
names, and mixing them up would misfire this whole mechanism:

- "Oracle Critical Patch Update Advisory - <Month> <Year>" — the quarterly
  one (3rd Tuesday of Jan/Apr/Jul/Oct), which is what includes the Database
  Release Update this project cares about.
- "Oracle Critical Security Patch Update Advisory - <Month> <Year>" — a
  newer (started 2026-05-28) *monthly* advisory for targeted CVE fixes, a
  narrower and different thing, NOT what carries a new Database RU.

This module only ever matches the first kind, and does so by requiring the
title NOT contain "Security" right after "Critical" — see
_QUARTERLY_CPU_TITLE_RE.

This module only ever detects and reports — it never downloads anything
(the patch itself is still MOS-gated, same as ever) and never modifies
knowledge_staging/ or triggers a DMZ transfer on its own. What a human does
with a "yes, a new one shipped" signal is still entirely up to them.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

RSS_URL = "https://www.oracle.com/ocom/groups/public/@otn/documents/webcontent/rss-otn-sec.xml"

# Matches "Oracle Critical Patch Update Advisory - ..." but not "Oracle
# Critical Security Patch Update Advisory - ..." — see module docstring.
_QUARTERLY_CPU_TITLE_RE = re.compile(r"^Oracle Critical Patch Update Advisory\b")


class FeedFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdvisoryEntry:
    title: str
    link: str
    pub_date: str
    guid: str


def fetch_feed(url: str = RSS_URL, timeout_seconds: float = 15) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "OraclePatchingAgent/1.0 (patch-release-monitor)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FeedFetchError(f"Could not fetch {url}: {exc}") from exc


def parse_quarterly_cpu_entries(feed_xml: str) -> list[AdvisoryEntry]:
    """Returns quarterly CPU advisory entries, newest first (the feed's own
    order), skipping monthly CSPU/CVE-alert entries entirely."""
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError as exc:
        raise FeedFetchError(f"Feed XML did not parse: {exc}") from exc

    entries = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        if not _QUARTERLY_CPU_TITLE_RE.match(title):
            continue
        entries.append(
            AdvisoryEntry(
                title=title,
                link=(item.findtext("link") or "").strip(),
                pub_date=(item.findtext("pubDate") or "").strip(),
                guid=(item.findtext("guid") or "").strip(),
            )
        )
    return entries


def _read_last_seen_guid(state_path: Path) -> Optional[str]:
    if not state_path.is_file():
        return None
    try:
        return json.loads(state_path.read_text()).get("last_seen_guid")
    except json.JSONDecodeError:
        return None


def _write_last_seen_guid(state_path: Path, guid: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_seen_guid": guid}, indent=2))


def check_for_new_release(feed_xml: str, state_path: Path) -> tuple[Optional[AdvisoryEntry], list[AdvisoryEntry]]:
    """Compares the newest quarterly CPU entry in the feed against what was
    last recorded at state_path. Returns (new_entry_or_None, all_quarterly_entries).

    State is only updated when there IS at least one entry to record — a feed
    that (unexpectedly) has zero quarterly entries leaves prior state alone
    rather than wiping it out.
    """
    entries = parse_quarterly_cpu_entries(feed_xml)
    if not entries:
        return None, []

    newest = entries[0]
    last_seen_guid = _read_last_seen_guid(state_path)
    is_new = newest.guid != last_seen_guid
    _write_last_seen_guid(state_path, newest.guid)
    return (newest if is_new else None), entries


if __name__ == "__main__":
    import sys

    _state_path = Path(__file__).parent.parent / "patch_release_monitor_state.json"
    try:
        _feed_xml = fetch_feed()
    except FeedFetchError as exc:
        print(f"Could not check for new releases: {exc}", file=sys.stderr)
        sys.exit(1)

    _new_entry, _all_entries = check_for_new_release(_feed_xml, _state_path)
    print(f"{len(_all_entries)} quarterly CPU advisory entries in the feed. Most recent 3:")
    for entry in _all_entries[:3]:
        print(f"  - {entry.title} ({entry.pub_date})\n    {entry.link}")

    if _new_entry:
        print(f"\nNEW since last check: {_new_entry.title}")
        print(f"  {_new_entry.link}")
        print("\nThis only means a new advisory was PUBLISHED — the patch itself still")
        print("requires MOS access to download. See cowork_prompt/download_cpu_patch.md.")
    else:
        print("\nNo new quarterly CPU advisory since the last check.")
