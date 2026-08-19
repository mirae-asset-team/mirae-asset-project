# Technical Proposal Delivery Plan

> Execute with `superpowers:executing-plans`, the PDF skill, and `superpowers:verification-before-completion`.

## Task 1: Freeze evidence-backed proposal content

1. Extract the complete official task/submission requirements from the supplied PDF.
2. Reconcile every metric with tracked release evidence and mark external gaps honestly.
3. Write `docs/submission/technical-proposal.md` with diagrams, API contract, limitations, and reproducibility steps.
4. Run terminology, placeholder, metric, and secret scans; commit `docs: draft contest technical proposal`.

## Task 2: Produce and visually verify the submission PDF

1. Generate `output/pdf/mirae-disclosure-agent-technical-proposal.pdf` with Korean fonts, consistent headers/footers, tables, and vector diagrams.
2. Reopen the PDF, check page count/text presence, render every page to PNG, and inspect for clipping, overlap, missing glyphs, and unreadable tables.
3. Correct all visual defects and repeat rendering.
4. Commit the final PDF and generation source as `docs: add contest technical proposal pdf`.

## Task 3: Final submission cross-check

1. Cross-check source, README, Docker, API specification, PDF, and public endpoint checklist against the three official submission items.
2. Record external-only blockers separately: rotated provider live gates and authenticated NCP UI redeploy.
3. Run the complete local verification suite and leave the worktree clean.

