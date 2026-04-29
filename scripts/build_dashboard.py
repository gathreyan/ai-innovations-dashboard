#!/usr/bin/env python3
"""
build_dashboard.py
------------------
Reads #tech-coo-managers-ai-adoption from Slack, identifies new solution posts
using the Claude API, generates HTML cards for them, and injects them into
ai-innovations-dashboard.html.

Required environment variables (set as GitHub Actions secrets):
  SLACK_BOT_TOKEN   – xoxb-… token with channels:history + users:read scopes
  ANTHROPIC_API_KEY – Claude API key

Run locally:
  export SLACK_BOT_TOKEN=xoxb-...
  export ANTHROPIC_API_KEY=sk-ant-...
  python scripts/build_dashboard.py
"""

import json
import os
import re
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
CHANNEL_ID        = "C08U4HX466R"
SLACK_API_BASE    = "https://slack.com/api"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL      = "claude-3-5-haiku-20241022"   # fast & cheap for card gen

REPO_ROOT         = Path(__file__).parent.parent
HTML_PATH         = REPO_ROOT / "ai-innovations-dashboard.html"
PROCESSED_PATH    = REPO_ROOT / "data" / "processed_ts.json"

# Marker the script uses to inject new cards into the HTML
NEW_CARDS_MARKER  = "<!-- ── NEW CARDS INJECTED BELOW ── -->"


# ── Helpers ───────────────────────────────────────────────────────────────────

def slack_get(method: str, params: dict) -> dict:
    token = os.environ["SLACK_BOT_TOKEN"]
    qs = urllib.parse.urlencode(params)
    url = f"{SLACK_API_BASE}/{method}?{qs}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error ({method}): {data.get('error')}")
    return data


def claude_complete(prompt: str) -> str:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data["content"][0]["text"].strip()


def load_processed() -> set:
    if PROCESSED_PATH.exists():
        return set(json.loads(PROCESSED_PATH.read_text()))
    return set()


def save_processed(ts_set: set):
    PROCESSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_PATH.write_text(json.dumps(sorted(ts_set), indent=2))


def fetch_channel_messages(oldest_ts: str | None = None) -> list[dict]:
    """Fetch up to 200 messages, newest-first. Optionally filter by oldest_ts."""
    params = {"channel": CHANNEL_ID, "limit": 200}
    if oldest_ts:
        params["oldest"] = oldest_ts
    data = slack_get("conversations.history", params)
    return data.get("messages", [])


def is_solution_post(text: str) -> bool:
    """Quick pre-filter: skip join/rename/admin/pure-discussion messages."""
    if not text or len(text) < 80:
        return False
    skip_patterns = [
        r"has joined the channel",
        r"has renamed the channel",
        r"set the channel",
        r"^FYI",
        r"what do you all think",
        r"I'm on the search for",
        r"AI Learning Day",
        r"AI Survey",
        r"please avoid including",
    ]
    for pat in skip_patterns:
        if re.search(pat, text, re.IGNORECASE):
            return False
    # Must mention at least one AI tool or describe a workflow
    tool_keywords = [
        "claude", "gemini", "cursor", "slackbot", "notebooklm", "notebook lm",
        "google vids", "writer ai", "miyo", "manager agent", "prompt",
        "vibe-cod", "dashboard", "automat", "workflow",
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in tool_keywords)


def resolve_username(user_id: str, cache: dict) -> str:
    if user_id in cache:
        return cache[user_id]
    try:
        data = slack_get("users.info", {"user": user_id})
        profile = data["user"]["profile"]
        name = profile.get("real_name") or profile.get("display_name") or user_id
    except Exception:
        name = user_id
    cache[user_id] = name
    return name


def extract_links(text: str) -> list[str]:
    return re.findall(r"<(https?://[^|>]+)(?:\|[^>]*)?>", text)


def ts_to_date(ts: str) -> str:
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return dt.strftime("%b %d, %Y")


