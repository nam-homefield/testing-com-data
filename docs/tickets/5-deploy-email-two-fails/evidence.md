# Evidence for the design of ticket 5 (round 3)

Read on 2026-11-24 from GitHub's docs (github/docs, main) and the actions/cache repo. Nothing was run on GitHub Actions.

## Design

Each deploy saves its own result (good or bad) in GitHub's Actions cache, and the next deploy restores the newest one. All of it sits inside the one deploy job. There is no second job and no reading of other runs from GitHub's API. The rounds 1 and 2 designs read other runs' records while GitHub was still writing them, which is where every reviewer finding came from.

## What the docs say, and what it is used for

- Contexts, steps: "When a `continue-on-error` step fails, the `outcome` is `failure`, but the final `conclusion` is `success`." So the five steps up to the deploy carry step level `continue-on-error`, the job stays green, and the decide step still reads each real outcome.
- Expressions, status check functions: "A default status check of `success()` is applied unless you include one of these functions." `always()`: "Causes the step to always execute, and returns `true`, even when canceled." So each deploy path step runs only after the one before it worked (the default `success()` plus the outcome check), and the restore, decide and save steps run even when a newer push cancels the run.
- Workflow cancellation reference: on cancel, "the server re-evaluates `if` conditions for the unfinished steps. If the condition evaluates to `true`, the step continues to run." That is how a deploy that finished well and was then cancelled still saves "good".
- Dependency caching reference, cache key matching: "If there are multiple partial matches for a restore key, the action returns the most recently created cache." And: "You cannot change the contents of an existing cache. Instead, you can create a new cache with a new key." So each run saves under `pages-deploy-result-<run id>-<attempt>` and restores with the prefix `pages-deploy-result-`.
- Same page: caches are scoped to the branch, and `push` and `workflow_dispatch` runs may create caches in the default branch's scope. Entries not accessed in 7 days are removed. There is no limit on the number of entries; the size limit is 10 GB per repo. Each entry here is one word.
- actions/cache README, cache version: the version is a hash of the `path` and the compression tool. Restore and save use the same `path`, `.pages-deploy-result`. actions/cache/restore README: `fail-on-cache-miss` defaults to `false`, so a miss is not an error. Version used: `actions/cache/restore@v6` and `actions/cache/save@v6` (latest release v6.1.0, 2026-06-26, runs on node24).
- Concurrency (workflow syntax reusable): "ensure that only a single job or workflow using the same concurrency group will run at a time." Kept at workflow level with `cancel-in-progress: true`, as on main. With one job, nothing else needs protecting from cancel, and the restore runs after the deploy steps, well after an older run's last steps.

## Real cancelled runs of this workflow

From round 1, all 40 cancelled runs of `pages.yml` here, jobs read from the API: 1 had no jobs, 28 had the deploy step never recorded, 3 had the deploy step cancelled, 8 had the deploy step succeed. None had a failed step. So a cancel never shows as a failure outcome. Records of each shape are in `fixtures/` and the test replays them.

## Only provable after the change is on main

- `always()` steps run after a cancel for this job (the docs above say so; it has no job level `if`).
- A cancelled step's outcome is never `failure` (the 40 real runs above agree).
- A push run can save to and restore from the cache. Look for "Earlier deploy: good." or "No earlier deploy result is saved" in the decide step of the first runs.
- The email really goes out on the second failure and not the first. Delivery depends on who started the run and their settings (unverified, as the ticket says).

## Known, not fixed

- A failure before any step runs (job setup, runner lost) turns the run red with nothing saved, so that one emails at once.
- A second failure cancelled in the seconds before the last step ends as cancelled, so no email for it. If its save step finished, "bad" is saved and the next failure emails.
- If a save fails, the next run reads the result before it.
