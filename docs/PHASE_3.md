# Phase 3: teach DeepSeek the platform

Phase 3 gives DeepSeek a structured understanding of Open-Manus and makes it the only user-facing controller entry point.

## Added components

- `PlatformKnowledge`: canonical product and model-role registry
- `ModelRoleProfile`: capabilities and boundaries for each model
- `PlatformContext`: safe per-turn runtime facts
- `DeepSeekController`: controller-only facade that injects the platform prompt before every conversation

## Controller invariant

Every request must enter `DeepSeekController.respond(...)`. The facade rejects a specialist adapter and rejects caller-supplied system messages, so the platform knowledge cannot be bypassed by a normal conversation call.

The facade does not choose a specialist model. It only gives DeepSeek the facts needed to make that decision. A later execution layer will interpret structured delegation commands from DeepSeek.

## Knowledge included

DeepSeek is taught:

- Its role as the central controller and final synthesizer
- The exact distinction between Qwen 2.5:3b research and Qwen2.5-Coder:7b coding
- Gemma's visual-analysis boundary
- Llama's creative role and the fact that text Llama alone is not image generation
- Current platform capabilities
- Capabilities that are not yet integrated
- Approval-required actions
- Secret-handling rules
- Failure, cancellation, and specialist-result rules
- Current workspace, tools, integrations, attachments, and task status

## Example

```python
from model_adapters import DeepSeekController, ModelRegistry, ModelRole

registry = ModelRegistry.from_environment()
controller = DeepSeekController(registry.get(ModelRole.CONTROLLER))
response = await controller.respond(
    [{"role": "user", "content": "Analyze this image and explain it."}],
    context=PlatformContext(attachments=("diagram.png",)),
)
```

The request is sent to DeepSeek first. Gemma may be selected later, but only as a specialist delegated by DeepSeek.
