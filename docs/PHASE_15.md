# Phase 15: project previews and artifacts

Phase 15 adds a controlled presentation layer for projects produced by the coding workflow.

## Preview capabilities

The coding workspace now supports:

- Live local website preview processes
- Desktop viewport metadata: `1440×900`
- Mobile viewport metadata: `390×844`
- Persisted preview console logs
- Preview readiness manifests
- Optional Playwright screenshots for desktop and mobile
- Read-only code views
- Console output views
- Downloadable ZIP project archives

Preview processes are still controlled by the coding workspace. They run inside the selected project directory, receive a preview ID, and can be queried or stopped.

## Preview readiness

DeepSeek decides when a preview is ready to show. Starting a process is not enough. DeepSeek should call `preview_manifest`, verify `ready: true`, inspect the target viewport, and then decide how to describe the preview to the user.

A manifest reports:

- Preview URL
- Running/stopped state
- Selected viewport and dimensions
- Project file tree
- Available views
- Readiness message

## Screenshots

`preview_screenshot` captures a running preview in desktop or mobile mode. The default implementation uses Playwright when installed. If Playwright or a browser is unavailable, the tool reports the missing runtime instead of claiming a screenshot exists.

## Code and console views

- `preview_code_view` returns a bounded, read-only source-file view.
- `preview_console` returns the persisted output log for a preview process.
- `coding_console_output` provides the same console view through the coding tool surface.

## Artifact types

`preview_register_artifact` supports these typed deliverables:

```text
image
research_report
document
presentation
screenshot
code
archive
```

Artifacts can be associated with a task through the SQLite artifact table and later retrieved with `preview_list_artifacts`. This covers generated images, research reports, documents, presentations, screenshots, source files, and exported archives without presenting every file as an undifferentiated attachment.

## Downloads

A local ZIP archive can be downloaded through:

```text
GET /api/workspace/download?project=<project-name>
```

The route validates the project through the coding workspace and returns an application/zip attachment.

## DeepSeek boundary

DeepSeek remains responsible for deciding:

- Whether the project is ready to preview
- Which viewport to show
- Whether to show a screenshot, live URL, code, console output, or artifact
- How to describe limitations or failed previews

No preview or artifact is claimed to exist until the corresponding tool result confirms it.
