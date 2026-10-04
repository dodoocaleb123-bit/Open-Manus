# Phase 8: API and GUI

Phase 8 adds the local application surface for Open-Manus: a standard-library HTTP API and a browser GUI backed by the Phase 7 SQLite store.

## Local architecture

```text
Browser GUI
    ↓ JSON HTTP
Python local API (`backend/api`)
    ↓
DeepSeekConversationLoop
    ↓
ControlledExecutor + SQLiteStateStore
    ↓
Ollama-compatible DeepSeek and specialist adapters
```

The API and GUI are local-first. They do not add a second planner: every task still enters DeepSeek, and the API exposes the existing controller/executor boundaries.

## Start the local application

```bash
cp .env.example .env
python3 scripts/run_local.py
```

Open <http://127.0.0.1:8000> in a browser. Use `OPENMANUS_HOST` and `OPENMANUS_PORT` to change the bind address and port. The default bind address is loopback-only for laptop safety.

The same SQLite database from Phase 7 is used by the API:

```text
./workspace/openmanus.sqlite3
```

## API routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Check the local runtime and configured model adapters |
| `GET` | `/api/tasks` | List persisted tasks |
| `POST` | `/api/tasks` | Start a DeepSeek conversation with `{ "message": "...", "context": {...} }` |
| `GET` | `/api/tasks/:id` | Read task state, messages, events, activity, attachments, and artifacts |
| `POST` | `/api/tasks/:id/input` | Continue a task waiting for user input |
| `POST` | `/api/tasks/:id/approve` | Approve a pending action and continue |
| `POST` | `/api/tasks/:id/cancel` | Cancel a task |
| `POST` | `/api/tasks/:id/attachments` | Register an authorized local attachment path |
| `POST` | `/api/tasks/:id/artifacts` | Register a generated artifact path and metadata |

## GUI behavior

The Local Control Room provides:

- New-task composer
- Persisted task list and status indicators
- DeepSeek conversation transcript
- Execution result visibility
- Approval cards with approve/cancel controls
- User-input continuation cards
- Task inspector with context, command count, and local database status
- Activity timeline
- Attachment and artifact path registration
- Responsive layout for laptop and narrow screens

The browser interface is static and has no Node build step or external asset dependency. `frontend/manus-routes.json` declares its page route for local web tooling.

## Security boundary

This phase is intended for one user on one laptop. The server defaults to `127.0.0.1`, does not implement remote authentication, and accepts local file paths only as metadata registration. Do not bind it to a public interface until a later authentication and authorization phase is implemented.
