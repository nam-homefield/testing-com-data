#!/usr/bin/env python3
"""Test for ticket 5: the "Run failed: Deploy Pages" email only on two failed deploys in a row.

Run it from the repo root:   python3 docs/tickets/5-deploy-email-two-fails/test_pages_two_fails.py
Point it at another copy of the workflow with PAGES_YML=<path> (used to show it red on the old file).

What it proves, in order:
  1. The workflow has the shape that carries a deploy's real result to the next run (structure).
  2. The decision script that is inside pages.yml, run as written (not a copy), gives the right
     answer for every sequence of run kinds up to four long, against an independent oracle.
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
    "cancel_before": CANCELLED_BEFORE,  # real run 43095722533
    "cancel_early": CANCEL_EARLY,  # real run 42068254991
    "cancel_mid": CANCEL_MID,  # real run 42806424818
    "cancel_after_good": CANCEL_AFTER_GOOD,  # real run 41471386526
}
# The oracle: what each kind means for the count. Written from the ticket, not from the script.
MEANING = {
    "good": "good", "cancel_after_good": "good",
    "bad_deploy": "bad", "bad_wait": "bad", "bad_job": "bad", "bad_post": "bad",
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


def scenario(name, history_kinds, current_kind, ids=None):
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
    return {
        "name": name,
        "current": {"id": ids[n], "jobs": cur_jobs},
        "history": list(reversed(history)),  # the API lists newest first
        "expect_loud": oracle_loud(history_kinds, current_kind),
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
        if (p.workflow_id !== 'pages.yml' || p.branch !== 'main' || p.status !== 'completed') {
          throw new Error('unexpected listWorkflowRuns params ' + JSON.stringify(p));
        }
        const runs = sc.history.slice(0, p.per_page).map(r => ({
          id: r.id, conclusion: r.conclusion, status: r.status, head_branch: r.head_branch,
        }));
        return { data: { total_count: runs.length, workflow_runs: runs } };
      },
    } } };
    const context = { repo: { owner: 'nam-homefield', repo: 'testing-com-data' }, runId: sc.current.id };
    const core = { info: m => infos.push(String(m)), setFailed: m => failed.push(String(m)) };
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


def logic_problems(script, scenarios):
    """Return a list of mismatches between the script's answer and the oracle."""
    problems = []
    for sc, res in zip(scenarios, run_scenarios(script, scenarios)):
        if res["error"]:
            problems.append(f"{sc['name']}: script threw: {res['error']}")
            continue
        loud = bool(res["failed"])
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


