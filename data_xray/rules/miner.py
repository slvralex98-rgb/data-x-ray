"""Discovery of hypotheses from one dataset, under explicit search budgets."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import math
import statistics
import time

from ..models import AnalysisResult
from ..semantics import leading_zero, shape
from .engine import _Context, _evaluate, _format_matches
from .models import MiningOptions, MiningResult, RuleSpec


class RuleMiner:
    def mine(self, dataset, profile_result: AnalysisResult | None = None, options: MiningOptions | None = None,
             progress=None, cancel=None) -> MiningResult:
        started = time.perf_counter()
        options = options or MiningOptions()
        if not isinstance(options, MiningOptions):
            raise ValueError("Ожидались MiningOptions.")
        # A profile may inform a caller's UI, but all evidence is recomputed from this dataset.
        # This prevents stale profiles from silently changing a rule or its denominator.
        context = _Context(dataset, options, cancel)
        context.checkpoint()
        progress = progress or (lambda *_: None)
        names = sorted(dataset.frame.columns)[:options.max_columns]
        notes = list(dataset.notes) + [
            "Кандидат — гипотеза на текущих данных, а не бизнес-истина. Перед сохранением требуется решение аналитика.",
            "Домен, формат и робастный диапазон изучены на тех же данных: их высокая доля соответствия не является независимой проверкой.",
            "Границы диапазона: Q1/Q3 ± множитель IQR; при IQR=0 используется частый уровень. Это не допустимые бизнес-минимум/максимум.",
            "Поиск ограничен бюджетами столбцов, пар, кардинальности и числа правил; полный перебор не выполняется.",
            "Пропуски пропускаются кроме not_null и следствия conditional_required. confidence = passed/evaluated; skipped показан отдельно.",
            "row_indices — позиции с 0; examples.row — номера записей с 1 после заголовка.",
        ]
        if len(names) < dataset.frame.width:
            notes.append(f"Бюджет столбцов: рассмотрены первые {len(names)} из {dataset.frame.width} по имени.")
        if dataset.truncated:
            notes.append("Кандидаты относятся только к загруженному фрагменту, не ко всему файлу.")
        results = []
        known_ids = set()
        summaries = {}
        budget_hit = False

        def offer(rule, extra=None):
            nonlocal budget_hit
            context.checkpoint()
            if len(results) >= options.max_rules:
                budget_hit = True
                return
            evaluation = _evaluate(context, rule)
            if evaluation.evaluated < options.min_support or evaluation.confidence is None or evaluation.confidence < options.min_confidence:
                return
            if rule.id not in known_ids:
                evaluation.evidence.update(extra or {})
                evaluation.evidence["min_support"] = options.min_support
                evaluation.evidence["min_confidence"] = options.min_confidence
                evaluation.evidence["hypothesis"] = True
                known_ids.add(rule.id)
                results.append(evaluation)

        for k, name in enumerate(names):
            context.checkpoint()
            values, missing = context.column(name)
            present = [v for v, m in zip(values, missing) if not m]
            # Do not serialize arbitrarily long cells into domains or dependency maps.
            counts = Counter(str(v) for v in present)
            safe_text = all(0 < len(v) <= 4096 for v in counts)
            category = safe_text and 2 <= len(counts) <= options.max_cardinality and len(counts) / max(1, len(present)) <= options.max_category_ratio
            numbers = context.parse(name, "number")
            dates = context.parse(name, "date")
            valid_numbers = [v for v in numbers if v is not None]
            valid_dates = [v for v in dates if v is not None]
            number = bool(present) and len(valid_numbers) / len(present) >= options.min_confidence and sum(leading_zero(v) for v in present) / len(present) < .5
            date = bool(present) and len(valid_dates) / len(present) >= options.min_confidence
            summaries[name] = {"counts": counts, "category": category, "number": number, "date": date,
                               "variable": len(counts) > 1, "missing": sum(missing)}
            offer(RuleSpec("not_null", (name,)))
            if category:
                frequent = sorted(value for value, count in counts.items() if count >= options.min_group_size)
                covered = sum(counts[value] for value in frequent)
                domain = frequent if frequent and covered / len(present) >= options.min_confidence else sorted(counts)
                offer(RuleSpec("allowed_values", (name,), {"values": domain}),
                      {"learned_domain_size": len(domain), "observed_domain_size": len(counts), "training_domain": True,
                       "rare_values_excluded": len(counts) - len(domain)})
            if number and len(valid_numbers) >= max(2, options.min_support):
                q1, _, q3 = statistics.quantiles(valid_numbers, n=4, method="inclusive")
                spread = q3 - q1
                lower, upper = q1 - options.iqr_multiplier * spread, q3 + options.iqr_multiplier * spread
                if math.isfinite(lower) and math.isfinite(upper):
                    offer(RuleSpec("robust_range", (name,), {"lower": lower, "upper": upper}), {"q1": q1, "q3": q3, "iqr_multiplier": options.iqr_multiplier})
            if present and date:
                offer(RuleSpec("string_shape", (name,), {"format": "iso_date"}))
            if present and not number and not date and safe_text:
                # Prefer a named, meaningful syntax predicate to a generic shape.
                selected = None
                for fmt in ("email", "uuid", "digits"):
                    context.checkpoint()
                    matches = sum(_format_matches(value, {"format": fmt}) for value in present)
                    if matches / len(present) >= options.min_confidence:
                        selected = {"format": fmt}
                        break
                if selected is None:
                    shapes = Counter(shape(v) for v in present)
                    dominant = sorted(shapes, key=lambda s: (-shapes[s], s))[0]
                    if 0 < len(dominant) <= 4096 and shapes[dominant] / len(present) >= options.min_confidence:
                        selected = {"format": "shape", "shape": dominant}
                if selected:
                    offer(RuleSpec("string_shape", (name,), selected))
            progress(int(5 + 35 * (k + 1) / max(1, len(names))), f"Кандидаты: {name}")
            if budget_hit:
                break

        pair_count = 0
        if not budget_hit:
            total_pairs = len(names) * (len(names) - 1) // 2
            for a, b in combinations(names, 2):
                context.checkpoint()
                if pair_count >= options.max_pairs:
                    break
                pair_count += 1
                for determinant, target in ((a, b), (b, a)):
                    sd, st = summaries[determinant], summaries[target]
                    if sd["category"] and st["variable"]:
                        av, am = context.column(determinant)
                        bv, bm = context.column(target)
                        groups = defaultdict(Counter)
                        for i, (x, y, mx, my) in enumerate(zip(av, bv, am, bm)):
                            if i % 512 == 0:
                                context.checkpoint()
                            if not mx and not my:
                                groups[str(x)][str(y)] += 1
                        mapping = {}
                        tied = 0
                        repeated_support = 0
                        for key, counts in sorted(groups.items()):
                            size = sum(counts.values())
                            ranking = sorted(counts, key=lambda v: (-counts[v], v))
                            if size < options.min_group_size:
                                continue
                            if len(ranking) > 1 and counts[ranking[0]] == counts[ranking[1]]:
                                tied += 1
                                continue
                            mode = ranking[0]
                            if 0 < len(mode) <= 4096:
                                mapping[key] = mode
                                repeated_support += size
                        if len(mapping) >= 2 and len(set(mapping.values())) >= 2 and repeated_support >= options.min_support:
                            offer(RuleSpec("functional_dependency", (determinant, target), {"mapping": mapping}),
                                  {"repeated_group_support": repeated_support, "min_group_size": options.min_group_size, "tied_groups_excluded": tied})
                    if sd["category"] and st["missing"]:
                        av, am = context.column(determinant)
                        for value in sorted(sd["counts"]):
                            if sd["counts"][value] >= options.min_support:
                                offer(RuleSpec("conditional_required", (determinant, target), {"value": value}),
                                      {"target_missing_total": st["missing"]})
                sa, sb = summaries[a], summaries[b]
                kind = "date" if sa["date"] and sb["date"] else "number" if sa["number"] and sb["number"] else None
                if kind and sa["variable"] and sb["variable"]:
                    # One strongest relation per pair; equality has the clearest semantics.
                    for op in ("=", "<=", ">="):
                        candidate = RuleSpec("comparison", (a, b), {"operator": op, "type": kind})
                        ev = _evaluate(context, candidate)
                        if ev.evaluated >= options.min_support and ev.confidence is not None and ev.confidence >= options.min_confidence:
                            offer(candidate)
                            break
                progress(40 + int(55 * pair_count / max(1, min(total_pairs, options.max_pairs))), f"Пары столбцов: {pair_count}")
                if budget_hit:
                    break
            if pair_count < total_pairs:
                notes.append(f"Поиск пар остановлен: проверено {pair_count} из {total_pairs} возможных пар.")
        if budget_hit:
            notes.append(f"Достигнут лимит {options.max_rules} правил; дальнейшие кандидаты не проверялись.")
        context.checkpoint()
        results.sort(key=lambda ev: (ev.rule.kind, ev.rule.columns, ev.rule.id))
        notes.append(f"Порог кандидатов: применимых записей ≥{options.min_support}, доля соответствия ≥{options.min_confidence:.1%}; проверено пар: {pair_count}.")
        progress(100, "Кандидаты готовы")
        context.checkpoint()
        return MiningResult(dataset.source, dataset.frame.height, results, notes, options, time.perf_counter() - started)
