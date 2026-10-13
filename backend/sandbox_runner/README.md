# Isolated code runner

This is a separate, trusted control plane. It is not part of the Open-Manus API process and must not be exposed publicly.

## Important deployment boundary

The service needs Docker-daemon authority to create and destroy sandbox containers. A compromise of this service can therefore compromise the machine that owns the daemon. For stronger protection, run it on a dedicated VM or a host with no unrelated credentials or workloads. Never mount the Docker socket into the Open-Manus application container or any generated-code container.

The runner expects a project directory root accessible to the runner process. It copies only bounded regular files into a staging directory, excludes environment files and key material, then uses docker cp to populate a size-limited tmpfs inside the job container. The generated process receives no host bind mounts. The runner copies back changed regular files after the container stops; deletions are not propagated. On Docker Desktop, validate project-root visibility with an actual integration run before enabling execution.

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
- Resource limits include CPU, memory, PID count, file descriptors, wall-clock time, bounded tmpfs workspace, bounded source/output size, bounded captured output, and a small concurrent-job semaphore. Hostile integration tests must verify these limits before this can be considered complete.
- The runner has not been validated on a live Docker daemon in CI. Unit tests of request validation are not proof of sandbox isolation.


## Docker Desktop deployment for the existing Open-Manus workspace volume

The optional compose file docker-compose.sandbox.yml assumes the existing named volume is openmanus-data, mounted at /app/OpenManus/workspace by Open-Manus. The runner mounts that same volume at /workspace, so its project root is /workspace/projects. The runner service has the Docker socket; the application container does not. Generated-code containers receive neither the volume nor the socket.

In PowerShell, create the private network and token, then build both images and start the runner:

```powershell
docker network create --internal openmanus-sandbox-control
$env:OPENMANUS_SANDBOX_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(48))"
docker build -f backend/sandbox_runner/Dockerfile -t openmanus-code-runner:latest backend/sandbox_runner
docker compose -f docker-compose.sandbox.yml up -d --build
```

When recreating the Open-Manus API container from your normal deployment command, add these environment values:

```powershell
-e OPENMANUS_SANDBOX_URL=http://openmanus-sandbox-runner:8765
-e OPENMANUS_SANDBOX_TOKEN=$env:OPENMANUS_SANDBOX_TOKEN
```

Keep the API container on its normal network for Ollama/host connectivity, then attach it to the private runner network:

```powershell
docker network connect openmanus-sandbox-control openmanus
```

Do not publish the runner's port to the host. The runner is reachable only to containers attached to the private control network. Keep the token stable across API/runner restarts and store it securely. If you recreate the API container, preserve its existing port binding and openmanus-data volume from your normal deployment. Verify with the live integration tests before enabling generated-code execution on a machine with important data.
