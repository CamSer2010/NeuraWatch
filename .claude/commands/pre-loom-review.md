Prepare NeuraWatch for the final demo narrative and delivery review before recording the Loom.

Use these project files as the primary context:
- `PROJECT_PLAN.md`
- `JIRA_TICKETS.md`
- `TECHNICAL_DESIGN_DOCUMENT.md`

If implementation artifacts exist, also review:
- `README.md`
- any delivery notes or summary docs in `docs/`

Workflow:
1. Read the available plan, technical, and delivery documents.
2. Use these subagents in parallel when possible:
   - `product-owner-neurawatch`
   - `staff-backend-neurawatch`
   - `staff-frontend-neurawatch`
   - `tech-lead-neurawatch`
3. Ask the product owner to review demo clarity, story flow, and whether the deliverables satisfy the assignment.
4. Ask the backend and frontend staff engineers to identify weak spots in the implementation story, unresolved risks, and likely questions from reviewers.
5. Ask the tech lead to synthesize the final narrative, including tradeoffs and “what we would do with 4 more weeks.”
6. Produce one integrated pre-Loom review.

Deliverable:
- A concise review with these sections:
  - demo readiness
  - architecture story
  - two hardest decisions and tradeoffs
  - what broke and how it was fixed
  - what is production-ready vs hacky
  - top risks if questioned live
  - strongest narrative for the Loom

Rules:
- Be honest about hacks and shortcuts
- Optimize for a credible demo story, not for pretending the project is more mature than it is
- Highlight the simplest strong narrative the presenter can tell clearly in 10 to 15 minutes
