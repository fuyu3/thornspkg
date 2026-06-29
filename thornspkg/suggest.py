# * Análise estática de fontes para sugestão de dependências — ajuda o usuário a descobrir
# * o que pode estar faltando no campo `depends` da receita antes de compilar.
# * Analisa: configure.ac (PKG_CHECK_MODULES, AC_CHECK_LIB), meson.build (dependency),
# *   CMakeLists.txt (find_package, pkg_check_modules), configure script (pkg-config).
# * Retorna (required, optional) — listas de DepSuggestion com nome, se é obrigatório,
# *   e o arquivo de origem. Nunca modifica receitas automaticamente.
# * Função principal: analyze_source(src_dir) → (required, optional).
# * Arquivo: thornspkg/suggest.py

"""Análise estática de fontes para sugestão de dependências.

Objetivo: dar dicas ao usuário sobre o que um pacote precisa antes que ele
tente compilar e receba erros de "library not found". Não tenta ser um
resolvedor completo — é propositalmente simples e conservador.

Arquivos analisados (em ordem de prioridade):
  configure.ac   — macros autoconf (PKG_CHECK_MODULES, AC_CHECK_LIB, …)
  meson.build    — chamadas dependency() e find_library()
  CMakeLists.txt — find_package() e pkg_check_modules()
  configure      — script gerado (apenas PKG_CHECK / pkg-config, primeiros 100 KB)

Cada sugestão tem um nível:
  required  — o build system indica que é obrigatória (ex: REQUIRED)
  optional  — o build system aceita que não esteja presente

LIMITAÇÕES (intencionais):
  - regex simples, sem parser completo de nenhuma linguagem
  - não cruza sugestões com receitas disponíveis
  - não modifica receitas automaticamente
  - falsos positivos são esperados; o usuário revisa antes de usar
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DepSuggestion:
    name:     str
    required: bool
    origin:   str   # nome do arquivo de onde veio


# Nomes que aparecem frequentemente mas não são dependências externas
_NOISE = frozenset({
    "c", "c++", "cxx", "cc", "m", "dl", "pthread", "rt",
    "threads", "check", "test", "tests",
    "host", "target", "build", "all", "install", "clean",
    "config", "version", "enable", "disable", "with", "without",
    "subdir", "subdirs", "prefix", "yes", "no", "true", "false",
})


def _is_noise(name: str) -> bool:
    return not name or len(name) <= 1 or name.lower() in _NOISE


def _dedup(
    items: list[DepSuggestion],
    exclude_names: set[str] | None = None,
) -> list[DepSuggestion]:
    excl = {n.lower() for n in (exclude_names or set())}
    seen: set[str] = set()
    result = []
    for s in items:
        key = s.name.lower()
        if key in seen or key in excl:
            continue
        seen.add(key)
        result.append(s)
    return sorted(result, key=lambda s: s.name.lower())


# ---------------------------------------------------------------------------
# ponto de entrada
# ---------------------------------------------------------------------------

def analyze_source(src_dir: Path) -> tuple[list[DepSuggestion], list[DepSuggestion]]:
    """Analisa src_dir e retorna (required, optional).

    Percorre cada arquivo de build suportado e coleta sugestões.
    Deduplicação: se um nome aparece como required em qualquer arquivo,
    ele não aparece na lista optional.
    """
    required: list[DepSuggestion] = []
    optional: list[DepSuggestion] = []

    _parsers = [
        ("configure.ac",   _parse_configure_ac),
        ("meson.build",    _parse_meson_build),
        ("CMakeLists.txt", _parse_cmake),
        ("configure",      _parse_configure_script),
    ]

    for filename, fn in _parsers:
        fpath = src_dir / filename
        if not fpath.is_file():
            continue
        try:
            req, opt = fn(fpath)
            required.extend(req)
            optional.extend(opt)
        except (OSError, UnicodeDecodeError):
            pass

    # Nomes já em required não aparecem em optional
    req_names = {s.name.lower() for s in required}
    required = _dedup(required)
    optional = _dedup(optional, exclude_names=req_names)

    return required, optional


# ---------------------------------------------------------------------------
# parsers por tipo de build system
# ---------------------------------------------------------------------------

def _parse_configure_ac(path: Path) -> tuple[list[DepSuggestion], list[DepSuggestion]]:
    """Analisa configure.ac / configure.in (autoconf)."""
    text = path.read_text(errors="replace")
    required, optional = [], []

    # PKG_CHECK_MODULES([VAR], [pkg >= ver], [FOUND-ACTION], [NOTFOUND-ACTION])
    # Se tem 4ª arg (NOTFOUND-ACTION), o projeto sobrevive sem ela → optional.
    for m in re.finditer(
        r"PKG_CHECK_MODULES\s*\(\s*\[?[\w]+\]?\s*,\s*\[?([^\],\)]+)\]?",
        text, re.IGNORECASE | re.DOTALL,
    ):
        pkg_spec = m.group(1).strip()
        # Conta argumentos totais da macro para distinguir required/optional
        after = text[m.start():]
        depth = args = 0
        for ch in after:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            elif ch == "," and depth == 1:
                args += 1
        is_optional = args >= 3   # 4 args = NOT_FOUND action presente

        for token in re.split(r"[\s,]+", pkg_spec):
            name = re.sub(r"[<>=].*", "", token).strip()
            # ignora tokens vazios, operadores e números de versão
            if not name or not name[0].isalpha() or _is_noise(name):
                continue
            s = DepSuggestion(name=name, required=not is_optional, origin="configure.ac")
            (optional if is_optional else required).append(s)

    # PKG_CHECK_EXISTS([pkg]) — geralmente guarda feature opcional
    for m in re.finditer(
        r"PKG_CHECK_EXISTS\s*\(\s*\[?([^\],\)]+)\]?", text, re.IGNORECASE,
    ):
        for token in re.split(r"[\s,]+", m.group(1)):
            name = re.sub(r"[<>=].*", "", token).strip()
            if not _is_noise(name):
                optional.append(DepSuggestion(name=name, required=False, origin="configure.ac"))

    # AC_CHECK_LIB([libname], [function])
    for m in re.finditer(r"AC_CHECK_LIB\s*\(\s*\[?([A-Za-z][A-Za-z0-9_\-]*)", text):
        name = m.group(1)
        if not _is_noise(name):
            optional.append(DepSuggestion(name=f"lib{name}", required=False, origin="configure.ac"))

    # AC_CHECK_HEADER(S)([header.h]) — extrai nome base sem extensão
    for m in re.finditer(
        r"AC_CHECK_HEADERS?\s*\(\s*\[?([A-Za-z][A-Za-z0-9_/\.\-]*\.h)", text,
    ):
        stem = Path(m.group(1)).stem
        if not _is_noise(stem):
            optional.append(DepSuggestion(name=stem, required=False, origin="configure.ac"))

    return required, optional


def _parse_meson_build(path: Path) -> tuple[list[DepSuggestion], list[DepSuggestion]]:
    """Analisa meson.build."""
    text = path.read_text(errors="replace")
    required, optional = [], []

    # dependency('name', required: true/false, ...)
    for m in re.finditer(r"dependency\s*\(\s*['\"]([^'\"]+)['\"]([^)]*)\)", text):
        name = m.group(1).strip()
        rest = m.group(2)
        if _is_noise(name):
            continue
        is_opt = bool(re.search(r"required\s*:\s*false", rest, re.IGNORECASE))
        s = DepSuggestion(name=name, required=not is_opt, origin="meson.build")
        (optional if is_opt else required).append(s)

    # find_library('name', required: ...)
    for m in re.finditer(r"find_library\s*\(\s*['\"]([^'\"]+)['\"]([^)]*)\)", text):
        name = m.group(1).strip()
        rest = m.group(2)
        if _is_noise(name):
            continue
        is_opt = bool(re.search(r"required\s*:\s*false", rest, re.IGNORECASE))
        s = DepSuggestion(name=f"lib{name}", required=not is_opt, origin="meson.build")
        (optional if is_opt else required).append(s)

    return required, optional


def _parse_cmake(path: Path) -> tuple[list[DepSuggestion], list[DepSuggestion]]:
    """Analisa CMakeLists.txt."""
    text = path.read_text(errors="replace")
    required, optional = [], []

    # find_package(Name [REQUIRED] ...)
    for m in re.finditer(
        r"find_package\s*\(\s*([A-Za-z][A-Za-z0-9_\-]*)([^)]*)\)", text, re.IGNORECASE,
    ):
        name = m.group(1).strip()
        rest = m.group(2).upper()
        if _is_noise(name):
            continue
        is_req = "REQUIRED" in rest
        s = DepSuggestion(name=name, required=is_req, origin="CMakeLists.txt")
        (required if is_req else optional).append(s)

    # pkg_check_modules(VAR [REQUIRED] pkg ...)
    for m in re.finditer(
        r"pkg_check_modules\s*\(\s*\w+\s*([^)]+)\)", text, re.IGNORECASE,
    ):
        args = m.group(1)
        is_req = bool(re.search(r"\bREQUIRED\b", args, re.IGNORECASE))
        _skip = {"REQUIRED", "QUIET", "IMPORTED_TARGET", "GLOBAL", "NO_CMAKE_PATH"}
        for token in re.split(r"\s+", args):
            clean = re.sub(r"[<>=].*", "", token).strip()
            if not clean or not clean[0].isalpha() or clean.upper() in _skip or _is_noise(clean):
                continue
            if re.match(r"[A-Za-z][A-Za-z0-9_\-\.\+]*$", clean):
                s = DepSuggestion(name=clean, required=is_req, origin="CMakeLists.txt")
                (required if is_req else optional).append(s)

    return required, optional


def _parse_configure_script(path: Path) -> tuple[list[DepSuggestion], list[DepSuggestion]]:
    """Analisa o script `configure` gerado pelo autoconf.

    Lê apenas os primeiros 100 KB (o arquivo pode ser enorme) e busca apenas
    chamadas ao pkg-config — o padrão mais confiável num script gerado.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(100 * 1024)
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        return [], []

    # Verifica se parece um script autoconf gerado (evita analisar Makefiles etc.)
    if "GNU Autoconf" not in text and "generated by GNU Autoconf" not in text:
        return [], []

    optional = []

    # pkg-config --exists NOME  ou  $PKG_CONFIG --exists NOME
    for m in re.finditer(
        r"(?:pkg.config|\$PKG_CONFIG)\s+--(?:exists|cflags|libs)\s+([A-Za-z][A-Za-z0-9_\-\.]*)",
        text,
    ):
        name = m.group(1).strip()
        if not _is_noise(name):
            optional.append(DepSuggestion(name=name, required=False, origin="configure"))

    return [], optional
