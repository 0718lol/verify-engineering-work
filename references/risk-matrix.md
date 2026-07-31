# Engineering Risk Matrix v1.2

This is a routing heuristic, not an objective safety score. Score impact risk before editing, revise it when discovery changes scope, and count each distinct factor once. Raise the classification when interacting risks make the sum understate blast radius or recovery difficulty. Classify execution risk separately so a high-impact local patch is not confused with an external state change.

## Add 3 points

- Mutate production state, external customer data, infrastructure, billing, or access control.
- Perform an irreversible or destructive operation without a proven rollback.
- Change authentication, authorization, secrets, cryptography, privacy, or another security boundary.
- Migrate persistent data or alter a production schema.

## Add 2 points

- Change a shared or public API, schema, protocol, package interface, or compatibility contract.
- Modify three or more coupled modules or services.
- Change concurrency, distributed coordination, caching consistency, retries, or idempotency.
- Change deployment, CI/CD, build, packaging, dependency resolution, or runtime configuration.
- Handle sensitive data even when the task does not directly change a security boundary.

## Add 1 point

- Use an unfamiliar, version-sensitive, recently changed, or poorly documented dependency.
- Work in an area with missing, flaky, or weak automated tests.
- Depend on credentials, ports, services, platform-specific behavior, timing, or external network state.
- Change a performance-sensitive path or resource limit.
- Continue after two materially similar failed attempts.
- Fulfill an explicit request for production readiness or strong completion evidence.

## Classify impact

- `0-1`: Low risk.
- `2-4`: Medium risk.
- `5+`: High risk.

Always classify impact as high when incorrect behavior could mutate production, perform an irreversible operation, change authentication or authorization behavior, rotate or expose secrets, modify cryptography or privacy controls, or weaken another security boundary, regardless of total score.

## Classify execution

- `Local/reversible`: inspect or edit files, run tests, or prepare an undeployed patch in the identified workspace. The user's implementation request authorizes these normal local actions; do not ask again solely because impact is high.
- `External/state-changing`: mutate production or another external system, customer data, infrastructure, billing, access control, secrets, or persistent data outside the workspace. Require explicit authorization for the exact target plus a viable rollback or recovery path.

Never infer deployment or external-write permission from authorization to prepare a local patch. Without the required external authorization and recovery path, limit work to read-only investigation, local artifacts, or an execution plan.

Read-only investigation does not inherit the execution risk of a future mutation. Rescore impact and execution before implementation, deployment, or any newly discovered external write.

Record the decision internally as one compact line. Expose it only when the classification changes authorization, scope, or verification depth:

```text
Impact: medium (4) - shared API +2, weak tests +1, unfamiliar dependency +1
Execution: local/reversible - identified workspace only
```
