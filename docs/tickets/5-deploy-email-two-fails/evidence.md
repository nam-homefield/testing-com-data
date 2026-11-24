# Evidence for the design of ticket 5

Everything here was read on 2026-11-24 from GitHub's own docs or from the GitHub API. Nothing was run on GitHub Actions.

## What GitHub's docs say (docs source files in github/docs, main)

- Workflow syntax, `jobs.<job_id>.continue-on-error`: "Prevents a workflow run from failing when a job fails. Set to `true` to allow a workflow run to pass when this job fails."
- Expressions, status check functions: "A default status check of `success()` is applied unless you include one of these functions." The verdict job uses `!cancelled()`, so a failed deploy job does not stop it. The docs recommend `!cancelled()` over `always()`.
- Contexts, `steps.<step_id>.conclusion` and `.outcome`: with a step level `continue-on-error`, a failed step's conclusion becomes success. This is why the deploy steps get no step level `continue-on-error`. Only the job has it, and the job's steps keep their real result.
- Workflow syntax, `jobs.<job_id>.concurrency`: "You can use `jobs.<job_id>.concurrency` to ensure that only a single job or workflow using the same concurrency group will run at a time." The concurrency concept page adds: "any additional pending runs cancel the previous one". Round 2 moves the block from the workflow to the deploy job so a newer push cancels a deploy and never the verdict job of a run whose deploy already ended.
- Notifications for workflow runs: a notification is sent when a run completes, with its status. The email follows the run's status, so a run that ends green sends no failure email. Delivery still depends on who started the run and their notification settings (not verified).

## Precedent for a job level `continue-on-error` (real public run)

Repo woocommerce/woocommerce-paypal-payments, run 35720477371, workflow `weekly-builds.yml`. The job `trigger-e2e` has `continue-on-error: true` at job level (line 117 of the workflow file). In that run:

- the run's conclusion is `success`
- the job "Trigger E2E tests (all:parallel)" has conclusion `failure`
- its step "Trigger E2E workflow" has conclusion `failure`

So the jobs API keeps the failed job and failed step on record while the run ends green. That is what the verdict job reads.

## Real cancelled runs of this workflow

All 40 cancelled runs of `pages.yml` in this repo, jobs read from the API:

| Count | Shape |
|---|---|
| 1 | no jobs at all (run 43095722533, cancelled before any job started) |
| 28 | job cancelled, deploy-pages step never recorded |
| 3 | job cancelled, deploy-pages step cancelled |
| 7 | job cancelled, deploy-pages step succeeded (cancelled after a good deploy) |
| 1 | run marked cancelled, job itself success with a successful deploy |

None of the 40 has a failed step. So a cancelled run never looks like a failure to the verdict job. Real records of the first four shapes are in `fixtures/` and the test replays them.

## Run of the real script against the real API (read only)

`live-replay.txt`. The test's `--live` mode gives the script the real answers of the two API calls it makes, for runs 45413361106 (good), 45415969592 (the one failure) and 45418761018 (recovery). All three stay quiet, and the failure run finds the good run before it.

## Only provable after the change is on main

- The verdict job starts after a failed deploy job. Only the docs above say so.
- A newer push, with concurrency on the deploy job only, cancels the older deploy and does not stop the older run's verdict job. Only the docs above say so.
- The verdict job's token may read runs and jobs (`actions: read`). The same calls worked here with the account's own token.
- The verdict job's read of its own run returns the finished deploy job. The first run after the merge must log "This deploy: good." in the verdict step. If it logs that the deploy job is not final, the read is slow or wrong, and the step then falls back to the needs context result, which may show `success` for a failed continue-on-error job. That case would stay quiet, so check the log.
- The email is really sent on the second failure and not on the first.
