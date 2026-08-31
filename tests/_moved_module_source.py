"""Where a module's source actually lives — asked of the module, not assumed.

⚠️ Source-reading tests in this repository used to spell the path themselves
(``PROJECT_ROOT / 'src' / 'application' / 'common' / 'x.py'``). That was true
until 2026-08-31, when the shared lanes left the monorepo and became installed
packages: the code those tests check still exists and still holds, but not at the
path they name. A test that hardcodes a location asserts about a *tree*, not
about the code it means to check, and the two stopped being the same thing.

Asking the module keeps the assertion pointed at the code wherever it lives.
"""
from __future__ import annotations

import importlib
from pathlib import Path


class ModuleSourceUnavailable(RuntimeError):
    """The module is importable but has no readable source on disk."""


def moved_module_source(dotted: str) -> Path:
    """Path to ``dotted``'s source file.

    Raises rather than returning a guess: a source-reading test given the wrong
    file would assert about something else and pass or fail for the wrong reason.
    """
    module = importlib.import_module(dotted)
    origin = getattr(module, '__file__', None)
    if not origin:
        raise ModuleSourceUnavailable(
            f'{dotted} has no __file__ (namespace package or built-in), so its '
            'source cannot be read'
        )
    path = Path(origin)
    if not path.is_file():
        raise ModuleSourceUnavailable(f'{dotted} reports {path}, which is not a file')
    return path
