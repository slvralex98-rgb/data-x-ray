"""Self-contained, escaped HTML. No network requests or embedded executable data."""
from __future__ import annotations

from html import escape
from pathlib import Path

from .models import AnalysisResult, Finding

SEVERITY = {"high": "Высокий", "medium": "Средний", "low": "Низкий", "info": "Информация"}
TYPES = {"number": "Число", "date": "Дата", "string": "Строка", "boolean": "Логический", "empty": "Пустой"}


def e(value, limit=600):
    s = str(value) if value is not None else "∅ NULL"
    return escape(s[:limit] + ("…" if len(s) > limit else ""), quote=True)


def finding_html(f: Finding) -> str:
    fields = " · ".join(e(c) for c in f.columns)
    examples = "".join("<tr><td>" + str(row["row"]) + "</td>" + "".join("<td>" + e(row["values"].get(c)) + "</td>" for c in f.columns) + "</tr>" for row in f.examples)
    return f'''<article class="finding {f.severity}">
    <div class="eyebrow">{SEVERITY[f.severity]} · Приоритет {f.score} · {"Наблюдение" if f.confidence == "fact" else "Гипотеза"}</div>
    <h2>{e(f.title)}</h2><p class="muted">{fields}</p>
    <p><strong>{f.count:,} / {f.total:,} записей</strong> · {f.count / max(1, f.total):.1%}</p>
    <p>{e(f.description, 4000)}</p><p><strong>Следующий шаг:</strong> {e(f.recommendation, 2000)}</p>
    <div class="scroll"><table><thead><tr><th>Запись №</th>{''.join('<th>' + e(c) + '</th>' for c in f.columns)}</tr></thead><tbody>{examples}</tbody></table></div>
    <p class="muted">Примеры: первые {len(f.examples)} затронутых записей. Длинные значения сокращены до 600 символов.</p></article>'''


STYLE = '''
:root{color-scheme:dark}*{box-sizing:border-box}body{font:15px/1.65 "Segoe UI",Arial,sans-serif;color:#e8edf4;background:#10141b;margin:0}main{max-width:1280px;margin:40px auto;padding:0 28px}h1{font-size:36px;letter-spacing:-1px;margin:4px 0;color:#fff2e7}h2{font-size:21px;margin:8px 0;color:#fff2e7}h3{font-size:23px;margin-top:35px;color:#ffb776}.eyebrow{color:#ffb776;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px}.muted{color:#a6b1c2}.cards{display:flex;gap:16px;flex-wrap:wrap;margin:26px 0}.card{flex:1;min-width:160px;padding:18px 24px;background:#1b222d;border:1px solid #cf7037;border-radius:14px;box-shadow:0 0 18px #f28c4510}.card b{font-size:30px;display:block;color:#ffb06d}.finding{background:#181e27;border:1px solid #b96230;border-left:5px solid #a6b1c2;padding:22px 26px;margin:18px 0;border-radius:12px}.finding.high{border-left-color:#ffabb3}.finding.medium{border-left-color:#ffd08a}.finding.low{border-left-color:#a4c8ff}.finding.info{border-left-color:#c4b9f5}.scroll{overflow:auto;border-radius:6px;scrollbar-color:#6a788d #10161f;scrollbar-width:thin}table{border-collapse:collapse;width:100%;background:#181e27}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #34404e;padding:10px 14px;max-width:400px;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#252e3b;color:#cbd5e2}tbody tr:nth-child(even){background:#1e2632}a{color:#ffb776;text-underline-offset:4px}a:hover{color:#ffd2ac}a:focus-visible{outline:2px solid #ffad68;outline-offset:4px}nav{display:flex;gap:20px;flex-wrap:wrap}@media print{:root{color-scheme:light}body{background:white;color:#172033}main{margin:0}h1,h2,h3,.eyebrow,.muted,.card b{color:#172033}.card,.finding,table,th,tbody tr:nth-child(even){background:white;color:#172033;border-color:#8a6b53}.finding{break-inside:avoid}nav{display:none}}'''



RULE_STATES = {"candidate": "Кандидат", "accepted": "Принято", "rejected": "Отклонено"}


