from __future__ import annotations

from itertools import combinations
import math

from . import Context, Finding, finding


class StructureDetector:
    def run(self, context: Context) -> list[Finding]:
        df = context.dataset.frame
        n = df.height
        found = []
        for p in context.profiles:
            context.checkpoint()
            if p.missing:
                indices = [i for i, missing in enumerate(context.missing[p.name]) if missing]
                found.append(finding("missing", "Пропущенные значения", "high" if p.missing / max(n, 1) >= .2 else "medium", "fact", [p.name], indices, n,
                    f"NULL: {p.nulls}; пустые/пробельные: {p.blanks}; текстовые маркеры: {p.markers}. Маркеры зависят от предметной области.",
                    "Проверьте обязательность поля и согласуйте представление отсутствующих значений."))
            if n and p.distinct == 1 and p.missing == 0:
                found.append(finding("constant", "Постоянный столбец", "info", "fact", [p.name], list(range(n)), n,
                    "У всех записей одно значение.", "Проверьте, ожидается ли такое распределение."))
        if n:
            indices = [i for i, duplicate in enumerate(df.is_duplicated().to_list()) if duplicate]
            if indices:
                extras = n - df.unique().height
                found.append(finding("duplicates", "Полные дубликаты строк", "high", "fact", df.columns, indices, n,
                    f"{len(indices)} строк входят в группы дубликатов; лишних копий: {extras}. Сравнение исходных значений, без нормализации.",
                    "Проверьте зерно таблицы перед удалением повторов."))
        eligible = {p.name for p in context.profiles if p.missing == 0 and not any(isinstance(v, float) and not math.isfinite(v) for v in context.values[p.name])}
        singles = {p.name for p in context.profiles if n >= 2 and p.name in eligible and p.distinct == n}
        for name in sorted(singles):
            found.append(finding("key", "Кандидат в ключ", "info", "heuristic", [name], list(range(n)), n,
                "Все значения уникальны и заполнены в анализируемом фрагменте. Это не гарантия бизнес-ключа.",
                "Подтвердите смысл и стабильность уникальности на других загрузках."))
        candidates = [p.name for p in context.profiles if p.name not in singles and p.name in eligible and p.distinct > 1][:context.options.max_key_columns]
        for a, b in combinations(candidates, 2):
            context.checkpoint()
            if n >= 2 and df.select(a, b).unique().height == n:
                found.append(finding("composite_key", "Составной кандидат в ключ", "info", "heuristic", [a, b], list(range(n)), n,
                    f"Пара столбцов уникальна; по отдельности столбцы не уникальны. Поиск ограничен первыми {context.options.max_key_columns} подходящими столбцами.",
                    "Подтвердите зерно таблицы и устойчивость пары."))
        return found
