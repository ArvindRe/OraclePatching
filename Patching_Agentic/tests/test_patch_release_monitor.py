# Changelog:
#   2026-09-22T21:06:16+05:30 — Initial patch release monitor tests — Arvind Regukumar

"""No real network calls here — feed content is a fixture string, same shape
as the real feed (confirmed live via `python -m rag.ingestion.patch_release_monitor`),
so these tests are fast and don't depend on Oracle's site being up.
"""

from __future__ import annotations

from rag.ingestion.patch_release_monitor import (
    check_for_new_release,
    parse_quarterly_cpu_entries,
)

_FEED_TEMPLATE = """<?xml version="1.0" encoding="utf-8" standalone="no"?>
<rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">
<channel>
<title>Oracle Security Alerts</title>
<item>
  <title>Oracle Critical Security Patch Update Advisory - September 2026</title>
  <link>https://www.oracle.com/security-alerts/cspusep2026.html</link>
  <pubDate>Tue, 15 Sep 2026 13:00:00 -0700</pubDate>
  <guid isPermaLink="false">CSPUSep2026</guid>
</item>
<item>
  <title>Oracle Critical Patch Update Advisory - July 2026</title>
  <link>https://www.oracle.com/security-alerts/cpujul2026.html</link>
  <pubDate>Tue, 21 Jul 2026 12:30:54 -0700</pubDate>
  <guid isPermaLink="false">CPUJul2026</guid>
</item>
<item>
  <title>Oracle Security Alert Advisory - CVE-2026-35273</title>
  <link>https://www.oracle.com/security-alerts/alert-cve-2026-35273.html</link>
  <pubDate>Wed, 10 Jun 2026 18:00:00 -0700</pubDate>
  <guid isPermaLink="false">alertcve202635273</guid>
</item>
<item>
  <title>Oracle Critical Patch Update Advisory - April 2026</title>
  <link>https://www.oracle.com/security-alerts/cpuapr2026.html</link>
  <pubDate>Tue, 21 Apr 2026 12:30:54 -0700</pubDate>
  <guid isPermaLink="false">CPUApr2026</guid>
</item>
</channel>
</rss>
"""


def test_parse_excludes_monthly_cspu_and_cve_alerts_keeps_only_quarterly_cpu():
    entries = parse_quarterly_cpu_entries(_FEED_TEMPLATE)

    titles = [e.title for e in entries]
    assert "Oracle Critical Patch Update Advisory - July 2026" in titles
    assert "Oracle Critical Patch Update Advisory - April 2026" in titles
    # The monthly CSPU and one-off CVE alert must NOT be mistaken for a
    # quarterly CPU — this distinction is the whole point of the regex.
    assert not any("Security Patch Update" in t for t in titles)
    assert not any("CVE-" in t for t in titles)
    assert len(entries) == 2


def test_parse_preserves_feed_order_newest_first():
    entries = parse_quarterly_cpu_entries(_FEED_TEMPLATE)
    assert entries[0].guid == "CPUJul2026"
    assert entries[1].guid == "CPUApr2026"


def test_first_check_reports_newest_as_new(tmp_path):
    state_path = tmp_path / "state.json"
    new_entry, all_entries = check_for_new_release(_FEED_TEMPLATE, state_path)

    assert new_entry is not None
    assert new_entry.guid == "CPUJul2026"
    assert len(all_entries) == 2


def test_second_check_with_same_feed_reports_no_new_release(tmp_path):
    state_path = tmp_path / "state.json"
    check_for_new_release(_FEED_TEMPLATE, state_path)  # first check establishes state

    new_entry, _ = check_for_new_release(_FEED_TEMPLATE, state_path)

    assert new_entry is None


def test_detects_a_genuinely_new_release_appearing_in_the_feed(tmp_path):
    state_path = tmp_path / "state.json"
    check_for_new_release(_FEED_TEMPLATE, state_path)  # sees CPUJul2026 first

    # Prepend a brand-new quarterly entry ahead of the existing ones.
    feed_with_new_release = _FEED_TEMPLATE.replace(
        "<channel>\n<title>Oracle Security Alerts</title>\n",
        "<channel>\n<title>Oracle Security Alerts</title>\n"
        "<item>\n"
        "  <title>Oracle Critical Patch Update Advisory - October 2026</title>\n"
        "  <link>https://www.oracle.com/security-alerts/cpuoct2026.html</link>\n"
        "  <pubDate>Tue, 20 Oct 2026 12:30:54 -0700</pubDate>\n"
        "  <guid isPermaLink=\"false\">CPUOct2026</guid>\n"
        "</item>\n",
    )

    new_entry, all_entries = check_for_new_release(feed_with_new_release, state_path)

    assert new_entry is not None
    assert new_entry.guid == "CPUOct2026"
    assert len(all_entries) == 3


def test_state_persists_across_process_runs_via_the_state_file(tmp_path):
    state_path = tmp_path / "state.json"
    check_for_new_release(_FEED_TEMPLATE, state_path)

    assert state_path.is_file()
    # A fresh call with the same state file — simulating a new process run —
    # must still recognize the same feed as "nothing new."
    new_entry, _ = check_for_new_release(_FEED_TEMPLATE, state_path)
    assert new_entry is None
