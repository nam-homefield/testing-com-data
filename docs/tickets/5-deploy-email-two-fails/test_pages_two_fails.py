#!/usr/bin/env python3
"""Ticket 5 test: the deploy emails only when two deploys in a row fail.

It runs the real pages.yml, step by step, the way GitHub does:
- each step's `if` is read from the file and evaluated (status functions, the
  default success() check, steps.<id>.outcome and outputs),
- `continue-on-error` is read from the file for each step and for the job,
- the decide step's own bash text from the file runs under `bash -e`,
- the cache works as GitHub's docs describe it: an entry is never overwritten,
  a restore tries the exact key, then a prefix of it, then each restore key as a
  prefix, and returns the newest match with the same path; a miss is not an error.
A run "emails" when its run ends as failure.

Usage:
  python3 test_pages_two_fails.py               # the branch file, plus the break cases
  python3 test_pages_two_fails.py --file F      # one other file only (for the red run)
Needs PyYAML and bash.
"""
import copy
import itertools
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "pages.yml"

KINDS = ["good", "fail_checkout", "fail_configure", "fail_upload", "fail_wait",
         "fail_deploy", "cancel_wait", "cancel_deploy", "cancel_after_good", "never_started"]
PATH_STEPS = ["checkout", "configure", "upload", "wait", "deploy"]


# ---------- the rule, written from the ticket ----------
def counted(kind):
    if kind in ("good", "cancel_after_good"):
        return "good"
    if kind.startswith("fail_"):
        return "bad"
    return None  # stopped before the deploy finished: skipped when counting


def oracle(seq):
    state, out = None, []
    for kind in seq:
        this = counted(kind)
        out.append(this == "bad" and state == "bad")
        if this:
            state = this
    return out


# ---------- expressions ----------
TOKEN = re.compile(r"\s*(?:(always|success|cancelled|failure)\(\)|steps\.([\w-]+)\.outcome"
                   r"|steps\.([\w-]+)\.outputs\.([\w-]+)|github\.(run_id|run_attempt)"
                   r"|'([^']*)'|(==|!=|&&|\|\||!|\(|\)|true|false))")


def evaluate(expr, ctx):
    expr = expr.strip()
    m = re.fullmatch(r"\$\{\{(.*)\}\}", expr, re.S)
    if m:
        expr = m.group(1).strip()
    py, pos, has_status = [], 0, False
    while pos < len(expr):
        m = TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            raise ValueError(f"expression form not modelled: {expr!r}")
        pos = m.end()
        fn, sid, oid, oname, gh, lit, op = m.groups()
        if fn:
            has_status = True
            py.append(f"_{fn}()")
        elif sid:
            py.append(repr(ctx["steps"].get(sid, {}).get("outcome", "")))
        elif oid:
            py.append(repr(ctx["steps"].get(oid, {}).get("outputs", {}).get(oname, "")))
        elif gh:
            py.append(repr(str(ctx[gh])))
        elif lit is not None:
            py.append(repr(lit))
        else:
            py.append({"&&": " and ", "||": " or ", "!": " not ", "true": "True",
                       "false": "False"}.get(op, op))
    return "".join(py), has_status


def eval_if(expr, ctx):
    if expr is None:
        expr = "success()"
    if isinstance(expr, bool):
        return expr
    code, has_status = evaluate(str(expr), ctx)
    if not has_status:
        code = f"_success() and ({code})"
    funcs = {"_always": lambda: True,
             "_success": lambda: ctx["status"] == "success" and not ctx["cancelled"],
             "_failure": lambda: ctx["status"] == "failure",
             "_cancelled": lambda: ctx["cancelled"]}
    return bool(eval(code, {"__builtins__": {}}, funcs))


def template(value, ctx):
    def one(m):
        code, _ = evaluate(m.group(1), ctx)
        v = eval(code, {"__builtins__": {}}, {})
        return "true" if v is True else "false" if v is False else str(v)
    return re.sub(r"\$\{\{(.*?)\}\}", one, str(value))


# ---------- steps ----------
def step_kind(step):
    uses, name = step.get("uses", ""), step.get("name", "")
    for prefix, kind in (("actions/checkout@", "checkout"), ("actions/configure-pages@", "configure"),
                         ("actions/upload-pages-artifact@", "upload"), ("actions/deploy-pages@", "deploy"),
                         ("actions/cache/restore@", "restore"), ("actions/cache/save@", "save")):
        if uses.startswith(prefix):
            return kind
    if name.startswith("Wait for the artifact"):
        return "wait"
    if "run" in step and not uses:
        return "bash"
    raise ValueError(f"step not modelled: {step}")


