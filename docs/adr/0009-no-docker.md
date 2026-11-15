# ADR-009 — No Docker image: nothing runs a container

- **Status:** accepted
- **Date:** 2026-11-15

## Context

Reviewing the repo conventions at the end of Phase 1 raised the question of
whether the project should ship a Docker image and a `make deploy` that builds,
versions and pushes it, as many data projects do.

Everything that runs this project runs it without a container runtime of
ours:

- **The pipeline** runs as a plain process on a GitHub-hosted runner
  (ADR-002): `uv sync --frozen`, then `python -m power_market_lens.flows.*`.
- **The app** runs on Streamlit Community Cloud, which builds its own
  environment from `app/requirements.txt` and does not accept an image.
- **dbt** runs inside the pipeline process against MotherDuck, a managed
  service.

Reproducibility, the usual reason for an image, already comes from
`requires-python = ">=3.13,<3.14"`, exact pins in `pyproject.toml`, `uv.lock`
for transitive dependencies, `uv sync --frozen` in CI, and a test that keeps
`app/requirements.txt` equal to the pins.

## Decision

No Dockerfile, no image registry, no `make deploy`. Merging to `main` is the
deploy: Streamlit Cloud redeploys the app from `main` and the scheduled
Pipeline runs `main`. Versions are named by tags and GitHub Releases
(`make release-pr`, CONTRIBUTING.md), which ship nothing by themselves.

## Alternatives considered

- **Image for the pipeline, run with `container:` in GitHub Actions** — adds a
  build-and-push step and GHCR storage to replace `uv sync`, which the uv cache
  already makes fast. The image would pin system libraries too, but every
  native dependency (DuckDB, pandas, pydantic-core) ships as a self-contained
  manylinux wheel.
- **Image for the app** — Streamlit Community Cloud cannot run it. Moving the
  app to a host that can (Cloud Run, Fly.io) breaks the zero-cost rule or adds
  a service for no user-visible gain.
- **Dev container for contributors** — `make install` already sets up the
  whole environment in one command with uv. A dev container would duplicate it.

## Consequences

- One fewer artefact to build, version, scan and explain. The deploy story is
  "merge to main".
- The runtime environment is the GitHub runner image and Streamlit Cloud's
  base image, which change under us. Accepted: Python and every package are
  pinned. Only the OS layer floats, and nothing here depends on it.
- Revisit if the project gains something that does run containers, such as a
  self-hosted API, a scheduled job outside GitHub Actions, or a host that
  needs an image. That would also be the point to add `make deploy`.
