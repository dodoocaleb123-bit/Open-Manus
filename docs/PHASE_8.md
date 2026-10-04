# Phase 8: API and GUI

Phase 8 is the local application surface for Open-Manus: a standard-library HTTP API and a browser GUI backed by the Phase 7 SQLite store. DeepSeek remains the only primary assistant visible in the conversation.

## Local architecture

```text
Browser GUI
    ↓ JSON HTTP / multipart upload
Python local API (`backend/api`)
    ↓
DeepSeekConversationLoop
    ↓
ControlledExecutor + SQLiteStateStore
    ↓
Ollama-compatible DeepSeek and specialist adapters
```

## Start the local application

```bash
cp .env.example .env
python3 scripts/run_local.py
```

Open <http://127.0.0.1:8000>. `OPENMANUS_HOST`, `OPENMANUS_PORT`, and `OPENMANUS_STATE_DB` configure the local server. The default bind address is loopback-only for laptop safety.

## GUI surfaces

### Left sidebar

- New Task
- Agents
- Skills
- Plugins
- Scheduled Tasks
- Library
- Projects
- Recent Tasks

The Agent catalog identifies DeepSeek as the primary controller and lists Qwen research, Qwen coder, Gemma vision, and Llama creative as specialist roles. Specialists are not separate chatbots.

### Main conversation

The conversation renders DeepSeek messages, execution updates, errors, retry controls, approval prompts, and generated output. Specialist work appears as status cards such as `research is working` with its objective and recorded activity. Assistant responses containing URLs receive a source/citation indicator, while execution results and errors remain visible in the transcript.

### Composer and task controls

- Text messages
- Browser file picker with real multipart upload to the local workspace
- Send
- Cancel
- Retry last failed command
- Approval and user-input continuation controls
- Local model/service status through the health endpoint

Uploaded files are saved under the configured workspace in `attachments/<task_id>/` and recorded in SQLite.

### Workspace panel

The task inspector includes tabs for:

- Preview
- Code
- Browser
- Files
- Logs
- Settings

Preview, Code, and Browser provide the local workspace surfaces and clear integration states; Files lists registered attachments and artifacts, Logs exposes persisted activity, and Settings shows the active workspace, persistence mode, and controller boundary.

## API routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Check all configured model adapters |
| `GET` | `/api/catalog` | Read agents, skills, plugins, schedules, projects, library, and services |
| `GET` | `/api/tasks` | List persisted recent tasks |
| `POST` | `/api/tasks` | Start a DeepSeek conversation |
| `GET` | `/api/tasks/:id` | Read state, messages, events, activity, files, and context |
| `POST` | `/api/tasks/:id/input` | Continue a task waiting for user input |
| `POST` | `/api/tasks/:id/approve` | Approve a pending action and continue |
| `POST` | `/api/tasks/:id/retry` | Retry the most recent failed command |
| `POST` | `/api/tasks/:id/cancel` | Cancel a task |
| `POST` | `/api/tasks/:id/attachments` | Upload a multipart file or register an authorized local path |
| `POST` | `/api/tasks/:id/artifacts` | Register a generated artifact path and metadata |

## Boundary and remaining integrations

The GUI is complete as the Phase 8 local control-room surface. Scheduled-task execution, remote authentication, full browser automation, live code editing, and project preview servers remain integration phases rather than hidden GUI claims. The GUI displays their status honestly instead of pretending those backends are already connected.
