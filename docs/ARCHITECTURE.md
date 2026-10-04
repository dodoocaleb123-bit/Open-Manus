# Open-Manus Architecture

## Design invariant

DeepSeek is the central controller. The platform must send every user request to DeepSeek first, without a keyword classifier or hidden specialist router in front of it.

DeepSeek is responsible for:

- Understanding the request
- Holding the conversation
- Planning and decomposing work
- Selecting specialist models
- Ordering subtasks
- Sending progress updates
- Requesting user input and approval
- Recovering from model/tool errors
- Synthesizing specialist results
- Presenting the final answer

## Phase 3 controller knowledge

`backend/model_adapters/platform_knowledge.py` is the canonical platform knowledge registry. It describes the five model roles, their capabilities, their boundaries, approval-required actions, and capabilities that are not yet integrated.

`DeepSeekController` injects that knowledge as the first system message before every user conversation. It accepts only the controller adapter and rejects caller-supplied system messages. It does not route requests itself; DeepSeek makes the delegation decision.

## Controlled execution boundary
`backend/execution` is the non-intelligent execution/state layer. `ControlledExecutor` validates the exact command emitted by DeepSeek, looks up only the named model role or registered tool, tracks the task and command-run state, enforces approval gates, and returns an `ExecutionResult`. It does not classify requests, create plans, select a different model, or replace DeepSeek's decisions.

Phase 5 introduced a thread-safe in-memory store. Phase 7 adds `SQLiteStateStore`, which mirrors task and command-run state into a local SQLite database and persists conversation messages, events, runtime context, activity, attachments, and artifacts.

## Phase 6 conversation loop
`backend/conversation` connects `DeepSeekController` and `ControlledExecutor`. For each turn it sends the current context to DeepSeek, executes the single validated command DeepSeek returns, appends the result, and sends that result back to DeepSeek. The loop pauses for user input or approval and stops on completion, cancellation, failure, or a configurable turn limit. It does not add a second planner.

## Phase 7 persistence boundary
`SQLiteStateStore` is the local durability boundary. A new process can load a task, its command history, conversation messages, controller events, and `PlatformContext`, then continue the same DeepSeek conversation. The database records metadata and local paths for attachments and artifacts; it does not move large files into the database. Persistence records state and activity but does not make planning decisions.

```text
User
  -> DeepSeek controller
  -> DeepSeek command
  -> controlled execution/state layer
  -> named specialist model or tool
  -> result returned to DeepSeek
  -> DeepSeek chooses the next step
```

## Model roles

| Model | Required role | Boundary |
| --- | --- | --- |
| `deepseek-r1:*` | Controller, conversation, planning, routing, synthesis | Receives every user request first |
| `qwen2.5:3b` | Web research and source gathering | Not the coding model |
| `qwen2.5-coder:7b` | Software engineering and app building | Not the research model |
| `gemma3:4b` | Image, document, and visual analysis | Returns findings to DeepSeek |
| `llama3.2:3b` | Creative concepts and content | Actual image generation requires a separate compatible image backend |

## Repository boundaries

`backend/openmanus-core` preserves the original OpenManus implementation. It remains a reusable backend foundation and is not modified into a GUI in Phase 1.

`backend/openmanus-rl` is a product boundary, not a vendored training environment. RL datasets and GPU-heavy training dependencies should remain separate from the interactive product runtime.

The design-intelligence documents are retained under `docs/design-intelligence`. They are source material for the future controller prompts and coding/creative skills; Phase 1 does not silently inject them into model context.
