# Phase 17: security and permissions

Phase 17 hardens the existing DeepSeek-first execution boundary without adding a second planner.

## Implemented controls

- **Authentication:** optional bearer-token authentication through `OPENMANUS_AUTH_TOKEN`; local development remains explicitly single-user when unset.
- **Project permissions:** owner/editor/viewer ACLs and permission checks for project operations.
- **Sandboxed commands:** coding commands remain executable-only, allowlisted, project-scoped, shell-disabled, and run with a restricted environment and resource limits.
- **File access restrictions:** project names and relative paths reject traversal and symlink escapes.
- **Safe-secret panel:** `SecretStore` encrypts values at rest with Fernet; normal task context and audit records contain only opaque references and masked metadata.
- **Tool permissions:** tools can declare a permission such as `project.write`, `tool.execute`, or `secret.use`; the executor checks it before dispatch.
- **Audit logs:** SQLite stores redacted security events, approvals, command decisions, and outcomes.
- **Rate limits:** the HTTP API has a configurable fixed-window request limiter.
- **Approval records:** approvals and rejections are persisted with actor, action, and timestamp.
- **Cancellation and recovery:** existing cancellation/retry paths are retained and now produce audit entries.

## Configuration

```dotenv
OPENMANUS_AUTH_TOKEN=
OPENMANUS_AUTH_PERMISSIONS=task.read,task.write,project.read,project.write,tool.execute,secret.use
OPENMANUS_SECRET_KEY=
OPENMANUS_RATE_LIMIT=120
OPENMANUS_RATE_WINDOW_SECONDS=60
OPENMANUS_COMMAND_CPU_SECONDS=120
OPENMANUS_COMMAND_MEMORY_MB=1024
```

`OPENMANUS_SECRET_KEY` must be set before creating or resolving secrets. Do not commit it or place it in model prompts.

## Security invariant

```text
User/event -> authenticated principal -> DeepSeek -> permissioned executor -> named tool/model
```

The security layer authorizes and records DeepSeek’s decisions; it never chooses a specialist or interprets user intent.
