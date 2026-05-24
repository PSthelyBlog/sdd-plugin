"""Convergence reporting: aggregate structural, scenario, and invariant results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdd.invariants import InvariantReport
    from sdd.runner import StructuralReport
    from sdd.scenario import ScenarioResult


@dataclass
class ConvergenceReport:
    """
    Aggregated result of all three convergence layers.

    Layer 1: Structural (StructuralReport)
    Layer 2: Scenario coverage (scenario results + coverage data)
    Layer 3: Property invariants (per-scenario InvariantReports)
    """

    # Layer 1
    structural: "StructuralReport | None" = None

    # Layer 2
    scenario_results: list["ScenarioResult"] = field(default_factory=list)
    transition_coverage: dict = field(default_factory=dict)
    state_coverage: dict = field(default_factory=dict)
    event_path_coverage: dict = field(default_factory=dict)

    # Layer 3 — aggregated across all scenarios
    invariant_results: list["InvariantReport"] = field(default_factory=list)

    # ---------- derived properties ----------

    @property
    def structural_passed(self) -> bool:
        return self.structural is not None and self.structural.is_valid

    @property
    def scenarios_passed(self) -> bool:
        return all(r.passed for r in self.scenario_results) and bool(self.scenario_results)

    @property
    def invariants_passed(self) -> bool:
        return all(ir.all_passed for ir in self.invariant_results) if self.invariant_results else True

    @property
    def coverage_complete(self) -> bool:
        """All transition/state/branch/event-path coverage at 100%."""
        for cov_dict in (self.transition_coverage, self.state_coverage):
            for machine_cov in cov_dict.values():
                if machine_cov.get("coverage", 0) < 1.0:
                    return False
        if self.event_path_coverage and self.event_path_coverage.get("coverage", 0) < 1.0:
            return False
        return True

    @property
    def is_converged(self) -> bool:
        """True when all three layers pass and coverage is complete."""
        return (
            self.structural_passed
            and self.scenarios_passed
            and self.invariants_passed
            and self.coverage_complete
        )

    # ---------- presentation ----------

    def summary(self) -> str:
        """Render the report in the format from docs/05."""
        lines: list[str] = []
        lines.append("CONVERGENCE REPORT")
        lines.append("=" * 18)
        lines.append("")

        # Structural section
        s = self.structural
        if s is None:
            lines.append("Structural Analysis: NOT RUN")
        else:
            status = "PASS" if s.is_valid else "FAIL"
            lines.append(f"Structural Analysis: {status}")
            lines.append(
                f"  - {len(s.machines_analyzed)} machines, "
                f"{s.total_states} states, {s.total_transitions} transitions"
            )
            lines.append(
                f"  - Unreachable states: "
                f"{sum(len(v) for v in s.unreachable_states.values())}"
            )
            lines.append(
                f"  - Terminal (deadlock) states: "
                f"{sum(len(v) for v in s.terminal_states.values())}"
            )
            lines.append(f"  - Dead letters: {len(s.dead_letters)}")
            lines.append(f"  - Phantom subscriptions: {len(s.phantom_subscriptions)}")
            lines.append(
                f"  - Guard-completeness gaps: "
                f"{sum(len(v) for v in s.guard_completeness_issues.values())}"
            )
        lines.append("")

        # Scenario section
        if not self.scenario_results:
            lines.append("Scenario Coverage: NOT RUN")
        else:
            status = "PASS" if self.scenarios_passed else "FAIL"
            passing = sum(1 for r in self.scenario_results if r.passed)
            lines.append(f"Scenario Coverage: {status}")
            lines.append(
                f"  - {passing}/{len(self.scenario_results)} scenarios passed"
            )
            # Transition coverage summary
            for cls_name, cov in self.transition_coverage.items():
                pct = int(cov.get("coverage", 0) * 100)
                lines.append(
                    f"  - {cls_name}: "
                    f"{len(cov.get('covered', []))}/{cov.get('total', 0)} "
                    f"transitions ({pct}%)"
                )
            # State coverage summary
            for cls_name, cov in self.state_coverage.items():
                pct = int(cov.get("coverage", 0) * 100)
                lines.append(
                    f"  - {cls_name}: "
                    f"{len(cov.get('entered', []))}/{cov.get('total', 0)} "
                    f"states ({pct}%)"
                )
            # Event-path coverage
            epc = self.event_path_coverage
            if epc:
                pct = int(epc.get("coverage", 0) * 100)
                lines.append(
                    f"  - Event subscriptions: "
                    f"{len(epc.get('triggered', []))}/{epc.get('total', 0)} "
                    f"({pct}%)"
                )
        lines.append("")

        # Invariant section
        if not self.invariant_results:
            lines.append("Property Invariants: NOT RUN")
        else:
            total_checked = sum(ir.total_checked for ir in self.invariant_results)
            total_failed = sum(ir.total_failed for ir in self.invariant_results)
            status = "PASS" if total_failed == 0 else "FAIL"
            lines.append(f"Property Invariants: {status}")
            lines.append(
                f"  - {total_checked - total_failed}/{total_checked} invariant checks passed"
            )
            if total_failed:
                lines.append("  - Failures:")
                seen: set[str] = set()
                for ir in self.invariant_results:
                    for f in ir.failures:
                        key = f"{f.invariant_name}:{f.violation_details}"
                        if key in seen:
                            continue
                        seen.add(key)
                        lines.append(
                            f"      [FAIL] {f.invariant_name}: {f.violation_details}"
                        )
        lines.append("")

        # Verdict
        if self.is_converged:
            lines.append("System is converged. Ready for I/O adaptation.")
        else:
            reasons: list[str] = []
            if not self.structural_passed:
                reasons.append("structural failures")
            if not self.scenarios_passed:
                reasons.append("scenario failures")
            if not self.invariants_passed:
                reasons.append("invariant violations")
            if self.scenario_results and not self.coverage_complete:
                reasons.append("incomplete coverage")
            if not reasons:
                reasons.append("missing layers (did you run all three?)")
            lines.append("Not converged: " + ", ".join(reasons) + ".")
        return "\n".join(lines)
