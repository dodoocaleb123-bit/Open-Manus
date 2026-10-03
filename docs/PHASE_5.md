# Phase 5: controlled execution and state layer

Phase 5 executes the exact command DeepSeek emits and tracks its lifecycle. It is intentionally non-intelligent: it does not classify user requests, create plans, choose a specialist, or replace DeepSeek.

## Components

- `InMemoryStateStore`: thread-safe task and command-run records
- `ControlledExecutor`: command dispatcher and lifecycle enforcer
- `ToolSpec`: explicit registry entry for a runnable tool
- `TaskStatus` and `RunStatus`: observable state transitions
- `ExecutionResult`: safe result envelope for returning status and output to the controller loop

## Supported behavior

The executor can:

- Delegate to the exact named specialist role from DeepSeek's command
- Run explicitly registered tools
- Pause for user input
- Pause for approval
- Resume a paused task
- Accept approved actions and run one-shot sensitive tools
- Retry, complete, or cancel task state
- Record command history and model/tool results
- Fail closed for unknown tools and invalid lifecycle transitions

## Specialist boundary

For `delegate_to_model`, the executor reads the role already selected by DeepSeek and looks up that role in `ModelRegistry`. It never infers intent or selects a fallback. `qwen2.5:3b` and `qwen2.5-coder:7b` remain distinct because the registry remains the source of role assignments.

## Approval boundary

A tool may declare `approval_action="push_to_github"` or another approved action. The executor will not invoke the handler until `approve(task_id, action)` is called. Approval is one-shot and is consumed before the handler runs. Rejecting an approval cancels the task.

## Current limitation

The state store is in-memory for this phase, so a process restart loses state. Phase 7 will add durable persistence and the API will expose snapshots and events. This phase provides the execution contract that a durable store can implement later.

## Example

```python
from execution import ControlledExecutor
from model_adapters import ControllerCommand, CommandType, ModelRegistry

executor = ControlledExecutor(ModelRegistry.from_environment())
task = executor.create_task("Research five websites")
result = await executor.execute(
    task.task_id,
    ControllerCommand(
        CommandType.DELEGATE_TO_MODEL,
        {
            "role": "research",
            "objective": "Research five websites",
            "expected_output": "Cited findings",
        },
    ),
)
```

The result returns to the future DeepSeek control loop. The executor does not decide what command should happen next.