def snapshot(work):
    return {str(p.relative_to(work)): p.read_text() for p in work.rglob("*") if p.is_file()}


_BASH = {}


def bash(script, env, files):
    """Runs a run: step under bash -e in a fresh folder holding `files`.
    Same input, same output, so results are kept to save time."""
    key = (script, tuple(sorted(env.items())), tuple(sorted(files.items())))
    if key not in _BASH:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for rel, text in files.items():
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                (d / rel).write_text(text)
            out = d / "_github_output"
            out.write_text("")
            p = subprocess.run(["bash", "-e", "-c", script], cwd=d, capture_output=True, text=True,
                               env=dict(env, GITHUB_OUTPUT=str(out), PATH="/usr/bin:/bin"))
            outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
            out.unlink()
            _BASH[key] = (p.stdout + p.stderr, p.returncode, outputs, snapshot(d))
    return _BASH[key]


def run_once(wf, kind, cache, run_id, restore_error=None):
    """One workflow run. Returns (conclusion, log, counts)."""
    if kind == "never_started":
        return "cancelled", "", {}
    job = next(iter(wf["jobs"].values()))
    ctx = {"steps": {}, "status": "success", "cancelled": False,
           "run_id": run_id, "run_attempt": 1}
    log, counts, done = [], {}, []
    if "if" in job and not eval_if(job["if"], ctx):
        return "skipped", "", {}
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        for step in job["steps"]:
            sk = step_kind(step)
            sid = step.get("id")
            if not eval_if(step.get("if"), ctx):
                if sid:
                    ctx["steps"][sid] = {"outcome": "skipped", "outputs": {}}
                continue
            counts[sk] = counts.get(sk, 0) + 1
            outputs = {}
            if sk in PATH_STEPS:
                need = PATH_STEPS[:PATH_STEPS.index(sk)]
                if sk in ("upload", "deploy") and any(done.count(k) == 0 for k in need):
                    raise AssertionError(f"{sk} ran after an earlier step did not succeed")
                if kind == f"cancel_{sk}":
                    outcome = "cancelled"
                    ctx["cancelled"] = True
                elif kind == f"fail_{sk}":
                    outcome = "failure"
                else:
                    outcome = "success"
                    done.append(sk)
            elif sk == "restore":
                # actions/cache v6 restoreImpl and toolkit restoreCache: a found entry sets the
                # cache-matched-key output; lookup-only sets it but downloads nothing; an
                # error while downloading or extracting is only a warning, sets no output, and
                # may leave extracted files behind ("partial"); a thrown error fails the step.
                outcome = "failure" if restore_error == "error" else "success"
                if restore_error != "error":
                    w = step["with"]
                    path, key = template(w["path"], ctx), template(w["key"], ctx)
                    rks = [k for k in str(w.get("restore-keys", "")).splitlines() if k.strip()]
                    same = [e for e in cache if e["path"] == path]
                    hit = [e for e in same if e["key"] == key] or [e for e in same if e["key"].startswith(key)]
                    for rk in rks:
                        if hit:
                            break
                        hit = [e for e in same if e["key"].startswith(template(rk.strip(), ctx))]
                    lookup_only = str(w.get("lookup-only", "false")).strip().lower() == "true"
                    if hit:
                        newest = max(hit, key=lambda e: e["seq"])
                        if restore_error != "partial":
                            outputs["cache-matched-key"] = newest["key"]
                        for rel, text in ({} if lookup_only else newest["files"]).items():
                            (work / rel).parent.mkdir(parents=True, exist_ok=True)
                            (work / rel).write_text(text)
            elif sk == "save":
                w = step["with"]
                path, key = template(w["path"], ctx), template(w["key"], ctx)
                files = {str(p.relative_to(work)): p.read_text()
                         for p in (work / path).rglob("*") if p.is_file()} if (work / path).exists() else {}
                outcome = "success"  # an existing key or empty path only warns
                if files and not any(e["key"] == key for e in cache):
                    cache.append({"seq": len(cache), "key": key, "path": path, "files": files})
            else:  # bash
                env = {k: template(v, ctx) for k, v in (step.get("env") or {}).items()}
                stdout, rc, outputs, files = bash(step["run"], env, snapshot(work))
                log.append(stdout)
                outcome = "success" if rc == 0 else "failure"
                for rel, text in files.items():
                    (work / rel).parent.mkdir(parents=True, exist_ok=True)
                    (work / rel).write_text(text)
            if sid:
                ctx["steps"][sid] = {"outcome": outcome, "outputs": outputs}
            if outcome == "failure" and step.get("continue-on-error") is not True:
                ctx["status"] = "failure"
            if kind == "cancel_after_good" and sk == "deploy":
                ctx["cancelled"] = True
    if ctx["cancelled"]:
        conclusion = "cancelled"
    elif ctx["status"] == "failure" and job.get("continue-on-error") is not True:
        conclusion = "failure"
    else:
        conclusion = "success"
    return conclusion, "".join(log), counts


