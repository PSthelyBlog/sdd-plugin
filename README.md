# sdd-plugin

A unified repository for **Simulation-Driven Development**: the Python framework and the Claude Code plugin that drives it, developed together so they never drift apart.

## What this repo contains

```
sdd-plugin/
├── .claude-plugin/         # plugin manifest — loaders find this at the root
│   └── plugin.json
├── skills/                 # the six phase-specific Claude Code skills
│   ├── start/SKILL.md      # bootstrap an SDD project
│   ├── decompose/SKILL.md  # Phase 0: task → state machines
│   ├── author-machine/SKILL.md  # Phase 1: implement each machine
│   ├── converge/SKILL.md   # Phases 2–5: structural + scenarios + invariants
│   ├── diagnose/SKILL.md   # fix-loop assistant
│   └── adapt/SKILL.md      # Phase 6: scaffold I/O adapters
├── framework/              # the SDD Python framework
│   ├── sdd/                # library code (StateMachine, EventBus, SimulationRunner, ...)
│   ├── docs/               # framework documentation (00–08)
│   ├── machines/           # reference machine: OrderLifecycle
│   ├── adapters/           # reference adapters (SQLite persistence, HTTP inbound)
│   ├── scenarios/          # reference scenarios
│   ├── invariants/         # reference invariants
│   ├── tests/              # 316 framework tests
│   └── pyproject.toml      # pip-installable
├── PLUGIN.md               # plugin-only README (original)
└── README.md               # you are here
```

## Why bundle them

The plugin's skill prose references specific framework APIs, file paths, and error categories. Co-locating them means:

- **One commit, one source of truth.** A framework API change and its skill-prose update land together.
- **Bundled install.** The `sdd:start` skill installs the framework from `<plugin>/framework/`, not from a separate pip package or git URL. Removes a network dependency and removes the possibility of version skew.
- **Easier development.** Open one repo, work on the framework in `framework/`, work on the plugin at the root. No tab juggling between two repos.

The framework retains its self-contained layout inside `framework/`, so it can still be lifted out and shipped standalone if anyone ever wants to. Nothing about the bundling is irreversible.

## Two ways to use this repo

### As a Claude Code plugin user

1. Install the plugin via Claude Code's plugin mechanism (Customize Claude in Claude Desktop, or copy `~/Desktop/sdd-plugin/` into your plugins directory).
2. In a new project, invoke `/sdd:start`. The skill scaffolds the project and installs the bundled framework into your project's Python environment.
3. Follow the workflow: `/sdd:decompose` → `/sdd:author-machine` → `/sdd:converge` → `/sdd:adapt`.

See `PLUGIN.md` for the plugin's own README, and `framework/docs/` for the framework documentation.

### As a developer of either

```bash
# Set up a dev environment for the framework
cd framework/
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/    # 316 tests should pass

# Edit a skill
$EDITOR ../skills/converge/SKILL.md
# Edit framework code
$EDITOR sdd/runner.py

# Verify framework still passes
pytest tests/

# Verify the plugin still loads (reload it in Claude Code)
```

When iterating on a skill, prefer editing it in this repo and re-syncing to your plugin install location, rather than editing the installed copy directly — keeps the canonical version in one place.

## Status

- Framework: 316/316 tests passing. Complete through Phase 8 (end-to-end reference adapters). See `framework/implementation-notes.md` for the per-phase history.
- Plugin: v0.1.0. Six skills, validated end-to-end against a library book loan tracker (see field report in `~/Desktop/sdd-test/SDD_FRAMEWORK_REPORT.md` if you have it).

## License

MIT. See individual files for attribution.
