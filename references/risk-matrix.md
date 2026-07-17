# Engineering Risk Matrix v1

Score risk before editing and revise the score when discovery changes scope. Count each distinct factor once.

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

## Classify

- `0-1`: Low risk.
- `2-4`: Medium risk.
- `5+`: High risk.

Always classify as high risk when the next action would directly mutate production, perform an irreversible external operation, expose secrets, or weaken a security boundary, regardless of total score.

Read-only investigation does not inherit the risk of a future mutation. Score the action currently authorized, then rescore before implementation or deployment.

Record the decision as one compact line:

```text
Risk: medium (4) - shared API +2, weak tests +1, unfamiliar dependency +1
```
