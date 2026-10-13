"""Design-System Fidelity Benchmark for Figma's MCP server.

Question: when an AI coding agent turns a Figma design into code through Figma's MCP
server, how often does it reuse the design system (components, layout primitives,
tokens) instead of re-creating it with raw HTML and hard-coded values -- and does a
design-system rules file fix that?

Testbed: Figma's open-source Simple Design System (SDS): a Figma community file plus a
React codebase with Code Connect, design tokens and Figma-written agent rules (AGENTS.md).

Usage (from this folder):
  python bench.py setup                      # clone SDS into work/sds
  python bench.py check                      # verify screens.json + claude CLI
  python bench.py fetch                      # (cached mode only) save MCP design context per screen
  python bench.py run --condition baseline --runs 1
  python bench.py run --condition rules --runs 1
  python bench.py score                      # writes results/runs.csv + results/summary.md
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import score as S

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
SDS = WORK / "sds"
RUNS = WORK / "runs"
CONTEXT = WORK / "context"
RESULTS = HERE / "results"
SCREENS = HERE / "screens.json"
CONDITIONS = ("baseline", "rules")
IGNORE = shutil.ignore_patterns(".git", "node_modules", "dist", "storybook-static")


# ---------------------------------------------------------------- helpers
def load_screens(only: str | None = None) -> list[dict]:
    data = json.loads(SCREENS.read_text(encoding="utf-8"))["screens"]
    data = [s for s in data if s.get("figma_url", "").strip()]
    if only:
        keep = {x.strip() for x in only.split(",")}
        data = [s for s in data if s["name"] in keep]
    return data


def clean_url(url: str) -> str:
    """Keep only file key + node-id, so the prompt has no '&' (breaks Windows .cmd shims)."""
    u = urlparse(url.strip())
    node = parse_qs(u.query).get("node-id", [""])[0]
    parts = [p for p in u.path.split("/") if p]
    key_idx = next((i for i, p in enumerate(parts) if p in ("design", "file", "proto")), 0)
    key = parts[key_idx + 1] if len(parts) > key_idx + 1 else ""
    return f"https://www.figma.com/design/{key}/?node-id={node}"


def claude_bin() -> str:
    exe = shutil.which("claude")
    if not exe:
        sys.exit("Claude Code CLI not found on PATH. Install it, then reopen the terminal.")
    return exe


def sha(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)).replace("\\", "/"): sha(p)
            for p in root.rglob("*") if p.is_file() and "node_modules" not in p.parts}


# ---------------------------------------------------------------- setup / check
def cmd_setup(a):
    WORK.mkdir(exist_ok=True)
    if not SDS.exists():
        subprocess.run(["git", "clone", "--depth", "1", "https://github.com/figma/sds.git", str(SDS)], check=True)
    commit = subprocess.run(["git", "-C", str(SDS), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    (WORK / "sds_commit.txt").write_text(commit, encoding="utf-8")
    print(f"SDS ready at {SDS} (commit {commit})")
    refs = sorted(p.stem for p in (SDS / "src" / "examples").glob("*.tsx") if p.stem != "Demo")
    print("Reference screens available (Figma's own implementations):", ", ".join(refs))


def cmd_check(a):
    exe = claude_bin()
    print("claude:", exe)
    print(subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip())
    print(subprocess.run([exe, "mcp", "list"], capture_output=True, text=True).stdout.strip())
    screens = load_screens()
    print(f"{len(screens)} screens with a Figma link:")
    for s in screens:
        print(f"  {s['name']:<16} ref={s.get('reference') or '-':<16} {clean_url(s['figma_url'])}")
    if not SDS.exists():
        print("! run `python bench.py setup` first")


# ---------------------------------------------------------------- workspace
def make_workspace(dest: Path, condition: str, screen: dict, mode: str) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SDS, dest, ignore=IGNORE)
    # hold out Figma's reference implementations -- they are the answer key
    shutil.rmtree(dest / "src" / "examples", ignore_errors=True)
    (dest / "src" / "App.tsx").write_text(
        'import { Footer, Header } from "compositions";\nimport { AllProviders } from "data";\n\n'
        "function App() {\n  return (\n    <AllProviders>\n      <Header />\n      <Footer />\n"
        "    </AllProviders>\n  );\n}\n\nexport default App;\n", encoding="utf-8")
    (dest / "src" / "screens").mkdir(exist_ok=True)
    agents = sorted(dest.rglob("AGENTS.md"))
    for f in dest.rglob("CLAUDE.md"):
        f.unlink()
    if condition == "baseline":
        for f in agents:
            f.unlink()
    else:  # rules: load Figma's own SDS agent rules into Claude Code's memory
        lines = ["# Project instructions", "", "Follow the design-system rules in these files:"]
        lines += ["@" + str(f.relative_to(dest)).replace("\\", "/") for f in agents]
        (dest / "CLAUDE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mode == "cached":
        ctx = CONTEXT / f"{screen['name']}.md"
        if not ctx.exists():
            raise FileNotFoundError(f"missing cached context {ctx}; run `python bench.py fetch`")
        (dest / "design").mkdir(exist_ok=True)
        shutil.copy(ctx, dest / "design" / ctx.name)


def prompt_for(screen: dict, mode: str) -> str:
    name = screen["name"]
    url = clean_url(screen["figma_url"])
    src = (f"Use the Figma MCP server to read the design of the frame at {url}." if mode == "live" else
           f"The design context for this frame was retrieved from the Figma MCP server and saved in "
           f"design/{name}.md. Use that file as your source for the design.")
    return (f"Implement a Figma design as a React screen in this codebase. {src} "
            f"Write the screen to src/screens/{name}.tsx and export a component named {name}. "
            f"You may add CSS files inside src/screens if needed. Do not modify any other files, "
            f"do not install packages, and do not start servers. When finished, reply with one line: DONE.")


def run_one(condition: str, screen: dict, r: int, mode: str, model: str | None, timeout: int) -> str:
    out = RUNS / condition / screen["name"] / f"r{r}"
    if (out / "claude.json").exists():
        return f"skip {condition}/{screen['name']}/r{r} (done)"
    ws = out / "ws"
    make_workspace(ws, condition, screen, mode)
    before = snapshot(ws)
    (out / "before.json").write_text(json.dumps(before), encoding="utf-8")
    tools = "Read,Write,Edit,Glob,Grep,LS" + (",mcp__figma" if mode == "live" else "")
    cmd = [claude_bin(), "-p", prompt_for(screen, mode), "--output-format", "json",
           "--allowedTools", tools, "--permission-mode", "acceptEdits"]
    if mode == "cached":
        cmd += ["--disallowedTools", "mcp__figma"]
    if model:
        cmd += ["--model", model]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        stdout, rc = p.stdout, p.returncode
        (out / "stderr.txt").write_text(p.stderr or "", encoding="utf-8")
    except subprocess.TimeoutExpired:
        stdout, rc = json.dumps({"is_error": True, "result": "timeout"}), -1
    meta = {"condition": condition, "screen": screen["name"], "run": r, "mode": mode, "model": model,
            "returncode": rc, "wall_s": round(time.time() - t0, 1), "prompt": prompt_for(screen, mode)}
    try:
        meta["claude"] = json.loads(stdout)
    except json.JSONDecodeError:
        meta["claude_raw"] = stdout[-4000:]
    (out / "claude.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return f"done {condition}/{screen['name']}/r{r} rc={rc} {meta['wall_s']}s"


def cmd_run(a):
    if not SDS.exists():
        sys.exit("run `python bench.py setup` first")
    screens = load_screens(a.screens)
    if not screens:
        sys.exit("No screens with a figma_url in screens.json")
    jobs = [(a.condition, s, r) for r in range(1, a.runs + 1) for s in screens]
    print(f"{len(jobs)} runs | condition={a.condition} mode={a.mode} model={a.model} parallel={a.parallel}")
    with ThreadPoolExecutor(max_workers=a.parallel) as ex:
        for msg in ex.map(lambda j: run_one(*j, a.mode, a.model, a.timeout), jobs):
            print(msg, flush=True)


def cmd_fetch(a):
    """Cached mode: one get_design_context call per screen, saved once and reused by every run.
    Keeps MCP usage inside low rate limits and holds the agent's input fixed across conditions."""
    CONTEXT.mkdir(parents=True, exist_ok=True)
    for s in load_screens(a.screens):
        dest = CONTEXT / f"{s['name']}.md"
        if dest.exists() and dest.stat().st_size > 200:
            print("skip", s["name"]); continue
        prompt = (f"Call the Figma MCP tool get_design_context once for the frame at {clean_url(s['figma_url'])}. "
                  f"Then write the complete tool output, verbatim and unedited, to the file {dest.name} "
                  f"in the current folder. Do not summarise it and do not call any other tool except Write.")
        p = subprocess.run([claude_bin(), "-p", prompt, "--allowedTools", "mcp__figma,Write",
                            "--permission-mode", "acceptEdits"], cwd=CONTEXT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=a.timeout)
        size = dest.stat().st_size if dest.exists() else 0
        print(f"{s['name']}: {size} bytes" + ("" if size else f"  !! {p.stdout[-300:]} {p.stderr[-300:]}"))


