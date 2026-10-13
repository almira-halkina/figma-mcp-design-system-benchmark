# Teardown: keeping AI-generated UI on-system

## 1. The user problem
Design-system teams spend years building components and tokens. Agentic design-to-code is quickly becoming the main
path from Figma to production. Figma's own team calls today's MCP output "a strong first pass, not a finished one":
unmapped colours, fonts and auto layout need fixing by hand or by the Figma agent. Every hard-coded colour and one-off
button an agent ships is design-system debt, and the design-system team often can't see it.

Users: (a) design-system leads, who need adoption and consistency at scale; (b) product engineers, who need code
they can merge without rework; (c) designers, who need what ships to match what they drew.

## 2. What I measured
I ran Figma's open-source Simple Design System (Figma community file + React codebase). The test set was 6 of its
example pages (Home, About, Contact Us, Pricing, Waitlist, Landing). Claude Code (Sonnet) implemented each page from its
Figma frame through Figma's remote MCP server, once per condition. Baseline was the MCP server plus the codebase. Rules
added Figma's own SDS `AGENTS.md`. Figma's React implementations of the examples were removed from the agent's copy and
used as the answer key. A static scorer counts SDS components versus raw HTML, SDS layout primitives versus raw `div`s,
and tokens versus hard-coded values.

## 3. Findings
- **The MCP server alone does not keep agents on-system.** Component reuse was 32% at baseline. 4 of 6 pages were
  rebuilt from scratch with zero SDS components and about 58 hard-coded values per screen, even though every needed
  component existed in the codebase. The design context tells the agent what the screen looks like, not what it is
  made of.
- **Some of it only looks on-system.** Baseline CSS often wrote `var(--sds-..., #fff)`, a token with a hard-coded fallback.
  It passes a casual review but breaks theming the moment a token is missing.
- **A rules file is the cheapest fix found.** With Figma's own `AGENTS.md`, reuse went to 99%, layout via SDS to 99%, and
  hard-coded values to 0.2 per screen, for about 10% more cost ($0.24 to $0.26 per screen). There were no invented
  tokens or hallucinated imports in either condition.
- **But rules files drift.** SDS's `AGENTS.md` still names `get_code`, `get_image` and `get_variable_map`; the
  server now exposes `get_design_context`, `get_screenshot` and `get_variable_defs`.
- **Limits:** one run per page, static metrics (no visual diff yet), one design system.

Observation already found while building the harness: the SDS `AGENTS.md` tells agents to call `get_code`,
`get_image` and `get_variable_map`. The current MCP server exposes `get_design_context`, `get_screenshot` and
`get_variable_defs`. Rules files drift as the tool surface changes.

## 4. Metric tree
- **North star:** share of agent-generated UI that is on-system (component reuse × token fidelity), weighted by
  screens shipped.
- **Inputs:** Code Connect coverage of the library; rules-file presence and freshness; variable coverage of the
  designs; agent and model.
- **Guardrails:** visual match to the Figma frame; build passes; files edited outside scope; cost and latency per screen.

## 5. Prioritised improvements
1. **Design-system fidelity score in Dev Mode / the MCP response.** After generation, report "N hard-coded values,
   M components re-created", each with the library item it should have used.
2. **Rules-file freshness check.** Flag rules that reference renamed MCP tools or components that no longer exist.
3. **Adoption analytics for agents.** Show design-system teams which components agents skip most often. These are
   the gaps in Code Connect mappings or docs.

## 6. Experiment plan
Run an A/B test of (1) on teams with a published library. Primary metric: on-system share of merged UI code.
Secondary metrics: review rework time and adoption of the fidelity score. Guardrail: generation latency.
Run 3 times per screen; report means with min–max.
