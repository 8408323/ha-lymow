---
agent: 'agent'
description: "Scan changes, commit, push, and create a PR. With confirmation at each step"
---
<!-- dotclaude:managed — generated from the dotclaude ship skill by /dotclaude:init. Edit the source in the dotclaude repo, not this file. -->

Ship the current changes through commit, push, and PR creation. Confirm with the user before each step using the an interactive confirmation.

## Step 1: Scan

- Run `git status` to see all changed, staged, and untracked files
- Run `git diff` to see what changed (staged + unstaged)
- Run `git log --oneline -5` to see recent commit style
- Present a clear summary to the user:
  - Files modified
  - Files added
  - Files deleted
  - Untracked files
- If there are no changes, tell the user and stop

## Step 2: Stage & Commit

- Propose which files to stage. **Never stage** these:
  - Secrets: `.env*`, `*.pem`, `*.key`, `credentials.json`
  - Lock files: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml` (unless intentionally updated)
  - Generated: `*.gen.ts`, `*.generated.*`, `*.min.js`, `*.min.css`
  - Build output: `dist/`, `build/`, `.next/`, `__pycache__/`
  - Dependencies: `node_modules/`, `vendor/`, `.venv/`
  - OS/editor: `.DS_Store`, `Thumbs.db`, `*.swp`, `.idea/`, `.vscode/settings.json`
- Draft a commit message based on the changes, matching the repo's existing commit style
- **ASK the user to confirm or edit**: show the exact files to stage and the proposed commit message
- Only after confirmation: stage the files and create the commit
- If the commit fails (e.g., pre-commit hook), fix the issue and try again with a NEW commit

## Step 3: Push

- Check if the current branch has an upstream remote
- If not, propose creating one with `git push -u origin <branch>`
- **ASK the user to confirm** before pushing
- Only after confirmation: push to remote

## Step 4: Pull Request

- Check if a PR already exists for this branch (`gh pr view`. If it exists, show the URL and stop)
- Analyze ALL commits on this branch vs the base branch (not just the latest commit)
- Draft a PR title (under 72 chars) and body with:
  - Summary: 2-4 bullet points
  - Test plan: how to verify
- **ASK the user to confirm or edit** the title and body
- Only after confirmation: create the PR with `gh pr create`
- Show the PR URL when done

## Step 5: Review & merge discipline

Follow the **PR Review Discipline** rule, and run this watch-and-iterate loop until the PR is clean. Do not merge an unreviewed PR.

Once the PR is open (the automatic review and any GitHub review apps post the first round):

1. **Poll for review activity.** `gh` has no watch mode for PR comments/reviews (only `gh pr checks --watch` for CI and `gh run watch` for Actions runs), so this is a sleep-and-poll loop: check, and if nothing new, sleep ~2 min and check again. Act the moment new comments land; don't sit out the interval if they're already there.
   ```
   gh pr view <n> --json reviews,comments,reviewDecision
   gh api repos/{owner}/{repo}/pulls/<n>/comments   # inline review threads
   ```
   Detect "new" by comparing the comment/review count or the latest `createdAt` against the previous poll (or use the REST `since` parameter). Keep polling up to ~10 minutes for a round to arrive (AI reviews can take several minutes). If a reviewer still hasn't posted after that, re-ping it or tell the user — don't block indefinitely, and don't treat the silence as approval.
2. **When comments arrive, address each one:**
   - Implement the change, **or** deliberately decline it with a short reply explaining why.
   - **Resolve the thread** for each comment you've handled (`gh api graphql` → `resolveReviewThread`).
   - **Do not resolve** a thread that asks a question or requests more information — reply with the info and leave it open.
3. **Push the fixes**, then re-request review in one comment mentioning every reviewer in use:
   ```
   gh pr comment <n> --body "Addressed the feedback — @claude review, @codex review, @codex[agent] review, @copilot review"
   ```
4. **Poll again the same way** (~2 min apart, up to ~10 min for the round). Iterate steps 1–4 until **every** reviewer explicitly states it has no more comments. For the CI half of the gate you *can* block on a real watch: `gh pr checks <n> --watch --fail-fast`. Silence is not sign-off — keep iterating, don't merge.
5. Merge only after the loop is clean **and** the user explicitly confirms.

## Rules

- NEVER skip a confirmation step. Each step requires explicit user approval
- NEVER force-push
- NEVER commit .env, secrets, or credential files
- If the user says "skip" at any step, skip that step and move to the next
- If ${input:args} is provided, use it as the commit message / PR title
