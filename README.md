# Open-Manus Platform

A DeepSeek-controlled agent platform built on the supplied OpenManus foundation.

> **Controller rule:** every user request enters DeepSeek first. DeepSeek plans, delegates, communicates, requests approval, and synthesizes the final result. The execution layer only runs and tracks DeepSeek's commands; it is not a second planner.

## Repository layout

```text
backend/openmanus-core/   Preserved OpenManus Python agent foundation
backend/openmanus-rl/     Boundary for optional RL training/evaluation code
docs/design-intelligence/ Six supplied design-intelligence documents
docs/references/          GUI behavior reference analysis
scripts/                  Environment and repository checks
tests/                    Product-level test space
```

OpenManus-RL is intentionally separated from the product runtime. Its datasets, training dependencies, and GPU-oriented tooling are not installed by the application foundation.

## Phase 1 status

This repository currently provides:

- A clean product repository boundary
- The original OpenManus core preserved under `backend/openmanus-core`
- Explicit separation for OpenManus-RL
- All six design-intelligence documents retained as source material
- A documented DeepSeek-first architecture
- Model-role configuration for DeepSeek, Qwen research, Qwen coder, Gemma vision, and Llama creative work
- Reproducible Python project metadata and environment checks
- No secrets committed to the repository

Phase 2 adds typed OpenAI-compatible adapters and a strict registry for the five model roles. Phase 3 adds the structured platform knowledge registry and the DeepSeek-only controller facade. Phase 4 adds DeepSeek's validated one-command-at-a-time controller protocol. Phase 5 adds the non-intelligent controlled execution and state layer. Phase 6 connects them in a local DeepSeek conversation loop. Phase 7 adds local SQLite persistence and restart-safe context reconstruction. Phase 8 adds the local JSON API and browser GUI. Phase 9 adds secure attachment ingestion, previews, extracted text, and explicit Gemma visual-analysis workflows.

## Quick start

```bash
cp .env.example .env
python3 scripts/check_environment.py
python3 -m compileall -q backend/openmanus-core
```

To run the preserved OpenManus CLI foundation directly:

```bash
cd backend/openmanus-core
python3 main.py
```

Install its dependencies in an isolated environment first; see `backend/openmanus-core/README.md` and `docs/SETUP.md`.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Setup](docs/SETUP.md)
- [Phase 1 acceptance checklist](docs/PHASE_1.md)
- [Phase 2 adapter guide](docs/PHASE_2.md)
- [Phase 3 controller guide](docs/PHASE_3.md)
- [Phase 4 protocol guide](docs/PHASE_4.md)
- [Phase 5 execution guide](docs/PHASE_5.md)
- [Phase 6 conversation-loop guide](docs/PHASE_6.md)
- [Phase 7 persistence guide](docs/PHASE_7.md)
- [Phase 8 API and GUI guide](docs/PHASE_8.md)
- [Phase 9 attachment and Gemma guide](docs/PHASE_9.md)
- [GUI behavior reference](docs/references/gui-behavior-analysis.md)
