from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import math
import re
import statistics
import unicodedata

from . import Context, Finding, finding
from ..semantics import date_value, numeric, shape, DATE_SHAPE


class ContentDetector:
    def run(self, context: Context) -> list[Finding]:
        result = []
        n = context.dataset.frame.height
        for p in context.profiles:
            context.checkpoint()
            values = context.values[p.name]
            valid = [(i, v) for i, v in enumerate(values) if not context.missing[p.name][i]]

            def add(code, title, indices, description, recommendation, severity="medium", confidence="heuristic"):
                if indices:
                    result.append(finding(code, title, severity, confidence, [p.name], indices, n, description, recommendation))

            nonfinite = [i for i, v in valid if str(v).strip().casefold() in ("inf", "-inf", "+inf", "infinity", "-infinity") or isinstance(v, float) and not math.isfinite(v)]
            add("nonfinite", "Не конечное числовое значение", nonfinite, "Найдены NaN или бесконечности, не пригодные для обычной арифметики.", "Проверьте деление на ноль и преобразования типов.", "high", "fact")
            if p.inferred_type == "number":
                parsed = [(i, numeric(v)) for i, v in valid]
                invalid = [i for i, v in parsed if v is None and i not in nonfinite]
                add("type", "Отклонение от числового типа", invalid,
                    "Не менее 90% заполненных значений похожи на числа; указанные значения не удалось разобрать. Допускается точка или запятая как десятичный знак, без разделителя тысяч.",
                    "Проверьте формат, единицы и исходные значения.", "high")
                numbers = [(i, v) for i, v in parsed if v is not None]
                if len(numbers) >= 8:
                    xs = [v for _, v in numbers]
                    q1, _, q3 = statistics.quantiles(xs, n=4, method="inclusive")
                    iqr = q3 - q1
                    if iqr > 0:
                        low, high = q1 - context.options.iqr_multiplier * iqr, q3 + context.options.iqr_multiplier * iqr
                        indices = [i for i, v in numbers if v < low or v > high]
                        add("outlier", "Числовые выбросы · IQR", indices,
                            f"Значения вне [{low:.6g}; {high:.6g}], границы Q1/Q3 ± {context.options.iqr_multiplier} × IQR. Выброс может быть корректным.",
                            "Сопоставьте с бизнес-диапазоном; не удаляйте автоматически.")
                    elif len(set(xs)) > 1 and Counter(xs).most_common(1)[0][1] / len(xs) >= .9:
                        mode = Counter(xs).most_common(1)[0][0]
                        add("numeric_rare", "Отклонение от постоянного уровня", [i for i, v in numbers if v != mode],
                            "IQR равен нулю; не менее 90% значений совпадают. Показаны остальные значения.", "Проверьте, допустимы ли редкие значения.", "low")
            if p.inferred_type == "date":
                parsed = [(i, date_value(v)) for i, v in valid]
                add("date_invalid", "Некорректная или несогласованная дата", [i for i, v in parsed if v is None],
                    "Большинство значений распознается как даты. Поддерживаются ISO, YYYY/MM/DD, DD.MM.YYYY и DD/MM/YYYY; последняя форма трактуется день/месяц.",
                    "Проверьте календарную корректность и формат.", "high")
                add("date_future", "Даты в будущем", [i for i, v in parsed if v and v.date() > date.today()],
                    f"Дата позже локальной даты анализа {date.today().isoformat()}. Будущие даты могут быть ожидаемыми.", "Проверьте смысл поля и часовой пояс.", "low")
                formats = defaultdict(list)
                for i, v in valid:
                    s = str(v).strip()
                    if date_value(v):
                        fmt = "ISO" if re.match(r"^\d{4}-", s) else "Y/M/D" if re.match(r"^\d{4}/", s) else "D.M.Y" if "." in s[:10] else "D/M/Y"
                        formats[fmt].append(i)
                if len(formats) > 1:
                    dominant = max(formats, key=lambda k: len(formats[k]))
                    add("date_format", "Смешанные форматы дат", [i for fmt, ids in formats.items() if fmt != dominant for i in ids],
                        f"Найдены форматы: {', '.join(formats)}. Показаны отличающиеся от наиболее частого.", "Унифицируйте формат при сохранении.", "low", "fact")
            elif len(valid) >= 5:
                # Catch date-shaped invalid values even if too many are broken for type inference.
                shaped = [(i, v) for i, v in valid if DATE_SHAPE.fullmatch(str(v).strip())]
                if len(shaped) / len(valid) >= context.options.dominance:
                    add("date_invalid", "Нераспознаваемые календарные даты", [i for i, v in shaped if date_value(v) is None],
                        "Значения имеют форму даты, но не проходят календарный разбор.", "Проверьте день, месяц и порядок компонентов.", "high")
            strings = [(i, str(v)) for i, v in valid if isinstance(v, str)]
            add("whitespace", "Пробелы по краям", [i for i, s in strings if s != s.strip()],
                "Внешние пробелы могут менять результат соединений и группировок.", "Согласуйте безопасную нормализацию пробелов.", "low", "fact")
            add("control", "Невидимые управляющие символы", [i for i, s in strings if any(unicodedata.category(c) in ("Cc", "Cf") for c in s)],
                "Есть управляющие символы, переносы или символы нулевой ширины. Переносы в тексте могут быть допустимы.", "Проверьте исходные строки и правила очистки.", "low", "fact")
            groups = defaultdict(set)
            for _, s in strings:
                groups[unicodedata.normalize("NFKC", s).strip().casefold()].add(s)
            variants = {key for key, spellings in groups.items() if len(spellings) > 1}
            add("variants", "Варианты написания одного значения", [i for i, s in strings if unicodedata.normalize("NFKC", s).strip().casefold() in variants],
                "Значения совпадают после NFKC, удаления внешних пробелов и приведения регистра.", "Проверьте, можно ли объединить эти категории.", "low")
            if p.inferred_type == "string" and len(strings) >= 10:
                patterns = Counter(shape(s) for _, s in strings)
                dominant, count = patterns.most_common(1)[0]
                if count / len(strings) >= .9 and len(patterns) > 1:
                    add("string_pattern", "Редкий шаблон строки", [i for i, s in strings if shape(s) != dominant],
                        f"Не менее 90% строк следуют шаблону {dominant!r} (A — буквы, 9 — цифры; повторы сжаты).", "Проверьте формат идентификаторов и категорий.", "low")
                lengths = [len(s) for _, s in strings]
                q1, _, q3 = statistics.quantiles(lengths, n=4, method="inclusive")
                limit = q3 + 3 * (q3 - q1)
                if limit > 0:
                    add("string_length", "Необычно длинные строки", [i for i, s in strings if len(s) > limit and len(s) > 2 * max(1, statistics.median(lengths))],
                        f"Длина больше Q3 + 3 × IQR ({limit:.0f}) и удвоенной медианы.", "Проверьте склейку полей и лишний текст.", "low")
        return result
