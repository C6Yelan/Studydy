# Studydy Project Agent Rules

## Project context

Studydy is a learning platform centered on converting uploaded learning materials into a Knowledge Map.

Core product data flow:

Material
→ Material Block
→ Concept
→ Concept Relation
→ Knowledge Map
→ Resource
→ Learning Path
→ Learning Node / Learning Output

The first development priority is to keep the data flow clear, the schema stable, the API boundary explicit, and the implementation minimal.

## Development environment

* Main development environment: Windows + WSL Ubuntu.
* Repository location: WSL Linux filesystem.
* Main repo path: `~/projects/Studydy`.
* Prefer WSL Codex CLI for coding, testing, and backend work.
* Codex App may be used for parallel threads, worktree-based tasks, review, or background exploration.
* Do not let multiple Codex windows modify the same branch or the same files at the same time.

## Branch rules

* Backend base branch: `be-dev`.
* Backend feature branches: `be/feature-*`.
* Frontend base branch: `fe-dev`.
* Frontend feature branches: `fe/feature-*`.
* Do not implement features directly on `be-dev` or `fe-dev`.
* Use a feature branch for real implementation work.
* Merge into `be-dev` or `fe-dev` only after local review, tests, and user confirmation.
* Pull requests are optional and should be used only when external review, discussion history, or GitHub-based checks are needed.
* Never force push unless the user explicitly confirms the risk.

## Multi-window workflow

The main workflow is multi-window role separation.

Use these roles:

1. Supervisor

   * Controlled by the user.
   * Responsible for task decision, scope control, PLAN approval, review approval, and final Git decisions.
   * Does not automatically implement.

2. Explorer / Planner window

   * Read-only by default.
   * Finds relevant files, existing patterns, risks, and minimal implementation scope.
   * Produces or updates the PLAN.
   * Must not modify files.

3. Implementer window

   * Workspace-write only after a PLAN is approved.
   * Implements only the approved PLAN.
   * Must not expand scope, add speculative features, or change unrelated files.
   * Must not commit or push unless the user explicitly asks.

4. Reviewer / Pruner window

   * Read-only by default.
   * Reviews the diff against the PLAN.
   * Looks for bugs, unnecessary abstraction, over-implementation, duplicated logic, security issues, missing tests, and documentation bloat.
   * Must not directly rewrite the implementation.

5. Doc Curator window

   * Read-only by default.
   * Checks whether documentation actually needs to change.
   * Suggests the smallest required documentation update only when behavior, setup, API, schema, command, or architecture changed.

## Subagent usage

Subagents are a short-term acceleration tool, not the main workflow.

Use subagents only when a single Codex window benefits from parallel work, such as:

* parallel repository exploration
* comparing implementation locations
* reviewing security, tests, and over-implementation separately
* summarizing large but bounded context

Do not use subagents for:

* long-term role separation
* cross-stage workflow management
* write-heavy parallel implementation on the same files
* unclear requirements
* uncontrolled documentation expansion

Each subagent must have:

1. a bounded objective
2. a read/write permission boundary
3. an expected output format
4. files or areas it must not touch

## Local handoff files

Use `docs_local/ai_workflow/` as the private local handoff area.

Recommended files:

* `current_task.md`
* `explorer_report.md`
* `plan.md`
* `implementer_report.md`
* `review_report.md`
* `doc_report.md`
* `decision_log.md`
* `prompts.md`

Rules:

* `docs_local/` is private local reference only.
* Never add, commit, or push `docs_local/`.
* AI windows may read or write handoff files only when explicitly instructed by the user.
* Handoff files are for coordination, not official project documentation.

## Planning rules

Do not start implementation immediately.

For non-trivial tasks, create or read a PLAN before implementation.

A PLAN must include:

1. task goal
2. scope
3. explicitly excluded work
4. affected files
5. smallest implementation strategy
6. test strategy
7. risks and limitations
8. completion definition

Implementation must not begin until the user confirms the PLAN.

## Code scope rules

* Prefer the smallest change that satisfies the current task.
* Do not add speculative future features.
* Do not introduce new packages, frameworks, background jobs, service layers, helpers, or abstractions unless explicitly required.
* Prefer existing project patterns over new architecture.
* Do not create large generic systems before the concrete use case exists.
* Do not add defensive code that has no current failure path or test value.
* After implementation, run a pruning pass to remove unused code, duplicated logic, unnecessary comments, and speculative extension points.

## Documentation rules

* Do not expand documentation by default.
* Update docs only when behavior, setup, API, schema, command, or architecture decision changed.
* Update only the smallest relevant section.
* Do not write implementation process notes into official docs.
* Do not write unconfirmed future plans into official docs.
* Do not treat reminder documents as meeting records.
* Do not copy AI discussion logs into official docs.

## Security rules

* Never add, commit, print, or push `.env` files.
* Never add, commit, print, or push API keys, connection strings, private keys, certificates, tokens, or secrets.
* Never commit private local notes or files from `docs_local/`.
* Ask before installing new dependencies.
* Ask before committing.
* Ask before pushing.
* Never use sandbox bypass mode unless the user explicitly confirms it is a disposable environment.

## Testing rules

* When changing backend behavior, run the relevant backend tests.
* When adding behavior, add tests only for required behavior and important edge cases.
* Do not add stub-only tests that do not verify real behavior.
* Do not remove meaningful tests just to make the suite pass.
* If tests cannot be run, report why.

## Git decision rules

Before commit, check:

* `git status`
* `git diff`
* `git diff --cached`

Confirm:

* no `.env` files
* no secrets
* no `docs_local/`
* no unrelated changes
* no unplanned documentation expansion
* no obvious over-implementation

Only the user decides when to commit, merge, or push.

## Final response rules

At the end of every task, report:

1. Files changed
2. Behavior changed
3. Tests run
4. Risks or limitations
5. Intentionally not done
