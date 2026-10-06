# Phase 14: GitHub integration

Phase 14 adds a GitHub tool surface controlled by DeepSeek and available to Qwen2.5-Coder:7b through the existing execution layer.

## Tools

- `github_auth_status` — checks authenticated `gh` CLI state without exposing credentials
- `github_list_repositories` — lists repositories available to the authenticated account
- `github_list_branches` — lists branches for a selected `OWNER/REPOSITORY`
- `github_diff` — shows changed files, unstaged diff, and staged diff
- `github_create_commit` — creates a local commit from an explicit file list and message
- `github_push` — pushes the selected branch after `push_to_github` approval
- `github_retry_push` — retries a failed approved push

## Required sequence

```text
DeepSeek selects a coding objective
  ↓
Qwen2.5-Coder:7b edits and tests the local project
  ↓
DeepSeek requests GitHub authentication status
  ↓
DeepSeek selects repository and branch
  ↓
DeepSeek displays changed files and diff
  ↓
DeepSeek creates a local commit from explicitly selected files
  ↓
DeepSeek asks for push_to_github approval
  ↓
Execution layer blocks github_push until approval
  ↓
Approved push runs through the GitHub tool
  ↓
Commit SHA and URL return to DeepSeek
  ↓
DeepSeek reports the result
```

Qwen2.5-Coder:7b does not bypass DeepSeek. GitHub commands are emitted as DeepSeek protocol commands and executed only by the controlled executor.

## Authentication

The integration uses the authenticated GitHub CLI (`gh`). Authentication status is checked with `gh auth status`; credentials and tokens are never included in model inputs or outputs. On a local laptop, authenticate separately with the GitHub CLI before using repository tools.

## Push failure recovery

A failed push returns:

- `pushed: false`
- `status: failed`
- Sanitized error text
- `retryable: true`
- Recovery guidance for authentication, remote, branch protection, or network issues

DeepSeek can review the failure, ask for missing user input if needed, and retry with `github_retry_push`. The retry remains approval-gated and must not silently reuse approval for a different target.

## Security boundary

- Repository names must use `OWNER/REPOSITORY` format.
- Branches, remotes, and refs are validated.
- Commit file paths remain inside the selected project.
- Commit creation is local and does not authorize a remote push.
- Push tools carry `push_to_github` approval metadata.
- Commit URLs are reported only after a successful push result.
- No GitHub operation is performed by a model adapter directly.
