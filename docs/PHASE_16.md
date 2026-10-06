# Phase 16: orchestration, scheduling, and integrations

Phase 16 adds two related capabilities without changing the DeepSeek authority boundary:

1. A read-only multi-agent collaboration dashboard.
2. Durable local schedules and a connector-neutral external integration gateway.

## Multi-agent orchestration dashboard

`backend/orchestration` derives dashboard data from persisted task activity:

- DeepSeek controller status
- Specialist status for Qwen 2.5:3b, Qwen2.5-Coder:7b, Gemma 3:4b, and Llama 3.2:3b
- Delegation edges from DeepSeek to specialists
- Current objectives and bounded result summaries
- Task status and pending approval data
- Collaboration rule shown to the UI

The dashboard is read-only. It does not plan, delegate, select models, or execute tools.

Endpoints:

```text
GET /api/dashboard
GET /api/dashboard?task_id=<task-id>
```

Tools:

```text
orchestration_overview
orchestration_task
```

Specialists remain status cards and activity summaries, not separate user-facing chatbots.

## Scheduling

`backend/automation` provides a durable local scheduler backed by SQLite.

Supported schedule types:

- `interval` with a minimum interval of 60 seconds
- `once` with a future Unix timestamp

Tools and endpoints:

```text
schedule_create
schedule_list
schedule_set_enabled

GET  /api/schedules
POST /api/schedules
POST /api/schedules/<schedule-id>/enabled
POST /api/schedules/run-due
```

A due schedule creates a normal DeepSeek task with the instruction marked as scheduled. It does not execute provider operations directly. A laptop deployment can invoke `/api/schedules/run-due` from its chosen local service runner or operating-system timer.

## External integrations

`backend/integrations` defines a connector-neutral gateway for:

- Email
- Calendar
- Slack
- Notion
- Storage providers
- Notifications
- Maps
- Commerce services

The gateway currently provides:

- Integration catalog and readiness status
- Event normalization
- Untrusted-payload labeling
- DeepSeek routing metadata
- Prepared-action descriptions
- Approval-required metadata for consequential provider operations

Tools and endpoint:

```text
integration_catalog
integration_status
integration_receive_event
integration_prepare_action

GET  /api/integrations
POST /api/integrations/<provider>/events
```

Provider flags are disabled by default in `.env.example`. A local flag means only that a connector is marked ready; it is not a substitute for provider-specific credentials or authorization.

## Event routing invariant

```text
User or event
  ↓
DeepSeek
  ↓
DeepSeek decides the required action
  ↓
Execution layer performs the authorized operation
  ↓
Result returns to DeepSeek
```

Incoming event payloads are explicitly treated as untrusted data. The event gateway never invokes a specialist, sends an external message, changes a service, or performs a commerce operation around DeepSeek.

## Current boundary

This phase implements the local contracts, durable schedule definitions, dashboard, event ingress, readiness flags, and approval metadata. It does not invent provider APIs or credentials. Real provider actions require the corresponding connector implementation and authorization before execution is enabled.
