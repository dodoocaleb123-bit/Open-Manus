# Phase 2: role-aware model adapters

Phase 2 adds the transport boundary for the five required models. It does not yet implement DeepSeek routing, task persistence, or the GUI.

## Adapter contract

Every adapter exposes:

- `generate(request)` for chat completions
- `health_check()` for reachability checks
- `cancel(request_id)` as a portable cancellation hook
- `role` and `model` properties

The implementation uses the OpenAI-compatible HTTP API, which is supported by Ollama at `http://localhost:11434/v1` and by other compatible providers.

## Role boundaries

| Role | Default model | Responsibility |
| --- | --- | --- |
| `controller` | `deepseek-r1:7b` | DeepSeek receives every user request and controls the workflow |
| `research` | `qwen2.5:3b` | Web research and source gathering |
| `coder` | `qwen2.5-coder:7b` | Software engineering and app building |
| `vision` | `gemma3:4b` | Image, PDF, screenshot, and document analysis |
| `creative` | `llama3.2:3b` | Creative concepts and content |

The registry rejects a role assignment that violates these boundaries. In particular, `qwen2.5:3b` and `qwen2.5-coder:7b` cannot be silently swapped.

## Usage

```python
from model_adapters import ModelRegistry, ModelRequest, ModelRole

registry = ModelRegistry.from_environment()
controller = registry.get(ModelRole.CONTROLLER)
response = await controller.generate(
    ModelRequest(messages=[{"role": "user", "content": "Hello"}])
)
```

The registry only constructs role-specific adapters. It does not decide which adapter to call; that decision belongs to DeepSeek and the future execution layer.

## Validation

```bash
python3 -m pytest -q
python3 -m compileall -q backend
```
