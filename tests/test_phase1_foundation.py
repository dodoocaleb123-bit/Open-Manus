from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_phase1_layout_exists():
    required = [
        ROOT / "backend/openmanus-core/main.py",
        ROOT / "backend/openmanus-rl/README.md",
        ROOT / "docs/ARCHITECTURE.md",
        ROOT / "docs/SETUP.md",
        ROOT / ".env.example",
    ]
    assert all(path.exists() for path in required)


def test_design_intelligence_is_preserved():
    files = list((ROOT / "docs/design-intelligence").glob("*.md"))
    assert len(files) == 6


def test_research_and_coder_roles_are_distinct():
    env = (ROOT / ".env.example").read_text()
    assert "RESEARCH_LLM_MODEL=qwen2.5:3b" in env
    assert "CODER_LLM_MODEL=qwen2.5-coder:7b" in env
    assert "RESEARCH_LLM_MODEL" in env and "CODER_LLM_MODEL" in env
