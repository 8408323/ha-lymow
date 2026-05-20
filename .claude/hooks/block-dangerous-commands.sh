#!/usr/bin/env bash
# Blocks dangerous shell commands: push to protected branches, force push,
# destructive operations. PreToolUse hook for Bash operations.
# Exit 2 = block. Exit 0 = allow.
#
# Configurable via env:
#   CLAUDE_PROTECTED_BRANCHES  comma list (default: derived from git + main,master)

set -uo pipefail

emit_deny() {
  # Emit a JSON deny decision and exit 2.
  # JSON-escape backslash first (so the subsequent substitutions don't
  # double-escape), then `"`, then the control characters (newline / CR /
  # tab). Several deny-reason strings in this file include literal `\$`
  # (intentional, to print a literal `$HOME` in the message); without
  # escaping the backslash here those messages emit invalid JSON, which
  # Claude Code can silently ignore — defeating the entire hook.
  local reason="${1//\\/\\\\}"
  reason="${reason//\"/\\\"}"
  reason="${reason//$'\n'/\\n}"
  reason="${reason//$'\r'/\\r}"
  reason="${reason//$'\t'/\\t}"
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$reason"
  exit 2
}

if ! command -v jq >/dev/null 2>&1; then
  emit_deny "jq is required for command protection hooks but is not installed."
fi

INPUT=$(cat)
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || true)
[ -z "$COMMAND" ] && exit 0