def run_seq(wf, seq, restore_error_at=None):
    cache, emails, logs = [], [], []
    for i, kind in enumerate(seq):
        conclusion, log, counts = run_once(wf, kind, cache, 1000 + i, restore_error=(restore_error_at[1] if restore_error_at and i == restore_error_at[0] else None))
        if counts.get("upload", 0) > 1 or counts.get("deploy", 0) > 1:
            raise AssertionError(f"more than one upload or deploy in one run: {counts}")
        emails.append(conclusion == "failure")
        logs.append(log)
    return emails, logs


# ---------- checks ----------
def structure(wf, text):
    bad = []
    if wf.get("concurrency") != {"group": "pages", "cancel-in-progress": True}:
        bad.append("workflow concurrency is not group pages with cancel-in-progress")
    if len(wf.get("jobs", {})) != 1:
        bad.append("not exactly one job")
    steps = next(iter(wf["jobs"].values()))["steps"]
    for kind in ("upload", "deploy"):
        n = sum(1 for s in steps if step_kind(s) == kind)
        if n != 1:
            bad.append(f"{n} {kind} steps in the file")
    # The model reads only these keys. Anything else could change what GitHub does,
    # so it is a failure, not something to skip.
    job = next(iter(wf["jobs"].values()))
    extra = set(job) - {"runs-on", "environment", "steps", "continue-on-error", "if"}
    if extra:
        bad.append(f"job keys not modelled: {sorted(extra)}")
    inputs = {"upload": {"path"}, "wait": {"script"}, "restore": {"path", "key", "restore-keys", "lookup-only"},
              "save": {"path", "key"}}
    for s in steps:
        if "run" in s and "${{" in s["run"]:
            bad.append(f"expression inside run text of {s.get('name')}")
        extra = set(s) - {"id", "name", "if", "uses", "with", "run", "env", "continue-on-error"}
        if extra:
            bad.append(f"step keys not modelled in {s.get('id') or s.get('name')}: {sorted(extra)}")
        if "continue-on-error" in s and s["continue-on-error"] is not True:
            bad.append(f"continue-on-error not literal true in {s.get('id') or s.get('name')}")
        extra = set(s.get("with") or {}) - inputs.get(step_kind(s), set())
        if extra:
            bad.append(f"inputs not modelled in {s.get('id') or s.get('name')}: {sorted(extra)}")
    if "continue-on-error" in job and job["continue-on-error"] is not True:
        bad.append("job continue-on-error not literal true")
    return bad


def replay_kind(run_id):
    jobs = json.loads((HERE / "fixtures" / f"jobs-{run_id}.json").read_text())["jobs"]
    names = {"Run actions/checkout@v4": "checkout", "Run actions/configure-pages@v5": "configure",
             "Run actions/upload-pages-artifact@v3": "upload", "Wait for the artifact to be listed": "wait",
             "Run actions/deploy-pages@v4": "deploy"}
    if not jobs or not jobs[0]["steps"]:
        return "never_started" if not jobs else "cancel_wait"
    steps = {names[s["name"]]: s["conclusion"] for s in jobs[0]["steps"] if s["name"] in names}
    for k in PATH_STEPS:
        if steps.get(k) == "failure":
            return f"fail_{k}"
    if steps.get("deploy") == "success":
        return "cancel_after_good" if jobs[0]["conclusion"] == "cancelled" else "good"
    return "cancel_deploy" if steps.get("deploy") == "cancelled" else "cancel_wait"