def rule_parameters(rule):
    p = rule.params
    if rule.kind == "unique":
        return "Уникальность заполненных " + ("значений" if len(rule.columns) == 1 else "комбинаций полей") + ": " + ", ".join(e(c) for c in rule.columns) + ". Все участники повтора считаются нарушениями. Строки с пропусками проверяются отдельно."
    if rule.kind == "not_null":
        return "Обязательное заполнение; NULL, пустота и маркеры считаются пропусками."
    if rule.kind == "allowed_values":
        return "Допустимые значения: " + ", ".join(e(v) for v in p["values"])
    if rule.kind == "robust_range":
        return f"Границы включительно: от {e(p['lower'])} до {e(p['upper'])}"
    if rule.kind == "numeric_constraint":
        parts = []
        if p["lower"] is not None:
            parts.append(("≥ " if p["lower_inclusive"] else "> ") + e(p["lower"]))
        if p["upper"] is not None:
            parts.append(("≤ " if p["upper_inclusive"] else "< ") + e(p["upper"]))
        return "Число: " + " и ".join(parts) + ". Пропуски проверяются отдельным условием."
    if rule.kind == "date_format":
        return "Календарная дата, точный формат: " + e(p["format"])
    if rule.kind == "regex":
        return "Полное совпадение с регулярным выражением: <code>" + e(p["pattern"], 1024) + "</code>"
    if rule.kind == "string_shape":
        formats = {"email": "email", "uuid": "UUID", "iso_date": "календарная дата ГГГГ-ММ-ДД", "digits": "цифры 0–9", "shape": "шаблон букв и цифр"}
        return "Формат: " + formats[p["format"]] + (" · " + e(p["shape"]) if "shape" in p else "")
    if rule.kind == "functional_dependency":
        return "Карта «" + e(rule.columns[0]) + "» → «" + e(rule.columns[1]) + "»: " + "; ".join(e(k) + " → " + e(v) for k, v in p["mapping"].items())
    if rule.kind == "conditional_required":
        return "Условие: «" + e(rule.columns[0]) + "» = «" + e(p["value"]) + "»; тогда «" + e(rule.columns[1]) + "» обязательно."
    return "Сравнение: " + e(p["operator"]) + " · значения: " + ("даты" if p["type"] == "date" else "числа")


def rule_html(rule, evaluation=None):
    """All imported values remain escaped data, never rich text."""
    head = f'<article class="finding"><div class="eyebrow">{RULE_STATES[rule.review_state]} · Условие</div>'
    description = f'<h2>{e(rule.description, 4000)}</h2><p>{rule_parameters(rule)}</p>'
    if evaluation is None:
        return head + '<p><strong>Не проверено — нужна проверка на текущем датасете.</strong></p>' + description + '</article>'
    ev = evaluation
    status = {"pass": "Условие выполнено", "fail": "Есть нарушения", "not_applicable": "Не применимо"}[ev.status]
    ratio = f"{ev.confidence:.2%}" if ev.evaluated and ev.confidence is not None else "Не применимо"
    reasons = {"rule_violation": "Условие нарушено", "missing": "Поле не заполнено", "missing_required": "Обязательное поле не заполнено", "unseen_determinant": "Неизвестное значение определителя", "invalid_number": "Не удалось разобрать число", "invalid_date": "Не удалось разобрать дату"}
    reasons.update(duplicate_value="Значение повторяется", duplicate_combination="Комбинация значений повторяется")
    examples = ''.join('<tr><td>' + e(ex['row']) + '</td><td>' + '<br>'.join(e(k) + ': ' + e(v) for k, v in ex['values'].items()) + '</td><td>' + e(reasons.get(ex['reason'], ex['reason']), 1000) + '</td></tr>' for ex in ev.examples)
    review_hint = {"candidate": "Проверьте смысл правила, прежде чем принять его.", "accepted": "Правило принято пользователем для повторных проверок.", "rejected": "Правило отклонено пользователем; в сохраняемый набор не входит."}[rule.review_state]
    evidence_labels = {"invalid_rows": "Неразбираемые значения", "unseen_determinants": "Неизвестные определители", "rare_values_excluded": "Исключено редких категорий", "missing_rows": "Пропущенные операнды", "condition_not_met": "Условие не сработало"}
    evidence = " · ".join(label + ": " + e(ev.evidence[key]) for key, label in evidence_labels.items() if key in ev.evidence)
    return head + f'''<p><strong>{status}</strong> · Нарушения: {ev.violations} · Применимые: {ev.evaluated} · Пропущено: {ev.skipped}</p>
    <p>Доля выполнения в применимых записях: <strong>{ratio}</strong></p>
    {description}<p>Всего: {ev.total_rows} · Выполнено: {ev.passed} · {e(ev.message, 2000)}</p>
    <div class="scroll"><table><tr><th>Запись №</th><th>Исходные значения</th><th>Причина</th></tr>{examples}</table></div>
    <p class="muted">Первые {len(ev.examples)} примеров нарушений. Номера записей с 1 после заголовка; они могут отличаться от физических строк файла.</p>
    <p>Доля — наблюдаемая частота выполнения. {review_hint} Автоматически найденные связи требуют проверки смысла.</p>
    <p>Доказательства: {evidence}</p></article>'''


def rules_html(rule_result, rule_specs=None):
    evaluations = {ev.rule.id: ev for ev in rule_result.evaluations} if rule_result else {}
    rules = rule_result.rules if rule_specs is None and rule_result else rule_specs or []
    notes = rule_result.notes if rule_result else ["Набор правил еще не проверен на данных этого отчета; оценки и примеры отсутствуют."]
    return '<h3 id="rules">Поиск правил</h3><p>Автоматические правила — гипотезы по загруженному фрагменту. Пропущенные и неприменимые проверки не считаются успехом. Принятие — решение пользователя. По умолчанию поиск требует 20 применимых записей и долю 95%; бюджеты: 32 столбца, 120 пар, 100 правил. Диапазоны: Q1/Q3 ± 1.5 × IQR. Собственные правила проверяются по заданным пользователем условиям.</p>' + ''.join(rule_html(rule, evaluations.get(rule.id)) for rule in rules) + '<ul>' + ''.join('<li>' + e(n, 4000) + '</li>' for n in notes) + '</ul>'


