# Contributing

## The loop

```bash
# 1. Always branch from a fresh main. Never branch from a branch.
git checkout main && git pull
git checkout -b feat/short-description

# 2. ... do the work ...

# 3. The gate — the same commands CI runs
make check

# 4. Commit (conventional commits: feat / fix / docs / refactor / test / chore / ci)
git add -A
git commit -m "feat(scope): what changed, from the user's point of view"

# 5. Publish and open a PR
git push -u origin feat/short-description
gh pr create --fill

# 6. Merge once CI is green
gh pr merge --merge --delete-branch
git checkout main && git pull
```

## Rules

- **`main` is always green.** Nothing lands without a passing CI run.
- **Secrets live in `.env`** (git-ignored). New keys go into `.env.example`
  with a comment saying where to get them.
- **Dependencies through uv only**: `uv add pkg` / `uv add --dev pkg`. Commit
  `uv.lock` with the change.
- **Non-obvious decisions get an ADR** — copy `docs/adr/0000-template.md`.
- **Verify by running.** Green unit tests are not proof the thing works; run it.
