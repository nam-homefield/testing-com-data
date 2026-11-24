#!/usr/bin/env python3
"""Test for ticket 5: the "Run failed: Deploy Pages" email only on two failed deploys in a row.

Run it from the repo root:   python3 docs/tickets/5-deploy-email-two-fails/test_pages_two_fails.py
Point it at another copy of the workflow with PAGES_YML=<path> (used to show it red on the old file).

What it proves, in order:
  1. The workflow has the shape that carries a deploy's real result to the next run (structure).
     The deploy job has continue-on-error, the concurrency block is on the deploy job only, and the
     verdict job and its step carry no key that could switch the decision off.
  2. The workflow's own flags (job conditions, continue-on-error, concurrency) are applied to each
     scenario the way GitHub applies them, and the decision script that is inside pages.yml runs as
     written (not a copy). The answer, did the run end as failed, is checked for every sequence of
     run kinds up to four long against an independent oracle.
  3. The unedited jobs records of four real runs give the right answer when fed to that script.
  4. The job and step names the script reads are the names GitHub recorded in those real records.
  5. Every break case turns the test red: each one is applied to the script or the workflow and
     the checks above must then fail.
  6. Optional, once: --live <run id>... feeds the script the real API answers (read-only gh api
     GET calls, nothing is deployed) with context.runId set to each run id. Needs gh logged in.
Needs python3 with PyYAML and node. The default run uses no network and no secrets.
"""
import copy
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WORKFLOW = Path(os.environ.get("PAGES_YML", ROOT / ".github" / "workflows" / "pages.yml"))
FIX = HERE / "fixtures"


def load_jobs(run_id):
    return json.loads((FIX / f"jobs-{run_id}.json").read_text())["jobs"]


# Real GitHub records, unedited (jobs API), for the four runs named in the ticket.
REAL_GOOD_1, REAL_BAD, REAL_GOOD_2, REAL_CANCELLED = "45413361106", "45415969592", "45418761018", "43095722533"
GOOD = load_jobs(REAL_GOOD_1)
BAD = load_jobs(REAL_BAD)
GOOD_2 = load_jobs(REAL_GOOD_2)
CANCELLED_BEFORE = load_jobs(REAL_CANCELLED)  # no jobs: cancelled before any job started
# Three more real cancelled runs, one of each shape the 40 cancelled runs of this workflow have.
REAL_CANCEL_EARLY, REAL_CANCEL_MID, REAL_CANCEL_AFTER = "42068254991", "42806424818", "41471386526"
CANCEL_EARLY = load_jobs(REAL_CANCEL_EARLY)  # job cancelled before any step was recorded
CANCEL_MID = load_jobs(REAL_CANCEL_MID)  # deploy-pages step cancelled
CANCEL_AFTER_GOOD = load_jobs(REAL_CANCEL_AFTER)  # deploy-pages step succeeded, then the run was cancelled

WAIT = "Wait for the artifact to be listed"
DEPLOY = "Run actions/deploy-pages@v4"
POST = "Post Run actions/checkout@v4"
DONE = "Complete job"


def with_steps(jobs, job_conclusion, **by_name):
    out = copy.deepcopy(jobs)
    out[0]["conclusion"] = job_conclusion
    for s in out[0]["steps"]:
        if s["name"] in by_name:
            s["conclusion"] = by_name[s["name"]]
    return out


# Each kind is a deploy job as GitHub records it. Real records where one exists (good,
# bad_deploy and all four cancel kinds), and a real record with the one field a failure of
# that kind changes for the three failures that have not happened yet (wait, job, post).
KINDS = {
    "good": GOOD,
    "bad_deploy": BAD,  # real run 45415969592
    "bad_wait": with_steps(GOOD, "failure", **{WAIT: "failure", DEPLOY: "skipped"}),
    "bad_job": with_steps(GOOD, "failure", **{WAIT: "cancelled", DEPLOY: "cancelled", POST: "cancelled"}),
    "bad_post": with_steps(GOOD, "failure", **{POST: "failure"}),
    "bad_job_after_deploy": with_steps(GOOD, "failure"),  # every step green, the job itself failed
    "cancel_before": CANCELLED_BEFORE,  # real run 43095722533
    "cancel_early": CANCEL_EARLY,  # real run 42068254991
    "cancel_mid": CANCEL_MID,  # real run 42806424818
    "cancel_after_good": CANCEL_AFTER_GOOD,  # real run 41471386526
}
# The oracle: what each kind means for the count. Written from the ticket, not from the script.
MEANING = {
    "good": "good", "cancel_after_good": "good",
    "bad_deploy": "bad", "bad_wait": "bad", "bad_job": "bad", "bad_post": "bad", "bad_job_after_deploy": "bad",
    "cancel_before": "skip", "cancel_early": "skip", "cancel_mid": "skip",
}


