# Security policy

E2H handles executable workloads, imported agent evidence, filesystem paths, privacy-sensitive text, protocol integrations, and release credentials. Security reports are welcome when they identify a concrete way those boundaries can be bypassed or exposed.

## Supported code

Security fixes target the current `main` branch. Older commits, local forks, and historical release artifacts are not guaranteed to receive backports unless maintainers explicitly state otherwise for a specific issue.

## Reporting a vulnerability

Do **not** post exploit details, credentials, private evidence, tokens, or sensitive logs in a public GitHub issue.

Preferred reporting path:

1. Open the repository's **Security** tab.
2. Use **Report a vulnerability** / GitHub Private Vulnerability Reporting when that option is available.
3. Include the smallest reproducible example that demonstrates the boundary failure.

If private vulnerability reporting is not available, open a public issue titled `Security contact request` containing only a non-sensitive description of the affected E2H area. Do not include exploit steps or confidential material. A maintainer can then establish an appropriate private reporting channel.

A useful private report includes:

- affected E2H component and commit/version when known;
- expected security boundary;
- observed bypass or exposure;
- minimal reproduction steps or artifact;
- realistic impact and prerequisites;
- whether the issue has been disclosed elsewhere.

Please replace real credentials, user data, local paths, and private agent evidence with synthetic equivalents whenever possible.

## Security-relevant areas

Examples of in-scope reports include:

- shell or command-execution boundary bypasses;
- working-directory, path traversal, symlink, archive extraction, or snapshot-restore escapes;
- sandbox/network/identity/resource restrictions that can be bypassed relative to their documented contract;
- secret or PII leakage through redaction, review reports, provenance, traces, logs, or inspection commands;
- malformed evidence/provider payloads that bypass strict validation in a security-relevant way;
- MCP, A2A, runtime, browser, or VS Code integration flaws that expose data or enable unintended execution;
- release-manifest, SBOM, artifact-handoff, OIDC, attestation, or publication-workflow flaws that permit artifact or credential substitution;
- denial-of-service behavior that defeats an explicit E2H input/resource bound.

E2H does not claim that arbitrary candidate code, imported content, model output, or third-party dependencies are inherently trustworthy. A report should describe how E2H violates a documented security boundary rather than only demonstrating that untrusted code can behave maliciously when executed without an appropriate sandbox.

## Local replay trust boundary

MCP/A2A handle-bound local replay protects the identity of the selected workspace and command working directories against pathname rebinding. It is **not** a filesystem, credential, syscall, process, or privilege sandbox.

Local replay commands execute as the MCP/A2A service account with that account's ordinary host permissions. The local runner also inherits the service process environment before applying capsule-declared environment overrides. A command can therefore observe environment values delivered to it and can access host files, processes, network resources, and other state that the service account itself is permitted to access. Descriptor-bound working directories do not change those ambient permissions.

When local replay is enabled, run the verification service under a dedicated low-privilege account or similarly isolated service context, minimize credentials and secrets in that process environment, and do not grant the service account access to host data that replayed capsules are not allowed to reach. Treat every capsule that can be selected through the configured root as executable code trusted to run with those service-account permissions.

The shared MCP/A2A replay host budgets limit aggregate command count, retained output, and declared check timeout. Those bounds reduce one class of resource exhaustion but do not turn local replay into a sandbox or prevent a command from consuming other host resources available to the service account while it runs.

## Container runtime trust boundary

When an E2H deployment delegates container execution or workspace preparation to Docker, the Docker daemon and every principal with unrestricted access to that daemon's API or control socket are part of the trusted operator boundary. Docker-control-plane access can enumerate, mount, modify, remove, or otherwise interfere with daemon-managed containers and volumes; E2H does not claim to isolate replay state from a peer that holds equivalent unrestricted Docker authority.

A remote replay service must therefore use one of these deployment models before Docker-backed replay can be treated as a security boundary:

- keep the Docker API/socket restricted to trusted operator processes, excluding untrusted peer workloads and principals; or
- place Docker behind a narrower broker/helper that exposes only the bounded create, archive-import, run, inspect, and cleanup operations required by E2H and does not grant the replay service ambient access to the general Docker control plane.

Filesystem protections such as descriptor-bound capture and sealed in-memory archives protect against pathname rebinding and same-UID mutation that do not require Docker authority. They do not convert the Docker control plane itself into an untrusted boundary. Runtime documentation and validation must state which deployment model is assumed whenever Docker-backed remote replay is enabled.

MCP/A2A configuration may expose an operator control-plane trust flag. That flag is an **attestation by the deployer**, not a measurement performed by E2H. A true value must never be interpreted as evidence that E2H inspected Docker socket ACLs, daemon authorization, peer capabilities, workload placement, or host policy. The default remains false, and container replay must fail closed when the attestation is absent even if all code-level sandbox checks otherwise pass.

The attestation is also not an override for runtime capability gates. Docker-backed remote replay remains unavailable until its independent code/runtime prerequisites are satisfied; declaring the control plane trusted does not make an unsupported runtime supported.

## Handling reports

Maintainers should reproduce reports with synthetic data when possible, minimize redistribution of sensitive artifacts, and add an adversarial regression test for confirmed boundary failures.

Public disclosure should avoid publishing active credentials, private evidence, or unnecessary exploit material. If a fix requires a release, E2H's verified release workflow should be used so the patched wheel/sdist, SBOM, provenance, and immutable release assets remain auditable.