def suite(text, verbose=False):
    """Returns (problems, red sequence count, sequence count)."""
    wf = yaml.safe_load(text)
    problems = structure(wf, text)
    seqs = [s for n in (1, 2, 3) for s in itertools.product(KINDS, repeat=n)]
    red = 0
    for seq in seqs:
        try:
            got, _ = run_seq(wf, seq)
        except Exception as e:  # a form the model does not know is a failure too
            got = f"error: {e}"
        if got != oracle(seq):
            red += 1
            if red <= 3:
                problems.append(f"{' > '.join(seq)}: email {got}, want {oracle(seq)}")
    # Pass test 4: no saved result, or a failed lookup, is "no earlier failure", said in the log.
    # Pass test B (round 4): a damaged or partial restore is "no earlier failure" too.
    for seq, at, words in ((("fail_deploy",), None, "No earlier deploy result was restored"),
                           (("fail_deploy", "fail_deploy"), (1, "error"), "Could not look up the earlier deploy result"),
                           (("fail_deploy", "fail_deploy"), (1, "partial"), "No earlier deploy result was restored")):
        try:
            got, logs = run_seq(wf, seq, restore_error_at=at)
            if got[-1] or words not in logs[-1] or "no earlier failure" not in logs[-1]:
                problems.append(f"lookup case {seq}: email {got}, log {logs[-1]!r}")
        except Exception as e:
            problems.append(f"lookup case {seq}: {e}")
    # Real runs.
    for ids in ((45413361106, 45415969592, 45418761018),
                (45415969592, 43095722533, 45415969592),
                (45415969592, 42806424818, 45415969592),
                (45415969592, 42068254991, 45415969592),
                (45415969592, 41471386526, 45415969592),
                (45413361106, 43095722533, 45415969592)):
        seq = tuple(replay_kind(i) for i in ids)
        try:
            got, _ = run_seq(wf, seq)
        except Exception as e:
            got = f"error: {e}"
        if got != oracle(seq):
            problems.append(f"replay {ids} as {seq}: email {got}, want {oracle(seq)}")
        elif verbose:
            print(f"  replay {ids} as {seq}: email {got}")
    return problems, red, len(seqs)


# ---------- break cases: each must turn the suite red ----------
def edit(old, new, count=1):
    def f(t):
        if t.count(old) < count:
            raise ValueError(f"break case text not found: {old!r}")
        return t.replace(old, new, count)
    return f


def step_block(header):
    """Edit one step: header is its first line, e.g. '- id: deployment'."""
    def apply(t, fn):
        i = t.index(header)
        j = t.find("\n      - ", i + 1)
        j = len(t) if j < 0 else j
        return t[:i] + fn(t[i:j]) + t[j:]
    return apply


def in_step(header, old, new):
    return lambda t: step_block(header)(t, edit(old, new))