def criteria_html(result=None, specs=None):
    evaluations = {ev.rule.id: ev for ev in result.evaluations} if result else {}
    rules = specs if specs is not None else result.rules if result else []
    return '<h3 id="criteria">Мои проверки</h3><p>Условия заданы пользователем. Заполненные неверные числа и даты считаются нарушениями. Для обязательности добавьте отдельное условие заполнения.</p>' + ''.join(rule_html(rule, evaluations.get(rule.id)) for rule in rules)


def render_report(result: AnalysisResult, rule_result=None, *, rule_specs=None, review=None, passport=None,
                  criteria_result=None, criteria_specs=None) -> str:
    # A mismatched evaluation must never lend evidence to an explicitly supplied set.
    if rule_result is not None and (rule_result.source != result.source or rule_result.rows != result.rows):
        rule_result = None
    if criteria_result is not None and (criteria_result.source != result.source or criteria_result.rows != result.rows):
        criteria_result = None
    review_valid = review is not None and review.matches(result)
    def reviewed_finding(f):
        if not review_valid:
            return finding_html(f)
        return review.detail_html(f) + finding_html(f)
    passport_section = ""
    if passport and passport.get("current"):
        from .passport import render_passport
        passport_section = '<h3>Паспорт таблицы</h3>' + render_passport(passport)
    missing = sum(p.missing for p in result.profiles)
    profile_rows = "".join(f"<tr><td>{e(p.name)}</td><td>{e(p.physical_type)}</td><td>{TYPES[p.inferred_type]}</td><td>{p.missing:,} ({p.missing / max(1, result.rows):.1%})</td><td>{p.distinct:,}</td><td>{p.unique_ratio:.1%}</td><td>{e(p.statistics, 1200)}</td><td>{e(p.top_values, 1200)}</td></tr>" for p in result.profiles)
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Data X-Ray · {e(Path(result.source).name)}</title><style>{STYLE}</style></head>
    <body><main><div class="eyebrow">Data X-Ray / Отчет</div><h1>Анализ данных</h1>
    <p>{e(result.source, 2000)} · {e(result.source_format.upper())} · {e(result.created_at)} · {result.elapsed_seconds:.2f} сек.</p>
    <nav><a href="#findings">Проблемы и подсказки</a><a href="#profiles">Профили столбцов</a><a href="#method">Методика</a></nav>
    <div class="cards"><div class="card">Записи<b>{result.rows:,}</b>{'Первые записи файла' if result.truncated else 'Весь датасет'}</div><div class="card">Столбцы<b>{result.columns}</b></div><div class="card">Пустые ячейки и маркеры<b>{missing:,}</b></div><div class="card">Проблемы и подсказки<b>{len(result.findings)}</b></div></div>
    <h3 id="findings">Проблемы и подсказки по приоритету</h3><p class="muted">Ошибки и наблюдения, которые стоит проверить. Кандидаты в ключи и постоянные значения — подсказки о структуре данных, они не обязательно указывают на ошибку. Сохраненные статусы и удаление относятся к обзору, а не к изменению данных.</p>{''.join(reviewed_finding(f) for f in result.findings) or '<p>Проблем и подсказок нет. Это не подтверждает отсутствие бизнес-ошибок.</p>'}
    <h3 id="profiles">Профили столбцов</h3><div class="scroll"><table><thead><tr><th>Столбец</th><th>Физический тип</th><th>Похож на</th><th>Пропуски</th><th>Различные заполненные</th><th>Уникальность заполненных</th><th>Статистика</th><th>Частые значения</th></tr></thead><tbody>{profile_rows}</tbody></table></div>
    <h3 id="method">Методика и ограничения</h3><ul>{''.join('<li>' + e(note, 4000) + '</li>' for note in result.notes)}</ul>
    <p>Пропуски: NULL, пустая/пробельная строка, текстовые маркеры {e(', '.join(result.options.null_tokens))}. Маркеры сопоставляются без учета регистра. Выбросы: Q1/Q3 ± {result.options.iqr_multiplier:g} × IQR. До {result.options.example_rows} примеров на результат; лимит чтения {result.options.max_rows:,} записей. Все проверки описательны. HTML содержит примеры исходных данных.</p>
    {rules_html(rule_result, rule_specs) if rule_result is not None or rule_specs else ''}
    {criteria_html(criteria_result, criteria_specs) if criteria_result is not None or criteria_specs else ''}
    {passport_section}
    </main></body></html>'''


def export_html(result: AnalysisResult, path: str | Path, rule_result=None, *, rule_specs=None, review=None, passport=None,
                criteria_result=None, criteria_specs=None) -> Path:
    path = Path(path)
    path.write_text(render_report(result, rule_result, rule_specs=rule_specs, review=review, passport=passport,
                                 criteria_result=criteria_result, criteria_specs=criteria_specs), encoding="utf-8")
    return path
