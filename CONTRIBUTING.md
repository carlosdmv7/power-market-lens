# Contributing

How changes get from an idea to `main`, and from `main` to a release.

## The loop

```bash
# 1. Always branch from a fresh main. Never branch from a branch.
git checkout main && git pull
git checkout -b feat/zone-price-map          # <type>/<short-kebab-description>

# 2. ... do the work, commit as often as you like ...

# 3. The gate: the same commands CI runs
make check

# 4. Publish and open a PR. Its title is the commit that lands on main.
git push -u origin feat/zone-price-map
gh pr create --title "feat(app): map page comparing one day's price across zones"

# 5. Squash-merge once CI is green
gh pr merge --squash --delete-branch
git checkout main && git pull
```

Two pieces of work at once (say, a dbt PR waiting for review while you start on
the app) go in separate worktrees, not in one directory switching branches:
`git worktree add ../power-market-lens-<topic> -b <type>/<topic> origin/main`.

## Types

One list, used for branch prefixes, commit messages and PR titles. The
commit-msg hook and the CI `PR title` check reject anything else.

| Type | Use it when | Example |
|---|---|---|
| `feat` | Something new a user of the app, the warehouse or the pipeline can see or use: a page, a mart, a column, a flow option. | `feat(dbt): fct_interchange_hourly with net flows per border` |
| `fix` | Something was wrong and now is right. Includes wrong data. | `fix(app): average the repeated DST hour instead of summing it` |
| `perf` | Same result, faster or cheaper. | `perf(ingest): fetch flow directions concurrently` |
| `refactor` | Same behaviour, better code. No user-visible change. | `refactor(ingest): one parser per ENTSO-E document type` |
| `docs` | README, ADRs, runbook, docstrings, dbt descriptions only. | `docs(adr): 0008 no Docker` |
| `test` | Tests or fixtures only. | `test(dbt): unit test for the DST day in int_prices__hourly` |
| `ci` | GitHub Actions workflows, pre-commit, Makefile targets. | `ci(pipeline): step timeouts on every run` |
| `chore` | Maintenance that fits nowhere else: dependency bumps, releases, config. | `chore(deps): bump duckdb to 1.5.7` |

When a PR mixes types, title it with the one a reader of the changelog most
needs to know about: a `feat` with its tests and docs is a `feat`.

**Scopes** (optional, in parentheses): `ingest`, `dbt`, `flows`, `app`, `ml`,
`pipeline`, `adr`, `readme`, `deps`, `release`. Add a new one when none fits.

**Breaking changes**: add `!` after the type (`feat(dbt)!: rename price_eur to
price_eur_mwh`) and say in the PR body what breaks and what to do. In this
project that means a mart column the app or a reader depends on changes name,
type or meaning.

**Subject line**: lower case after the colon, no full stop, under ~72
characters. Say what changed from the reader's point of view, not which files
moved.

## Pull requests

- **Squash merge only.** One PR becomes one commit on `main`, named by the PR
  title. That keeps `main`'s history readable, and it is what release notes are
  generated from. Commits inside a branch can be messy; the title cannot.
- **Small and single-purpose.** If the description needs "and also", it is
  probably two PRs.
- **The body follows the template** (`.github/pull_request_template.md`): what
  and why, how it was verified (the command you ran, not "tests pass"), and
  what is left.
- **dbt changes are reviewed line by line by the owner** before merging.
- **CI must be green.** Nothing lands on a red or cancelled run.

## Releases

A release is a tag `vX.Y.Z` on `main` plus a GitHub Release whose notes list
the PR titles merged since the previous one. Each phase of the project closes
with a release (Phase 1 → `v0.1.0`), and a release can also mark any other point
worth naming.

Versions follow SemVer while in `0.x`: `minor` for a phase or new features,
`patch` for fixes only. `1.0.0` is when the forecast and the public app are both
live.

```bash
make release-pr BUMP=minor   # or patch / major; BUMP=none releases the current version
# → opens a PR "chore(release): vX.Y.Z" that bumps pyproject.toml and uv.lock.
#   Add the phase summary or release notes to it if there are any.
gh pr merge --squash --delete-branch
# → the Release workflow sees the chore(release) commit on main, checks it
#   matches pyproject.toml, then creates the tag and the GitHub Release.
```

There is no deploy step: merging to `main` is the deploy. Streamlit Community
Cloud redeploys the app from `main`, and the scheduled Pipeline runs `main`. A
release names a version; it does not ship anything.

## Rules

- **`main` is always green.** Nothing lands without a passing CI run.
- **Prod (`power_prod`) is written only by the main-branch Pipeline workflow.**
  Never `make dbt-build TARGET=prod` locally.
- **Secrets live in `.env`** (git-ignored). New keys go into `.env.example`
  with a comment saying where to get them.
- **Dependencies through uv only**: `uv add pkg` / `uv add --dev pkg`, exact
  pins. Commit `uv.lock` with the change.
- **Non-obvious decisions get an ADR**: copy `docs/adr/0000-template.md`.
- **Hooks run on every commit** (`make install` installs them): ruff, sqlfluff,
  file hygiene and the commit message check.
- **Verify by running.** Green unit tests are not proof the thing works; run it.
