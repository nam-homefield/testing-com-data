## Verdict: Needs another pass

1. A test fed two failure labels can pass while the workflow misses the second email. For example, the workflow could read the first run's green status as a successful deploy. Prove that the workflow carries the actual deploy result into the next run and sets the final status from it. Fails: “The check of the "once or twice in a row" rule is shown failing before the change and passing after it.” Where: “a test of the decision logic covering one failure, two in a row” and the deploy steps in pages.yml. Always must-fix because the check can pass on broken output. [Current workflow](https://github.com/nam-homefield/testing-com-data/blob/ab4df52e3d6086f4c7dcda25a7d570b1748cc9c6/.github/workflows/pages.yml#L59-L73), [GitHub step results](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#steps-context).
2. If a newer push cancels a run after its deploy succeeds, skipping that run can erase the success. The next isolated failure could then send an email. Fails: “every failed deploy after that until one works.” Where: “A cancelled (superseded) run is not a failure and is skipped when looking back.” Often 2, bad 4, product 8. [GitHub workflow cancellation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation).

## Known, not fixed

- “so GitHub emails” promises inbox delivery. GitHub bases delivery on who started the run and their settings. Nam triggered all 387 runs in the draft's window. Inbox delivery remains unverified. Often 1, bad 3, product 3. [GitHub workflow notifications](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs).

## What Nam did not ask for

- “The report site keeps updating after every update, as it does today.” The Goal asks about email frequency. GitHub already skips superseded updates. [Cancelled main run](https://github.com/nam-homefield/testing-com-data/actions/runs/43095722533).
- “plus any test or helper file the builder adds.” The Goal supports a test of the email rule. It gives no basis for an open-ended file list.
- “install it or skip it and say which.” The Goal gives no basis for installing a new local tool.
- “Technical details” is longer than the visible ticket. The Goal gives no basis for carrying the full incident history in the ticket.

## Break cases

- Guards the live site's failure emails. The test must go red if the first failure sends an alert, a second or later failure is suppressed, or a successful deploy fails to reset the streak.
- The test must also go red if a skipped deploy step hides an earlier job failure, or if a green run hides the failed deploy from the next run. Use an artifact wait failure as the first example.
- Test cancellation on both sides of the deploy result. A run cancelled before a deploy finishes should leave the streak alone. A deploy that already succeeded should reset it even if its run is cancelled afterward.
- Replay the unedited GitHub run and job records. Success, isolated failure and recovery should all be quiet. The run cancelled before any job started should leave the streak unchanged. [Success](https://github.com/nam-homefield/testing-com-data/actions/runs/45413361106), [Failure](https://github.com/nam-homefield/testing-com-data/actions/runs/45415969592), [Recovery](https://github.com/nam-homefield/testing-com-data/actions/runs/45418761018), [Cancellation](https://github.com/nam-homefield/testing-com-data/actions/runs/43095722533).