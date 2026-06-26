# * Módulo de saída colorida ANSI — zero dependências externas.
# * Fornece duas funções principais:
# *   c()  — colore texto para stdout (ex: c("OK", "green"), c("alerta", "bold+yellow"))
# *   ce() — mesma coisa, mas para stderr (verifica isatty de stderr)
# * Respeita a variável de ambiente NO_COLOR (https://no-color.org/).
# * Suporta combinação de estilos com "+": c("texto", "bold+green").
# * Arquivo: thornspkg/colors.py

"""Saída colorida (ANSI) sem dependências externas.

Suporta combinação de estilos: c("texto", "bold+green").
"""

import os
import sys

_CODES: dict[str, str] = {
    "reset": "0",
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "bright_red": "91",
    "bright_green": "92",
    "bright_yellow": "93",
    "bright_cyan": "96",
}


def _enabled() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, style: str) -> str:
    if not _enabled():
        return text
    codes = ";".join(_CODES[s.strip()] for s in style.split("+") if s.strip() in _CODES)
    return f"\033[{codes}m{text}\033[0m" if codes else text


def ce(text: str, style: str) -> str:
    """Igual a c(), mas para stderr (verifica isatty de stderr)."""
    if not sys.stderr.isatty() or os.environ.get("NO_COLOR"):
        return text
    codes = ";".join(_CODES[s.strip()] for s in style.split("+") if s.strip() in _CODES)
    return f"\033[{codes}m{text}\033[0m" if codes else text
