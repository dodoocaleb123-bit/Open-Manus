# Phase 3 app-building lifecycle report

Date: 2026-10-13
Branch: `phase-3/tested-app-building-lifecycle`
Base: `main`

## Scope

This change restores project preview startup through the existing isolated sandbox runner. It does not add a host-execution fallback. It adds deterministic lifecycle tests for project creation, file editing, command/test/build result handling, failure-and-repair cycles, preview lifecycle delegation, desktop/mobile viewport metadata, snapshots, exports, persistence after workspace re-open, secret exclusion, and invalid-path/command rejection.

## Source inspection findings

- `backend/coding/workspace.py` already implemented project creation/editing, bounded file reads/writes, allowlisted runner commands, snapshots, ZIP exports, and fail-closed runner calls.
- `backend/coding/runner_client.py` already provided authenticated runner calls and explicit failure when runner configuration is missing.
- `backend/sandbox_runner/server.py` already implemented runner-managed preview containers and a loopback-published fixed-target proxy. The workload runs on an internal network in a constrained container; the proxy is separately constrained.
- The workspace's `start_preview` method nevertheless unconditionally raised an error, so the implemented runner preview API was unreachable through the workspace tool layer. This PR wires that gap to the existing runner and returns explicit readiness and viewport metadata.
- Existing tests cover staging, secret exclusion, path traversal, bearer authentication, bounded jobs, and symlink attacks. New tests extend the workspace-level lifecycle.

## Security invariants preserved

- No host subprocess fallback was introduced.
- Preview commands must be argv arrays, not shell strings, and start with an allowlisted executable.
- Preview ports are range-checked.
- Preview startup calls the authenticated isolated runner only.
- Incomplete preview response envelopes trigger cleanup when a preview ID is available.
- Existing runner network isolation, resource limits, secret filtering, loopback proxy, and cleanup code are not weakened or modified.

## Automated test additions

New file: `tests/test_phase3_app_lifecycle.py`

- Create/edit/read project files.
- Test/build command result propagation, including failure logs and repair rerun.
- Preview startup, readiness, viewport metadata, status, logs, and stop/cleanup.
- Reject invalid ports, shell strings, and unapproved commands.
- Fail closed when the sandbox runner is unavailable.
- Snapshot and ZIP export validation, secret exclusion, and project data persistence after reopening the workspace.
- Reject traversal paths.

## CI results

PR head commit: `109109e919ec494a566149121554ec6c0e409410`.

- [Phase 3 app lifecycle workflow run 38336573690](https://github.com/dodoocaleb123-bit/Open-Manus/actions/runs/38336573690): **passed**. Ubuntu lifecycle/security tests, Windows workspace lifecycle tests, and both sandbox images built successfully.
- [Python tests workflow run 38336573693](https://github.com/dodoocaleb123-bit/Open-Manus/actions/runs/38336573693): **passed**. Windows pytest, Ubuntu pytest, Docker sandbox integration, private runner Compose validation, and live Linux Docker isolation tests all passed.

CI validates the automated mocks and Linux sandbox path; it does not replace the target deployment smoke test below. Exact test counts are not available in the workflow-job summaries returned to this audit.

Target-deployment verification is not available from repository CI alone. Windows Docker Desktop, live HTTP/browser interaction against the real preview proxy, app-container restart recovery, and actual persisted workspace-volume recovery must be exercised in the user's deployment before those are marked verified. The runner currently keeps preview records in process memory and startup cleanup removes stale sandbox resources; preview sessions themselves are not durable across runner restart. Project files and workspace snapshots/exports persist on the mounted project volume.
