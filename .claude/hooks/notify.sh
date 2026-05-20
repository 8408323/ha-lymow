#!/bin/bash
# Sends a native OS notification when Claude needs user input.
# Used as a Notification hook.
# Supports macOS (osascript), Linux (notify-send), and WSL (powershell).

INPUT=$(cat 2>/dev/null)

# Extract the notification message if jq is available
MESSAGE="Claude Code needs your attention"
if command -v jq >/dev/null 2>&1 && [ -n "$INPUT" ]; then
  MSG=$(echo "$INPUT" | jq -r '.message // empty' 2>/dev/null)
  if [ -n "$MSG" ]; then
    MESSAGE="$MSG"
  fi
fi

TITLE="Claude Code"

# Per-shell escaping. The raw $MESSAGE is untrusted (comes from the
# hook input JSON) and must never be interpolated unescaped into another
# shell or scripting host — otherwise embedded quotes / newlines / shell
# metacharacters could break the notification or, worse, change the
# command being executed.

# macOS — escape backslashes and double-quotes for AppleScript string literals;
# convert newlines to spaces so the string stays on one line.
if command -v osascript >/dev/null 2>&1; then
  MSG_OSA=$(printf '%s' "$MESSAGE" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n\r' '  ')
  TITLE_OSA=$(printf '%s' "$TITLE"   | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  osascript -e "display notification \"$MSG_OSA\" with title \"$TITLE_OSA\"" 2>/dev/null
  exit 0
fi

# Linux (native) — notify-send accepts title/body as separate argv positions,
# so quoting in bash is enough; no in-string interpolation happens.
if command -v notify-send >/dev/null 2>&1; then
  notify-send -- "$TITLE" "$MESSAGE" 2>/dev/null
  exit 0
fi

# WSL → Windows toast. Pass title/message as parameters to a small inline
# script instead of splicing them into the command string. PowerShell's
# parameter binder treats them as plain strings — no quote/newline/`$`
# interpretation against the script body.
if command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -Command \
    'param($t,$m); [System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms") | Out-Null; $n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; $n.ShowBalloonTip(5000, $t, $m, "Info")' \
    -t "$TITLE" -m "$MESSAGE" 2>/dev/null
  exit 0
fi

# No notification method available. Silent exit
exit 0
