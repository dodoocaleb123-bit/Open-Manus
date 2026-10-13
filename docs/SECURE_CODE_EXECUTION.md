# Secure generated-code execution

## Security decision

Generated project code is untrusted. It must not run in the Open-Manus application process, in the application container, or with the application's operating-system identity. The existing `OPENMANUS_ALLOW_UNSANDBOXED_CODE` switch is a temporary trusted-development escape hatch only; it is not a sandbox and must not be enabled in production.

## Target architecture

```text
Open-Manus API / orchestrator
    |
    | narrow authenticated job protocol (project ID, argv, limits, job ID)
    v
Dedicated sandbox runner service / isolated VM
    |
    | fixed, server-side policy; no arbitrary runtime options
    v
Ephemeral per-job execution environment
    |-- unprivileged UID
    |-- read-only base image
    |-- sanitized project copy in bounded writable tmpfs (no host bind mount)
    |-- no application secrets or host/runtime sockets
    |-- no network by default
    |-- dropped capabilities + no-new-privileges
    |-- CPU, memory, PID, disk, wall-clock and output quotas
    |-- process-tree kill and environment teardown on every exit
    v
bounded result: exit code, status, truncated stdout/stderr, duration
```

The application must not expose a general container-management API or pass user-controlled Docker flags. The sandbox control plane and its runtime credentials must be unavailable inside generated-code jobs. Prefer a dedicated runner VM/daemon with a narrow API over mounting a host Docker socket into the application container. A conventional container is defense in depth, not a guarantee against kernel vulnerabilities; deployments with stronger adversarial requirements should use a VM boundary or a sandboxed runtime such as gVisor where supported.

## Default execution policy

- Fail closed if the runner is absent, unhealthy, unauthorized, or rejects a request.
- No host-process fallback, even for previews, tests, package scripts, or dependency installation.
- Network is disabled by default. If package installation is required, use a separate explicit dependency-fetch policy or a controlled proxy; do not silently grant arbitrary egress.
- Copy only bounded regular project files into an ephemeral tmpfs. Exclude environment files, key material, internal state, symlinks, and repository metadata. Never mount host directories, home directories, credential stores, application config, the Docker socket, or unrelated projects into the job.
- The job receives no application environment variables or credentials.
- Set explicit, server-enforced ceilings for CPU, memory, PIDs, writable storage, wall-clock time, and captured output.
- Terminate and remove the entire job container after completion or timeout; the job has no persistent descendants. Cancellation and runner-disconnect cleanup must be integration-tested.
- Previews use the same isolation. Publish only an explicitly selected port through a local-only proxy; do not bind arbitrary user processes to public interfaces.
- Treat artifacts, logs, archives, and preview output as untrusted. Do not follow symlinks when staging or syncing files; exclude internal state and known secret files; cap file count, total bytes, per-file size, and workspace tmpfs size.

## Threat model

Protect the host, application credentials, unrelated user projects, runner control plane, and other jobs from malicious or compromised generated code and dependencies. The model includes hostile commands, malicious package install scripts, filesystem traversal, symlink attacks, fork bombs, resource exhaustion, output floods, network probing, background descendants, and attempts to reach local services.

The model does not promise protection against a compromised host kernel/hypervisor, a malicious administrator, or vulnerabilities in the selected isolation runtime. Those risks require patching, hardened host configuration, and—where needed—a stronger VM boundary.

## Current implementation status (2026-10-13)

- Added an authenticated runner client and a separate HTTP control-plane prototype under `backend/sandbox_runner/`.
- The coding workspace routes command/test/build/dependency commands through that client; if the runner is not configured or fails, it raises an error and does not execute locally.
- The current runner protocol supports bounded one-shot jobs only. Secure previews are intentionally disabled until a runner-managed preview lifecycle and local-only port proxy are implemented.
- The runner image and protocol tests have been added, but this branch has not yet demonstrated a live Docker integration run. Do not describe the security requirement as complete until the acceptance gates below pass on a real runner.

## Implementation and acceptance gates

1. Define a narrow runner protocol and immutable server-side policy. Validate project IDs, argv shape, supported operations, and resource ceilings.
2. Implement a runner adapter that has no host-execution fallback; runner health and configuration must be checked before enabling execution.
3. Implement ephemeral job lifecycle, project-only mounts, network policy, non-root identity, read-only root filesystem, capability removal, no-new-privileges, resource quotas, output caps, and guaranteed teardown.
4. Route command, test, build, and dependency operations through the runner. Secure preview execution remains disabled until a runner-managed lifecycle and controlled local-only port proxy are implemented; no preview may fall back to host execution.
5. Add unit tests for policy validation and fail-closed behavior; add integration tests in the actual runner for host-file/secret access, symlink traversal, network denial, resource exhaustion, output caps, timeout, descendant cleanup, and teardown after cancellation.
6. Test on Linux and Windows hosts. Windows may use a dedicated Linux VM/container runtime, but the application must not expose unrestricted runtime control to generated jobs.
7. Repeat the security review against the actual code and integration evidence before considering this complete.

Green unit tests or CI alone do not establish isolation. The security gate is complete only when the runner is integrated and hostile integration tests demonstrate the required boundary.
