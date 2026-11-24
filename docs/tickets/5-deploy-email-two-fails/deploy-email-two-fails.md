# Report site deploy (pages.yml): "Run failed: Deploy Pages - main" email sent after every single failed deploy

Parent: none

**Goal.** Nam asked on 2026-11-24: "stop the emails unless it fails twice in a row." He got a "Run failed: Deploy Pages - main" email from GitHub. Since Nov 21, 14 of 387 report site deploys failed, and each one was followed by a good deploy about 15 minutes later.

**What done looks like.**
- [ ] A deploy that fails once, with the next deploy working, sends Nam no "Run failed" email.
- [ ] When two deploys in a row fail, Nam still gets the "Run failed: Deploy Pages" email, and so does every failed deploy after that until one works.
- [ ] A deploy stopped by a newer update before it finished is skipped when counting. A deploy that finished well and was stopped afterwards counts as a good one.
- [ ] Proof that the deploy itself hands its real result to the next deploy, and that the next deploy ends as failed from it. A test of the rule on its own is not enough.
- [ ] Each check is shown failing before the change and passing after it.
- [ ] All the project's checks pass on the shared version.

**Not part of this.** GitHub email settings on Nam's account. The cause of the one-off failures, which sits on GitHub's side. How often the reports publish.

<details><summary>Technical details</summary>

Files: `.github/workflows/pages.yml` and one test file the builder names. Ticket folder: `docs/tickets/<N>-deploy-email-two-fails/`.
Checks to run: the builder's test, red then green. No actionlint here; skip it.
Folders the checks need: `.github/workflows/` and the test's folder.
Read first: the header comment of `pages.yml`. Failure: "No artifacts named github-pages" from deploy-pages after the wait step passed (run 45415969592).
Constraints: method is the builder's. Any job failure counts. A green run must not hide its failed deploy from the next run. Keep cancel-in-progress. No second upload, no left-over deploy lock. No secrets; the repo is public and fully served. Update the header comment.
Break cases (test goes red if): the first failure fails the run; a second or later failure passes; a good deploy does not reset the count; a skipped step hides an earlier failure (use the wait step failing); cancel before the deploy ends changes the count; cancel after a good deploy does not reset it. Replay real runs 45413361106, 45415969592, 45418761018 (all quiet) and 43095722533 (cancelled, count unchanged).
Unverified: email delivery depends on who started the run and their settings.

</details>
