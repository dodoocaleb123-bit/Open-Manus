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

The existing GUI workspace panel can use this endpoint for its Code, Files, Logs, Preview, and Settings surfaces.

## Safety boundary

- Project names must be simple directories beneath the projects root
- File paths cannot be absolute or traverse with `..`
- File writes are bounded to 10 MB per file
- Commands must begin with an allowlisted executable
- npm/pnpm/yarn, pip/pip3, and git subcommands are separately allowlisted
- Commands run without a shell and with a bounded timeout
- Working directories are always project-scoped
- Preview processes receive IDs and can be queried or stopped
- Snapshots and exports are ZIP archives; `.git` content is excluded

The coding workspace does not permit arbitrary `bash -c` or host-wide shell execution.

## Role separation

`qwen2.5-coder:7b` is the coding specialist. `qwen2.5:3b` remains research-only. The model registry, platform knowledge, protocol prompt, catalog, and tests all preserve this distinction. Qwen Coder returns implementation work and repair results to DeepSeek; it does not become the user's primary assistant.
