Polish the NeuraWatch project plan using the project subagents and produce a single updated recommendation.

Use these project files as the primary context:
- `PROJECT_PLAN.md`
- `JIRA_TICKETS.md`
- `TECHNICAL_DESIGN_DOCUMENT.md`

Workflow:
1. Read the three project documents first.
2. In parallel when possible, ask these three subagents to review the plan:
   - `product-owner-neurawatch`
   - `staff-backend-neurawatch`
   - `staff-frontend-neurawatch`
3. After the first review round, perform a mediated interaction round:
   - Ask the backend engineer to respond to the biggest frontend concerns.
   - Ask the frontend engineer to respond to the biggest backend concerns.
   - Ask the product owner to resolve the most important tradeoffs and decide what must stay in MVP.
4. Synthesize the result into one polished plan.

Deliverable:
- A concise revised plan with these sections:
  - MVP definition
  - Architecture decisions
  - Ordered implementation plan
  - Risks and mitigations
  - Suggested ticket changes
  - Explicit keep / cut / defer decisions

Rules:
- Optimize for shipping by Friday EOD CDMX time.
- Prefer simplicity over completeness.
- If the existing plan is too ambitious, reduce scope instead of inventing more work.
- Call out any ticket or design item that is not justified for the deadline.