# ---------------------------------------------------------------- scoring
def cmd_score(a):
    tokens, exports = S.defined_tokens(SDS), S.exported_names(SDS)
    refs = {p.stem: S.score_tsx(p, tokens, exports) for p in (SDS / "src" / "examples").glob("*.tsx")}
    screens = {s["name"]: s for s in json.loads(SCREENS.read_text(encoding="utf-8"))["screens"]}
    rows = []
    for meta_f in sorted(RUNS.glob("*/*/r*/claude.json")):
        out = meta_f.parent
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        ws = out / "ws"
        before = json.loads((out / "before.json").read_text(encoding="utf-8"))
        after = snapshot(ws)
        changed = [k for k, v in after.items() if before.get(k) != v]
        outside = [k for k in changed if not k.startswith("src/screens/")]
        name = meta["screen"]
        main = ws / "src" / "screens" / f"{name}.tsx"
        if not main.exists():
            cands = sorted((ws / "src" / "screens").glob("*.tsx"))
            main = cands[0] if cands else None
        css = [ws / k for k in changed if k.endswith(".css")]
        c = meta.get("claude", {})
        row = {"condition": meta["condition"], "screen": name, "run": meta["run"], "mode": meta["mode"],
               "model": meta.get("model"), "produced_file": bool(main), "files_changed_outside_screens": len(outside),
               "outside_files": ";".join(outside), "cost_usd": c.get("total_cost_usd"),
               "turns": c.get("num_turns"), "wall_s": meta.get("wall_s"), "agent_error": c.get("is_error")}
        if main:
            sc = S.score_tsx(main, tokens, exports, css)
            row.update(sc.row())
            ref_name = screens.get(name, {}).get("reference")
            if ref_name in refs:
                row["ref_recall"], row["ref_precision"] = S.reference_overlap(sc, refs[ref_name])
        rows.append(row)
    RESULTS.mkdir(exist_ok=True)
    if not rows:
        sys.exit("No runs found under work/runs")
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in rows[0], k))
    with open(RESULTS / "runs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
    write_summary(rows, refs, screens)
    print((RESULTS / "summary.md").read_text(encoding="utf-8"))


def mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.mean(xs), 3) if xs else None


