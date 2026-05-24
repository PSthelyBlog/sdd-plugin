# sdd — Simulation-Driven Development for Claude Code

A Claude Code plugin for building production systems via Simulation-Driven Development. State machines are the production core; you verify them by simulation, then connect I/O adapters. The machines that come out of simulation are the machines that ship.

This plugin packages the SDD methodology as six phase-specific skills. Each skill triggers when the corresponding phase of the workflow is appropriate.

## What's in the plugin

| Skill | Phase | Triggers |
|---|---|---|
| `sdd:start` | Bootstrap | "start an SDD project", "scaffold a state-machine project", "set up SDD" |
| `sdd:decompose` | Phase 0 | "decompose this", "break this into machines", "plan the SDD machines for X" |
| `sdd:author-machine` | Phase 1 | "implement MachineName", "author the X machine", "write the OrderLifecycle machine" |
| `sdd:converge` | Phases 2–5 | "run convergence", "does the system converge?", "check convergence" |
| `sdd:diagnose` | Fix loop | "why did this fail?", "diagnose this", "trace this cascade" |
| `sdd:adapt` | Phase 6 | "scaffold adapters", "connect to HTTP", "add database persistence" |

## Install

This plugin is a standard Claude Code plugin directory. To install in a Claude Code project, copy or symlink the `sdd/` directory into your plugins location (typically `~/.claude/plugins/` or a project-local `.claude/plugins/`).

The skills assume the [SDD framework](https://github.com/PSthelyBlog/simulation-driven-development) Python package is installable in the target project. The `sdd:start` skill handles installation via:

```bash
pip install git+https://github.com/PSthelyBlog/simulation-driven-development.git
```

## When SDD is appropriate

SDD is built for:

- **Greenfield** code (no existing domain logic to refactor around)
- **Multi-entity workflows** with cross-domain interactions — orders + payments + inventory, subscriptions + billing + plan changes, reservations + rooms + payments
- Projects where **correctness of business rules** matters more than raw speed of delivery

SDD is **not** the right tool for:

- Simple CRUD apps (use a framework's scaffolding)
- Performance-critical low-level code
- Pure presentation / UI work
- Maintaining legacy code without a domain rewrite

The `sdd:start` skill explicitly asks the user to confirm fit before proceeding.

## The workflow at a glance

```
sdd:start          → scaffold project layout + install framework
   ↓
sdd:decompose      → produce decomposition (domains, states, events, invariants)
   ↓                  user reviews & approves
sdd:author-machine → implement each machine (optionally in parallel via subagents)
   ↓
sdd:converge       → assemble + run structural / scenario / invariant checks
   ↓                  iterate via sdd:diagnose on failures
sdd:adapt          → only after is_converged: True
                     scaffold inbound/outbound adapters
```

Each skill suggests the next one when it completes. The user can always interrupt or pick a different skill.

## The framework / runtime split

This plugin formalizes a design choice that makes SDD work: the framework holds what must be deterministic for correctness (state machine semantics, event delivery, structural checks, coverage tracking, invariant evaluation), and Claude Code holds what benefits from judgment (decomposition, brief construction, diagnosis, fix dispatch, adapter authorship).

That's why this plugin contains six skills with prose instructions instead of, say, twenty MCP tools. The skills are *prompts* that tell Claude Code how to operate the framework in each phase. The framework's structured outputs (`StructuralReport`, `RoutingFailure`, `ConvergenceReport`, etc.) are what Claude Code reads to make decisions.

If you want to read more: the [framework's docs](https://github.com/PSthelyBlog/simulation-driven-development/tree/main/docs) lay out the architecture, the protocol, and the convergence criteria.

## License

MIT. See the framework repository for full license text.
