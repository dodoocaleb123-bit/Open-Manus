# Local setup

## Prerequisites

- Python 3.12 or newer for the supplied OpenManus core
- Ollama, if using local models
- The required local models pulled into Ollama
- Git

## Create an isolated environment

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/openmanus-core/requirements.txt
```

The core's dependency file is kept with the preserved source so upstream compatibility remains visible. Product-specific dependencies will be added in later phases.

## Configure local models

```bash
cp .env.example .env
```

The environment template keeps these roles separate:

- `DEEPSEEK_CONTROLLER_MODEL`: central controller
- `RESEARCH_LLM_MODEL`: `qwen2.5:3b`
- `CODER_LLM_MODEL`: `qwen2.5-coder:7b`
- `VISION_LLM_MODEL`: `gemma3:4b`
- `CREATIVE_LLM_MODEL`: `llama3.2:3b`

The current preserved OpenManus CLI still reads its original TOML configuration. Copy its example file and configure it separately when running the legacy CLI:

```bash
cp backend/openmanus-core/config/config.example.toml backend/openmanus-core/config/config.toml
```

Do not commit either `.env` or `config.toml`.

## Validate the foundation

```bash
python3 scripts/check_environment.py
python3 -m compileall -q backend/openmanus-core
```
