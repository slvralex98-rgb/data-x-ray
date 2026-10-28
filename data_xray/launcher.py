"""Windows launcher: retain console semantics for reports, detach the desktop."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from .__main__ import build_parser, main as application_main


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Use the application's parser, including --help, errors and abbreviations.
    args = build_parser().parse_args(argv)
    if (sys.platform != "win32" or args.html is not None or args.json is not None
            or any(getattr(args, flag, None) for flag in ("mine_rules", "rule_set", "criteria", "save_passport", "compare_passport"))):
        return application_main(argv)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    try:
        subprocess.Popen(
            [str(pythonw), "-m", "data_xray.gui_entry", *argv],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except OSError as exc:
        message = f"Не удалось запустить Data X-Ray: {exc}"
        print(message, file=sys.stderr)
        from .gui_entry import show_failure
        show_failure(message)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
