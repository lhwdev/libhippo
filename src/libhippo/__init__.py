"""LibHippo: Permanent Knowledge Library for LLM."""

import logging
import sys
from rich.console import Console, ConsoleRenderable
from rich.logging import RichHandler
from rich.text import Text

__version__ = "0.1.0"


class _HippoRichHandler(RichHandler):
    """RichHandler that parses embedded ANSI escape codes via Text.from_ansi."""

    def render_message(self, record: logging.LogRecord, message: str) -> ConsoleRenderable:
        if "\x1b[" in message:
            return Text.from_ansi(message)
        return super().render_message(record, message)


_libhippo_logger = logging.getLogger("libhippo")
if not _libhippo_logger.handlers:
    _console = Console(stderr=True)
    _rich_handler = _HippoRichHandler(
        console=_console,
        show_time=False,
        show_path=False,
        markup=False,
        rich_tracebacks=True,
    )
    _libhippo_logger.addHandler(_rich_handler)
    _libhippo_logger.setLevel(logging.INFO)
