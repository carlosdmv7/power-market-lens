<!--
Title: <type>(<scope>): <what changed>, e.g. "fix(app): average the repeated DST hour".
It becomes the squash commit on main and a line in the release notes.
Types: feat, fix, perf, refactor, docs, test, ci, chore. See CONTRIBUTING.md.
-->

## What and why

<!-- The change and the reason for it. Link the issue or ADR if there is one. -->

## How it was verified

<!-- What you ran and what you saw: `make check`, `make dbt-build`, `make app`,
a query against power_dev, a screenshot. "Tests pass" alone is not enough. -->

## Checklist

- [ ] `make check` passes locally
- [ ] dbt: every new model and column has a description; keys have `unique` and `not_null`
- [ ] New env vars are in `.env.example`
- [ ] Trade-off decisions have an ADR in `docs/adr/`
- [ ] Breaking change? The title has `!` and the body says what breaks

## Left for later

<!-- Anything deliberately not done here. Delete if nothing. -->
