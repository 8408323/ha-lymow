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

Follow the **PR Review Discipline** rule. The critical point: a green, "clean"/mergeable PR is **not** ready to merge if a reviewer is still running — Copilot/Codex can be mid-review with no comments posted yet. Wait for the round to *finish*, not just for checks to pass.

**Batching:** if you know you'll make several pushes, make them all first, *then* run this loop once on the final commit. Don't re-request review on intermediate pushes — it churns reviewers on states you're about to change.

Once the PR is open (or after the final push you want reviewed):

1. **Wait for the review round to finish.** `gh` has no watch for reviews, so poll: sleep ~2 min between checks, up to ~10 min.
   ```
   gh pr view <n> --json reviewRequests,latestReviews,reviewDecision
   gh api repos/{owner}/{repo}/issues/<n>/timeline   # shows "review_requested" / Copilot "is reviewing"
   ```
   A reviewer still in `reviewRequests` (or shown reviewing in the timeline) is **not done** — keep waiting. The round is finished when every expected reviewer (Copilot, Codex, …) has posted its review/comments on the **current head commit** and none remain pending. Don't evaluate comments or merge until then.
   - **Timeout path (deterministic):** if ~10 min elapses and an expected reviewer still hasn't posted for the current commit, re-request that reviewer once. If it still doesn't engage after another short wait, stop polling and tell the user which reviewer is missing — ask whether to wait longer or proceed without it. Never wait indefinitely, and never merge counting a missing reviewer as approval.
2. **Address each comment:** implement it, or decline with a short reply. **Resolve the thread** for each handled comment (`gh api graphql` → `resolveReviewThread`). **Don't resolve** a thread that asks a question / for more info — reply and leave it open.
3. **Pushing fixes is a new push to review.** Resolve the fixed threads, then re-request in one comment, and go back to step 1:
   ```
   gh pr comment <n> --body "Addressed the feedback — @claude review, @codex review, @codex[agent] review, @copilot review"
   ```
4. **Merge only when** every reviewer has *finished* the latest round with no remaining comments, all threads are resolved, and CI is green (`gh pr checks <n> --watch --fail-fast`) — **and** the user explicitly confirms. Never merge while a review is in progress or on silence.

## Rules

- NEVER skip a confirmation step. Each step requires explicit user approval
- NEVER force-push
- NEVER commit .env, secrets, or credential files
- If the user says "skip" at any step, skip that step and move to the next
- If ${input:args} is provided, use it as the commit message / PR title
