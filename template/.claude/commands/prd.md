---
description: Create or update the main PRD or a feature brief before engineering planning
---

Use the product consultant flow to decide whether the work should:
- create the main PRD (`docs/prd/active/PRD-001.md`),
- update the main PRD,
- create or update a feature brief under `docs/prd/changes/active/`, or
- refuse PRD creation and route the user to direct `/plan` when the request is really a bugfix/small engineering change.

Rules:
- Focus on product definition, business logic, user experience, MVP scope, and non-goals.
- Do not do technical design, implementation planning, or task breakdown here.
- Ask one high-value question at a time until the product definition is clear enough.
- Treat the main PRD as the project's first-principles product document.
- Use feature briefs for additive/local product changes that should not rewrite the main PRD.
- Bugfixes, small repairs, localized optimizations, and test-only work should usually skip `/prd` and go directly to `/plan`.
- Only generate the final document when the requirement is clear enough, or when the user explicitly says to generate it now.
- End the document with a short handoff summary for engineering planning.

User input: $ARGUMENTS

Deliverables:
1. Product-memory summary in Chinese when new information is confirmed.
2. Final PRD or feature brief in Simplified Chinese when ready.
3. A saved file at one of:
   - `docs/prd/active/PRD-001.md`
   - `docs/prd/changes/active/FEATURE-XXX.md`
