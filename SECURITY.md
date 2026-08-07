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

## Handling reports

Maintainers should reproduce reports with synthetic data when possible, minimize redistribution of sensitive artifacts, and add an adversarial regression test for confirmed boundary failures.

Public disclosure should avoid publishing active credentials, private evidence, or unnecessary exploit material. If a fix requires a release, E2H's verified release workflow should be used so the patched wheel/sdist, SBOM, provenance, and immutable release assets remain auditable.
