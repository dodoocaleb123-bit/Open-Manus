# ZIP Capability Audit — Phase 1 Baseline

**Audit date:** 2026-10-13  
**Repository:** `dodoocaleb123-bit/Open-Manus`  
**Baseline branch:** `main` (audit changes are isolated on `audit/phase-1-capability-baseline`)  
**Input specification:** `Open Manus Description.zip`, including `OPENMANUS APP BUILDING INSTRUCTIONS AND DESCRIPTION.docx`, six Design Intelligence markdown files, and the GUI behavior recording.

## Executive summary

The repository has a substantial documented architecture: DeepSeek-first control, distinct research/coding/vision/creative roles, a local API and GUI, SQLite-backed task state, approval/security controls, research/citation tools, coding workspaces, previews/artifacts, GitHub workflows, and a separate isolated runner. The docs also correctly state that Llama does not itself generate image pixels; real image generation needs a configured image service.

**Important distinction:** this is a source-and-contract audit, not a claim that every advertised capability has been exercised end-to-end. The repository docs and deterministic tests do not prove live Ollama inference, connected third-party providers, GUI fidelity, successful .pptx/.pdf generation, or Windows Docker Desktop deployment. Those remain verification gates.

## Evidence and test baseline

- Repository documentation inspected: `README.md`, `docs/ARCHITECTURE.md`, `docs/PHASE_1.md`, `docs/PHASE_12.md`, `docs/PHASE_15.md`, `docs/PHASE_17.md`, `docs/PHASE_18.md`, `scripts/check_environment.py`, `pyproject.toml`, and `backend/openmanus-core/README.md`.
- Specification extracted from the uploaded DOCX; ZIP also contains six design-intelligence files and a nested GUI behavior ZIP.
- Latest recorded CI run on this audit branch: [run 38334279283](https://github.com/dodoocaleb123-bit/Open-Manus/actions/runs/38334279283). Linux pytest, Windows pytest, and Linux Docker sandbox integration all completed successfully. The sandbox job built both images, validated the private runner compose configuration, and ran live Docker isolation tests. The earlier main-baseline run [38332207875](https://github.com/dodoocaleb123-bit/Open-Manus/actions/runs/38332207875) also passed.
- Test strategy documented in `docs/PHASE_18.md` uses deterministic local fake adapters/fixtures so CI does not require Ollama, browser credentials, GitHub credentials, or internet access.
- **Local re-run status: BLOCKED in this audit environment.** A shallow clone attempt failed because this execution environment cannot resolve `github.com`; therefore I did not execute the current checkout's pytest suite locally and will not invent a local pass/fail count. The CI result above is the verified test baseline available.
- **Laptop deployment status: NOT VERIFIED by this audit.** CI does not validate the user's Docker Desktop, current Ollama model tags, memory pressure, preview URL routing, or the full local GUI.

## Requirement-to-implementation map

Status labels:
- **Documented / designed:** repository docs identify an implementation boundary; not proof of every path working.
- **Partially supported / provider-gated:** some workflows exist, but complete capability depends on a provider, runtime, or missing end-to-end proof.
- **Unverified:** source/docs are insufficient to claim working behavior without targeted tests.
- **Not evidenced:** no reliable evidence in the inspected files that the full requested capability is implemented.

| Requirement from ZIP | Repository mapping / evidence | Audit status and next verification |
|---|---|---|
| DeepSeek receives every user request first; sole planner; context, decomposition, delegation, progress, synthesis, recovery | `docs/ARCHITECTURE.md`; `docs/PHASE_3.md`, `PHASE_4.md`, `PHASE_6.md`; controller, protocol and conversation boundaries described | **Documented / test required.** Prove with tests that no keyword/router bypasses DeepSeek and specialist outputs return to DeepSeek before user-facing claims. |
| Five model roles: DeepSeek controller, Qwen research, Qwen coder, Gemma vision, Llama creative | `backend/model_adapters/platform_knowledge.py` is named as canonical role registry; role guides in `docs/PHASE_9.md`–`PHASE_12.md` and `PHASE_18.md` | **Documented / live models unverified.** Align configured model tags with actual Ollama tags and exercise each adapter independently. |
| Research, web search, page extraction, multi-source comparison, citations, dynamic browser | `backend/research`; `docs/PHASE_10.md`, architecture, phase-18 matrix | **Documented / provider and live browser unverified.** Validate real sources, citation-to-source integrity, browser fallback and dynamic-page extraction. Parallel wide-scale research is not proven by a role name alone. |
| Coding: multi-file projects, frontend/backend/API/database/auth, dependency handling, tests/build/repair, versioning and export | `backend/coding`; `docs/PHASE_11.md`; coding tools described in architecture | **Documented / workflow coverage incomplete until tested.** Verify supported project types, network-restricted dependency install behavior, bounded repair loops, snapshots/rollback and archive integrity. |
| Website preview, desktop/mobile viewport, screenshots, source/console views, typed artifacts, download | `docs/PHASE_15.md`; `GET /api/workspace/download?project=...`; Playwright optional | **Documented / runtime-dependent.** Screenshot explicitly depends on Playwright/browser. Verify real preview readiness, URL access, screenshots, logs, artifact persistence and browser-level GUI behavior. |
| Six design-intelligence documents applied to coding and creative workflows | `docs/design-intelligence/` and README says six supplied docs retained | **Source retained; application not yet demonstrated.** Trace each document into prompts/tool calls and add design acceptance tests; mere file presence is not evidence the models use them. |
| Gemma image, screenshot, PDF/document and visual analysis | `backend/attachments`; `docs/PHASE_9.md`, `PHASE_18.md` | **Documented / live multimodal proof needed.** Test each allowed attachment type, OCR/text extraction, visual question-answering, and ensure only the current message's attachment is used. |
| Llama creative briefs, copy, visual direction, image prompts and presentation structures | `backend/creative`; `docs/PHASE_12.md` | **Documented.** Verify outputs are structured and revisions are routed through DeepSeek. |
| Actual image generation/editing, graphics, logos and product visuals | `creative_generate_image` requires `OPENMANUS_IMAGE_BASE_URL`, key and model; `docs/PHASE_12.md` explicitly says Llama alone cannot generate pixels | **Provider-gated.** Do not claim generated images unless an actual image file is returned and validated. Image editing/transformation needs separate explicit evidence. |
| Actual PowerPoint (.pptx) and PDF export with slide layouts/charts/visuals | `docs/PHASE_12.md` mentions presentation structures; `docs/PHASE_15.md` has a typed `presentation` artifact | **Unverified / possible gap.** Verify a real valid .pptx and PDF with expected slides/assets, not only an outline or generic artifact type. |
| GitHub repo selection, diffs, local commits, approval-gated pushes, push retry | `docs/ARCHITECTURE.md` and `docs/PHASE_14.md`; GitHub workflow is described | **Documented / live credentials and full journey unverified.** Test no-repository state, selection, diff, approval/rejection, push success/failure, retry and truthful confirmation. |
| Sensitive-operation approvals, permissions, auth, ACLs, secrets, audit, rate limits | `backend/security`; `docs/PHASE_13.md`, `docs/PHASE_17.md` | **Documented.** Security guide says code execution fails closed unless `OPENMANUS_ALLOW_UNSANDBOXED_CODE=true`; this is a gate, not a sandbox. Verify auth and permission boundaries with adversarial tests. |
| Isolated generated-code execution and previews | Separate trusted runner and Docker sandbox CI; run 38332207875 | **CI verified on Linux integration; target Windows deployment unverified.** Dependency downloads fail closed by default; confirm network policy and runner reachability on the laptop. |
| GUI matching supplied recording: conversation, task activity, contextual panels, code/files/preview/logs and approvals | Frontend/API boundary described in architecture; `docs/references/gui-behavior-analysis.md` listed | **Unverified visually and behaviorally.** Map every reference interaction to a component, API, persisted field and browser test; mark visual-only controls. |
| SQLite persistence, restart-safe context, event/activity/artifact records | `SQLiteStateStore` and architecture persistence boundary | **Documented / recovery test required.** Verify process restart while running/waiting, attachment/artifact references, cancellation and recovery from corrupted/missing files. |
| Scheduled tasks, event/email/calendar/Slack/Notion workflows, cross-app automation, persistent cloud computer | README describes durable local schedules and connector-neutral boundaries in phase 16; ZIP asks for numerous real integrations | **Partial / provider-gated / not evidenced end-to-end.** Inventory each connector separately. Do not imply an integration is active because a boundary or schedule table exists. |
| Maps, notifications, storage, voice-to-text, mobile/iOS/Android and device features | Listed in ZIP; inspected docs do not establish a complete live provider workflow for each | **Not evidenced as end-to-end in inspected evidence.** Explicitly mark unsupported or create provider-specific implementation and tests. |
| Data collection/cleaning/statistics/spreadsheets/financial models/charts | ZIP assigns broad analysis to coder | **Unverified / underspecified.** Add a supported-input/output matrix and tests for numerical correctness, file import/export and chart artifacts. |
| Local deployment, startup checks, model tags, resource limits and no-secret handling | `scripts/check_environment.py`, `.env.example`, `docs/SETUP.md`, `pyproject.toml` | **Static checks documented; target-laptop deployment unverified.** Python requirement is `>=3.12`; verify current Python version/dependencies, installed model tags, Docker services and disk/RAM limits. |

## Configuration reconciliation

The ZIP describes `deepseek-r1:1b` as the controller; the user's supplied local environment currently sets `deepseek-r1:7b`, `qwen2.5:3b`, `qwen2.5-coder:7b`, `gemma3:4b`, and `llama3.2:3b` (the installed tag was also previously reported as `llama3.2:latest`). This is not automatically a defect: model size/tag may be a deliberate resource/performance choice. The app must, however, use one canonical mapping and explicitly report missing tags. Do not silently switch the controller model.

The repository's `pyproject.toml` declares Python `>=3.12`, pytest/pytest-asyncio/Ruff as dev extras, and cryptography as a base dependency. Environment check defaults to the Ollama-compatible local endpoint and treats Ollama as optional for static Phase 1 validation.

## Prioritized issue list

1. **P0 — Security/deployment gate:** verify the isolated runner and preview route on the target Windows Docker Desktop laptop; preserve fail-closed behavior and existing `openmanus-data` volume. Related existing issue: [#2](https://github.com/dodoocaleb123-bit/Open-Manus/issues/2).
2. **P1 — Five-role end-to-end acceptance and test baseline:** add a requirement-to-test matrix and opt-in live Ollama smoke tests; prove DeepSeek-first delegation and cross-role flows. [Issue #4](https://github.com/dodoocaleb123-bit/Open-Manus/issues/4).
3. **P1 — Model tag/config normalization:** align spec, `.env.example`, adapters, setup docs, GUI status and actual Ollama tags without silently changing models. [Issue #5](https://github.com/dodoocaleb123-bit/Open-Manus/issues/5).
4. **P1 — GUI behavior verification:** map reference recording to real component/API/state/test; remove or label placeholders. [Issue #6](https://github.com/dodoocaleb123-bit/Open-Manus/issues/6).
5. **P1 — Capability truth / external integrations:** verify real image and .pptx/.pdf outputs, and classify maps, notifications, voice, mobile, email/calendar/Slack/Notion and durable automations by actual provider/test status. [Issue #7](https://github.com/dodoocaleb123-bit/Open-Manus/issues/7).
6. **P2 — Design intelligence usage:** prove the six design documents influence design decisions and add representative design-quality checks.
7. **P2 — Workflow durability and data analysis:** restart/cancel/retry tests for schedules and task recovery; define and test data-analysis inputs/outputs.
8. **P2 — Resource/latency baseline:** measure first-response latency, total task duration, RAM/disk, token/context limits and concurrency on the 16 GB target laptop; no performance claim until measured.

## Reproducible baseline to preserve

| Dimension | Baseline |
|---|---|
| Audit date | 2026-10-13 |
| Latest recorded CI | [38334279283](https://github.com/dodoocaleb123-bit/Open-Manus/actions/runs/38334279283) — audit branch |
| Linux pytest | Passed in CI |
| Windows pytest | Passed in CI |
| Linux Docker isolation integration | Passed in CI |
| Local test execution in this audit environment | Blocked: cannot resolve `github.com` to clone checkout |
| Live Ollama model smoke tests | Not run / not established by deterministic CI |
| Live browser, image provider, GitHub credentials | Not run / provider-dependent |
| Windows Docker Desktop target laptop | Not verified |
| GUI browser E2E / pixel-level comparison | Not run |
| .pptx/.pdf output validation | Not established |
| Model tags, Python version, RAM/disk and latency on target laptop | Must be measured during deployment verification |

## Definition of audit completion

- [x] Extract and map the user's ZIP specification at capability-category level.
- [x] Inspect architecture, phase guides, environment checker and project test strategy.
- [x] Record current CI result and distinguish it from local/laptop verification.
- [x] Create prioritized actionable GitHub issues.
- [x] Re-run CI on the audit branch after the runtime-level addendum: Linux pytest, Windows pytest, and Linux Docker sandbox integration all passed in run [38334279283](https://github.com/dodoocaleb123-bit/Open-Manus/actions/runs/38334279283).
- [ ] Inspect every runtime module and every test directly in a runnable checkout; source-level review is targeted, not exhaustive.
- [ ] Execute the full test suite locally and publish exact counts; blocked in this audit environment by inability to resolve `github.com`.
- [ ] Run live model/provider and browser-level tests.
- [ ] Validate the GUI against the complete recording.
- [ ] Validate actual .pptx/.pdf/image artifacts and each named integration.

The unchecked items are explicit remaining audit work, not assumed passes.


## Runtime-level inspection addendum (2026-10-13)

Additional direct source files inspected:
- `backend/model_adapters/types.py`
- `backend/model_adapters/controller.py`
- `backend/model_adapters/platform_knowledge.py`
- `scripts/check_environment.py`
- `.env.example`
- `pyproject.toml`
- `docs/PHASE_9.md` through `docs/PHASE_18.md` (targeted phase guides).

### Source-level findings

1. **Controller boundary has a useful guard.** `DeepSeekController` rejects a non-controller adapter and rejects caller-supplied system messages before adding the platform system prompt. This is a concrete source-level invariant; it does not by itself prove all production request paths use this facade.
2. **The environment checker is not a model readiness check.** `scripts/check_environment.py` checks required paths and whether an Ollama-compatible `/api/tags` endpoint is reachable, but it does not parse the returned tags to verify that all five configured models are installed. It also prints model names from environment variables without loading `.env` itself. A reachable endpoint can therefore still conceal missing model tags.
3. **Model tag is a real consistency risk, not yet a proven runtime failure.** `.env.example` defaults creative to `llama3.2:3b`; the previously reported laptop tag was `llama3.2:latest`. These may resolve to compatible weights, but tag equivalence must be checked against `/api/tags`; don't silently change the user's config. Controller `deepseek-r1:7b` is explicitly allowed by the example, while the ZIP's target configuration names `deepseek-r1:1b`.
4. **Actual image generation is intentionally external.** `creative_generate_image` needs `OPENMANUS_IMAGE_BASE_URL`, API key and model. The app should report this dependency instead of presenting an image prompt as an image.
5. **Automation is durable scheduling, not autonomous always-on execution by itself.** `docs/PHASE_16.md` says schedules create normal DeepSeek tasks and a local service runner/OS timer must call `/api/schedules/run-due`. Connector-neutral interfaces and enabled flags do not establish live email/calendar/Slack/Notion connections.
6. **Browser and presentation outputs have explicit limits.** Research dynamic-page inspection depends on Playwright and otherwise falls back to static extraction; screenshot capture also requires Playwright/browser. The inspected docs describe presentation structures and typed artifacts but do not establish a verified, valid `.pptx`/PDF exporter.
7. **Security docs distinguish an execution gate from a sandbox.** `docs/PHASE_17.md` explicitly states unsandboxed execution, when opted in, retains the Open-Manus process's OS permissions. The newer separate Docker runner has Linux CI coverage, but Windows laptop deployment remains a separate acceptance gate.
8. **The dependency manifest is intentionally light.** `pyproject.toml` sets Python `>=3.12`, with pytest/pytest-asyncio/Ruff in dev extras and `cryptography` as a base dependency. Runtime dependencies also live in `backend/openmanus-core/requirements.txt`; installing only the root project's base dependencies should not be treated as a complete deployment.

### CI rerun on the audit PR

The audit PR commit `99bb4ca2f0dd46fa3608dbf6030336af8b2ea104` triggered [workflow run 38334279283](https://github.com/dodoocaleb123-bit/Open-Manus/actions/runs/38334279283). The following jobs all completed successfully:
- `pytest (ubuntu-latest)`
- `pytest (windows-latest)`
- `Docker sandbox integration (Linux)`

This confirms the repository's CI checks pass on the audit branch. The connector returns job/step conclusions but not the test runner's printed summary, so **exact test counts remain unavailable** from this evidence. No claim is made that live Ollama, the image service, browser E2E, generated PowerPoint/PDF, or target laptop was tested. The workflow connector exposes job and step conclusions but not the printed test summary, so exact test counts remain unavailable.

### Runtime-level priority adjustment

- **P0:** Enhance the environment check to compare configured role tags with actual `/api/tags`, redact sensitive config, and produce a machine-readable readiness result.
- **P0:** Run the current project test suite on the user's actual Windows checkout and execute the separate runner/preview smoke test without deleting or recreating `openmanus-data`.
- **P1:** Add opt-in five-role Ollama smoke tests and a cross-role acceptance flow; keep standard CI deterministic/offline.
- **P1:** Prove actual GUI task-state/API wiring, valid generated artifacts, and provider availability before broad feature work.
- **P2:** Add measurements for first-token latency, task duration, memory/disk and cancellation/restart recovery on the target 16 GB laptop.