def unreadable_scenarios():
    """This run's own deploy job read back wrongly: not final yet, never final, or missing."""
    in_progress = copy.deepcopy(KINDS["good"])
    in_progress[0]["status"], in_progress[0]["conclusion"] = "in_progress", None
    for st in in_progress[0]["steps"]:
        if st["name"] in (DEPLOY, POST, DONE):
            st["status"], st["conclusion"] = "in_progress", None
    scs = []
    one = scenario("own job not final on the first read, then a failure (after a failure): loud", ["bad_deploy"], "bad_deploy")
    one["current"]["reads"] = [in_progress, one["current"]["jobs"]]
    scs.append(one)
    two = scenario("own job not final on the first read, then a failure (after a good deploy): quiet", ["good"], "bad_deploy")
    two["current"]["reads"] = [in_progress, two["current"]["jobs"]]
    scs.append(two)
    never = scenario("own job never final: the run fails, result unknown", ["good"], "good")
    never["current"]["reads"] = [in_progress]
    never["expect_loud"] = True
    scs.append(never)
    missing = scenario("own deploy job missing from the record: the run fails, result unknown", ["good"], "good")
    missing["current"]["reads"] = [[]]
    missing["expect_loud"] = True
    scs.append(missing)
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
    if doc.get("concurrency") != {"group": "pages", "cancel-in-progress": True}:
        p.append("concurrency block changed (needs group pages and cancel-in-progress: true)")
    if doc.get("permissions") != {"contents": "read", "pages": "write", "id-token": "write"}:
        p.append("workflow-level permissions changed")
    if not deploy:
        p.append("no deploy job")
        return p
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
    if "environment" in verdict:
        p.append("verdict job must not use the github-pages environment")
    vsteps = verdict.get("steps", [])
    if len(vsteps) != 1 or not str(vsteps[0].get("uses", "")).startswith("actions/github-script@"):
        p.append("verdict must be one actions/github-script step")
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
    check("structure: deploy result is carried to a decision, one upload, one deploy, concurrency kept",
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
    check(f"logic sweep: every sequence of {len(KINDS)} run kinds, 1 to 4 runs long",
          logic_problems(script, sweep), f"{len(sweep)} sequences, {loud} must fail the run, {len(sweep) - loud} must stay quiet")
    unreadable = unreadable_scenarios()
    check("logic: this run's own deploy job read back not final, or missing",
          logic_problems(script, unreadable), f"{len(unreadable)} cases")
    replay = real_replay_scenarios()
    check("replay: real records of runs 45413361106, 45415969592, 45418761018, 43095722533 and three more cancelled runs",
          logic_problems(script, replay), f"{len(replay)} replays")

    # 5. break cases on the script
    script_breaks = [
        ("first failure fails the run",
         "if (now !== 'bad') return;", "if (now === 'bad') { core.setFailed('first'); return; }\nif (now !== 'bad') return;"),
        ("a second or later failure passes",
         "core.setFailed('The deploy failed in this run", "core.info('The deploy failed in this run"),
        ("a good deploy does not reset the count",
         "if (before === 'skip') continue;", "if (before === 'skip' || before === 'good') continue;"),
        ("a skipped step hides an earlier failure (wait step failed, deploy skipped)",
         "if (steps.some(s => failed(s.conclusion))) return 'bad';", ""),
        ("cancel before the deploy ends counts as a failure",
         "if (failed(job.conclusion)) return 'bad';\n  return 'skip';", "return 'bad';"),
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
        ("an unreadable own result stays quiet",
         "core.setFailed('Could not read the finished deploy job", "core.info('Could not read the finished deploy job"),
        ("the deploy step is read under the wrong name", "'Run actions/deploy-pages@v4'", "'Deploy'"),
    ]
    probe = sweep + replay + unreadable
    for label, old, new in script_breaks:
        if script.count(old) != 1:
            check(f"break case: {label}", [f"could not apply: text found {script.count(old)} times, expected once"])
            continue
        mutated = script.replace(old, new)
        found = logic_problems(mutated, probe)
        must_be_red(f"break case: {label}", found, f"{len(found)} of {len(probe)} sequences go red")

    # 5b. break cases on the workflow shape
    shape_breaks = [
        ("deploy job fails the run by itself (continue-on-error removed)",
         "    continue-on-error: true\n    runs-on: ubuntu-latest\n    environment:", "    runs-on: ubuntu-latest\n    environment:"),
        ("verdict does not wait for deploy", "    needs: deploy\n", ""),
        ("verdict is skipped after a failed deploy", "    if: ${{ !cancelled() }}\n", ""),
        ("verdict cannot read the jobs API", "    permissions:\n      actions: read\n", ""),
        ("verdict can never fail the run", "  verdict:\n    needs: deploy\n", "  verdict:\n    continue-on-error: true\n    needs: deploy\n"),
        ("deploy step gets a name (the recorded name changes)", "      - id: deployment\n        uses: actions/deploy-pages@v4",
         "      - name: Deploy\n        id: deployment\n        uses: actions/deploy-pages@v4"),
        ("cancel-in-progress removed", "  cancel-in-progress: true\n", "  cancel-in-progress: false\n"),
        ("a second upload is added", "      - uses: actions/configure-pages@v5\n",
         "      - uses: actions/configure-pages@v5\n      - uses: actions/upload-pages-artifact@v3\n"),
        ("the deploy step is repeated", "      - id: deployment\n        uses: actions/deploy-pages@v4",
         "      - uses: actions/deploy-pages@v4\n      - id: deployment\n        uses: actions/deploy-pages@v4"),
    ]
    for label, old, new in shape_breaks:
        if text.count(old) != 1:
            check(f"break case: {label}", [f"could not apply: text found {text.count(old)} times, expected once"])
            continue
        found = structure_problems(text.replace(old, new))
        must_be_red(f"break case: {label}", found, f"{len(found)} structure problems")

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
    listing = gh_get(f"repos/{repo}/actions/workflows/pages.yml/runs", "branch=main", "status=completed", "per_page=30")
    runs = listing["workflow_runs"]
    print(f"real API: {len(runs)} completed runs on main in the 30 run window, newest id {runs[0]['id']}, oldest {runs[-1]['id']}")
    jobs_by_id = {r["id"]: gh_get(f"repos/{repo}/actions/runs/{r['id']}/jobs", "per_page=100")["jobs"] for r in runs}
    for rid in run_ids:
        rid = int(rid)
        if rid not in jobs_by_id:
            jobs_by_id[rid] = gh_get(f"repos/{repo}/actions/runs/{rid}/jobs", "per_page=100")["jobs"]
        sc = {"name": f"live {rid}", "current": {"id": rid, "jobs": jobs_by_id[rid]},
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
