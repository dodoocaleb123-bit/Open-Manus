# Phase 13: approvals and secure actions

Phase 13 adds a centralized approval gate for external, destructive, sensitive, and irreversible operations.

## Protected actions

DeepSeek must request approval before:

- Pushing to GitHub
- Sending messages externally
- Publishing or deploying a website
- Modifying a connected service
- Deleting files or user data
- Using or changing sensitive secrets
- Making an irreversible change

The controller protocol accepts these explicit action names:

```text
push_to_github
send_external_message
publish_or_deploy
modify_connected_service
delete_user_data
use_or_change_sensitive_secrets
make_irreversible_change
```

## Secure execution sequence

```text
DeepSeek reviews the proposed result
  ↓
DeepSeek shows preview, changed files, target, and impact
  ↓
DeepSeek requests approval for the exact action
  ↓
Execution layer moves the task to waiting_for_approval
  ↓
The secure tool cannot run before approval
  ↓
User approves or rejects
  ↓
The exact pending command is resumed once
  ↓
Result and approval are persisted in activity history
  ↓
DeepSeek reports success or failure
```

For GitHub, the intended sequence is:

1. `secure_preview_changes` shows repository state and diff summary.
2. DeepSeek asks for `push_to_github` approval.
3. `secure_github_push` is blocked by the executor until approval.
4. After approval, the exact Git push command resumes.
5. DeepSeek can delegate supporting implementation work to Qwen2.5-Coder:7b, but the push remains protected by the execution gate.

## Secure tools

- `secure_preview_changes` — no approval required; read-only preview
- `secure_github_push` — `push_to_github`
- `secure_send_message` — `send_external_message`
- `secure_publish` — `publish_or_deploy`
- `secure_modify_service` — `modify_connected_service`
- `secure_delete_files` — `delete_user_data`
- `secure_use_secret` — `use_or_change_sensitive_secrets`
- `secure_irreversible_change` — `make_irreversible_change`

The external message, publish, service, secret, and irreversible handlers fail honestly when their connectors are not configured. They do not pretend that an external action occurred.

## Guardrails

- Approval is tied to the exact pending task and action.
- Approval is not reused for a different operation.
- Git remotes and branches are validated.
- Git execution does not use a shell.
- File deletion is confined to the coding project workspace.
- Secret values are never accepted as task arguments or echoed in results.
- Rejections cancel the pending task and are recorded.
- Approval, rejection, command, and result activity are persisted.