# Reach inside `<shell> -c <payload>` wrappers WITHOUT globally stripping
# quotes from the whole command. Globally stripping quotes over-blocks
# benign cases like `rg "rm -rf /" .` (a search) or `echo "DROP TABLE"`
# (a print) — the quoted payload to those commands is plain text, not
# something that gets executed.
#
# Instead, specifically detect `<shell> -c <payload>` invocations (where
# shell ∈ bash/sh/zsh/ksh/dash/fish and the `-c` flag may be combined with
# other letters like `-lc`/`-ic`). For each, extract the payload that
# follows — quoted or unquoted — and append it to a synthetic analysis
# string joined by `;`. Every downstream protection checks $EFFECTIVE_CMD,
# which contains the original command plus every extracted -c payload as
# independent shell-segment-equivalent strings, so a wrapper-encoded
# `git push origin main` or `rm -rf /` is still inspected without
# treating `rg "rm -rf /"` as an invocation.
INNER_PAYLOADS=$(printf '%s' "$COMMAND" | awk '
  # ORS is bare newline (not `;\n`) so trailing tokens in each payload
  # sit at end-of-line. Several downstream regexes anchor on `$` (end of
  # line) — a literal `;` right after the last word would break those
  # anchors (e.g. `git push origin main;` would not satisfy the
  # `($BR_REGEX)([[:space:]]|$)` trailing constraint).
  BEGIN { ORS = "\n" }
  {
    s = $0
    while (match(s, /(^|[[:space:]])(bash|sh|zsh|ksh|dash|fish)[[:space:]]+-[A-Za-z]*c[A-Za-z]*[[:space:]]+/)) {
      after = substr(s, RSTART + RLENGTH)
      if (length(after) == 0) { s = ""; break }
      first = substr(after, 1, 1)
      if (first == "\x27") {
        # Single-quoted payload: bash treats everything inside literally;
        # no escape sequences are honoured (you cannot escape `\x27` inside
        # a `\x27...\x27` block). Walk to the next `\x27`.
        rest = substr(after, 2)
        end = index(rest, "\x27")
        if (end > 0) {
          print substr(rest, 1, end - 1)
          s = substr(rest, end + 1)
        } else {
          s = ""
        }
      } else if (first == "\"") {
        # Double-quoted payload: bash honours `\"` as an escape for a
        # literal `"`. Walk char-by-char, treating `\<anything>` as an
        # escape sequence to skip over (without it, an embedded escaped
        # quote like `bash -lc "echo \"x\"; git push origin main"` would
        # terminate the payload at the first `\"` and let the trailing
        # `git push origin main` escape every downstream guard.
        rest = substr(after, 2)
        i = 1
        found = 0
        while (i <= length(rest)) {
          c = substr(rest, i, 1)
          if (c == "\\") { i += 2; continue }
          if (c == "\"") { found = 1; break }
          i++
        }
        if (found) {
          print substr(rest, 1, i - 1)
          s = substr(rest, i + 1)
        } else {
          s = ""
        }
      } else {
        # Unquoted payload (rare). Take the rest of the line.
        print after
        s = ""
      }
    }
  }
')
EFFECTIVE_CMD="$COMMAND"
[ -n "$INNER_PAYLOADS" ] && EFFECTIVE_CMD="$COMMAND"$'\n'"$INNER_PAYLOADS"

# ── Protected branch list ────────────────────────────────────────────────
DEFAULT_BRANCHES="main,master"
if GIT_DEFAULT=$(git config --get init.defaultBranch 2>/dev/null) && [ -n "$GIT_DEFAULT" ]; then
  DEFAULT_BRANCHES="$DEFAULT_BRANCHES,$GIT_DEFAULT"
fi
PROTECTED_BRANCHES="${CLAUDE_PROTECTED_BRANCHES:-$DEFAULT_BRANCHES}"
# Build a regex alternation: main|master|develop|...
# Each branch name is regex-escaped before being placed in the alternation
# so branch names containing metacharacters (`.`, `+`, `/`, `[`, `(`, etc.)
# don't cause false matches or bypass the guard. The set of chars we
# escape is the BRE/ERE metaclass: \ . [ ] ( ) { } * + ? | ^ $ /
BR_REGEX=$(printf '%s' "$PROTECTED_BRANCHES" | tr ',' '\n' | awk '
  NF {
    s = $0
    gsub(/[][\\.^$*+?(){}|\/]/, "\\\\&", s)
    printf "%s%s", sep, s
    sep = "|"
  }
')

# `--` ends grep's option parsing — without it, a pattern that ever begins
# with `-` (e.g. a future protected-branch name starting with `-`, or a
# regex fragment that happens to start with `-`) would be misread as a
# flag and the check would fail open. Belt-and-braces against that.
contains_cmd() { printf '%s' "$EFFECTIVE_CMD" | grep -qE -- "$1"; }
contains_icmd() { printf '%s' "$EFFECTIVE_CMD" | grep -qiE -- "$1"; }

# ── Git push protections ────────────────────────────────────────────────
# Outer guard: detect `git push` even when preceded by common shell prefixes
# like environment-variable assignments (`FOO=1 git push`), `env FOO=1 git push`,
# or wrappers (`command`, `exec`, `sudo`, `nice`, `time`). Each prefix token is
# either a wrapper word, an env-var assignment, or a single-flag token (e.g.
# `sudo -E`). This stops a malicious or sloppy `env FOO=1 git push origin main`
# from sliding past the protected-branch checks.
if contains_cmd '(^|[;&|()]+[[:space:]]*)((env|command|exec|sudo|nice|time|bash|sh|zsh|ksh|dash|fish)([[:space:]]+|$)|[A-Za-z_][A-Za-z0-9_]*=("[^"]*"|[^[:space:]]*)[[:space:]]+|-[^[:space:]]+[[:space:]]+)*git([[:space:]]+[^[:space:]]+)*[[:space:]]+push'; then
  # Explicit refspec to a protected branch:
  #   `git push origin main`
  #   `git push origin :main`         (delete-ref form)
  #   `git push origin HEAD:main`     (src:dst form)
  #   `git push origin refs/heads/main`         (full destination ref)
  #   `git push origin HEAD:refs/heads/main`    (src:full-dst)
  #   `git push -u origin main`       (push options before remote)
  #   `git push --force-with-lease origin main`
  # `([[:space:]]+[^[:space:]]+)*[[:space:]]+` accepts zero or more
  # intermediate tokens (flags / options / remote) before the branch token,
  # which makes the rule robust against `git push [<options>] [<remote>]
  # <protected>` forms. The previous version assumed the token directly
  # after `push` was always the remote and let `-u origin main` through.
  if contains_cmd "git([[:space:]]+[^[:space:]]+)*[[:space:]]+push([[:space:]]+[^[:space:]]+)*[[:space:]]+([^[:space:]]*:)?(refs/heads/)?($BR_REGEX)(\$|[[:space:]])"; then
    MATCHED_BRANCH=$(printf '%s' "$EFFECTIVE_CMD" | grep -oE -- "($BR_REGEX)(\$|[[:space:]])" | head -1 | tr -d '[:space:]')
    emit_deny "Blocked: push to protected branch '${MATCHED_BRANCH:-main}'. Use a feature branch and open a PR."
  fi
  if contains_cmd "git([[:space:]]+[^[:space:]]+)*[[:space:]]+push.*:(refs/heads/)?($BR_REGEX)(\$|[[:space:]])"; then
    MATCHED_BRANCH=$(printf '%s' "$EFFECTIVE_CMD" | grep -oE -- "($BR_REGEX)(\$|[[:space:]])" | head -1 | tr -d '[:space:]')
    emit_deny "Blocked: push to protected branch '${MATCHED_BRANCH:-main}' via refspec. Use a feature branch and open a PR."
  fi
  # Dynamic refspecs — `$VAR`, `${VAR}`, `$(cmd)`, or `\`cmd\`` — resolve
  # to whatever the shell sets at exec time, so they can target a protected
  # branch without any literal `main`/`master` token in the command string.
  # Conservatively deny any `git push` whose post-`push` argv contains a
  # shell expansion (matches the same posture as the rm guard, which
  # denies unresolved `$VAR` / `${VAR}` paths).
  if contains_cmd 'git([[:space:]]+[^[:space:]]+)*[[:space:]]+push([[:space:]]+[^[:space:]]+)*[[:space:]]+[^[:space:]]*(\$|`)'; then
    emit_deny "Blocked: 'git push' with a shell-expanded refspec (\$VAR, \${VAR}, \$(...), or backtick). Use a literal branch name so the protected-branch guard can verify the target."
  fi
  # Bare `git push` while on a protected branch — covers `git push`,
  # `git push <remote>`, `git push <flags...>`, `git push <flags...> <remote>`
  # (no refspec). All of these default to pushing the current branch, so if
  # the current branch is protected the push is blocked.
  if contains_cmd 'git([[:space:]]+[^[:space:]]+)*[[:space:]]+push(([[:space:]]+-[^[:space:]]+)*([[:space:]]+[^[:space:]-][^[:space:]]*)?)?[[:space:]]*($|[;&|])'; then
    CURRENT=$(git branch --show-current 2>/dev/null || true)
    if [ -n "$CURRENT" ] && printf '%s' ",$PROTECTED_BRANCHES," | grep -q ",$CURRENT,"; then
      emit_deny "Blocked: you are on '$CURRENT' (a protected branch); a bare push would target it. Switch to a feature branch."
    fi
  fi
  # Force push (but allow --force-with-lease)
  if contains_cmd 'git([[:space:]]+[^[:space:]]+)*[[:space:]]+push([[:space:]]+[^[:space:]]+)*[[:space:]]+(-[a-zA-Z]*f[a-zA-Z]*|--force)([[:space:]=]|$)' \
     && ! contains_cmd '\-\-force-with-lease'; then
    emit_deny "Blocked: force push is not allowed. Use --force-with-lease if you must overwrite remote."
  fi
  # --all and --mirror push every local branch (including protected ones)
  # to the remote in one shot — neither names a specific branch, so the
  # explicit-refspec check above can't catch them. Treat both as
  # unconditionally dangerous; the operator can run them manually outside
  # the agent session if truly needed.
  # (`--delete <protected>` is already covered by the explicit-refspec
  # check above, since the protected branch name appears at the end of
  # the command and the leading-tokens alternation matches `--delete origin`
  # as intermediate tokens.)
  if contains_cmd 'git([[:space:]]+[^[:space:]]+)*[[:space:]]+push([[:space:]]+[^[:space:]]+)*[[:space:]]+--all([[:space:]=]|$)'; then
    emit_deny "Blocked: 'git push --all' would push every local branch (including protected ones). Push a specific feature branch instead."
  fi
  if contains_cmd 'git([[:space:]]+[^[:space:]]+)*[[:space:]]+push([[:space:]]+[^[:space:]]+)*[[:space:]]+--mirror([[:space:]=]|$)'; then
    emit_deny "Blocked: 'git push --mirror' mirrors every local ref to the remote (including protected branches and tags). Run manually if intended."
  fi
fi

# ── Destructive filesystem operations ───────────────────────────────────
# rm dangerous-target detection. Operate on $EFFECTIVE_CMD (which has any
# `<shell> -c <payload>` payloads appended as `;`-separated synthetic
# segments). Within each shell-separator-split segment, only treat `rm`
# as a command if it's the FIRST non-space token of the segment — that
# way a literal argument to `echo`, `rg`, `printf`, etc. (e.g.
# `echo rm -rf /`, `rg "rm -rf /"`) isn't mistaken for an invocation.
# Each rm segment must satisfy: recursion flag AND force flag AND a
# dangerous target. Quote characters are stripped within the segment
# before path matching so `rm -rf "/etc/foo"` (quoted system dir) still
# trips the system-dir rule.
while IFS= read -r rm_seg || [[ -n "$rm_seg" ]]; do
  [[ -z "$rm_seg" ]] && continue
  printf '%s' "$rm_seg" | grep -qE '(^|[[:space:]])(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)([[:space:]=]|$)' || continue
  printf '%s' "$rm_seg" | grep -qE '(^|[[:space:]])(-[a-zA-Z]*f[a-zA-Z]*|--force)([[:space:]=]|$)' || continue
  rm_seg_nq=$(printf '%s' "$rm_seg" | tr -d "'\"")
  if printf '%s' "$rm_seg_nq" | grep -qE '(^|[[:space:]])(--[[:space:]]+)?(/([[:space:]]|\*|$)|~|\$(\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)|\.\./\.\.)' ; then
    emit_deny "Blocked: recursive force-delete on /, ~, \$HOME / \${HOME}, an unresolved \$VAR / \${VAR}, or .../.. path. Specify a concrete safe target."
  fi
  if printf '%s' "$rm_seg_nq" | grep -qE '(^|[[:space:]])(--[[:space:]]+)?/(usr|etc|var|bin|sbin|lib|opt|root|boot)([[:space:]/]|$)'; then
    emit_deny "Blocked: recursive delete targeting a system directory."
  fi
done < <(
  # Portability note: BSD/macOS awk treats `RS` as a literal string,
  # not a regex/character class, so `RS = "[;&|()\n]"` doesn't actually
  # split on those characters on Mac. Use `tr` to convert every shell
  # separator (`;`, `&`, `|`, `(`, `)`) to a newline first, then let awk
  # use its default newline RS. This is portable across GNU/BSD awk.
  #
  # Within each resulting segment, treat `rm` as a real rm invocation
  # only if it is the FIRST non-space token. Wrapped bypasses like
  # `bash -lc rm -rf /` arrive here via $EFFECTIVE_CMD as a second
  # segment whose first non-space token IS `rm` (the wrapper was
  # extracted upstream). Plain-text mentions like `echo rm -rf /` or
  # `rg "rm -rf /"` do not qualify because their first non-space token
  # is `echo`/`rg`, not `rm`.
  printf '%s' "$EFFECTIVE_CMD" | tr ';&|()' '\n' | awk '/^[[:space:]]*rm[[:space:]]/ { print }'
)

# ── Dangerous database operations ───────────────────────────────────────
# Walk $EFFECTIVE_CMD segment-by-segment so we can skip segments whose
# first non-space token is a print/search command (echo, printf, cat, rg,
# grep, fgrep, egrep, less, more, tail, head). These commands take their
# arguments as plain text, not as SQL to execute — without this skip,
# `echo "DROP TABLE users"` or `rg "DELETE FROM" .` would trip the keyword
# substring matches as false positives.
PRINT_SEARCH_RE='^[[:space:]]*(echo|printf|cat|rg|grep|fgrep|egrep|less|more|tail|head)([[:space:]]|$)'

while IFS= read -r sql_seg || [[ -n "$sql_seg" ]]; do
  [[ -z "$sql_seg" ]] && continue
  # Skip segments that start with a print/search tool — anything after is
  # textual argument, not executed SQL.
  printf '%s' "$sql_seg" | grep -qE -- "$PRINT_SEARCH_RE" && continue
  # DROP TABLE|DATABASE|SCHEMA
  if printf '%s' "$sql_seg" | grep -qiE 'DROP[[:space:]]+(TABLE|DATABASE|SCHEMA)[[:space:]]+'; then
    emit_deny "Blocked: DROP TABLE/DATABASE/SCHEMA detected. Run manually if intended."
  fi
  # TRUNCATE TABLE
  if printf '%s' "$sql_seg" | grep -qiE 'TRUNCATE[[:space:]]+TABLE'; then
    emit_deny "Blocked: TRUNCATE TABLE detected. Run manually if intended."
  fi
  # DELETE FROM without WHERE on the same statement.
  # Strip SQL comments before testing for WHERE so commented-out predicates
  # (`DELETE FROM t -- WHERE id=1`) don't smuggle a fake-WHERE past the guard.
  # Use `toupper()` for case-insensitive matching — `IGNORECASE=1` is a
  # GNU-awk extension and is silently ignored by POSIX/BSD awk, which would
  # let lowercase `delete from …` slip past on a Mac dev machine.
  if printf '%s\n' "$sql_seg" | awk '
    BEGIN { RS=";" }
    {
      upper = toupper($0)
      if (upper ~ /DELETE[[:space:]]+FROM[[:space:]]+[A-Z_][A-Z0-9_.]*/) {
        s = upper
        gsub(/--[^\n]*/, "", s)
        while (match(s, /\/\*[^*]*\*+([^/*][^*]*\*+)*\//)) {
          s = substr(s, 1, RSTART - 1) substr(s, RSTART + RLENGTH)
        }
        if (s !~ /WHERE/) { print "BAD"; exit }
      }
    }
  ' | grep -q BAD; then
    emit_deny "Blocked: DELETE FROM without a WHERE clause. Add a WHERE or run manually."
  fi
done < <(printf '%s' "$EFFECTIVE_CMD" | tr ';&|()' '\n')

# ── git commit safety: block hook/signing bypass flags ──────────────────
# CLAUDE.md prohibits `--no-verify` (skips pre-commit hooks), `--no-gpg-sign`
# (bypasses commit signing), and `-c commit.gpgsign=false` (same). The
# pre-commit hooks installed by this repo (secret scanning, file
# protection) are bypassed silently if an agent appends `--no-verify`, so
# the rule needs a hard hook block — not just a CLAUDE.md note.
#
# Use the same segment walker the SQL guards use, so a quoted message
# like `git commit -m "fix --no-verify thing"` is not false-positived:
#   - skip segments whose first non-space token is a print/search tool;
#   - within a non-skipped segment, require both a `git ... commit` token
#     AND one of the prohibited flags as its own token (leading whitespace
#     anchor) so the flag has to be a real argument, not part of a quoted
#     commit message.
# Pre-process $EFFECTIVE_CMD BEFORE splitting on shell separators: replace
# the contents of every `-m` / `--message` / `-F` / `--file` argument with
# a placeholder `M`. Without this, a shell separator (`;`, `&`, `|`, `(`,
# `)`) embedded in a quoted commit message would artificially split the
# segment and stop `git commit` and `--no-verify` from co-occurring in any
# fragment — exactly the bypass `git commit -m "fix; note" --no-verify`
# exploits. With messages collapsed to a placeholder first, the segment
# walker sees only real shell-level separators.
COMMIT_CMD=$(printf '%s' "$EFFECTIVE_CMD" | sed -E '
  # All the double-quoted forms use ([^"\\]|\\.)* rather than [^"]* to
  # honour backslash-escaped characters (notably \") the same way bash
  # does inside "...". Without this, a message like "fix \" & note"
  # gets only partially masked, leaving the shell separator behind it
  # visible to the later tr-based shell-separator split.
  #
  # --message="quoted" / --file="quoted"
  s/(-m|-F|--message|--file)="([^"\\]|\\.)*"/\1=M/g
  s/(-m|-F|--message|--file)='\''[^'\'']*'\''/\1=M/g
  # -m"attached" / -F"attached" (short form, no space before quote)
  s/(-m|-F)"([^"\\]|\\.)*"/\1 M/g
  s/(-m|-F)'\''[^'\'']*'\''/\1 M/g
  # -m "spaced" / --message "spaced" (space then quoted)
  s/(-m|-F|--message|--file)[[:space:]]+"([^"\\]|\\.)*"/\1 M/g
  s/(-m|-F|--message|--file)[[:space:]]+'\''[^'\'']*'\''/\1 M/g
  # -m=unquoted / --message=unquoted (must come AFTER the quoted-= forms
  # so the quoted alternation matches first, not the bare =).
  s/(-m|-F|--message|--file)=[^[:space:]]*/\1=M/g
  # -m unquoted / --message unquoted (space, then non-flag token).
  s/(-m|-F|--message|--file)[[:space:]]+[^[:space:]-][^[:space:]]*/\1 M/g
')

while IFS= read -r commit_seg || [[ -n "$commit_seg" ]]; do
  [[ -z "$commit_seg" ]] && continue
  printf '%s' "$commit_seg" | grep -qE -- "$PRINT_SEARCH_RE" && continue
  printf '%s' "$commit_seg" | grep -qE 'git([[:space:]]+[^[:space:]]+)*[[:space:]]+commit([[:space:]]|$)' || continue
  # Drop the remaining quote characters so positional argv elements
  # (which the shell passes literally to git after stripping quotes)
  # match the same way git sees them. Without this, `git commit -m x
  # "--no-verify"` and `git -c "commit.gpgsign=false" commit` slip the
  # boundary check — bash hands `--no-verify` and `commit.gpgsign=false`
  # to the process as real argv elements, but our regex would still see
  # the leading `"` and refuse to match. Quoting is transparent to the
  # receiving process, so it should be transparent to our scan too.
  seg_argv=$(printf '%s' "$commit_seg" | tr -d "'\"")
  # Prohibited tokens:
  #   --no-verify (and any --no-v* abbreviation — git accepts unambiguous
  #     long-option prefixes; for `git commit` no other --no-v* exists)
  #   --no-gpg-sign (and any --no-g* abbreviation, same reasoning)
  #   -n / combined short-flag bundles containing n (`-an`, `-Sn`, …)
  #   -c <key>=<falsy>  for commit.gpgsign — git-config booleans: false,
  #     no, off, 0, empty string; key is case-insensitive (-i flag below)
  # `--config-env=<key>=<envvar>` reads the value from $envvar at exec time
  # (per `git -h`), so it bypasses the literal-value scan for
  # `-c commit.gpgsign=<falsy>`. Conservatively deny any
  # `--config-env=commit.gpgsign=…` — we can't see the env value at hook
  # time, and there's no legitimate reason for an agent to override that
  # specific key via the env-indirection form.
  if printf '%s' "$seg_argv" | grep -qiE '(^|[[:space:]])(--no-verify|--no-v[a-z-]*|--no-gpg-sign|--no-g[a-z-]*|-[A-Za-z]*n[A-Za-z]*|-c[[:space:]]+commit\.gpgsign=(false|no|off|0|)([[:space:]=]|$)|--config-env=commit\.gpgsign=[^[:space:]]*)'; then
    emit_deny "Blocked: 'git commit' with --no-verify (or its abbreviations: -n, --no-v…), --no-gpg-sign (or --no-g…), -c commit.gpgsign=<falsy>, or --config-env=commit.gpgsign=<envvar>. CLAUDE.md prohibits skipping hooks or bypassing signing — fix the underlying hook failure instead."
  fi
  # Shell expansion ANYWHERE in commit argv (e.g. `git commit $NV -m x`
  # where NV=-n, `git commit $(printf %s --no-verify)`, or the embedded
  # form `git commit x$NV -m msg` where $NV is ` --no-verify` and bash
  # word-splits at exec time). The literal text `$VAR` / `$(...)` / `…$VAR…`
  # resolves only at exec time, so the prohibited-flag scan above can't
  # see it. Match `$` or backtick anywhere in the (message-stripped)
  # commit argv — same conservative posture the `git push` guard already
  # takes for dynamic refspecs. After the upstream message-strip masked
  # `-m "$(date)"` to `-m M`, the surviving `$`/backtick characters are
  # real argv-level expansions and not message content.
  if printf '%s' "$seg_argv" | grep -qE '(\$|`)'; then
    emit_deny "Blocked: 'git commit' with a shell-expanded argv element (\$VAR, \${VAR}, \$(...), \`cmd\`, or any embedded form like x\$VAR). Bash resolves these at exec time and may word-split them into real flag arguments, so the hook can't verify what reaches git — use literal arguments instead, or split the substitution into a separate \`var=\$(...)\` step then pass an unambiguous literal."
  fi
done < <(printf '%s' "$COMMIT_CMD" | tr ';&|()' '\n')

# ── Dangerous system commands ───────────────────────────────────────────
# chmod: any world-writable/universal mode (0?777 or a+rwx)
if contains_cmd 'chmod([[:space:]]+-[a-zA-Z]+)*[[:space:]]+0?777([[:space:]]|$)' \
  || contains_cmd 'chmod([[:space:]]+-[a-zA-Z]+)*[[:space:]]+a\+rwx([[:space:]]|$)'; then
  emit_deny "Blocked: chmod 777 / a+rwx grants everyone full access. Use restrictive perms."
fi

# curl/wget piped to a shell
if contains_cmd '(curl|wget)[[:space:]].*\|[[:space:]]*(sudo[[:space:]]+)?(bash|sh|zsh|ksh|fish|dash|csh)([[:space:]]|$)'; then
  emit_deny "Blocked: piping downloaded content directly to a shell is dangerous."
fi

# Disk / partition. Note: only REDIRECTIONS to /dev/ are destructive. `2>/dev/null` is not.
# Pattern matches: `>[ ]*/dev/<something>` but NOT `2>/dev/null` or `&>/dev/null` style for fd-null.
# Strategy: match `>` optionally with whitespace, followed by /dev/<name>, EXCLUDING /dev/null and /dev/stderr/stdout.
if printf '%s' "$EFFECTIVE_CMD" | grep -qE '(^|[^0-9&])>[[:space:]]*/dev/[a-zA-Z][a-zA-Z0-9]*' \
   && ! printf '%s' "$EFFECTIVE_CMD" | grep -qE '>[[:space:]]*/dev/(null|stdout|stderr|tty|zero|random|urandom)([[:space:]]|$)' ; then
  emit_deny "Blocked: redirection into a raw device file can destroy data."
fi
if contains_cmd '(^|[;&|[:space:]])(mkfs|mkfs\.[a-z0-9]+)([[:space:]]|$)' \
  || contains_cmd '(^|[;&|[:space:]])dd[[:space:]]+[^|]*(if|of)=/dev/[a-zA-Z]' ; then
  emit_deny "Blocked: mkfs/dd against a device node. Irreversible data loss."
fi

# ── Destructive git ─────────────────────────────────────────────────────
if contains_cmd 'git[[:space:]]+reset[[:space:]]+--hard'; then
  emit_deny "Blocked: git reset --hard discards uncommitted changes permanently."
fi
# Catch both short `-f` (possibly bundled with other letters like `-fd`)
# and long `--force` forms; `git clean -h` documents `-f, --[no-]force`,
# and either form deletes untracked files just as permanently. The
# leading `git([[:space:]]+[^[:space:]]+)*[[:space:]]+clean` mirrors the
# pattern used for `git push` and accepts any number of git-global
# options between `git` and the `clean` subcommand
# (e.g. `git --no-pager clean -f`, `git -c key=value clean --force`).
if contains_cmd 'git([[:space:]]+[^[:space:]]+)*[[:space:]]+clean([[:space:]]+[^[:space:]]+)*[[:space:]]+(-[a-zA-Z]*f([a-zA-Z]*([[:space:]]|$))|--force([[:space:]=]|$))'; then
  emit_deny "Blocked: git clean -f permanently deletes untracked files."
fi

# ── Accidental package publishing ───────────────────────────────────────
# Allow --dry-run variants (npm publish --dry-run is safe and common in CI).
publish_patterns=(
  '(npm|yarn|pnpm|bun)[[:space:]]+publish'
  'cargo[[:space:]]+publish'
  'gem[[:space:]]+push'
  'twine[[:space:]]+upload'
)
for pat in "${publish_patterns[@]}"; do
  if contains_cmd "$pat" && ! contains_cmd '(^|[[:space:]])(--dry-run|-n)([[:space:]=]|$)'; then
    emit_deny "Blocked: publishing packages should run in CI or manually, not via Claude."
  fi
done

exit 0
