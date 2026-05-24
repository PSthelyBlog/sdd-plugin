"""Invariant checking for cross-machine property validation."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from sdd.events import EventLog


@dataclass
class InvariantResult:
    """Result of checking a single invariant."""

    passed: bool
    invariant_name: str
    violation_details: str | None = None


@dataclass
class InvariantReport:
    """Aggregated results of all invariant checks."""

    results: list[InvariantResult] = field(default_factory=list)
    total_checked: int = 0
    total_passed: int = 0
    total_failed: int = 0

    @property
    def all_passed(self) -> bool:
        """True if all invariants passed."""
        return self.total_failed == 0

    @property
    def failures(self) -> list[InvariantResult]:
        """Return only failed invariant results."""
        return [r for r in self.results if not r.passed]


class InvariantFunction(Protocol):
    """Protocol for invariant functions.

    Invariants are functions that take an event log and machine states,
    and raise AssertionError if the invariant is violated.

    The states dict maps (machine_class_name, instance_id) tuples to state names.
    """

    def __call__(self, log: "EventLog", states: dict[tuple[str, str], str]) -> None:
        """Check the invariant.

        Args:
            log: The event log to check.
            states: Dict mapping (machine_name, instance_id) to current state.

        Raises:
            AssertionError: If the invariant is violated.
        """
        ...


def check_invariant(
    invariant: Callable[["EventLog", dict[tuple[str, str], str]], None],
    log: "EventLog",
    states: dict[tuple[str, str], str],
) -> InvariantResult:
    """Check a single invariant and return the result.

    Args:
        invariant: The invariant function to check.
        log: The event log to check.
        states: Dict mapping (machine_name, instance_id) to current state.

    Returns:
        InvariantResult with pass/fail status and details.
    """
    invariant_name = invariant.__name__

    try:
        invariant(log, states)
        return InvariantResult(passed=True, invariant_name=invariant_name)
    except AssertionError as e:
        return InvariantResult(
            passed=False,
            invariant_name=invariant_name,
            violation_details=str(e) if str(e) else "Invariant assertion failed",
        )


def check_invariants(
    invariants: list[Callable[["EventLog", dict[tuple[str, str], str]], None]],
    log: "EventLog",
    states: dict[tuple[str, str], str],
) -> InvariantReport:
    """Check multiple invariants and return an aggregated report.

    Args:
        invariants: List of invariant functions to check.
        log: The event log to check.
        states: Dict mapping (machine_name, instance_id) to current state.

    Returns:
        InvariantReport with all results aggregated.
    """
    report = InvariantReport()

    for invariant in invariants:
        result = check_invariant(invariant, log, states)
        report.results.append(result)
        report.total_checked += 1
        if result.passed:
            report.total_passed += 1
        else:
            report.total_failed += 1

    return report


def load_invariants(
    directory: Path | str,
) -> list[Callable[["EventLog", dict[tuple[str, str], str]], None]]:
    """Load all invariant functions from Python files in a directory.

    Invariant functions are identified by:
    1. Being defined in a .py file in the given directory (or subdirectories)
    2. Having a name starting with 'invariant_'
    3. Being callable

    Args:
        directory: Path to the directory containing invariant modules.

    Returns:
        List of invariant functions found in the directory.

    Raises:
        FileNotFoundError: If the directory does not exist.
    """
    path = Path(directory)
    if not path.exists():
        raise FileNotFoundError(f"Invariants directory not found: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    invariants: list[Callable[["EventLog", dict[tuple[str, str], str]], None]] = []

    # Find all Python files in the directory
    for py_file in sorted(path.glob("**/*.py")):
        # Skip __pycache__ and __init__ files
        if py_file.name.startswith("__"):
            continue

        # Load the module
        module_name = f"_invariants_{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            continue

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        try:
            spec.loader.exec_module(module)
        except Exception:
            # Skip files that can't be loaded
            continue

        # Find all invariant functions in the module
        for attr_name in dir(module):
            if not attr_name.startswith("invariant_"):
                continue

            attr = getattr(module, attr_name)
            if callable(attr):
                invariants.append(attr)

    return invariants


def load_invariants_from_module(
    module,
) -> list[Callable[["EventLog", dict[tuple[str, str], str]], None]]:
    """Load all invariant functions from a module object.

    Useful for testing or when invariants are defined inline.

    Args:
        module: A Python module object containing invariant functions.

    Returns:
        List of invariant functions found in the module.
    """
    invariants: list[Callable[["EventLog", dict[tuple[str, str], str]], None]] = []

    for attr_name in dir(module):
        if not attr_name.startswith("invariant_"):
            continue

        attr = getattr(module, attr_name)
        if callable(attr):
            invariants.append(attr)

    return invariants
