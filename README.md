# Design-System Fidelity Benchmark for Figma's MCP Server

**Question.** When an AI coding agent turns a Figma design into code through Figma's MCP server, how often does it
reuse the design system (its components, layout primitives and tokens), and how often does it rebuild the design
with raw HTML and hard-coded values? Does a design-system rules file close the gap?

Design systems only pay off if they are adopted. If agents become the main way designs reach code, then the share
of generated UI that stays on-system becomes the adoption metric for design systems at scale.

## Testbed

Figma's open-source **[Simple Design System (SDS)](https://github.com/figma/sds)** has three parts:
a [Figma community file](https://www.figma.com/community/file/1380235722331273046/simple-design-system) (components,
variables, example screens), a React codebase with Code Connect and `--sds-*` tokens, and Figma-written agent rules
(`AGENTS.md`). SDS also ships Figma's own React implementations of its example screens (`src/examples`). These are
**held out from the agent** and used as the answer key.

## Method

For each example screen, a fresh copy of the SDS codebase (with `src/examples` removed) is given to Claude Code, the
Figma MCP server and one identical prompt: implement this Figma frame as `src/screens/<Name>.tsx`.

| Condition | What the agent gets |
|---|---|
| `baseline` | Figma MCP server + the SDS codebase, with no rules file |
| `rules` | The same, plus Figma's own SDS `AGENTS.md` rules loaded through `CLAUDE.md` |

The model is fixed across conditions (`--model`), and each screen is run several times (`--runs 3`) because agent
output varies run to run.

## Metrics (static analysis, `score.py`)

| Metric | Definition |
|---|---|
| Component reuse | SDS component elements ÷ (SDS components + raw HTML that SDS already covers + new one-off components) |
| Layout fidelity | Share of layout done with SDS `Flex` / `Section` / `Grid` rather than raw `div`s |
| Token fidelity | `var(--sds-*)` that exist ÷ (those + hard-coded hex / rgb / px / rem / font-family + invented tokens) |
| Reference recall | Share of the SDS components in Figma's reference implementation that the agent also used |
| Invented tokens / hallucinated imports | `var(--sds-…)` names or SDS imports that do not exist in the system |
| Scope creep | Files the agent edited outside `src/screens` |
| Cost, turns | From Claude Code's JSON output |

**Validating the scorer.** On Figma's reference screens, the scorer gives component reuse of 0.85–1.0 with 0 hard-coded
values and 0 hallucinated imports (`tests/test_score.py`). Synthetic bad screens are caught: raw buttons, inline hex
colours, one-off components, invented tokens and non-existent imports.

## Run it

Requirements: Python 3.10+, git, and [Claude Code](https://docs.claude.com/en/docs/claude-code) with Figma's remote MCP
server added and authenticated:

```
claude mcp add --transport http figma https://mcp.figma.com/mcp
```

1. Duplicate the SDS community file to your drafts, then paste each example frame's link into `screens.json`.
2. Run these commands:

```
python bench.py setup
python bench.py check
python bench.py run --condition baseline --runs 3
python bench.py run --condition rules --runs 3
python bench.py score
```

Results are written to `results/summary.md` (means per condition against Figma's reference code) and `results/runs.csv`
(one row per run).

**Cached mode** is for low MCP rate limits. `python bench.py fetch` calls `get_design_context` once per screen and
saves the output. `run --mode cached` then reuses it, which also holds the agent's input fixed across conditions.

## Results (13 Oct 2026: 6 SDS example pages, 1 run per condition, Claude Code with Sonnet)

| Metric | Baseline (MCP only) | Rules (MCP + SDS `AGENTS.md`) | Figma reference code |
|---|---|---|---|
| Component reuse | 32% | 99% | 100% |
| Layout via SDS layout components | 27% | 99% | 100% |
| Token fidelity | 19% | 83% | 100% |
| Hard-coded values per screen | 58.3 | 0.2 | 0 |
| Raw HTML replacing SDS components per screen | 13.3 | 0 | 0 |
| Invented tokens / hallucinated imports | 0 / 0 | 0 / 0 | n/a |
| Cost per screen (USD) | 0.24 | 0.26 | n/a |

- **Without rules, the agent built 4 of 6 pages (Home, Pricing, Waitlist, Landing) with zero SDS components.** It made
  raw `<button>`, `<h1>` and `<div>` elements with a page-specific CSS file, even though the codebase had matching
  `Button`, `Hero` and `Navigation` components.
- **On-system theatre.** The baseline CSS often wrote `var(--sds-color-..., #fff)`: tokens with hard-coded fallbacks.
  It looks on-system in review, but nothing enforces it.
- **With Figma's rules file, 6 of 6 pages were assembled from SDS components and layout primitives** (e.g. `Hero`,
  `Navigation`, `Flex`, `Section`) with almost no hard-coded values, for about 10% more cost per screen.
- **The rules file has drifted:** it still tells agents to call `get_code`, `get_image` and `get_variable_map`, which the
  current MCP server has renamed. It worked anyway, because the value is in the component guidance, not the tool names.

Full numbers: [`results/summary.md`](results/summary.md), [`results/runs.csv`](results/runs.csv). Product write-up: [`docs/teardown.md`](docs/teardown.md).
Caveat: this is one run per page, so the next step is `--runs 3` and a visual diff against the Figma screenshots.

## Limits

- The metrics are static: they measure *how* a screen was built, not whether it looks right. Visual diffing against the
  Figma screenshot is a planned next step.
- There are six screens from one design system, so the findings are directional, not general.
- `rules` tests one fix (Figma's rules file). Code Connect publishing and `create_design_system_rules` are further levers.
