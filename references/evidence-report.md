# Evidence Report

Use this structure for full-workflow tasks: medium risk scored `4`, high risk, or a lower-scored task whose interacting risks require escalation. Keep it internal during execution and summarize only material fields to the user unless a report artifact is requested.

## Required fields

```markdown
Impact: <low|medium|high> (<score>)
Factors: <scored factors>
Execution: <local/reversible|external/state-changing>
Authorization: <request covers local work|exact external authorization and target>

Contract:
- Requested behavior: <observable outcome>
- In scope: <files, modules, systems>
- Out of scope: <important exclusions>

Evidence:
- <fact> - <repository path, command, installed source, or official source>

Changes:
- <behavioral change and location>

Verification:
| Check | Result | Evidence |
|---|---|---|
| <command or observation> | pass/fail/skipped | <key output or reason> |

Baseline failures:
- <pre-existing, flaky, unrelated, or none observed>

Limitations:
- <unverified assumption, missing authority, or residual risk>

Rollback: <required for external state-changing work; otherwise not applicable>
```

## Rules

- Cite evidence that another engineer can reproduce.
- Distinguish deterministic or independent verification from self-checked reasoning; never present self-review as independent sign-off.
- Separate direct observation from unverified assumptions or inference; never present an assumption as evidence.
- Note any conflicts discovered between docs/examples and actual installed code or runtime output.
- Never convert a skipped or blocked check into a pass, and state explicitly when a required fact is unknown or inaccessible.
- Report failures caused by the change separately from baseline failures.
- Redact secrets, tokens, cookies, credentials, personal data, and sensitive connection details.
- Do not create an evidence-report file in the repository unless the user requests one.