COE = "\n        continue-on-error: true"
BREAKS = {
    # The ticket's kinds.
    "first failure fails the run (no continue-on-error on the deploy)": in_step("- id: deployment", COE, ""),
    "first failure fails the run (no continue-on-error on checkout)": in_step("- id: checkout", COE, ""),
    "first failure fails the run (no continue-on-error on configure)": in_step("- id: configure", COE, ""),
    "first failure fails the run (no continue-on-error on upload)": in_step("- id: upload", COE, ""),
    "first failure fails the run (no continue-on-error on wait)": in_step("- name: Wait for the artifact", COE, ""),
    "first failure fails the run (rule alerts on any bad)": edit('[ "$this" = bad ] && [ "$before" = bad ]', '[ "$this" = bad ]'),
    "second failure passes (rule never alerts)": edit("alert=true", "alert=false"),
    "good does not reset the count": edit("then this=good; fi", "then this=; fi"),
    "skipped step hides an earlier failure (wait not counted)": edit('"$UPLOAD" "$WAIT" "$DEPLOY"', '"$UPLOAD" "$DEPLOY"'),
    "cancel before the deploy ends changes the count": edit('if [ "$outcome" = failure ]', 'if [ "$outcome" != success ]'),
    "cancel after a good deploy does not reset (decide not always)": in_step("- name: Decide this deploy's result", "if: always()", "if: success()"),
    "cancel after a good deploy does not reset (save not always)": in_step("- name: Save this deploy's result", "if: always() && ", "if: "),
    # The check switched off.
    "fail step if false": in_step("- name: Fail the run", "if: always() && steps.decide.outputs.alert == 'true'", "if: false"),
    "fail step if ${{ false }}": in_step("- name: Fail the run", "if: always() && steps.decide.outputs.alert == 'true'", "if: ${{ false }}"),
    "fail step removed": lambda t: t[:t.index("      - name: Fail the run")],
    "fail step continue-on-error": in_step("- name: Fail the run", "\n        run: |", COE + "\n        run: |"),
    "job continue-on-error": edit("    runs-on: ubuntu-latest\n", "    runs-on: ubuntu-latest\n    continue-on-error: true\n"),
    "decide step if false": in_step("- name: Decide this deploy's result", "if: always()", "if: false"),
    "save step if false": in_step("- name: Save this deploy's result", "if: always() && steps.decide.outputs.result != ''", "if: false"),
    "restore step if false": in_step("- name: Restore the last saved", "if: always()", "if: false"),
    # Saved state wired wrong.
    "save key without the run id (entry never replaced)": in_step("- name: Save this deploy's result", "-${{ github.run_id }}-${{ github.run_attempt }}", "-latest"),
    "restore keys removed": in_step("- name: Restore the last saved", "\n          restore-keys: pages-deploy-result-", ""),
    "save path differs": in_step("- name: Save this deploy's result", "path: .pages-deploy-result", "path: .pages-result"),
    # Settings the model does not read.
    "restore lookup-only (never downloads)": in_step("- name: Restore the last saved", "\n          restore-keys:", "\n          lookup-only: true\n          restore-keys:"),
    "partial restore trusted (file read without a matched key)": edit('if [ -n "$MATCHED" ] && [ -f "$file" ]', 'if [ -f "$file" ]'),
    "matched key read from the wrong step": edit("${{ steps.previous.outputs.cache-matched-key }}", "${{ steps.decide.outputs.cache-matched-key }}"),
    "decide step working-directory": in_step("- name: Decide this deploy's result", "\n        if: always()", "\n        if: always()\n        working-directory: /tmp"),
    "deploy continue-on-error ${{ false }}": in_step("- id: deployment", COE, "\n        continue-on-error: ${{ false }}"),
    "save step timeout-minutes": in_step("- name: Save this deploy's result", COE, COE + "\n        timeout-minutes: 0"),
    # Broken chain and shape.
    "deploy runs after the wait failed": in_step("- id: deployment", "\n        if: steps.wait.outcome == 'success'", ""),
    "upload runs after configure failed": in_step("- id: upload", "\n        if: steps.configure.outcome == 'success'", ""),
    "second upload": edit("      - id: upload\n", "      - uses: actions/upload-pages-artifact@v3\n      - id: upload\n"),
    "second deploy": edit("      # The two-in-a-row rule.", "      - uses: actions/deploy-pages@v4\n        if: always()\n      # The two-in-a-row rule."),
    "cancel-in-progress off": edit("cancel-in-progress: true", "cancel-in-progress: false"),
    "concurrency removed": edit("concurrency:\n  group: pages\n  cancel-in-progress: true\n", ""),
}


def main():
    if "--file" in sys.argv:
        f = Path(sys.argv[sys.argv.index("--file") + 1])
        problems, red, n = suite(f.read_text())
        print(f"{f}: {red} of {n} sequences wrong")
        for p in problems:
            print("  FAIL", p)
        print("RESULT:", "FAIL" if problems else "PASS")
        return 1 if problems else 0

    text = WORKFLOW.read_text()
    print(f"Branch file {WORKFLOW.relative_to(REPO)}:")
    problems, red, n = suite(text, verbose=True)
    print(f"  sweep: {red} of {n} sequences wrong (want 0); oracle emails in "
          f"{sum(any(oracle(s)) for k in (1, 2, 3) for s in itertools.product(KINDS, repeat=k))} of {n}")
    for p in problems:
        print("  FAIL", p)
    failed = bool(problems)

    print(f"Break cases ({len(BREAKS)}), each must be red:")
    green = 0
    for name, fn in BREAKS.items():
        try:
            broken = fn(text)
        except ValueError as e:
            print(f"  FAIL {name}: {e}")
            failed = True
            continue
        try:
            bp, bred, _ = suite(broken)
        except Exception as e:
            bp, bred = [f"error: {e}"], 0
        state = "red" if bp else "GREEN"
        if not bp:
            green += 1
        print(f"  {state}: {name} ({bred} of {n} sequences wrong, {len(bp)} problems)")
    print(f"  {len(BREAKS) - green} of {len(BREAKS)} break cases red")
    failed = failed or green > 0
    print("RESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
