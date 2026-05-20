#!/usr/bin/env bash
# Blocks edits to sensitive or generated files.
# PreToolUse hook for Edit|Write operations.
# Exit 2 = block. Exit 0 = allow.

set -uo pipefail

emit() {
  # $1 = decision (deny|ask) ; $2 = reason
  # JSON-escape backslash first (so subsequent substitutions don't double-
  # escape), then double-quote, then control characters (newline / CR / tab).
  # Plain `printf` was emitting invalid JSON when the filename contained
  # any of these — Claude Code may then ignore the decision entirely.
  local decision="$1"
  local reason="${2//\\/\\\\}"
  reason="${reason//\"/\\\"}"
  reason="${reason//$'\n'/\\n}"
  reason="${reason//$'\r'/\\r}"
  reason="${reason//$'\t'/\\t}"
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"%s","permissionDecisionReason":"%s"}}\n' "$decision" "$reason"
  exit 2
}

if ! command -v jq >/dev/null 2>&1; then
  emit deny "jq is required for file protection hooks but is not installed."
fi

INPUT=$(cat)
FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || true)
[ -z "$FILE_PATH" ] && exit 0

# Strip any directory part with parameter expansion to avoid `basename` portability
# pitfalls — BSD/macOS `basename` does not treat `--` as an option terminator, and
# a path starting with `-` (uncommon but possible in `$FILE_PATH`) would otherwise
# be interpreted as a flag by GNU basename. Parameter expansion is shell-builtin
# and consistent across bash/zsh.
BASENAME=${FILE_PATH##*/}
# Case-insensitive comparison copy
BASENAME_LC=$(printf '%s' "$BASENAME" | tr '[:upper:]' '[:lower:]')
PATH_LC=$(printf '%s' "$FILE_PATH" | tr '[:upper:]' '[:lower:]')

# Protected basename patterns. Matched case-insensitively via BASENAME_LC.
# `.env.*` was previously a catch-all that also blocked the tracked
# `.env.example` template — but that file is the canonical list of env
# vars and must remain editable when new vars are introduced. Enumerate
# specific dangerous suffixes instead, leaving `.env.example` alone.
PROTECTED_PATTERNS=(
  ".env"
  ".env.local"
  ".env.development"
  ".env.production"
  ".env.staging"
  ".env.test"
  ".env.*.local"
  "*.pem"
  "*.key"
  "*.crt"
  "*.p12"
  "*.pfx"
  "id_rsa"
  "id_ed25519"
  "credentials.json"
  ".npmrc"
  ".pypirc"
  "package-lock.json"
  "yarn.lock"
  "pnpm-lock.yaml"
  "*.gen.ts"
  "*.generated.*"
  "*.min.js"
  "*.min.css"
)

shopt -s nocasematch 2>/dev/null || true
for pattern in "${PROTECTED_PATTERNS[@]}"; do
  # Using bash case with nocasematch for case-insensitive glob match.
  case "$BASENAME_LC" in
    $pattern)
      emit deny "Protected file: $BASENAME matches pattern '$pattern'"
      ;;
  esac
done

# Sensitive directories (use lower-cased path for case-insensitive on mac/Windows).
case "$PATH_LC" in
  .git/*|*/.git/*)
    emit deny "Cannot edit files inside .git/" ;;
  secrets/*|*/secrets/*)
    emit deny "Cannot edit files inside secrets/" ;;
  .env|.env.local|.env.development|.env.production|.env.staging|.env.test|.env.*.local)
    emit deny "Cannot edit .env files (excluding the tracked .env.example template)" ;;
  */.env|*/.env.local|*/.env.development|*/.env.production|*/.env.staging|*/.env.test|*/.env.*.local)
    emit deny "Cannot edit .env files (excluding the tracked .env.example template)" ;;
  .claude/hooks/*|*/.claude/hooks/*)
    emit deny "Cannot edit hook scripts. These enforce security boundaries." ;;
  .claude/settings.json|*/.claude/settings.json|.claude/settings.local.json|*/.claude/settings.local.json)
    emit ask "Editing settings.json. This controls permissions and hooks. Confirm this change." ;;
esac

exit 0
