"""Small helpers every module shares."""
import re
import subprocess


class Fail(Exception):
    """A request the engine refuses; main() turns it into {"ok": false}."""


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    try:
        return subprocess.run(argv, **kw)
    except OSError as exc:  # binary missing: an empty answer, not a crash
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


_UNITS = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
          "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}


def parse_size(text: str) -> int:
    """'564.5 MB' -> 564500000, '109.9 MiB' -> 115238502. 0 if unparseable."""
    m = re.match(r"\s*([\d.,]+)\s*([A-Za-z]+)\s*$", text or "")
    if not m:
        return 0
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(value * _UNITS.get(m.group(2).upper(), 0))
