#!/bin/bash
# sync_to_soma.sh
# ───────────────────────────────────────────────────────────────────────────
# Pulls the latest dashboard from github.com and pushes it to the gh-pages
# branch on git.soma.salesforce.com.
#
# Runs daily via a macOS LaunchAgent (on your Mac, which is on the SF network).
# Log file: ~/Library/Logs/ai-dashboard-sync.log
#
# One-time setup:
#   1. chmod +x scripts/sync_to_soma.sh
#   2. Set SOMA_TOKEN in your shell environment OR in ~/.soma_token:
#        echo "YOUR_PAT_HERE" > ~/.soma_token && chmod 600 ~/.soma_token
#   3. Run: bash scripts/sync_to_soma.sh   (to test manually)
#   4. Install the LaunchAgent: bash scripts/sync_to_soma.sh --install
# ───────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────
GITHUB_REPO="https://github.com/gathreyan/ai-innovations-dashboard.git"
SOMA_OWNER="gathreyan"
SOMA_REPO="AI-Innovation-Dashboard"
SOMA_HOST="git.soma.salesforce.com"
LOG="$HOME/Library/Logs/ai-dashboard-sync.log"
WORK_DIR="$HOME/.cache/ai-dashboard-sync"
LAUNCHAGENT_LABEL="com.gathreyan.ai-dashboard-sync"
LAUNCHAGENT_PLIST="$HOME/Library/LaunchAgents/${LAUNCHAGENT_LABEL}.plist"

# ── Load PAT ──────────────────────────────────────────────────────────────
# Preference order: env var → ~/.soma_token file → gh CLI keyring token
if [[ -n "${SOMA_TOKEN:-}" ]]; then
  TOKEN="$SOMA_TOKEN"
elif [[ -f "$HOME/.soma_token" ]]; then
  TOKEN="$(cat "$HOME/.soma_token")"
elif command -v gh &>/dev/null; then
  TOKEN="$(gh auth token --hostname git.soma.salesforce.com 2>/dev/null || true)"
fi

if [[ -z "${TOKEN:-}" ]]; then
  echo "ERROR: Could not find a git.soma token." >&2
  echo "Run: gh auth login --hostname git.soma.salesforce.com --git-protocol https" >&2
  exit 1
fi

# ── Install mode: create macOS LaunchAgent ────────────────────────────────
if [[ "${1:-}" == "--install" ]]; then
  SCRIPT_ABS="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  mkdir -p "$(dirname "$LAUNCHAGENT_PLIST")"
  cat > "$LAUNCHAGENT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHAGENT_LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${SCRIPT_ABS}</string>
  </array>

  <!-- Run every day at 9 AM local time (one hour after the github.com refresh) -->
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>   <integer>9</integer>
    <key>Minute</key> <integer>0</integer>
  </dict>

  <key>StandardOutPath</key>
  <string>${LOG}</string>
  <key>StandardErrorPath</key>
  <string>${LOG}</string>

  <!-- Only run if the Mac is awake; skip if it was asleep -->
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
PLIST

  launchctl unload "$LAUNCHAGENT_PLIST" 2>/dev/null || true
  launchctl load  "$LAUNCHAGENT_PLIST"
  echo "✅ LaunchAgent installed and loaded."
  echo "   It will run daily at 9 AM."
  echo "   Log: $LOG"
  echo "   To uninstall: launchctl unload $LAUNCHAGENT_PLIST && rm $LAUNCHAGENT_PLIST"
  exit 0
fi

# ── Sync ──────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════"
echo "  AI Dashboard → git.soma sync  $(date '+%Y-%m-%d %H:%M %Z')"
echo "═══════════════════════════════════════════════════"

# Fresh working directory
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# Pull just the HTML from github.com (shallow, main branch only)
echo "📥  Pulling latest dashboard from github.com…"
git clone --depth 1 --branch main --no-tags \
  "$GITHUB_REPO" source --quiet
echo "    ✓ Done ($(wc -c < source/ai-innovations-dashboard.html | tr -d ' ') bytes)"

# Set up a fresh repo for the gh-pages push
echo "📤  Preparing gh-pages commit for git.soma…"
mkdir -p gh-pages-work
cp source/ai-innovations-dashboard.html gh-pages-work/index.html
# Also keep the original filename so both URLs work
cp source/ai-innovations-dashboard.html gh-pages-work/ai-innovations-dashboard.html

cd gh-pages-work
git init --quiet
git checkout -b gh-pages --quiet
git config user.name  "ai-dashboard-sync"
git config user.email "ai-dashboard-sync@noreply"

git add .
git commit --quiet -m "sync: $(date -u '+%Y-%m-%d %H:%M UTC') from github.com"

SOMA_URL="https://${SOMA_OWNER}:${TOKEN}@${SOMA_HOST}/${SOMA_OWNER}/${SOMA_REPO}.git"
git push "$SOMA_URL" gh-pages:gh-pages --force --quiet

echo "✅  Successfully synced to git.soma!"
echo "    https://${SOMA_HOST}/pages/${SOMA_OWNER}/${SOMA_REPO}/"
echo ""

# Cleanup
cd / && rm -rf "$WORK_DIR"
