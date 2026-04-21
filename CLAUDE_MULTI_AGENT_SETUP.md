# Claude Multi-Agent Setup for NeuraWatch

This setup is designed for **Claude Code**, which supports project-level subagents through `.claude/agents/` and reusable prompts through `.claude/commands/`.

## What This Setup Creates

- `product-owner-neurawatch`
- `staff-backend-neurawatch`
- `staff-frontend-neurawatch`
- `tech-lead-neurawatch`
- `/polish-plan` command to orchestrate the review
- `/plan-to-jira` command to refine the backlog
- `/pre-loom-review` command to prepare the final story

## Important Constraint

Claude Code subagents can review in parallel and return results independently, but they do **not** behave like three persistent peers chatting directly with each other. In practice, the **main Claude session orchestrates the interaction**:

1. Main Claude sends work to each subagent
2. Each subagent reviews independently
3. Main Claude passes the important disagreements back into a second round
4. Main Claude synthesizes the final output

That is the cleanest way to get the "agents interact between them" behavior in Claude Code today.

## How to Use It

From the project root in Claude Code:

1. Run `/agents`
2. Confirm the four project agents appear
3. Run `/polish-plan`

You can also invoke them explicitly with a prompt like:

```text
Read PROJECT_PLAN.md, JIRA_TICKETS.md, and TECHNICAL_DESIGN_DOCUMENT.md.
Use the product-owner-neurawatch, staff-backend-neurawatch, and staff-frontend-neurawatch subagents in parallel to critique the plan.
Then run one interaction round where backend addresses frontend concerns, frontend addresses backend concerns, and product owner resolves scope tradeoffs.
Finally, synthesize a revised plan optimized for shipping by Friday EOD CDMX time.
```

## Recommended Working Pattern

Use this operating model:

- Product owner:
  - guards scope
  - tightens acceptance criteria
  - decides what gets cut
- Staff backend engineer:
  - challenges inference, API, performance, and deployment assumptions
  - protects feasibility and FPS
- Staff frontend engineer:
  - challenges live-feed UX, polygon interactions, and dashboard usability
  - protects demo clarity and browser reliability
- Tech lead:
  - resolves disagreements
  - decides sequencing and ownership
  - protects end-to-end integration quality

## Commands

- `/polish-plan`
  - best before implementation starts
  - tightens MVP, architecture, and sequence
- `/plan-to-jira`
  - best after the plan exists and before the team starts executing
  - sharpens tickets, dependencies, and sprint slices
- `/pre-loom-review`
  - best after the prototype works end to end
  - prepares the strongest final delivery story

## Best Prompt Style

When you want strong delegation in Claude Code, be explicit:

- name the subagents you want used
- ask for them to work "in parallel when possible"
- ask for a second round to resolve disagreements
- ask for a final synthesized decision, not three separate essays

## Suggested Review Cadence

Use the multi-agent flow at these points:

1. Before implementation starts, to tighten the MVP
2. Before execution begins, to align the backlog and sprint structure
3. After the first end-to-end prototype, to cut nonessential work
4. Before recording the Loom, to sharpen the story and call out tradeoffs

## Files Added

- `.claude/agents/product-owner-neurawatch.md`
- `.claude/agents/staff-backend-neurawatch.md`
- `.claude/agents/staff-frontend-neurawatch.md`
- `.claude/agents/tech-lead-neurawatch.md`
- `.claude/commands/polish-plan.md`
- `.claude/commands/plan-to-jira.md`
- `.claude/commands/pre-loom-review.md`
