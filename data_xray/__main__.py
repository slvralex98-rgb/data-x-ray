from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys


def build_parser():
    parser = argparse.ArgumentParser(description="Data X-Ray — локальное профилирование CSV, XLSX, Parquet")
    parser.add_argument("file", nargs="?", help="Файл датасета (без --html открывается GUI)")
    parser.add_argument("--html", type=Path, help="Анализ без GUI с экспортом HTML")
    parser.add_argument("--json", type=Path, help="Дополнительный JSON-отчет (вместе с --html)")
    parser.add_argument("--demo", action="store_true", help="Создать examples/demo.* и открыть демо")
    parser.add_argument("--max-rows", type=int, default=200000)
    parser.add_argument("--separator", help="Разделитель CSV")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--sheet", help="Имя листа XLSX")
    rules = parser.add_mutually_exclusive_group()
    rules.add_argument("--mine-rules", action="store_true", help="Поиск правил (требует --html)")
    rules.add_argument("--rule-set", type=Path, help="Проверить принятый JSON-набор (требует --html)")
    parser.add_argument("--criteria", type=Path, help="JSON-набор моих проверок для поиска проблем (требует --html)")
    parser.add_argument("--save-passport", type=Path, help="Сохранить паспорт текущей таблицы (требует --html)")
    parser.add_argument("--compare-passport", type=Path, help="Сравнить с сохраненным паспортом (требует --html)")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_rows < 1:
        parser.error("--max-rows должен быть положительным.")
    if (args.mine_rules or args.rule_set is not None or args.criteria or args.save_passport or args.compare_passport) and not args.html:
        parser.error("Параметры правил, проверок и паспорта используются вместе с --html; для GUI откройте нужный раздел приложения.")
    try:
        path = args.file
        if args.demo:
            from .demo import create_demo
            path = str(create_demo(Path.cwd() / "examples"))
        if args.html:
            if not path:
                parser.error("Для --html укажите файл или --demo.")
            from .engine import Analyzer
            from .loaders import load_dataset
            from .models import AnalysisOptions
            from .report import export_html
            dataset = load_dataset(path, max_rows=args.max_rows, separator=args.separator, encoding=args.encoding, sheet=args.sheet)
            result = Analyzer().analyze(dataset, AnalysisOptions(max_rows=args.max_rows))
            criteria_result = None
            if args.criteria:
                from .criteria import evaluate_criteria, evaluations_to_findings
                from .rules import load_rule_set
                criteria_result = evaluate_criteria(dataset, load_rule_set(args.criteria))
                result.findings.extend(evaluations_to_findings(criteria_result))
                result.findings.sort(key=lambda f: (-f.score, f.code, f.columns))
            passport = None
            if args.save_passport or args.compare_passport:
                from .passport import build_passport, save_passport, load_passport, compare_passports
                current = build_passport(dataset, result)
                baseline = load_passport(args.compare_passport) if args.compare_passport else None
                passport = {"current": current, "baseline": baseline,
                            "comparison": compare_passports(current, baseline) if baseline else None}
                if args.save_passport:
                    save_passport(current, args.save_passport)
            rule_result = None
            if args.mine_rules or args.rule_set is not None:
                from .rules import RuleMiner, evaluate_rules, load_rule_set
                rule_result = (RuleMiner().mine(dataset, profile_result=result) if args.mine_rules else
                               evaluate_rules(dataset, load_rule_set(args.rule_set)))
            export_html(result, args.html, rule_result, passport=passport, criteria_result=criteria_result)
            if args.json:
                payload = result.to_dict()
                if rule_result is not None:
                    payload["rule_miner"] = rule_result.to_dict()
                if criteria_result is not None:
                    payload["custom_criteria"] = criteria_result.to_dict()
                if passport is not None:
                    payload["table_passport"] = passport
                def safe(value):
                    if isinstance(value, float) and not math.isfinite(value):
                        return str(value)
                    if isinstance(value, dict):
                        return {key: safe(item) for key, item in value.items()}
                    if isinstance(value, (tuple, list)):
                        return [safe(item) for item in value]
                    return value
                extended = rule_result is not None or criteria_result is not None or passport is not None
                args.json.write_text(json.dumps(safe(payload) if extended else payload, ensure_ascii=False, indent=2, default=str, allow_nan=not extended), encoding="utf-8")
            print(f"{result.rows} rows, {result.columns} columns, {len(result.findings)} findings. HTML: {args.html.resolve()}")
            return 0
        if args.json:
            parser.error("--json используется вместе с --html.")
        from .ui import run_gui
        return run_gui(path, max_rows=args.max_rows, separator=args.separator, encoding=args.encoding, sheet=args.sheet)
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
