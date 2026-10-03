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

This phase does **not** yet implement the GUI, model adapters, orchestration protocol, persistence, or external integrations. Those are subsequent phases.

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
- [GUI behavior reference](docs/references/gui-behavior-analysis.md)