def write_summary(rows, refs, screens):
    metrics = [("component_reuse", "Component reuse"), ("layout_fidelity", "Layout via SDS layout"),
               ("token_fidelity", "Token fidelity"), ("ref_recall", "Recall of reference components"),
               ("hardcoded_values", "Hard-coded values / screen"), ("local_components", "One-off components / screen"),
               ("raw_content", "Raw HTML replacing SDS components / screen"),
               ("files_changed_outside_screens", "Files edited outside src/screens"),
               ("cost_usd", "Cost per screen (USD)"), ("turns", "Agent turns")]
    conds = [c for c in CONDITIONS if any(r["condition"] == c for r in rows)]
    lines = ["# Results", "", f"Runs: {len(rows)} | screens: {len({r['screen'] for r in rows})} | "
             f"SDS commit: {(WORK / 'sds_commit.txt').read_text().strip() if (WORK / 'sds_commit.txt').exists() else '?'}", ""]
    ref_used = [refs[s["reference"]] for s in screens.values() if s.get("reference") in refs]
    header = "| Metric | " + " | ".join(conds) + " | Figma reference code |"
    lines += [header, "|" + "---|" * (len(conds) + 2)]
    for key, label in metrics:
        vals = [str(mean([r.get(key) for r in rows if r["condition"] == c])) for c in conds]
        refv = mean([getattr(x, key) if hasattr(x, key) else x.row().get(key) for x in ref_used]) \
            if key in ("component_reuse", "layout_fidelity", "token_fidelity", "hardcoded_values",
                       "local_components", "raw_content") else ""
        lines.append(f"| {label} | " + " | ".join(vals) + f" | {refv if refv is not None else ''} |")
    lines += ["", "## Per screen (component reuse, mean over runs)", "",
              "| Screen | " + " | ".join(conds) + " |", "|" + "---|" * (len(conds) + 1)]
    for name in sorted({r["screen"] for r in rows}):
        vals = [str(mean([r.get("component_reuse") for r in rows if r["screen"] == name and r["condition"] == c]))
                for c in conds]
        lines.append(f"| {name} | " + " | ".join(vals) + " |")
    inv = sorted({v for r in rows for v in (r.get("invented_vars") or "").split(";") if v})
    hal = sorted({v for r in rows for v in (r.get("hallucinated_imports") or "").split(";") if v})
    lines += ["", f"Invented tokens (var(--sds-*) that do not exist): {', '.join(inv) or 'none'}",
              f"Hallucinated SDS imports (names SDS does not export): {', '.join(hal) or 'none'}", ""]
    (RESULTS / "summary.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").set_defaults(f=cmd_setup)
    sub.add_parser("check").set_defaults(f=cmd_check)
    f = sub.add_parser("fetch"); f.add_argument("--screens"); f.add_argument("--timeout", type=int, default=600)
    f.set_defaults(f=cmd_fetch)
    r = sub.add_parser("run")
    r.add_argument("--condition", choices=CONDITIONS, required=True)
    r.add_argument("--runs", type=int, default=1)
    r.add_argument("--screens", help="comma-separated subset, e.g. FAQs,PricingGrid")
    r.add_argument("--mode", choices=("live", "cached"), default="live")
    r.add_argument("--model", default="sonnet", help="Claude Code model alias; keep it fixed across conditions")
    r.add_argument("--parallel", type=int, default=2)
    r.add_argument("--timeout", type=int, default=900)
    r.set_defaults(f=cmd_run)
    sub.add_parser("score").set_defaults(f=cmd_score)
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
