import asyncio
import threading
import urllib.request
import zipfile
from pathlib import Path

from api import OpenManusAPI, create_server
from coding import CodingWorkspace
from execution import ControlledExecutor, SQLiteStateStore
from model_adapters import ModelRole
from previews import PreviewService


class Registry:
    def __init__(self):
        self.adapters = {role: object() for role in ModelRole}

    def get(self, role):
        return self.adapters[role]

    def roles(self):
        return tuple(self.adapters)


def test_preview_viewports_console_and_screenshot(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "demo"})
    workspace.write_file({"project": "demo", "path": "index.html", "content": "<h1>Hello preview</h1>"})
    preview = workspace.start_preview({"project": "demo", "port": 18766, "viewport": "mobile"})

    def screenshotter(url, viewport, target):
        Path(target).write_bytes(b"PNG")
        return {"path": str(target), "url": url, "viewport": viewport}

    service = PreviewService(workspace, screenshotter=screenshotter)
    try:
        manifest = service.manifest({"project": "demo", "preview_id": preview["preview_id"], "viewport": "mobile"})
        assert manifest["ready"] is True
        assert manifest["viewport_size"] == {"width": 390, "height": 844}
        shot = service.screenshot({"preview_id": preview["preview_id"], "viewport": "mobile"})
        assert Path(shot["path"]).read_bytes() == b"PNG"
        console = service.console({"preview_id": preview["preview_id"]})
        assert console["status"] == "running"
    finally:
        workspace.stop_preview({"preview_id": preview["preview_id"]})


def test_code_view_and_typed_artifacts_persist(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "demo"})
    workspace.write_file({"project": "demo", "path": "report.md", "content": "# Research"})
    store = SQLiteStateStore(tmp_path / "artifacts.sqlite3")
    task = store.create_task("Show project outputs")
    service = PreviewService(workspace, store=store)
    code = service.code_view({"project": "demo", "path": "report.md"})
    assert code["read_only"] is True
    artifact = service.register_artifact({"task_id": task.task_id, "project": "demo", "path": "report.md", "artifact_type": "research_report", "metadata": {"title": "Research"}})
    assert artifact["registered"] is True
    assert service.list_artifacts({"task_id": task.task_id})["artifacts"][0]["artifact_type"] == "research_report"
    store.close()


def test_api_registers_preview_tools_and_archive_download(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_PROJECTS_ROOT", str(tmp_path / "projects"))
    store = SQLiteStateStore(tmp_path / "api.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=object(), executor=executor)
    assert {"preview_manifest", "preview_screenshot", "preview_code_view", "preview_console", "preview_register_artifact", "preview_list_artifacts"} <= set(executor._tools)
    api.coding_workspace.create_project({"project": "demo"})
    api.coding_workspace.write_file({"project": "demo", "path": "index.html", "content": "hello"})
    server = create_server("127.0.0.1", 0, api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        response = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/workspace/download?project=demo")
        assert response.headers["Content-Type"] == "application/zip"
        archive = response.read()
        archive_path = tmp_path / "download.zip"
        archive_path.write_bytes(archive)
        with zipfile.ZipFile(archive_path) as zipped:
            assert "index.html" in zipped.namelist()
    finally:
        server.shutdown()
        server.server_close()
        store.close()
