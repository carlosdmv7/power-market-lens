#!/usr/bin/env bash
# Open the pipeline incident issue, or comment on the one already open.
#
#   report_incident.sh "<what happened, in Markdown>"
#
# One open issue at a time: a second failure of the same outage adds a comment
# instead of a new issue. The issue is closed by a person, never by a later
# green run: a scheduled attempt that finds the day's work done exits green
# without proving that what broke is fixed (ADR-007).
#
# Needs GH_TOKEN with `issues: write`, and the variables GitHub Actions sets.
set -euo pipefail

LABEL="pipeline-incident"
summary="$1"
run_url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
body="$summary

Workflow: $GITHUB_WORKFLOW ($GITHUB_EVENT_NAME), [run $GITHUB_RUN_ID]($run_url)"

# --force: create the label, or leave it in place when it already exists.
gh label create "$LABEL" --color B60205 --force \
  --description "The pipeline did not finish, or power_prod is stale"

open_issue=$(gh issue list --label "$LABEL" --state open --limit 1 --json number --jq '.[0].number // empty')
if [ -n "$open_issue" ]; then
  gh issue comment "$open_issue" --body "$body"
else
  gh issue create --label "$LABEL" \
    --title "Pipeline incident, $(date -u +%Y-%m-%d)" \
    --body "$body

Close this issue once the cause is fixed and a run has refreshed the data. Later failures are added here as comments while it stays open."
fi
