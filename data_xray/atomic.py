"""Replace an export only after its complete contents reach a sibling file."""
import os
from pathlib import Path
import tempfile


def atomic_write_bytes(path: str | Path, payload: bytes) -> Path:
    path = Path(path)
    # A sibling keeps replacement on the same filesystem. Closing the handle
    # before replace also permits replacement on Windows.
    stream = tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix="." + path.name + ".", suffix=".tmp", delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