def oracle_loud(history_kinds, current_kind):
    """Expected: does this run end as failed (so GitHub emails)?"""
    if MEANING[current_kind] != "bad":
        return False
    for k in reversed(history_kinds):
        if MEANING[k] == "skip":
            continue
        return MEANING[k] == "bad"
    return False


def run_record(run_id, kind, loud):
    """A finished run as the API lists it under the new workflow: a quiet failure ends green."""
    meaning = MEANING[kind]
    if kind.startswith("cancel"):
        conclusion = "cancelled"
    else:
        conclusion = "failure" if loud else "success"
    verdict = {
        "name": "verdict", "status": "completed",
        "conclusion": "failure" if loud else ("cancelled" if kind.startswith("cancel") else "success"),
        "steps": [{"name": "Set up job", "status": "completed", "conclusion": "success", "number": 1},
                  {"name": "Fail the run only when this deploy and the one before it both failed",
                   "status": "completed", "conclusion": "failure" if loud else "success", "number": 2}],
    }
    jobs = copy.deepcopy(KINDS[kind]) + ([] if kind == "cancel_before" else [verdict])
    return {"id": run_id, "conclusion": conclusion, "status": "completed", "head_branch": "main",
            "jobs": jobs, "meaning": meaning}


def scenario(name, history_kinds, current_kind, ids=None, superseded=False):
    """Build one scenario: earlier runs (oldest first) and the current run."""
    n = len(history_kinds)
    ids = ids or [1000 + 10 * i for i in range(n + 1)]
    history = []
    for i, k in enumerate(history_kinds):
        loud = oracle_loud(history_kinds[:i], k)
        history.append(run_record(ids[i], k, loud))
    cur_jobs = copy.deepcopy(KINDS[current_kind])
    if current_kind != "cancel_before":
        cur_jobs.append({"name": "verdict", "status": "in_progress", "conclusion": None,
                         "steps": [{"name": "Set up job", "status": "completed", "conclusion": "success", "number": 1}]})
    meaning = MEANING[current_kind]
    deploy_result = "failure" if meaning == "bad" else ("cancelled" if current_kind.startswith("cancel") else "success")
    return {
        "name": name,
        "current": {"id": ids[n], "jobs": cur_jobs},
        "history": list(reversed(history)),  # the API lists newest first
        "expect_loud": oracle_loud(history_kinds, current_kind),
        "deploy_result": deploy_result,  # what the deploy job really ended as
        "needs_result": deploy_result,  # what ${{ needs.deploy.result }} would hold
        "superseded": superseded,  # a newer push arrives after this deploy ended, while the verdict is still running
    }


