"""Console-free entry point with a persistent log and visible failure message."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import os
from pathlib import Path
import tempfile
import traceback


def open_startup_log():
    roots = [Path(os.environ.get("LOCALAPPDATA", Path.home())), Path(tempfile.gettempdir())]
    for root in roots:
        path = root / "DataXRay" / "logs" / "startup.log"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return path, path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            continue
    raise OSError("Не удалось создать журнал запуска в LOCALAPPDATA или временной папке.")


def show_failure(message):
    # This must also work when Qt itself cannot be imported.
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, message, "Data X-Ray — ошибка запуска", 0x10)


def main(argv=None):
    log_path = None
    try:
        log_path, log = open_startup_log()
        with log, redirect_stdout(log), redirect_stderr(log):
            print(f"\n=== Data X-Ray {datetime.now().isoformat(timespec='seconds')} ===")
            try:
                from .__main__ import main as application_main
                result = application_main(argv)
            except SystemExit as exc:
                result = exc.code if isinstance(exc.code, int) else 1
                if result:
                    traceback.print_exc()
            except Exception:
                traceback.print_exc()
                result = 1
        if not result:
            return 0
        message = f"Не удалось запустить Data X-Ray.\nПодробности в журнале:\n{log_path}"
    except Exception as exc:
        message = f"Не удалось запустить Data X-Ray:\n{exc}"
        if log_path:
            message += f"\nЖурнал: {log_path}"
    show_failure(message)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
