# Phase 7: persistence and context management

Phase 7 makes the local Open-Manus runtime restart-safe. It adds a dependency-free SQLite state store and connects it to the DeepSeek conversation loop.

## Persisted data

`SQLiteStateStore` persists:

- Task state, status, pending input, pending approval, results, errors, and command history
- Every controller command and command-run result
- Conversation messages sent to DeepSeek
- Assistant decision and execution-result events
- Runtime `PlatformContext` used to rebuild the next controller request
- Controller plans/decisions and execution activity
- Specialist delegation calls and model results
- Tool activity, approval actions, user input, retry/error activity
- Attachment metadata and local file paths
- Generated artifact metadata and local file paths

The database stores metadata and paths; it does not silently copy large files into the database. Attachments and artifacts remain in the local workspace.

## Local database

The default path is:

```text
./workspace/openmanus.sqlite3
```

Configure it with `OPENMANUS_STATE_DB` in `.env` or create a store explicitly:

```python
from execution import SQLiteStateStore

store = SQLiteStateStore.from_environment()
```

SQLite uses WAL mode and a thread-safe connection suitable for the local single-user deployment target.

## Restart recovery

The conversation loop now persists every message and event. A new process can construct a new `DeepSeekConversationLoop` with a new controller and the same `SQLiteStateStore`, then continue a task by its task ID:

```python
store = SQLiteStateStore.from_environment()
loop = DeepSeekConversationLoop(controller, ControlledExecutor(registry, store=store))
result = await loop.provide_user_input(task_id, "Linux")
```

The loop reconstructs message history, event history, turn count, and runtime context from SQLite before asking DeepSeek for the next decision.

## Boundary

Persistence does not become a second planner. DeepSeek still owns planning, delegation, progress messages, approvals, and synthesis. SQLite only records and restores the state needed to continue that controller conversation safely.

## Current limitation

The database is local to the laptop and is not encrypted by this phase. Protect the workspace directory with normal operating-system permissions. Cloud sync, multi-user access, database encryption, and GUI/API exposure are later concerns.
