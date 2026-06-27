# Studydy Project Agent Rules

## Project context

Studydy is a learning platform centered on converting uploaded learning materials into a Knowledge Map.

Core product direction:

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

- Main development environment: Windows + WSL Ubuntu.
- Repository location: WSL Linux filesystem.
- Main repo path: `~/projects/Studydy`.
- Prefer WSL Codex CLI for coding, testing, and backend work.
- Codex App may be used only as a helper for review, worktree management, or parallel non-conflicting tasks.

## Branch rules

- Backend base branch: `be-dev`.
- Backend feature branches: `be/feature-*`.
- Merge through PR only.
- One PR must have one purpose.
- Do not commit directly to `be-dev`.

## Security rules

- Never add, commit, print, or push `.env` files.
- Never add, commit, print, or push API keys, connection strings, private keys, certificates, tokens, or secrets.
- `docs_local/` is private local reference only.
- Never add, commit, or push `docs_local/`.
- `backend/.env.example` may be committed only with placeholder values.
- Ask before installing new dependencies.
- Ask before committing.
- Never force push.

## Code scope rules

- Do not start implementation immediately.
- First identify task scope, affected files, and existing project patterns.
- Prefer the smallest change that satisfies the current task.
- Do not add speculative future features.
- Do not introduce new packages, frameworks, background jobs, service layers, helpers, or abstractions unless explicitly required.
- Prefer existing project patterns over new architecture.
- After implementation, run a pruning pass to remove unused code, duplicated logic, unnecessary comments, and speculative extension points.

## AI workflow rules

- Use one main Codex thread as the Supervisor for planning, scope control, and final integration.
- Use subagents only when the task benefits from parallel reading, testing, review, triage, or summarization.
- Do not use parallel write-heavy agents on the same files.
- Each subagent must have a bounded objective, permission boundary, expected output format, and files or areas it should not touch.
- For non-trivial tasks, create a PLAN before implementation.

## Documentation rules

- Do not expand documentation by default.
- Update docs only when behavior, setup, API, schema, command, or architecture decision changed.
- Update only the smallest relevant section.
- Do not write implementation process notes into official docs.
- Do not write unconfirmed future plans into official docs.
- Do not treat reminder documents as meeting records.

## Testing rules

- When changing backend behavior, run the relevant backend tests.
- When adding behavior, add tests only for required behavior and important edge cases.
- Do not add stub-only tests that do not verify real behavior.
- Do not remove meaningful tests just to make the suite pass.

## Final response rules

At the end of every task, report:

1. Files changed
2. Behavior changed
3. Tests run
4. Risks or limitations
5. Intentionally not done
