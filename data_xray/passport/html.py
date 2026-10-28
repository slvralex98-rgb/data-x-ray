"""Escaped fragments usable in Qt and in the standalone report."""
from html import escape
import json

from .core import METHOD

LABELS = {"physical_type": "Физический тип", "inferred_type": "Предполагаемый тип",
    "missing": "Пропуски", "nulls": "NULL", "blanks": "Пустые / пробельные",
    "markers": "Текстовые маркеры", "missing_ratio": "Доля пропусков",
    "distinct": "Различные заполненные", "unique_ratio": "Уникальность заполненных",
    "statistics.min": "Минимум", "statistics.max": "Максимум", "statistics.mean": "Среднее",
    "statistics.median": "Медиана", "statistics.parsed": "Разобрано значений",
    "statistics.min_length": "Минимальная длина", "statistics.max_length": "Максимальная длина",
    "sample_patterns": "Шаблоны по top-5", "top_values": "Частые значения"}
TYPES = {"number": "Число", "string": "Строка", "date": "Дата", "boolean": "Логический", "empty": "Пустой"}


def e(value, limit=4000):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False)
    value = "—" if value is None else str(value)
    return escape(value[:limit] + ("…" if len(value) > limit else ""), quote=True)


def _table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join('<th>' + e(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + str(c) + '</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def passport_html(passport):
    if passport is None:
        return "<h2>Паспорт таблицы</h2><p>Загрузите и проанализируйте источник. Паспорт появится после анализа.</p>"
    p = passport
    rows = []
    for c in p["columns"]:
        top = '<br>'.join(e(v["value"]) + ('… [сокращено]' if v["truncated"] else '') + ': ' + e(v["count"]) for v in c["top_values"])
        patterns = '<br>'.join(e(v["pattern"]) + ': ' + e(v["count"]) for v in c["sample_patterns"])
        rows.append([e(c["name"]), e(c["physical_type"]), e(TYPES.get(c["inferred_type"], c["inferred_type"])),
            f'{c["missing"]:,} ({c["missing_ratio"]:.1%})<br>NULL {c["nulls"]} / пустые {c["blanks"]} / маркеры {c["markers"]}',
            f'{c["distinct"]:,} ({c["unique_ratio"]:.1%})', e(c["statistics"]), top or '—', patterns or '—'])
    evidence = ''.join('<li><strong>' + e({'key': 'Кандидат в ключ', 'composite_key': 'Составной кандидат в ключ', 'duplicates': 'Полные дубликаты'}[f['code']]) + '</strong>: ' + e(', '.join(f['columns'])) + f' · {f["count"]:,} / {f["total"]:,}<br>' + e(f['description']) + '</li>' for f in p['evidence'])
    return '<h2>Паспорт таблицы</h2><p>' + e(p['source']) + f' · {p["rows"]:,} строк · {len(p["columns"])} столбцов · ' + ('Первые строки; источник усечен' if p['truncated'] else 'Источник прочитан полностью') + '</p><p>' + e(p['created_at']) + ' · ' + e(p['source_format']) + '</p>' + _table(['Столбец', 'Физический тип', 'Похож на', 'Пропуски', 'Различные / уникальность', 'Статистика', 'Частые значения', 'Шаблоны примеров'], rows) + '<h3>Ключи и дубликаты исходных значений</h3><ul>' + (evidence or '<li>Находок нет. Отсутствие кандидата не доказывает отсутствие бизнес-ключа.</li>') + '</ul><p>Ключи — кандидаты из анализа исходных значений, проверенные только на загруженном фрагменте. Дубликаты: число строк в группах повторов, включая первые экземпляры.</p><h3>Отпечатки SHA-256</h3><p>Схема: ' + e(p['fingerprints']['schema']) + '<br>Профиль: ' + e(p['fingerprints']['profile']) + '</p><p>' + e(METHOD) + '</p><h3>Параметры анализа</h3><p>' + e(p['options']) + '</p><ul>' + ''.join('<li>' + e(n) + '</li>' for n in p['notes']) + '</ul>'


def _format(value, metric="", delta=False):
    if value is None:
        return "—"
    if metric.endswith("ratio") and isinstance(value, (int, float)):
        return f'{value * 100:+.2f} п.п.' if delta else f'{value:.2%}'
    if isinstance(value, (int, float)) and delta:
        return f'{value:+g}'
    return e(value, 1200)


def _keys_text(keys):
    return '; '.join(' + '.join(e(name) for name in columns) for columns in keys) or 'кандидатов не найдено'


def _duplicates_text(findings):
    if not findings:
        return 'находки о дубликатах нет'
    return '<br>'.join(f'{f["count"]:,} / {f["total"]:,} строк входят в группы повторов. ' + e(f['description']) for f in findings)


def comparison_html(comparison):
    if comparison is None:
        return "<h2>Сравнение паспортов</h2><p>Откройте сохраненный эталон и нажмите «Сравнить».</p>"
    c = comparison
    rows = []
    distributions = []
    for change in c['changed']:
        for key, item in change['changes'].items():
            if key == 'top_values':
                distrows = [[e(v['value']), 'не в top-5' if v['baseline_share'] is None else f'{v["baseline_share"]:.2%}',
                    'не в top-5' if v['current_share'] is None else f'{v["current_share"]:.2%}',
                    'неизвестно' if v['delta'] is None else f'{v["delta"] * 100:+.2f} п.п.'] for v in item['values']]
                distributions.append('<h3>Частые значения: ' + e(change['column']) + '</h3><p>Покрытие top-5: ' + f'{item["baseline_covered_share"]:.1%} → {item["current_covered_share"]:.1%}' + '. Известная общая масса: ' + f'{item["known_shared_mass"]:.1%}' + ' — нижняя граница пересечения распределений по сохраненным значениям, не оценка сходства всей таблицы.</p>' + _table(['Значение', 'Эталон: доля', 'Текущий: доля', 'Дельта'], distrows))
            else:
                rows.append([e(change['column']), e(LABELS.get(key, key)), _format(item['baseline'], key),
                             _format(item['current'], key), (f'{item["delta_seconds"] / 86400:+g} дней' if 'delta_seconds' in item else _format(item.get('delta'), key, True))])
    before, after = c['rows']['baseline'], c['rows']['current']
    structural = '<p>Добавлены: ' + e(', '.join(c['added']) or 'нет') + '<br>Удалены: ' + e(', '.join(c['removed']) or 'нет') + '<br>Порядок общих столбцов: ' + ('изменился' if c['column_order_changed'] else 'сохранился') + '</p>'
    return '<h2>Сравнение: текущий паспорт с эталоном</h2><p>Эталон: ' + e(c['baseline_source']) + '<br>Текущий: ' + e(c['current_source']) + f'</p><p>Строки: {before:,} → {after:,} · дельта {c["rows"]["delta"]:+,}</p>' + structural + '<p>Отпечаток схемы: ' + ('совпадает' if c['same_schema_fingerprint'] else 'различается') + '. Отпечаток профиля: ' + ('совпадает' if c['same_profile_fingerprint'] else 'различается') + '.</p><ul>' + ''.join('<li>' + e(w) + '</li>' for w in c['warnings']) + '</ul>' + (_table(['Столбец', 'Метрика', 'Эталон', 'Текущий', 'Дельта'], rows) if rows else '<p>Изменений скалярных метрик общих столбцов не обнаружено.</p>') + ''.join(distributions) + '<h3>Кандидаты в ключи</h3><p>Эталон: ' + _keys_text(c['keys']['baseline']) + '<br>Текущий: ' + _keys_text(c['keys']['current']) + '</p><h3>Полные дубликаты</h3><p>Эталон: ' + _duplicates_text(c['duplicates']['baseline']) + '<br>Текущий: ' + _duplicates_text(c['duplicates']['current']) + '</p>'


def render_passport(current=None, baseline=None, comparison=None):
    """Accept either explicit arguments or PassportPanel.export_content() payload."""
    if isinstance(current, dict) and 'current' in current and 'format' not in current:
        content = current
        current, baseline, comparison = (content.get(k) for k in ('current', 'baseline', 'comparison'))
    return '<section id="passport">' + passport_html(current) + (comparison_html(comparison) if comparison else '') + ('<h3>Сохраненный эталон</h3><p>' + e(baseline['source']) + ' · ' + e(baseline['created_at']) + '</p>' if baseline else '') + '</section>'