def classify_tags(text: str) -> str:
    """Return space-separated data-tags value."""
    tags = []
    tl = text.lower()
    if any(k in tl for k in ["claude", "cursor", "claude code"]):
        tags.append("claude")
    if "gemini" in tl:
        tags.append("gemini")
    if "slackbot" in tl:
        tags.append("slackbot")
    if any(k in tl for k in ["notebooklm", "notebook lm"]):
        tags.append("notebook")
    if any(k in tl for k in ["dashboard", "kanban", "chart", "visualization"]):
        tags.append("dashboard")
    if any(k in tl for k in ["google vids", "google docs", "google slides"]):
        tags.append("google")
    if not tags:
        tags.append("productivity")
    tags.append("productivity")
    return " ".join(dict.fromkeys(tags))  # deduplicated, ordered


def generate_card_html(msg: dict, username: str) -> str:
    """Ask Claude to generate a dashboard card HTML snippet for this Slack post."""
    text = msg.get("text", "")
    links = extract_links(text)
    date_str = ts_to_date(msg["ts"])
    ts = msg["ts"]
    slack_url = f"https://salesforce-internal.slack.com/archives/{CHANNEL_ID}/p{ts.replace('.', '')}"
    tags = classify_tags(text)

    # Build initials for avatar
    parts = username.split()
    initials = (parts[0][0] + parts[-1][0]).upper() if len(parts) >= 2 else username[:2].upper()

    # Strip Slack markup for cleaner reading
    clean_text = re.sub(r"<(?:https?://[^|>]+\|)?([^>]+)>", r"\1", text)
    clean_text = re.sub(r"<@\w+\|?([^>]*)?>", r"@\1", clean_text)
    clean_text = clean_text[:2000]  # cap for prompt

    # Pick accent class
    accent = "acc-multi"
    if "claude" in tags:      accent = "acc-claude"
    elif "gemini" in tags:    accent = "acc-gemini"
    elif "slackbot" in tags:  accent = "acc-slackbot"
    elif "notebook" in tags:  accent = "acc-notebook"
    elif "google" in tags:    accent = "acc-google"

    prompt = f"""You are generating a single HTML card snippet for an AI innovations dashboard.
The card must use ONLY the CSS classes already defined in the page — no new styles.

Available CSS classes for structure:
  card-accent (+ one of: acc-claude acc-gemini acc-slackbot acc-cursor acc-notebook acc-google acc-multi)
  card-body, card-header, card-title, card-emoji, card-author, avatar, card-desc, card-tags, tag
  card-footer, card-date, reactions, card-links, btn-link, btn-link secondary

Here is the Slack post to turn into a card:
---
Author: {username}
Date: {date_str}
Links found in post: {json.dumps(links)}
Text:
{clean_text}
---

Rules:
1. Output ONLY the raw HTML div — nothing else, no markdown fences, no explanation.
2. Start with:  <div class="card" data-tags="{tags}" data-text="...">
   Fill data-text with 10-15 lowercase keywords describing the solution.
3. Card structure inside must be exactly:
   <div class="card-accent {accent}"></div>
   <div class="card-body"> ... </div>
   <div class="card-footer"> ... </div>
4. card-body must contain: card-header (title + emoji), card-author (avatar + name),
   card-desc (Problem → Solution → Result, concise), card-tags (3-5 tag spans).
5. card-footer must contain: left side = card-date + reactions div;
   right side = card-links with at most 2 <a> buttons.
   - If a direct artifact link exists (dashboard, doc, skill, tool), use it as primary btn-link.
   - Always include a "Slack Post" secondary btn-link pointing to: {slack_url}
6. Keep card-desc under 60 words. Be specific about the time saved or impact if mentioned.
7. Choose a relevant single emoji for card-emoji.
8. Do NOT include any <script>, <style>, or outer wrapper tags.
"""

    html = claude_complete(prompt)

    # Safety: strip any accidental markdown fences
    html = re.sub(r"^```html?\s*", "", html, flags=re.MULTILINE)
    html = re.sub(r"\s*```$", "", html, flags=re.MULTILINE)
    return html.strip()


