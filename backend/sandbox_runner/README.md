# Isolated code runner

This is a separate, trusted control plane. It is not part of the Open-Manus API process and must not be exposed publicly.

## Important deployment boundary

The service needs Docker-daemon authority to create and destroy sandbox containers. A compromise of this service can therefore compromise the machine that owns the daemon. For stronger protection, run it on a dedicated VM or a host with no unrelated credentials or workloads. Never mount the Docker socket into the Open-Manus application container or any generated-code container.

The runner expects a project directory root that is visible to the Docker daemon itself, not merely a path visible inside the runner container. Configure OPENMANUS_SANDBOX_PROJECTS_ROOT accordingly. The Open-Manus API and runner must see the same project data, even if their filesystem paths differ. On Docker Desktop, validate Windows path and bind-mount behavior with an actual integration run before enabling execution.

## Configure

Required environment variables:

- OPENMANUS_SANDBOX_TOKEN: long random bearer token shared with the Open-Manus API.
- OPENMANUS_SANDBOX_PROJECTS_ROOT: absolute project-root path as understood by the Docker daemon.

Optional:

- OPENMANUS_SANDBOX_IMAGE: execution image tag (default openmanus-code-runner:latest).
- OPENMANUS_SANDBOX_BIND: bind address (default loopback 127.0.0.1).
- OPENMANUS_SANDBOX_PORT: HTTP port (default 8765).

Build the execution image from backend/sandbox_runner/Dockerfile, tag it openmanus-code-runner:latest, then run the runner service in a separate trusted process. Set OPENMANUS_SANDBOX_URL and the same OPENMANUS_SANDBOX_TOKEN in the API environment. If the API runs in a container, loopback refers to that container; configure a private reachable endpoint and restrict access with host/network firewall rules. Do not bind the runner to a public interface without additional transport protection.

## Current limitations

- The protocol currently supports one-shot command jobs, not preview lifecycle management. Open-Manus preview start/stop/status intentionally fails closed until a local-only port proxy is implemented.
- Network access is disabled inside job containers. Dependency downloads are therefore not supported by this runner yet; do not enable arbitrary egress to work around this.
- Resource limits currently include CPU, memory, PID count, file descriptors, wall-clock time, and captured result truncation. A hard project disk quota and robust per-host concurrency admission control still need implementation and integration tests.
- The runner has not been validated on a live Docker daemon in CI. Unit tests of request validation are not proof of sandbox isolation.
