# Phase 6: DeepSeek conversation loop

Phase 6 connects the DeepSeek controller protocol to the controlled executor for a local end-to-end conversation cycle.

## Loop

```text
User message
  ↓
DeepSeek decides one message plus one command
  ↓
ControlledExecutor runs that exact command
  ↓
Execution result is added to conversation context
  ↓
DeepSeek decides the next step
  ↓
Repeat until complete, cancelled, failed, paused, or waiting for the user
```

`DeepSeekConversationLoop` never classifies the request or selects a specialist. It calls `DeepSeekController.decide(...)`, then passes the exact validated command to `ControlledExecutor.execute(...)`.

## Supported interaction methods

- `start(...)`: begin a new local task
- `provide_user_input(...)`: continue after `request_user_input`
- `approve(...)`: approve and replay the exact pending command, then continue
- `cancel(...)`: stop a task safely

The loop emits `ConversationEvent` objects for assistant messages and execution results. A UI or API can subscribe through `event_sink` without changing controller behavior.

## Safety and limits

- One DeepSeek decision and one command are processed per turn.
- Specialist results are returned to DeepSeek as conversation context.
- Approval-required commands stop the loop until explicit approval is supplied.
- User-input commands stop the loop until input is supplied.
- A configurable `max_turns` prevents an accidental infinite loop.
- The current conversation context is process-local; durable conversation persistence is a later phase.

## Local usage

```python
from conversation import DeepSeekConversationLoop
from execution import ControlledExecutor
from model_adapters import DeepSeekController, ModelRegistry, ModelRole

registry = ModelRegistry.from_environment()
loop = DeepSeekConversationLoop(
    DeepSeekController(registry.get(ModelRole.CONTROLLER)),
    ControlledExecutor(registry),
)
result = await loop.start("Research five websites and summarize them.")
```

The default model endpoints remain local Ollama-compatible endpoints from `.env.example`.