def inject_cards(new_cards_html: list[str]) -> int:
    """Insert new cards right after NEW_CARDS_MARKER in the HTML file."""
    content = HTML_PATH.read_text(encoding="utf-8")

    if NEW_CARDS_MARKER not in content:
        # Insert marker just after the opening of the grid div
        content = content.replace(
            "<!-- ═══════════ CARD GRID ═══════════ -->\n<div class=\"grid\" id=\"card-grid\">",
            f"<!-- ═══════════ CARD GRID ═══════════ -->\n<div class=\"grid\" id=\"card-grid\">\n\n  {NEW_CARDS_MARKER}\n",
        )

    injection = "\n\n".join(new_cards_html)
    content = content.replace(
        NEW_CARDS_MARKER,
        f"{NEW_CARDS_MARKER}\n\n{injection}",
    )
    HTML_PATH.write_text(content, encoding="utf-8")
    return len(new_cards_html)


def update_solution_count(delta: int):
    """Increment the solution-count in the HTML header."""
    content = HTML_PATH.read_text(encoding="utf-8")
    match = re.search(r'id="solution-count">(\d+)<', content)
    if match:
        old = int(match.group(1))
        new = old + delta
        content = content.replace(
            f'id="solution-count">{old}<',
            f'id="solution-count">{new}<',
        )
        HTML_PATH.write_text(content, encoding="utf-8")


def update_refresh_timestamp():
    """Stamp the current UTC date into the static last-refresh span."""
    content = HTML_PATH.read_text(encoding="utf-8")
    date_str = datetime.now(tz=timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    # Replace whatever is currently in the span (could be empty or a previous date)
    content = re.sub(
        r'(<strong id="last-refresh">)[^<]*(</strong>)',
        rf'\g<1>{date_str}\g<2>',
        content,
    )
    HTML_PATH.write_text(content, encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Gracefully skip if secrets not configured
    if not os.environ.get("SLACK_BOT_TOKEN"):
        print("⚠️  SLACK_BOT_TOKEN not set — skipping Slack fetch, stamping timestamp only.")
        update_refresh_timestamp()
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("⚠️  ANTHROPIC_API_KEY not set — skipping card generation, stamping timestamp only.")
        update_refresh_timestamp()
        return

    print("🔄  Fetching Slack channel messages…")
    processed = load_processed()

    messages = fetch_channel_messages()
    print(f"    Got {len(messages)} messages. Already processed: {len(processed)}")

    new_msgs = [m for m in messages if m.get("ts") not in processed and m.get("type") == "message"]
    solution_msgs = [m for m in new_msgs if is_solution_post(m.get("text", ""))]

    print(f"    New unprocessed messages: {len(new_msgs)}")
    print(f"    Solution posts to card-ify: {len(solution_msgs)}")

    if not solution_msgs:
        print("✅  No new solution posts — updating timestamp and exiting.")
        update_refresh_timestamp()
        # Still mark all new non-solution messages as processed so we don't recheck them
        for m in new_msgs:
            processed.add(m["ts"])
        save_processed(processed)
        return

    user_cache: dict = {}
    new_cards: list[str] = []

    for msg in reversed(solution_msgs):  # oldest-first so they appear in chronological order
        ts = msg["ts"]
        user_id = msg.get("user", "")
        username = resolve_username(user_id, user_cache)
        print(f"  📝  Generating card for {username} (ts={ts})…")
        try:
            card_html = generate_card_html(msg, username)
            new_cards.append(card_html)
            processed.add(ts)
            print(f"       ✓ Done")
        except Exception as exc:
            print(f"       ✗ Failed: {exc}", file=sys.stderr)
            # Don't mark as processed — retry next run

    # Mark non-solution new messages as processed too
    for m in new_msgs:
        processed.add(m["ts"])

    if new_cards:
        count = inject_cards(new_cards)
        update_solution_count(count)
        print(f"✅  Injected {count} new card(s) into the dashboard.")

    update_refresh_timestamp()
    save_processed(processed)
    print("✅  Done. Dashboard updated.")


if __name__ == "__main__":
    main()
