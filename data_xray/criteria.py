"""Qt-free adapter: explicit saved rules become actionable quality findings."""
from .models import Finding
from .rules import evaluate_rules


def evaluations_to_findings(result):
    """Only violations become findings; unavailable checks remain in evaluations."""
    return [Finding(
        code=f"custom:{ev.rule.id}", title="Моя проверка: " + ", ".join(ev.rule.columns),
        severity="high", confidence="fact", columns=list(ev.rule.columns),
        count=ev.violations, total=ev.evaluated, description=ev.rule.description,
        recommendation="Исправьте значения или уточните критерий, затем повторите проверку.",
        row_indices=list(ev.row_indices),
        examples=[{"row": example["row"], "values": dict(example["values"])} for example in ev.examples],
        score=85 + min(15, int(15 * ev.violations / max(1, ev.evaluated))),
    ) for ev in result.evaluations if ev.status == "fail" and ev.violations > 0]


def evaluate_criteria(dataset, rules, options=None, progress=None, cancel=None):
    """Return MiningResult, preserving not_applicable statuses and row positions."""
    return evaluate_rules(dataset, rules, options=options, progress=progress, cancel=cancel)
