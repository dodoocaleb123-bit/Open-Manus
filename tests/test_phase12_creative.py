import pytest

from api import OpenManusAPI
from creative import CreativeWorkflowError, CreativeWorkflows
from execution import ControlledExecutor, SQLiteStateStore
from model_adapters import ModelRole


class Registry:
    def __init__(self):
        self.adapters = {role: object() for role in ModelRole}

    def get(self, role):
        return self.adapters[role]

    def roles(self):
        return tuple(self.adapters)


def test_creative_workflows_build_structured_deliverables():
    workflows = CreativeWorkflows()
    brief = workflows.create_brief({"objective": "Launch a new tea brand", "audience": "young professionals", "tone": "warm", "brand": "Mori"})
    assert brief["workflow"] == "creative_brief"
    assert brief["brief"]["brand"] == "Mori"
    copy = workflows.write_copy({"objective": "Launch a new tea brand", "headline": "Slow down beautifully", "body_points": ["Single-origin leaves"]})
    assert copy["workflow"] == "copywriting"
    direction = workflows.visual_direction({"objective": "Launch visual", "style": "quiet editorial", "palette": ["forest green", "cream"]})
    assert direction["deliverable"]["palette"] == ["forest green", "cream"]
    prompt = workflows.image_prompt({"objective": "A tea package", "style": "quiet editorial", "avoid": ["plastic gloss"]})
    assert "plastic gloss" in prompt["deliverable"]["prompt"]
    presentation = workflows.presentation_structure({"objective": "Brand launch", "sections": ["Problem", "Idea", "Launch"]})
    assert len(presentation["deliverable"]["slides"]) == 3


def test_llama_does_not_claim_to_generate_images_without_external_service(monkeypatch):
    monkeypatch.delenv("OPENMANUS_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("OPENMANUS_IMAGE_MODEL", raising=False)
    with pytest.raises(CreativeWorkflowError, match="cannot generate images"):
        CreativeWorkflows().generate_image({"objective": "A sunrise poster"})


def test_api_registers_creative_tools_and_image_service_is_separate(tmp_path):
    store = SQLiteStateStore(tmp_path / "creative.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=object(), executor=executor)
    assert {"creative_create_brief", "creative_write_copy", "creative_visual_direction", "creative_make_image_prompt", "creative_structure_presentation", "creative_generate_image"} <= set(executor._tools)
    assert api.catalog()["agents"][4]["model"] == "llama3.2:3b"
    store.close()
