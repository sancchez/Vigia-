# Skill Registry — ciberseguridad (Vigia)

Infra file for SDD sub-agents. Not an SDD artifact — do not archive.

## Project conventions
- `D:\freestyle\CLAUDE.md` — parent-level, describes the Freestyle ecosystem (9 proyectos), orchestrator/Agent Teams rules, SDD workflow, token efficiency rules.
- `docs/open-core.md` — public repo vs `vigia_core_private` split. Consult before touching prompts, cumplimiento/ISO mapping, remediacion/priorizacion reasoning, or takedown/lookalike scoring heuristics.

## Relevant skills (user-level, `~/.claude/skills/`)
| Skill | Use for |
|---|---|
| fastapi-advanced | New endpoints, dependency injection, background tasks in `api/` |
| langgraph-patterns | Changes to `orchestrator/graph.py`, agent nodes in `agents/` |
| pytest | Writing/extending tests in `tests/` |
| react-19 | Frontend components in `frontend/src/` |
| typescript | Frontend type work |
| tailwind-4 | Frontend styling (if adopted) |
| zustand-5 | Frontend state (if adopted) |
| docker-compose-agents | Fase 3c: self-hosted certstream-server-go |
| llm-evaluation | Fase 7: evals de agentes IA |
| playwright | E2E verification of new UI (drill-down, lifecycle board, brand protection panel) |
| prompt-engineering | Any change to agent prompts (goes to vigia_core_private if it's reasoning logic) |
| python-async | Async endpoints/background scan jobs |

No project-level skills (`.claude/skills/`, `.agent/skills/`) found in this repo.

## SDD skills (this ecosystem)
sdd-init, sdd-explore, sdd-propose, sdd-spec, sdd-design, sdd-tasks, sdd-apply, sdd-verify, sdd-archive — see parent CLAUDE.md for the dependency graph and phase table.
