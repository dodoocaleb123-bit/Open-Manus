# Phase 4: DeepSeek controller protocol

Phase 4 gives DeepSeek a strict, machine-readable command protocol. DeepSeek remains the only planner and controller. The protocol does not execute commands yet; it defines what the later execution layer will safely consume.

## Decision shape

Every structured decision contains:

```json
{
  "assistant_message": "short user-facing progress or final message",
  "status": "working",
  "command": {
    "type": "delegate_to_model",
    "arguments": {
      "role": "research",
      "objective": "...",
      "inputs": {},
      "expected_output": "..."
    }
  }
}
```

There is at most one command per decision. After the command result is returned to DeepSeek, DeepSeek decides the next command.

## Supported commands

| Command | Purpose |
| --- | --- |
| `delegate_to_model` | Ask Qwen research, Qwen coder, Gemma vision, or Llama creative to perform a bounded subtask |
| `run_tool` | Run a registered tool with structured arguments |
| `request_user_input` | Pause until a required user choice or piece of information is provided |
| `request_user_approval` | Pause before GitHub push, publishing, external messaging, deletion, or sensitive-secret actions |
| `pause_task` / `resume_task` | Control a paused task |
| `retry_task` | Request a safe retry after a failure |
| `cancel_task` | Stop the current task with a reason |
| `complete_task` | Mark the task complete after results are verified |

## Safety rules

The parser rejects malformed JSON, unknown commands, missing fields, controller delegation, unknown approval actions, and invalid role assignments. The parser does not execute anything. It produces a typed `ControllerDecision` for the future execution layer.

`DeepSeekController.decide(...)` requests JSON mode from the controller adapter, parses the response, and returns the validated decision. `DeepSeekController.respond(...)` remains available for ordinary text responses, but task execution should use `decide(...)`.

## Example

```python
from model_adapters import DeepSeekController, ModelRegistry, ModelRole

registry = ModelRegistry.from_environment()
controller = DeepSeekController(registry.get(ModelRole.CONTROLLER))
decision = await controller.decide(
    [{"role": "user", "content": "Research five cosmetic sites."}]
)
```

The next phase will implement the non-intelligent execution/state layer that runs this command, tracks it, and returns the result to DeepSeek.
