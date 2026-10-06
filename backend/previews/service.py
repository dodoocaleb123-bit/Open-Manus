"""Presentation layer for local previews and generated project artifacts."""
from __future__ import annotations

import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from coding import CodingWorkspace, CodingWorkspaceError


class PreviewServiceError(RuntimeError):
    """Raised when a preview or artifact request is invalid."""


class PreviewService:
    ARTIFACT_TYPES = {"image", "research_report", "document", "presentation", "screenshot", "code", "archive"}
    VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "mobile": {"width": 390, "height": 844}}

    def __init__(self, workspace: CodingWorkspace, *, store: Any | None = None, screenshotter: Callable[..., Mapping[str, Any]] | None = None) -> None:
        self.workspace = workspace
        self.store = store
        self.screenshotter = screenshotter

    def manifest(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project = self.workspace._project(arguments.get("project", ""))
        preview_id = str(arguments.get("preview_id", ""))
        viewport = str(arguments.get("viewport", "desktop"))
        if viewport not in self.VIEWPORTS:
            raise PreviewServiceError("viewport must be desktop or mobile")
        preview = self.workspace.preview_status({"preview_id": preview_id}) if preview_id else None
        files = self.workspace.file_tree({"project": project.name})
        ready = bool(preview and preview["status"] == "running")
        return {"project": project.name, "preview_id": preview_id or None, "ready": ready, "decision_owner": "deepseek", "url": preview["url"] if preview else None, "viewport": viewport, "viewport_size": self.VIEWPORTS[viewport], "files": files, "available_views": ["preview", "code", "browser", "console", "files", "artifacts"], "message": "Preview is ready to show." if ready else "Start a preview and verify it before showing it to the user."}

    def screenshot(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        preview_id = str(arguments.get("preview_id", ""))
        viewport = str(arguments.get("viewport", "desktop"))
        if viewport not in self.VIEWPORTS:
            raise PreviewServiceError("viewport must be desktop or mobile")
        status = self.workspace.preview_status({"preview_id": preview_id})
        if status["status"] != "running":
            raise PreviewServiceError("screenshot requires a running preview")
        project = self.workspace._project(status["project"])
        target = project / ".openmanus" / "previews" / f"{preview_id}-{viewport}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.screenshotter is not None:
            result = dict(self.screenshotter(status["url"], self.VIEWPORTS[viewport], target))
        else:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise PreviewServiceError("screenshot requires Playwright; install playwright and a Chromium browser") from exc
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page(viewport=self.VIEWPORTS[viewport], device_scale_factor=1)
                    page.goto(status["url"], wait_until="networkidle", timeout=30000)
                    page.screenshot(path=str(target), full_page=True)
                    browser.close()
                result = {"path": str(target)}
            except Exception as exc:
                raise PreviewServiceError(f"preview screenshot failed: {exc}") from exc
        return {"preview_id": preview_id, "project": status["project"], "viewport": viewport, "width": self.VIEWPORTS[viewport]["width"], "height": self.VIEWPORTS[viewport]["height"], "path": result.get("path", str(target)), "artifact_type": "screenshot", "ready": True}

    def code_view(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project, path = self.workspace._file_path(arguments)
        if not path.is_file():
            raise PreviewServiceError("code view requires a file")
        if path.stat().st_size > 2 * 1024 * 1024:
            raise PreviewServiceError("code view is limited to 2 MB")
        return {"project": project.name, "path": str(path.relative_to(project)), "language": mimetypes.guess_type(path.name)[0] or "text/plain", "content": path.read_text(encoding="utf-8", errors="replace"), "read_only": True}

    def console(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self.workspace.console_output(arguments)

    def register_artifact(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        artifact_id = str(arguments.get("artifact_id", "artifact_" + uuid.uuid4().hex)).strip()
        artifact_type = str(arguments.get("artifact_type", "file")).strip()
        if artifact_type not in self.ARTIFACT_TYPES:
            raise PreviewServiceError(f"artifact_type must be one of {sorted(self.ARTIFACT_TYPES)}")
        project = self.workspace._project(arguments.get("project", ""))
        path_value = str(arguments.get("path", "")).strip()
        if not path_value:
            raise PreviewServiceError("artifact path is required")
        _, path = self.workspace._file_path({"project": project.name, "path": path_value})
        if not path.is_file():
            raise PreviewServiceError("artifact file does not exist")
        record = {"artifact_id": artifact_id, "path": str(path), "relative_path": str(path.relative_to(project)), "artifact_type": artifact_type, "metadata": dict(arguments.get("metadata", {})), "created_at": time.time()}
        task_id = str(arguments.get("task_id", "")).strip()
        if task_id and self.store is not None:
            self.store.record_artifact(task_id, artifact_id, str(path), artifact_type, record["metadata"] | {"project": project.name, "relative_path": record["relative_path"]})
        return {"artifact": record, "registered": bool(task_id and self.store is not None)}

    def list_artifacts(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(arguments.get("task_id", "")).strip()
        if not task_id or self.store is None:
            return {"artifacts": []}
        return {"task_id": task_id, "artifacts": list(self.store.list_artifacts(task_id))}

    def specs(self) -> tuple[tuple[str, Callable[[Mapping[str, Any]], Any], str], ...]:
        return (
            ("preview_manifest", self.manifest, "Preview readiness, viewport modes, and available views"),
            ("preview_screenshot", self.screenshot, "Capture a desktop or mobile preview screenshot"),
            ("preview_code_view", self.code_view, "Show a read-only project code file"),
            ("preview_console", self.console, "Show live preview console output"),
            ("preview_register_artifact", self.register_artifact, "Register an image, report, document, presentation, code, screenshot, or archive"),
            ("preview_list_artifacts", self.list_artifacts, "List persisted task artifacts"),
        )
