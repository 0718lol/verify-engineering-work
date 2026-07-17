# Evidence Report

Use this structure for medium- and high-risk tasks. Keep it internal during execution and summarize only material fields to the user unless a report artifact is requested.

## Required fields

```markdown
Risk: <low|medium|high> (<score>)
Factors: <scored factors>

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

Rollback: <required for high-risk state-changing work>
```

## Rules

- Cite evidence that another engineer can reproduce.
- Separate direct observation from inference.
- Never convert a skipped check into a pass.
- Report failures caused by the change separately from baseline failures.
- Redact secrets, tokens, cookies, credentials, personal data, and sensitive connection details.
- Do not create an evidence-report file in the repository unless the user requests one.