# Runs the script exactly as actions/github-script does: an async function of github, context, core.
HARNESS = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
globalThis.setTimeout = f => queueMicrotask(f); // the script waits 3 seconds between reads; do not wait here
(async () => {
  const out = [];
  for (const sc of input.scenarios) {
    const jobsById = new Map();
    jobsById.set(sc.current.id, sc.current.jobs);
    for (const r of sc.history) jobsById.set(r.id, r.jobs);
    let currentReads = 0; // sc.current.reads, when given, are the successive answers for this run's own jobs
    const failed = [];
    const infos = [];
    let error = null;
    const github = { rest: { actions: {
      listJobsForWorkflowRun: async p => {
        if (p.owner !== 'nam-homefield' || p.repo !== 'testing-com-data') throw new Error('wrong repo');
        if (!jobsById.has(p.run_id)) throw new Error('unknown run ' + p.run_id);
        if (p.run_id === sc.current.id && sc.current.reads) {
          const jobs = sc.current.reads[Math.min(currentReads++, sc.current.reads.length - 1)];
          return { data: { total_count: jobs.length, jobs } };
        }
        return { data: { total_count: jobsById.get(p.run_id).length, jobs: jobsById.get(p.run_id) } };
      },
      listWorkflowRuns: async p => {
        if (p.workflow_id !== 'pages.yml' || p.branch !== 'main' || p.per_page !== 100 || !(p.page >= 1) || 'status' in p) {
          throw new Error('unexpected listWorkflowRuns params ' + JSON.stringify(p));
        }
        const runs = sc.history.slice((p.page - 1) * p.per_page, p.page * p.per_page).map(r => ({
          id: r.id, conclusion: r.conclusion, status: r.status, head_branch: r.head_branch,
        }));
        return { data: { total_count: runs.length, workflow_runs: runs } };
      },
    } } };
    const context = { repo: { owner: 'nam-homefield', repo: 'testing-com-data' }, runId: sc.current.id };
    const core = { info: m => infos.push(String(m)), warning: m => infos.push('WARNING ' + m), setFailed: m => failed.push(String(m)) };
    process.env.DEPLOY_RESULT = sc.env && sc.env.DEPLOY_RESULT !== undefined ? sc.env.DEPLOY_RESULT : '';
    try {
      await new AsyncFunction('github', 'context', 'core', input.script)(github, context, core);
    } catch (e) {
      error = String(e && e.message || e);
    }
    out.push({ name: sc.name, failed, error, infos });
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def run_scenarios(script, scenarios):
    payload = json.dumps({"script": script, "scenarios": scenarios})
    r = subprocess.run(["node", "-e", HARNESS], input=payload, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("node failed: " + r.stderr[-800:])
    return json.loads(r.stdout)


IN_PROGRESS = copy.deepcopy(KINDS["good"])
IN_PROGRESS[0]["status"], IN_PROGRESS[0]["conclusion"] = "in_progress", None
for _st in IN_PROGRESS[0]["steps"]:
    if _st["name"] in (DEPLOY, POST, DONE):
        _st["status"], _st["conclusion"] = "in_progress", None


def simulate(doc, script, scenarios):
    """For each scenario: does the run end as failed (so GitHub emails)?

    This follows the workflow's own flags, read from the parsed file, the way GitHub applies them:
      - a failed deploy job fails the run unless the deploy job has continue-on-error: true
      - a newer push cancels the whole run only when concurrency is on the workflow, and then
        the verdict job's condition decides whether it still runs
      - the verdict job runs when its if allows it (no if means success() of its needs)
      - the verdict step runs unless it has an if, and its failure fails the run unless a
        continue-on-error is set on the step or the job
      - the verdict step's env DEPLOY_RESULT holds the deploy job's result when it is
        ${{ needs.deploy.result }}
    and runs the script from the file, as it stands, for every scenario where the step runs."""
    jobs = doc["jobs"]
    deploy, verdict = jobs["deploy"], jobs.get("verdict")
    workflow_cancels = bool((doc.get("concurrency") or {}).get("cancel-in-progress"))
    out = [None] * len(scenarios)
    to_run = []
    for i, sc in enumerate(scenarios):
        dr = sc["deploy_result"]
        fails = dr == "failure" and deploy.get("continue-on-error") is not True
        step = (verdict or {}).get("steps", [None])[0] if verdict else None
        if verdict and step is not None:
            cancelled = workflow_cancels and (dr == "cancelled" or sc.get("superseded"))
            cond = verdict.get("if")
            if cond is None:
                runs = dr == "success"
            elif cond == "${{ !cancelled() }}":
                runs = not cancelled
            else:
                runs = False
            if "if" in step:
                runs = False
            if runs:
                sc2 = dict(sc)
                if verdict.get("needs") not in ("deploy", ["deploy"]):
                    sc2["current"] = dict(sc["current"], reads=[IN_PROGRESS])  # it starts before the deploy ends
                env_expr = (step.get("env") or {}).get("DEPLOY_RESULT")
                sc2["env"] = {"DEPLOY_RESULT": sc["needs_result"] if env_expr == "${{ needs.deploy.result }}" else ""}
                can_fail = not (step.get("continue-on-error") is True or verdict.get("continue-on-error") is True)
                to_run.append((i, sc2, can_fail, fails))
                continue
        out[i] = (fails, None)
    if to_run:
        results = run_scenarios(script, [t[1] for t in to_run])
        for (i, _sc2, can_fail, fails), res in zip(to_run, results):
            step_failed = bool(res["failed"]) or bool(res["error"])
            out[i] = (fails or (step_failed and can_fail), res["error"])
    return out


def logic_problems(doc, script, scenarios):
    """Return a list of mismatches between what the workflow would do and the oracle."""
    problems = []
    for sc, (loud, error) in zip(scenarios, simulate(doc, script, scenarios)):
        if error:
            problems.append(f"{sc['name']}: script threw: {error}")
        if loud != sc["expect_loud"]:
            problems.append(f"{sc['name']}: expected {'FAIL (email)' if sc['expect_loud'] else 'quiet'}, got {'FAIL (email)' if loud else 'quiet'}")
    return problems


CURRENT_KINDS = [k for k in KINDS if k != "cancel_before"]  # the verdict job never runs with no deploy job


def sweep_scenarios(max_len=4):
    scs = []
    for n in range(0, max_len):  # n earlier runs, then the current run
        for hist in itertools.product(KINDS, repeat=n):
            for cur in CURRENT_KINDS:
                scs.append(scenario(" > ".join(list(hist) + [cur]), list(hist), cur))
    return scs


def superseded_scenarios():
    """The same sequences, up to 3 runs long, with a newer push arriving while the verdict runs."""
    scs = []
    for n in range(0, 3):
        for hist in itertools.product(KINDS, repeat=n):
            for cur in CURRENT_KINDS:
                scs.append(scenario(" > ".join(list(hist) + [cur]) + " (newer push)", list(hist), cur, superseded=True))
    return scs


def long_history_scenarios():
    """A failure, then many cancelled runs, then a failure: the cancelled runs must not hide the first."""
    scs = []
    for n in (40, 250):
        sc = scenario(f"bad, {n} cancelled runs, bad", ["bad_deploy"] + ["cancel_before"] * n, "bad_deploy")
        scs.append(sc)
        sc = scenario(f"good, {n} cancelled runs, bad", ["good"] + ["cancel_before"] * n, "bad_deploy")
        scs.append(sc)
    return scs


def unreadable_scenarios():
    """This run's own deploy job read back wrongly: not final yet, never final, or missing."""
    in_progress = IN_PROGRESS
    scs = []
    one = scenario("own job not final on the first read, then a failure (after a failure): loud", ["bad_deploy"], "bad_deploy")
    one["current"]["reads"] = [in_progress, one["current"]["jobs"]]
    scs.append(one)
    two = scenario("own job not final on the first read, then a failure (after a good deploy): quiet", ["good"], "bad_deploy")
    two["current"]["reads"] = [in_progress, two["current"]["jobs"]]
    scs.append(two)
    # Unreadable for good: the needs result decides. Only a failure counts, and it still needs a failure before it.
    for reads_name, reads in (("never final", [in_progress]), ("missing", [[]])):
        for needs, hist, loud in (("success", ["good"], False), ("success", ["bad_deploy"], False),
                                  ("failure", ["bad_deploy"], True), ("failure", ["good"], False)):
            sc = scenario(f"own job {reads_name}, needs result {needs}, after {hist[0]}", hist, "good")
            sc["current"]["reads"] = reads
            sc["needs_result"], sc["deploy_result"], sc["expect_loud"] = needs, ("failure" if needs == "failure" else "success"), loud
            scs.append(sc)
    return scs


def real_replay_scenarios():
    """Real records, in the order the runs really happened, and the two cancel cases."""
    r = REAL_CANCELLED, REAL_GOOD_1, REAL_BAD, REAL_GOOD_2
    ids = [int(x) for x in r]
    scs = [
        scenario("real: 45413361106 success after the cancelled run", ["cancel_before"], "good", ids[:2]),
        scenario("real: 45415969592 lone failure after a good deploy", ["cancel_before", "good"], "bad_deploy", ids[:3]),
        scenario("real: 45418761018 recovery after the failure", ["cancel_before", "good", "bad_deploy"], "good", ids),
        # Real records, renumbered, in new orders.
        scenario("real: bad, cancelled, bad (cancel does not reset the count)", ["bad_deploy", "cancel_before"], "bad_deploy"),
        scenario("real: good, cancelled, bad (still one failure)", ["good", "cancel_before"], "bad_deploy"),
        scenario("real: bad, cancelled early, bad", ["bad_deploy", "cancel_early"], "bad_deploy"),
        scenario("real: bad, cancelled during the deploy, bad (still two in a row)", ["bad_deploy", "cancel_mid"], "bad_deploy"),
        scenario("real: bad, cancelled after a good deploy, bad (the good deploy resets)", ["bad_deploy", "cancel_after_good"], "bad_deploy"),
        scenario("real: good, cancelled during the deploy, bad (one failure)", ["good", "cancel_mid"], "bad_deploy"),
        scenario("real: bad, bad (second in a row)", ["bad_deploy"], "bad_deploy"),
        scenario("real: bad, bad, bad (third in a row)", ["bad_deploy", "bad_deploy"], "bad_deploy"),
        scenario("real: bad, good, bad (a good deploy resets)", ["bad_deploy", "good"], "bad_deploy"),
    ]
    return scs


# ---------------------------------------------------------------- structure

def load_workflow(text):
    doc = yaml.safe_load(text)
    doc["_on"] = doc.get("on", doc.get(True))  # PyYAML reads the key "on" as True
    return doc


def verdict_script(doc):
    try:
        return doc["jobs"]["verdict"]["steps"][0]["with"]["script"]
    except (KeyError, IndexError, TypeError):
        return None


def structure_problems(text):
    p = []
    try:
        doc = load_workflow(text)
    except Exception as e:  # not valid YAML
        return [f"workflow is not valid YAML: {e}"]
    jobs = doc.get("jobs", {})
    deploy, verdict = jobs.get("deploy"), jobs.get("verdict")
    if doc.get("name") != "Deploy Pages":
        p.append("workflow name changed (the email subject uses it)")
    if (doc["_on"] or {}).get("push", {}).get("branches") != ["main"]:
        p.append("push trigger is not main")
    if "concurrency" in doc:
        p.append("concurrency is on the workflow, so a newer push can cancel the verdict job and lose the email")
    if doc.get("permissions") != {"contents": "read", "pages": "write", "id-token": "write"}:
        p.append("workflow-level permissions changed")
    if not deploy:
        p.append("no deploy job")
        return p
    if deploy.get("concurrency") != {"group": "pages", "cancel-in-progress": True}:
        p.append("deploy job concurrency must be group pages with cancel-in-progress: true")
    if set(deploy) != {"continue-on-error", "concurrency", "runs-on", "environment", "steps"}:
        p.append(f"deploy job keys changed: {sorted(deploy)}")
    if deploy.get("continue-on-error") is not True:
        p.append("deploy job lacks continue-on-error: true, so a failed deploy fails the run at once")
    if "name" in deploy:
        p.append("deploy job has a name: line, so the job name the verdict reads is not 'deploy'")
    steps = deploy.get("steps", [])
    if any("continue-on-error" in s for s in steps):
        p.append("a deploy step has continue-on-error (hides the step failure from the jobs record)")
    uploads = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-pages-artifact")]
    deploys = [s for s in steps if str(s.get("uses", "")).startswith("actions/deploy-pages")]
    if len(uploads) != 1:
        p.append(f"{len(uploads)} upload steps (must be exactly 1: a second upload breaks the lookup)")
    if len(deploys) != 1:
        p.append(f"{len(deploys)} deploy-pages steps (must be exactly 1: no retry, no left-over lock)")
    else:
        d = deploys[0]
        if d.get("uses") != "actions/deploy-pages@v4" or "name" in d or "if" in d or "continue-on-error" in d:
            p.append("deploy-pages step must be 'uses: actions/deploy-pages@v4' with no name, if or continue-on-error")
    if not verdict:
        p.append("no verdict job, so nothing carries the deploy result to a decision")
        return p
    if verdict.get("needs") not in ("deploy", ["deploy"]):
        p.append("verdict does not need deploy")
    if verdict.get("if") != "${{ !cancelled() }}":
        p.append("verdict condition is not '${{ !cancelled() }}' (it must run after a failed deploy, and not when cancelled)")
    if "continue-on-error" in verdict:
        p.append("verdict job has continue-on-error, so it could never fail the run")
    if verdict.get("permissions") != {"actions": "read"}:
        p.append("verdict permissions must be exactly actions: read (it reads the jobs API)")
    if set(verdict) != {"needs", "if", "runs-on", "permissions", "steps"}:
        p.append(f"verdict job keys must be exactly needs, if, runs-on, permissions, steps (found {sorted(verdict)})")
    vsteps = verdict.get("steps", [])
    if len(vsteps) != 1 or not str(vsteps[0].get("uses", "")).startswith("actions/github-script@"):
        p.append("verdict must be one actions/github-script step")
    elif set(vsteps[0]) != {"name", "uses", "env", "with"} or set(vsteps[0]["with"]) != {"script"}:
        p.append(f"verdict step has keys other than name, uses, env, with.script (found {sorted(vsteps[0])}); an if or a continue-on-error would switch the decision off")
    elif vsteps[0]["env"] != {"DEPLOY_RESULT": "${{ needs.deploy.result }}"}:
        p.append("verdict step env must be exactly DEPLOY_RESULT: ${{ needs.deploy.result }}")
    script = verdict_script(doc)
    if not script:
        p.append("verdict script is missing")
    else:
        if "context.runId" not in script or "listJobsForWorkflowRun" not in script:
            p.append("verdict script does not read this run's own jobs record")
        if re.search(r"\b(run|r)\.conclusion\b", script):
            p.append("verdict script reads a run conclusion (a quiet failure ends green and would be hidden)")
        want_step = None if len(deploys) != 1 else "Run " + deploys[0]["uses"]
        if want_step and f"const DEPLOY_STEP = '{want_step}';" not in script:
            p.append(f"verdict script does not read the deploy step by its recorded name {want_step!r}")
        if "const DEPLOY_JOB = 'deploy';" not in script:
            p.append("verdict script does not read the job by its recorded name 'deploy'")
    return p


# ---------------------------------------------------------------- the checks

RESULTS = []


def check(name, problems, detail=""):
    ok = not problems
    RESULTS.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    for line in problems[:6]:
        print("        - " + line)
    if len(problems) > 6:
        print(f"        ... and {len(problems) - 6} more")


def must_be_red(name, problems, detail=""):
    """A break case: applying it must make the checks fail. 'problems' is what the checks found."""
    ok = bool(problems)
    RESULTS.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        print("        - the checks stayed green on this break case")


def main():
    text = WORKFLOW.read_text()
    try:
        shown = WORKFLOW.relative_to(ROOT)
    except ValueError:
        shown = f"{WORKFLOW.name} (outside the repo)"
    print(f"workflow under test: {shown}")
    doc = None
    try:
        doc = load_workflow(text)
    except Exception:
        pass
    script = verdict_script(doc) if doc else None

    # 1. structure
    check("structure: deploy result is carried to a decision, one upload, one deploy, concurrency on the deploy job",
          structure_problems(text))

    # 4. names match the real records
    names = []
    for rid in (REAL_GOOD_1, REAL_BAD, REAL_GOOD_2, REAL_CANCEL_MID, REAL_CANCEL_AFTER):
        jobs = load_jobs(rid)
        if [j["name"] for j in jobs] != ["deploy"]:
            names.append(f"run {rid}: job names {[j['name'] for j in jobs]}")
        if DEPLOY not in [s["name"] for s in jobs[0]["steps"]]:
            names.append(f"run {rid}: no step named {DEPLOY!r}")
    check("names: the job 'deploy' and step 'Run actions/deploy-pages@v4' exist in the real job records", names)
    if script is None:
        check("logic: the decision script is inside pages.yml", ["no verdict script found, so every logic check is red"])
        print("\nRESULT: FAIL (%d of %d checks failed)" % (RESULTS.count(False), len(RESULTS)))
        return 1

    # 2. the whole sweep against the oracle, 3. real replays
    sweep = sweep_scenarios(4)
    loud = sum(1 for s in sweep if s["expect_loud"])
    check(f"logic sweep, through the workflow's own flags: every sequence of {len(KINDS)} run kinds, 1 to 4 runs long",
          logic_problems(doc, script, sweep), f"{len(sweep)} sequences, {loud} must fail the run, {len(sweep) - loud} must stay quiet")
    newer = superseded_scenarios()
    check("logic: the same sequences, up to 3 runs long, with a newer push while the verdict runs",
          logic_problems(doc, script, newer), f"{len(newer)} sequences")
    longh = long_history_scenarios()
    check("logic: a failure, then 40 or 250 cancelled runs, then a failure",
          logic_problems(doc, script, longh), f"{len(longh)} cases")
    unreadable = unreadable_scenarios()
    check("logic: this run's own deploy job read back not final, or missing",
          logic_problems(doc, script, unreadable), f"{len(unreadable)} cases")
    replay = real_replay_scenarios()
    check("replay: real records of runs 45413361106, 45415969592, 45418761018, 43095722533 and three more cancelled runs",
          logic_problems(doc, script, replay), f"{len(replay)} replays")

    # 5. break cases on the script
    script_breaks = [
        ("first failure fails the run",
         "if (now !== 'bad') return;", "if (now === 'bad') { core.setFailed('first'); return; }\nif (now !== 'bad') return;"),
        ("a second or later failure passes",
         "core.setFailed('The deploy failed in this run", "core.info('The deploy failed in this run"),
        ("a good deploy does not reset the count",
         "if (before === 'skip') continue;", "if (before === 'skip' || before === 'good') continue;"),
        ("a skipped step hides an earlier failure (wait step failed, deploy skipped)",
         "if (steps.some(s => failed(s.conclusion)) || failed(job.conclusion)) return 'bad';", ""),
        ("a failed job whose deploy step succeeded counts as good",
         "steps.some(s => failed(s.conclusion)) || failed(job.conclusion)", "steps.some(s => failed(s.conclusion))"),
        ("cancel before the deploy ends counts as a failure",
         "if (deploy && deploy.conclusion === 'success') return 'good';\n  return 'skip';",
         "if (deploy && deploy.conclusion === 'success') return 'good';\n  return 'bad';"),
        ("a run cancelled before any job started counts as a failure",
         "if (!job) return 'skip';", "if (!job) return 'bad';"),
        ("cancel after a good deploy does not reset it",
         "if (deploy && deploy.conclusion === 'success') return 'good';",
         "if (deploy && deploy.conclusion === 'success' && job.conclusion === 'success') return 'good';"),
        ("a green run hides its failed deploy (reads the run conclusion)",
         "const before = classify(await jobsOf(run.id));",
         "const before = run.conclusion === 'success' ? 'good' : run.conclusion === 'cancelled' ? 'skip' : 'bad';"),
        ("a cancelled run in between ends the look back",
         "if (before === 'skip') continue;", "if (before === 'skip') break;"),
        ("this run's own failure is ignored", "now = classify([job]);", "now = 'good';"),
        ("this run's own deploy job is read before it is final",
         "if (job && job.status === 'completed') {", "if (job) {"),
        ("an unreadable own result is treated as a failure",
         "Treating it as not failed.');\n    return;", "Treating it as not failed.');\n    now = 'bad';"),
        ("an unreadable own result ignores the needs result",
         "process.env.DEPLOY_RESULT === 'failure'", "false"),
        ("the look back stops after the first page of runs", "if (runs.length < 100) break;", "break;"),
        ("the look back stops after one page count", "const PAGES = 5;", "const PAGES = 1;"),
        ("the deploy step is read under the wrong name", "'Run actions/deploy-pages@v4'", "'Deploy'"),
    ]
    probe = sweep + replay + unreadable + longh
    for label, old, new in script_breaks:
        if script.count(old) != 1:
            check(f"break case: {label}", [f"could not apply: text found {script.count(old)} times, expected once"])
            continue
        mutated = script.replace(old, new)
        found = logic_problems(doc, mutated, probe)
        must_be_red(f"break case: {label}", found, f"{len(found)} of {len(probe)} sequences go red")

    # 5b. break cases on the workflow shape. Red means the structure check finds a problem, or the
    # workflow's flags, run through the same sweep, give a wrong answer, or both.
    sim_probe = sweep_scenarios(3) + newer + longh[:2] + unreadable + replay
    verdict_head = "  verdict:\n    needs: deploy\n"
    step_head = "      - name: Fail the run only when this deploy and the one before it both failed\n"
    job_conc = "    concurrency:\n      group: pages\n      cancel-in-progress: true\n"
    shape_breaks = [
        ("deploy job fails the run by itself (continue-on-error removed)",
         [("    continue-on-error: true\n    # The concurrency", "    # The concurrency")]),
        ("verdict does not wait for deploy", [("    needs: deploy\n", "")]),
        ("verdict is skipped after a failed deploy (job condition removed)", [("    if: ${{ !cancelled() }}\n", "")]),
        ("verdict job condition is switched off", [("    if: ${{ !cancelled() }}\n", "    if: ${{ false }}\n")]),
        ("verdict step is switched off with an if", [(step_head, step_head + "        if: ${{ false }}\n")]),
        ("verdict step can never fail the run (continue-on-error on the step)", [(step_head, step_head + "        continue-on-error: true\n")]),
        ("verdict job can never fail the run (continue-on-error on the job)", [(verdict_head, "  verdict:\n    continue-on-error: true\n    needs: deploy\n")]),
        ("verdict step loses the needs result", [("        env:\n          DEPLOY_RESULT: ${{ needs.deploy.result }}\n", "")]),
        ("verdict cannot read the jobs API", [("    permissions:\n      actions: read\n", "")]),
        ("deploy step gets a name (the recorded name changes)",
         [("      - id: deployment\n        uses: actions/deploy-pages@v4", "      - name: Deploy\n        id: deployment\n        uses: actions/deploy-pages@v4")]),
        ("cancel-in-progress removed", [("      cancel-in-progress: true\n", "      cancel-in-progress: false\n")]),
        ("concurrency moved back to the workflow (a newer push can cancel the verdict job)",
         [(job_conc, ""), ("jobs:\n  deploy:\n", "concurrency:\n  group: pages\n  cancel-in-progress: true\n\njobs:\n  deploy:\n")]),
        ("a second upload is added",
         [("      - uses: actions/configure-pages@v5\n", "      - uses: actions/configure-pages@v5\n      - uses: actions/upload-pages-artifact@v3\n")]),
        ("the deploy step is repeated",
         [("      - id: deployment\n        uses: actions/deploy-pages@v4",
           "      - uses: actions/deploy-pages@v4\n      - id: deployment\n        uses: actions/deploy-pages@v4")]),
    ]
    for label, edits in shape_breaks:
        mutated, bad = text, None
        for old, new in edits:
            if mutated.count(old) != 1:
                bad = f"could not apply: text found {mutated.count(old)} times, expected once ({old[:40]!r})"
                break
            mutated = mutated.replace(old, new)
        if bad:
            check(f"break case: {label}", [bad])
            continue
        struct = structure_problems(mutated)
        try:
            mdoc = load_workflow(mutated)
            mscript = verdict_script(mdoc) or script
            sim = logic_problems(mdoc, mscript, sim_probe)
        except Exception as e:  # a file that no longer parses is red too
            sim = [f"workflow no longer loads: {e}"]
        must_be_red(f"break case: {label}", struct + sim,
                    f"{len(struct)} structure problems, {len(sim)} of {len(sim_probe)} sequences go red")

    failed = RESULTS.count(False)
    print()
    print("RESULT: %s (%d checks, %d failed)" % ("PASS" if not failed else "FAIL", len(RESULTS), failed))
    return 1 if failed else 0


def gh_get(path, *fields):
    cmd = ["gh", "api", "-X", "GET", path]
    for f in fields:
        cmd += ["-f", f]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def live(run_ids):
    """Feed the workflow's script the real API answers, read only, for each run id."""
    repo = "nam-homefield/testing-com-data"
    script = verdict_script(load_workflow(WORKFLOW.read_text()))
    # The same call, with the same parameters, that the script makes.
    listing = gh_get(f"repos/{repo}/actions/workflows/pages.yml/runs", "branch=main", "per_page=100", "page=1")
    runs = listing["workflow_runs"][:30]  # jobs are fetched for the newest 30 only, to keep the calls few
    print(f"real API: first page of runs on main ({len(listing['workflow_runs'])} runs), using the newest {len(runs)}: ids {runs[0]['id']} to {runs[-1]['id']}")
    jobs_by_id = {r["id"]: gh_get(f"repos/{repo}/actions/runs/{r['id']}/jobs", "per_page=100")["jobs"] for r in runs}
    for rid in run_ids:
        rid = int(rid)
        if rid not in jobs_by_id:
            jobs_by_id[rid] = gh_get(f"repos/{repo}/actions/runs/{rid}/jobs", "per_page=100")["jobs"]
        sc = {"name": f"live {rid}", "current": {"id": rid, "jobs": jobs_by_id[rid]}, "env": {"DEPLOY_RESULT": ""},
              "history": [dict(id=r["id"], conclusion=r["conclusion"], status=r["status"],
                               head_branch=r["head_branch"], jobs=jobs_by_id[r["id"]]) for r in runs]}
        res = run_scenarios(script, [sc])[0]
        print(f"run {rid}: " + ("script threw: " + res["error"] if res["error"] else
              ("FAILS THE RUN: " + "; ".join(res["failed"]) if res["failed"] else "quiet (run stays green)")))
        for line in res["infos"]:
            print("    log: " + line)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--live":
        sys.exit(live(sys.argv[2:]))
    sys.exit(main())
