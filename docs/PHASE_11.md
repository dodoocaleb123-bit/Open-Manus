# Phase 11: Qwen2.5-Coder:7b coding and execution workflows

Phase 11 adds a controlled coding workspace for `qwen2.5-coder:7b`. DeepSeek defines the build objective and selects one workspace command at a time; Qwen Coder performs the coding subtask, and the execution layer returns logs before the next DeepSeek decision.

## Coding sequence

```text
DeepSeek defines the build objective
  ↓
Qwen2.5-Coder:7b creates or edits project files
  ↓
Execution layer runs allowlisted tests/build commands
  ↓
Failed logs return to DeepSeek and then Qwen Coder for repair
  ↓
Tests/build are rerun; success must be observed, not assumed
  ↓
Preview server starts and is tracked
  ↓
DeepSeek presents the result
```

## Coding workspace

Projects are stored under `OPENMANUS_PROJECTS_ROOT` or the default `./workspace/projects`. The controlled tool layer provides:

- `coding_create_project`
- `coding_write_file`
- `coding_read_file`
- `coding_file_tree`
- `coding_manage_dependencies`
- `coding_run_tests`
- `coding_run_build`
- `coding_run_command`
- `coding_snapshot`
- `coding_export_project`
- `coding_start_preview`
- `coding_preview_status`
- `coding_stop_preview`

The local API exposes the project tree through:

```text
GET /api/workspace?project=<project-name>
```

The existing GUI workspace panel can use this endpoint for its Code, Files, Logs, Preview, and Settings surfaces. Preview startup now forwards an explicit, allowlisted command and validated port to the authenticated isolated runner. The runner creates a separate workload container and local-only proxy; the workspace never starts generated servers directly on the host. Status, logs, and stop operations use the runner-managed preview ID.

## Safety boundary and execution policy

- Project names must be simple directories beneath the projects root
- File paths reject absolute paths and `..` traversal; this is not a defense against every symlink race
- File writes are bounded to 10 MB per file
- Commands begin with an executable allowlist and selected package-manager subcommands
- Commands run without a shell and have a bounded wall-clock timeout
- POSIX commands are placed in a new session/process group; Windows cleanup uses `taskkill /T /F`
- Preview startup requires an explicit allowlisted command and port and is performed only by the isolated runner
- Preview processes receive IDs and can be queried, inspected through bounded logs, or stopped
- Snapshots and exports are ZIP archives; `.git` content is excluded

**Important:** an executable allowlist, restricted environment, timeout, and process-group cleanup do not form an OS-enforced sandbox. A permitted Python/Node/package-manager command can execute project-controlled code with the OpenManus process's operating-system permissions. Therefore, command execution and preview startup fail closed by default unless a real isolated runner is configured. The workspace adapter validates preview inputs and runner response shape; actual browser interaction, viewport coverage, and cleanup behavior still require Docker-backed integration testing on the target deployment.

For temporary trusted local development only, set `OPENMANUS_ALLOW_UNSANDBOXED_CODE=true` or explicitly construct `CodingWorkspace(..., allow_unsafe_execution=True)`. Do not enable this for untrusted generated code or multi-user deployments. Process-tree cleanup is best-effort and must not be treated as a security boundary. A dedicated isolated runner with least privilege, bounded resources/output, controlled mounts, and explicit network policy remains required before executing untrusted code.

## Role separation

`qwen2.5-coder:7b` is the coding specialist. `qwen2.5:3b` remains research-only. The model registry, platform knowledge, protocol prompt, catalog, and tests all preserve this distinction. Qwen Coder returns implementation work and repair results to DeepSeek; it does not become the user's primary assistant.
