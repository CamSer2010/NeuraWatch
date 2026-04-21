# Contributing to NeuraWatch

This repo uses a **branch-per-ticket** workflow. Every change on `main` must come through a pull request.

## Workflow

1. Pick a ticket from `JIRA_TICKETS.md` (e.g. `NW-1201`).
2. **Branch off `main`** — one branch per ticket.
3. Do the work, commit in focused chunks.
4. Open a **PR into `main`** using the template.
5. Merge after the acceptance criteria are green.

Do not push directly to `main`. Do not bundle unrelated tickets in one branch.

## Branch naming

Format: `<type>/NW-####-short-kebab-description`

Examples:
- `chore/NW-1001-monorepo-foundation`
- `feat/NW-1201-webcam-input`
- `feat/NW-1301-polygon-editor`
- `fix/NW-1304-debounce-edge-case`
- `docs/NW-1601-readme`

Keep the description short (≤5 words). Use the same `<type>` prefix as the commits on the branch.

## Commit messages

Format: `<type>(NW-####): <imperative lowercase subject>`

Examples:
- `chore(NW-1001): initialize monorepo folder structure`
- `feat(NW-1201): add webcam capture at 640x480`
- `fix(NW-1304): reset debounce counter on track loss`
- `docs(NW-1601): document ngrok setup in README`

### Allowed types

| Type | Use for |
|---|---|
| `feat` | New user-facing functionality |
| `fix` | Bug fix |
| `chore` | Tooling, scaffolding, deps, non-functional housekeeping |
| `docs` | Documentation only |
| `refactor` | Non-functional code restructure |
| `perf` | Performance improvement |
| `test` | Adding or updating tests |
| `build` | Build system, package manifests, bundling |
| `ci` | CI configuration |
| `revert` | Revert a prior commit |

### Subject rules

- Lowercase, imperative mood ("add", not "adds" or "added")
- No trailing period
- ≤72 characters including the prefix
- Ticket ID is mandatory when the work maps to a ticket in `JIRA_TICKETS.md`

### Multi-line commits

Use the body when the "why" is non-obvious:

```
feat(NW-1303): emit exactly one alert per zone transition

Per-track zone state is kept in ZoneService; transition is recorded
only after DEBOUNCE_FRAMES consecutive frames confirm the new state.
See PROJECT_PLAN.md ratified decisions #7, #9.
```

Keep the body wrapped at ~72 chars.

## Pull requests

Every PR must:

- Target `main`
- Have a title that mirrors the commit format: `feat(NW-1201): add webcam capture at 640x480`
- Reference the ticket and tick off its acceptance criteria in the template
- Include screenshots/recordings for any UI change
- Not include unrelated changes

The template lives at `.github/pull_request_template.md` and auto-populates on PR creation.

## Ticket mapping

- Every branch and PR maps to exactly one `NW-####` ticket.
- Foundational or cross-cutting work that predates a feature ticket (repo scaffolding, conventions, pre-planning artifacts) maps to `NW-1001`.
- If work doesn't map to any ticket, create one first (or fold it into a related ticket).

## Definition of done (per ticket)

A ticket is done when:
- All acceptance criteria in `JIRA_TICKETS.md` are met
- The PR is merged to `main`
- Any related doc updates have shipped in the same PR

## Source of truth

- `PROJECT_PLAN.md` — ratified plan, architecture decisions, day-by-day sequencing
- `JIRA_TICKETS.md` — backlog with tightened acceptance criteria
- `TECHNICAL_DESIGN_DOCUMENT.md` — deeper technical reference (superseded by PROJECT_PLAN.md where they conflict)
