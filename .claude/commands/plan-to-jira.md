Turn the current NeuraWatch plan into a tighter Jira-ready backlog update.

Use these project files as the primary context:
- `PROJECT_PLAN.md`
- `JIRA_TICKETS.md`
- `JIRA_IMPORT.csv`
- `TECHNICAL_DESIGN_DOCUMENT.md`

Workflow:
1. Read the planning, ticketing, and technical design files first.
2. Use these subagents in parallel when possible:
   - `product-owner-neurawatch`
   - `staff-backend-neurawatch`
   - `staff-frontend-neurawatch`
   - `tech-lead-neurawatch`
3. Ask the product owner to identify missing outcomes, acceptance criteria gaps, and scope cuts.
4. Ask backend and frontend staff engineers to identify missing implementation tickets, dependency mismatches, and sequencing risks.
5. Ask the tech lead to reconcile their feedback and recommend a final backlog structure.
6. Produce one consolidated Jira recommendation.

Deliverable:
- A concise backlog update with these sections:
  - ticket changes to make
  - tickets to add
  - tickets to split
  - tickets to defer
  - dependency/order changes
  - suggested sprint 1 and sprint 2 slices

Rules:
- Keep the `NW-####` convention
- Optimize for execution clarity, not backlog completeness theater
- Prefer fewer, sharper tickets over many vague ones
- If a ticket is too large for one focused working session, recommend splitting it
