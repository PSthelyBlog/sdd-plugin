---
name: sdd-start
description: Bootstrap a new Simulation-Driven Development project — scaffold the machines/, scenarios/, invariants/, adapters/ layout and install the sdd-framework Python package. Use when the user is starting a new project that fits SDD's profile (greenfield, domain-heavy, multi-entity workflows with cross-domain interactions where correctness of business rules matters) and wants the SDD layout in place. Triggers include "start an SDD project", "set up SDD here", "scaffold a state machine project", "initialize SDD".
---

# sdd:start — Bootstrap an SDD project

You are bootstrapping a new Simulation-Driven Development project. Your job is to set up the directory layout, install the framework, and verify the setup works — not to start implementing machines yet (that's `sdd:author-machine`).

## When this skill fires

The user is at the beginning of a project that fits SDD's profile:

- Greenfield code (no existing domain logic to refactor around)
- Multi-entity workflows where multiple processes coordinate (orders + payments + inventory; subscriptions + billing + plan changes; reservations + rooms + payments)
- The user cares about correctness of business rules, not just plumbing

SDD is **not** the right tool for: simple CRUD apps, performance-critical low-level code, pure presentation/UI work, or maintaining existing legacy code without a domain rewrite. If the project doesn't match, tell the user and suggest they skip SDD.

## What to do

### 1. Confirm the project fits

Ask the user briefly:
- What problem are you modeling?
- Are there multiple interacting domains (e.g. orders + payments + inventory)?
- Is correctness of the business rules a priority?

If yes on all three, proceed. If the project is single-entity CRUD or UI-heavy, say so and suggest they don't need SDD.

### 2. Install the framework

This plugin ships the SDD framework bundled at `<plugin>/framework/`. Install it into the project's Python environment from the bundled copy:

```bash
# If the user has a venv, activate it first.
python -m venv .venv && source .venv/bin/activate   # if needed

# Install the bundled framework. Common install locations:
pip install ~/.claude/plugins/sdd/framework/
# or, if you're developing the plugin itself:
pip install /path/to/sdd-plugin/framework/
```

If the bundled framework isn't accessible (rare — only if the plugin was installed in a non-standard location), fall back to a git install. The framework lives in the `framework/` subdirectory of the plugin repo:

```bash
pip install "git+https://github.com/PSthelyBlog/sdd-plugin.git#subdirectory=framework"
```

Confirm the install with `python -c "from sdd.runner import SimulationRunner; print('ok')"`.

**Why bundled, not pip-published:** the framework and plugin co-evolve. Bundling guarantees skill prose and framework API stay in sync — no version-skew between what the skill describes and what the installed framework supports.

### 3. Scaffold the directory layout

Create these directories (only if absent — don't overwrite existing work):

```
machines/         # production-core state machines, one per domain
scenarios/        # YAML scenarios — happy paths, failure paths, timeouts
invariants/       # cross-machine property checks
adapters/
  inbound/        # HTTP / queue consumers / CLI → runner.fire(...)
  outbound/       # DB / email / webhooks → subscribe to bus
tests/            # adapter tests only — core is tested via simulation
```

Touch an `__init__.py` in each Python directory.

### 4. Create a CLAUDE.md for the project

The CLAUDE.md tells Claude Code how to operate in SDD mode in this project. Copy the SDD principles from the framework's CLAUDE.md or write a project-specific version:

```markdown
## Project

[One-sentence description of what this project models.]

## Core Principles (non-negotiable)

1. State machines are the production core — they ship unchanged after convergence.
2. Zero infrastructure imports in machines (only sdd.protocol + stdlib).
3. Events are the only inter-machine coupling — no machine references another.
4. Failures are explicit states, not try/except.
5. Adapters contain no domain logic.
6. Convergence before adaptation — no adapter code until structural + scenario + invariant checks all pass.

## Operating the framework

The runner is programmatic, not a CLI. To run convergence:

\`\`\`python
from sdd.runner import SimulationRunner
from sdd.scenario import ScenarioParser
from sdd.invariants import load_invariants

runner = SimulationRunner()
# runner.register(...) each machine class
# runner.register_resolver(...) each
scenarios = ScenarioParser().parse_directory("scenarios")
invariants = load_invariants("invariants")
report = runner.converge(scenarios=scenarios, invariants=invariants)
print(report.summary())
\`\`\`
```

### 5. Verify the setup

Run `runner.check()` on an empty registry — it should return a valid (if empty) StructuralReport without errors. Show the user.

### 6. Suggest the next step

When the layout is ready, tell the user:

> Layout ready. Next: run `/sdd:decompose` to break your problem into state machines.

## What not to do

- Do not start authoring machine code in this skill. That belongs in `sdd:author-machine`.
- Do not write any adapter code. That's `sdd:adapt`, only after convergence.
- Do not invent scenarios yet — the user hasn't decomposed the problem.
- Do not assume the framework is pip-installable as `sdd-framework` unless you've confirmed it. The repo install via `git+https://` is the reliable path right now.
