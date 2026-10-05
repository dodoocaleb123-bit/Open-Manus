"""Controlled creative workflows for Llama 3.2:3b.

Llama 3.2:3b is a language model. It can create concepts, copy, direction,
and prompts, but it cannot produce pixels by itself. Image generation therefore
requires an explicitly configured compatible image service.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


class CreativeWorkflowError(RuntimeError):
    """Raised when a creative workflow or image service is unavailable."""


@dataclass(frozen=True)
class CreativeBrief:
    objective: str
    audience: str = ""
    tone: str = ""
    brand: str = ""
    medium: str = ""
    constraints: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CreativeResult:
    workflow: str
    brief: CreativeBrief
    deliverable: Any
    llama_role: str = "llama3.2:3b"
    image_service_used: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImageGenerationService:
    """OpenAI-compatible image endpoint; configuration is explicit and optional."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None, timeout_seconds: float = 120.0) -> None:
        self.base_url = (base_url or os.getenv("OPENMANUS_IMAGE_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("OPENMANUS_IMAGE_API_KEY", "")
        self.model = model or os.getenv("OPENMANUS_IMAGE_MODEL", "")
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def generate(self, prompt: str, *, size: str = "1024x1024", count: int = 1) -> dict[str, Any]:
        if not self.configured:
            raise CreativeWorkflowError(
                "Llama 3.2:3b cannot generate images by itself; configure OPENMANUS_IMAGE_BASE_URL and OPENMANUS_IMAGE_MODEL"
            )
        payload = json.dumps({"model": self.model, "prompt": prompt, "size": size, "n": count}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(f"{self.base_url}/images/generations", data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise CreativeWorkflowError(f"image generation service failed: {exc}") from exc
        return {"service": self.base_url, "model": self.model, "response": result}


class CreativeWorkflows:
    """Build structured creative outputs without making controller decisions."""

    def __init__(self, image_service: ImageGenerationService | None = None) -> None:
        self.image_service = image_service or ImageGenerationService()

    def create_brief(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        brief = self._brief(arguments)
        return CreativeResult("creative_brief", brief, brief.to_dict()).to_dict()

    def write_copy(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        brief = self._brief(arguments)
        deliverable = {
            "headline": str(arguments.get("headline", "")),
            "tagline": str(arguments.get("tagline", "")),
            "body_points": [str(item) for item in arguments.get("body_points", [])],
            "call_to_action": str(arguments.get("call_to_action", "")),
            "revision_notes": [str(item) for item in arguments.get("revision_notes", [])],
            "copy_guidance": f"Write for {brief.audience or 'the intended audience'} in a {brief.tone or 'clear'} tone for {brief.medium or 'the requested medium'}.",
        }
        return CreativeResult("copywriting", brief, deliverable).to_dict()

    def visual_direction(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        brief = self._brief(arguments)
        deliverable = {
            "concept": str(arguments.get("concept", brief.objective)),
            "style": str(arguments.get("style", "")),
            "palette": [str(item) for item in arguments.get("palette", [])],
            "composition": str(arguments.get("composition", "")),
            "typography": str(arguments.get("typography", "")),
            "do": [str(item) for item in arguments.get("do", [])],
            "avoid": [str(item) for item in arguments.get("avoid", [])],
        }
        return CreativeResult("visual_direction", brief, deliverable).to_dict()

    def image_prompt(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        brief = self._brief(arguments)
        direction = self.visual_direction(arguments)["deliverable"]
        prompt = "; ".join(
            part for part in [
                direction["concept"],
                f"style: {direction['style']}" if direction["style"] else "",
                f"palette: {', '.join(direction['palette'])}" if direction["palette"] else "",
                f"composition: {direction['composition']}" if direction["composition"] else "",
                f"typography: {direction['typography']}" if direction["typography"] else "",
                f"avoid: {', '.join(direction['avoid'])}" if direction["avoid"] else "",
            ] if part
        )
        return CreativeResult("image_prompt", brief, {"prompt": prompt, "generation_route": "external_image_service_required"}).to_dict()

    def presentation_structure(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        brief = self._brief(arguments)
        sections = [str(item) for item in arguments.get("sections", [])]
        if not sections:
            sections = ["Context", "Core idea", "Supporting evidence", "Execution", "Next steps"]
        slides = [{"number": index, "title": title, "purpose": f"Develop the {title.lower()} section for {brief.objective}."} for index, title in enumerate(sections, 1)]
        return CreativeResult("presentation_structure", brief, {"slides": slides, "speaker_notes_guidance": "Keep the narrative coherent and let DeepSeek review the final structure."}).to_dict()

    def generate_image(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        prompt_result = self.image_prompt(arguments)
        prompt = prompt_result["deliverable"]["prompt"]
        generated = self.image_service.generate(prompt, size=str(arguments.get("size", "1024x1024")), count=min(max(int(arguments.get("count", 1)), 1), 4))
        return CreativeResult("image_generation", self._brief(arguments), generated, image_service_used=generated.get("service")).to_dict()

    def _brief(self, arguments: Mapping[str, Any]) -> CreativeBrief:
        return CreativeBrief(
            objective=str(arguments.get("objective", "")).strip() or "Develop the requested creative direction",
            audience=str(arguments.get("audience", "")),
            tone=str(arguments.get("tone", "")),
            brand=str(arguments.get("brand", "")),
            medium=str(arguments.get("medium", "")),
            constraints=tuple(str(item) for item in arguments.get("constraints", [])),
            references=tuple(str(item) for item in arguments.get("references", [])),
        )


class CreativeToolset:
    def __init__(self, workflows: CreativeWorkflows | None = None) -> None:
        self.workflows = workflows or CreativeWorkflows()

    def handlers(self) -> dict[str, Any]:
        return {
            "creative_create_brief": self.workflows.create_brief,
            "creative_write_copy": self.workflows.write_copy,
            "creative_visual_direction": self.workflows.visual_direction,
            "creative_make_image_prompt": self.workflows.image_prompt,
            "creative_structure_presentation": self.workflows.presentation_structure,
            "creative_generate_image": self.workflows.generate_image,
        }
