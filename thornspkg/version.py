# * Parser e comparador de versões semânticas com suporte a operadores.
# * Suporta formatos comuns: "3.12.4", "9.1", "2.40-1", "1:5.0-3", "3.5+r1".
# * Operadores suportados: >, >=, <, <=, =, !=, == (alias de =).
# * Funções principais: parse_version() → tuple comparável,
# *   compare(a, b) → -1/0/1, satisfies(provided, constraint) → bool,
# *   parse_constraint(spec) → (nome, operador, versão).
# * Arquivo: thornspkg/version.py

"""Parser e comparador de versões com suporte a operadores.

Suporta os operadores: >, >=, <, <=, =, !=, == (alias de =).

Formatos de versão aceitos (inspirados em dpkg/rpm):
  - 3.12.4
  - 9.1
  - 2.40-1            (com sufixo de release)
  - 1:5.0-3           (com epoch)
  - 3.5+r1            (com sufixo debian-like)
  - 1.2.3rc1          (com sufixo pré-release)

A comparação é forte o suficiente para os casos do LFS/BLFS, mas
NÃO é uma implementação completa do algoritmo deb/rpm — é um
comparador pragmático que cobre >95% dos casos reais.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# exceções
# ---------------------------------------------------------------------------

class VersionError(Exception):
    """Erro de parsing de versão ou constraint."""
    pass


# ---------------------------------------------------------------------------
# parsing de versão
# ---------------------------------------------------------------------------

# Ordem de "força" dos sufixos: alpha < beta < pre < rc < (release) < patch/r1
_SUFFIX_RANK: dict[str, int] = {
    "alpha": -4, "a": -4,
    "beta":  -3, "b":  -3,
    "pre":   -2, "c":  -2,
    "rc":    -1,
}

# Padrão que separa a parte numérica de sufixos alfanuméricos
_VERSION_RE = re.compile(
    r"""
    ^
    (?:(?P<epoch>\d+):)?               # epoch opcional (debian-style)
    (?P<numeric>\d+(?:\.\d+)*)         # parte numérica principal (obrigatória)
    (?P<suffix>[^\s]*)$                # sufixo opcional (alpha, rc1, +r1, -1)
    """,
    re.VERBOSE,
)


def _split_numeric(numeric: str) -> tuple[int, ...]:
    """Converte '3.12.4' → (3, 12, 4)."""
    return tuple(int(p) for p in numeric.split("."))


def _parse_suffix(suffix: str) -> tuple[int, list[tuple[int, str]]]:
    """Converte sufixo em um par (rank, tokens) para comparação.

    Sufixo vazio → rank máximo (release final).
    Sufixo pré-release (alpha/beta/pre/rc) → rank negativo.
    Sufixo de patch (+r1, -1) → rank positivo.

    Returns:
        (rank, tokens) onde tokens é uma lista ordenada de (numeric, str)
        usada para desempate.
    """
    if not suffix:
        return (0, [])

    # Sufixo que começa com + (debian-like: +r1, +deb1)
    if suffix.startswith("+"):
        rest = suffix[1:]
        return (1, _tokenize_alphanum(rest))

    # Sufixo que começa com - (release: -1, -2)
    if suffix.startswith("-"):
        rest = suffix[1:]
        # Se for puramente numérico, é um release number
        if rest.isdigit():
            return (2, [(int(rest), "")])
        return (2, _tokenize_alphanum(rest))

    # Sufixo colado: rc1, alpha2, beta, pre3
    m = re.match(r"^([a-zA-Z]+)(\d*)$", suffix)
    if m:
        name, num = m.group(1).lower(), m.group(2)
        rank = _SUFFIX_RANK.get(name)
        if rank is not None:
            num_val = int(num) if num else 0
            return (rank, [(num_val, name)])
        # Sufixo alfanumérico desconhecido: trata como patch fraco
        return (1, _tokenize_alphanum(suffix))

    # Caso geral: tokeniza alfanumérico
    return (1, _tokenize_alphanum(suffix))


def _tokenize_alphanum(s: str) -> list[tuple[int, str]]:
    """Divide string em tokens (numeric, string) alternados.

    'r1deb2' → [(1, 'r'), (2, 'deb')]
    '1'      → [(1, '')]
    """
    tokens: list[tuple[int, str]] = []
    for match in re.finditer(r"(\d*)([a-zA-Z]*)", s):
        num_str, alpha_str = match.group(1), match.group(2)
        if not num_str and not alpha_str:
            continue
        num = int(num_str) if num_str else 0
        tokens.append((num, alpha_str.lower()))
    return tokens or [(0, "")]


@dataclass(frozen=True)
class Version:
    """Versão parseada, comparável."""
    epoch: int
    numeric: tuple[int, ...]
    suffix_rank: int
    suffix_tokens: tuple[tuple[int, str], ...]
    raw: str

    @classmethod
    def parse(cls, raw: str) -> "Version":
        """Faz o parse de uma string de versão."""
        if not raw or not isinstance(raw, str):
            raise VersionError(f"versão inválida: {raw!r}")
        raw = raw.strip()
        m = _VERSION_RE.match(raw)
        if not m:
            raise VersionError(f"versão inválida: {raw!r}")

        epoch = int(m.group("epoch")) if m.group("epoch") else 0
        numeric = _split_numeric(m.group("numeric"))
        if not numeric:
            raise VersionError(f"versão sem parte numérica: {raw!r}")

        rank, tokens = _parse_suffix(m.group("suffix") or "")
        return cls(
            epoch=epoch,
            numeric=numeric,
            suffix_rank=rank,
            suffix_tokens=tuple(tokens),
            raw=raw,
        )

    def __str__(self) -> str:
        return self.raw

    def _comparison_key(self) -> tuple:
        """Chave normalizada usada para comparação e hash.

        Garante que versões semanticamente iguais (ex: 1.0 e 1.0.0)
        produzam a mesma chave (e portanto o mesmo hash).
        """
        # Normaliza numeric removendo zeros à direita
        normalized_numeric = list(self.numeric)
        while len(normalized_numeric) > 1 and normalized_numeric[-1] == 0:
            normalized_numeric.pop()
        return (self.epoch, tuple(normalized_numeric),
                self.suffix_rank, self.suffix_tokens)

    def __lt__(self, other: "Version") -> bool:
        return _compare(self, other) < 0

    def __le__(self, other: "Version") -> bool:
        return _compare(self, other) <= 0

    def __gt__(self, other: "Version") -> bool:
        return _compare(self, other) > 0

    def __ge__(self, other: "Version") -> bool:
        return _compare(self, other) >= 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return _compare(self, other) == 0

    def __hash__(self) -> int:
        return hash(self._comparison_key())


def _compare_tokens(
    a: tuple[tuple[int, str], ...],
    b: tuple[tuple[int, str], ...],
) -> int:
    """Compara duas listas de tokens alfanuméricos.

    Regras (estilo dpkg):
      - tokens numéricos comparam numericamente
      - tokens string comparam lexicamente, mas string vazia > qualquer string
        (release final > pré-release)
      - listas mais curtas são "menores" se forem prefixo da outra
    """
    for (na, sa), (nb, sb) in zip(a, b):
        if na != nb:
            return -1 if na < nb else 1
        # string vazia é "maior" (versão final sem sufixo)
        if sa == sb:
            continue
        if not sa:
            return 1
        if not sb:
            return -1
        return -1 if sa < sb else 1

    if len(a) == len(b):
        return 0
    return -1 if len(a) < len(b) else 1


def _compare(a: Version, b: Version) -> int:
    """Comparação completa: epoch → numeric → suffix_rank → suffix_tokens."""
    if a.epoch != b.epoch:
        return -1 if a.epoch < b.epoch else 1

    # Compara parte numérica (preenche com zeros para equalizar comprimento)
    na, nb = a.numeric, b.numeric
    max_len = max(len(na), len(nb))
    na_padded = na + (0,) * (max_len - len(na))
    nb_padded = nb + (0,) * (max_len - len(nb))
    if na_padded != nb_padded:
        return -1 if na_padded < nb_padded else 1

    if a.suffix_rank != b.suffix_rank:
        return -1 if a.suffix_rank < b.suffix_rank else 1

    return _compare_tokens(a.suffix_tokens, b.suffix_tokens)


def parse_version(raw: str) -> Version:
    """Atalho para Version.parse()."""
    return Version.parse(raw)


def compare(a: str | Version, b: str | Version) -> int:
    """Compara duas versões. Retorna -1, 0 ou 1.

    Aceita strings ou objetos Version.
    """
    va = a if isinstance(a, Version) else Version.parse(a)
    vb = b if isinstance(b, Version) else Version.parse(b)
    return _compare(va, vb)


# ---------------------------------------------------------------------------
# parsing de constraints (ex: "openssl>=3.0")
# ---------------------------------------------------------------------------

_CONSTRAINT_RE = re.compile(
    r"""
    ^
    (?P<name>[A-Za-z][A-Za-z0-9_\-\.\+]*)    # nome do pacote (greedy: para no 1º operador)
    \s*
    (?P<op>>=|<=|!=|==|>|<|=)?               # operador (opcional)
    \s*
    (?P<ver>[^<>=!\s]+)?                     # versão (opcional)
    $
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class Constraint:
    """Restrição de versão de uma dependência.

    Attributes:
        name:     nome do pacote (sempre presente)
        op:       operador (>, >=, <, <=, =, !=, ==) ou None
        version:  versão exigida pelo operador, ou None
    """
    name: str
    op: str | None
    version: str | None

    def __str__(self) -> str:
        if self.op and self.version:
            return f"{self.name}{self.op}{self.version}"
        return self.name


def parse_constraint(spec: str) -> Constraint:
    """Faz o parse de uma especificação de dependência.

    Exemplos:
        "openssl>=3.0"   → Constraint("openssl", ">=", "3.0")
        "python<3.15"    → Constraint("python", "<",  "3.15")
        "curl=8.9.1"     → Constraint("curl",   "=",  "8.9.1")
        "zlib"           → Constraint("zlib", None, None)
        "glibc >= 2.40"  → Constraint("glibc", ">=", "2.40")
    """
    if not spec or not isinstance(spec, str):
        raise VersionError(f"constraint inválida: {spec!r}")
    s = spec.strip()
    m = _CONSTRAINT_RE.match(s)
    if not m or not m.group("name"):
        raise VersionError(f"constraint inválida: {spec!r}")

    name = m.group("name")
    op = m.group("op")
    ver = m.group("ver")

    # Normaliza == → =
    if op == "==":
        op = "="

    # Se há versão mas não há operador, assume "="
    if ver and not op:
        op = "="
    # Se há operador mas não há versão, erro
    if op and not ver:
        raise VersionError(f"constraint com operador mas sem versão: {spec!r}")

    return Constraint(name=name, op=op, version=ver)


def split_dep(spec: str) -> tuple[str, str | None, str | None]:
    """Atalho: retorna (nome, op, versão) a partir de uma string de dependência."""
    c = parse_constraint(spec)
    return (c.name, c.op, c.version)


# ---------------------------------------------------------------------------
# verificação de satisfação
# ---------------------------------------------------------------------------

def _apply_op(op: str, actual: Version, required: Version) -> bool:
    if op == ">":
        return actual > required
    if op == ">=":
        return actual >= required
    if op == "<":
        return actual < required
    if op == "<=":
        return actual <= required
    if op in ("=", "=="):
        return actual == required
    if op == "!=":
        return actual != required
    raise VersionError(f"operador desconhecido: {op!r}")


def satisfies(provided_name: str, provided_version: str | None,
              constraint: str | Constraint) -> bool:
    """Verifica se (nome, versão) fornecidos satisfazem uma constraint.

    Exemplos:
        satisfies("openssl", "3.5", "openssl>=3.0")     → True
        satisfies("python", "3.15", "python<3.15")      → False
        satisfies("curl", "8.9.1", "curl=8.9.1")        → True
        satisfies("zlib", "1.3", "zlib")                → True (sem constraint)

    Se o nome não bater, retorna False imediatamente.
    """
    c = constraint if isinstance(constraint, Constraint) else parse_constraint(constraint)

    if provided_name != c.name:
        return False

    # Sem operador/versão → qualquer versão satisfaz
    if c.op is None or c.version is None:
        return True

    if provided_version is None:
        return False

    try:
        actual = Version.parse(provided_version)
        required = Version.parse(c.version)
    except VersionError:
        # Se não conseguimos parsear uma das versões, fazemos comparação de string
        # como fallback (preserva comportamento anterior).
        if c.op == "=":
            return provided_version == c.version
        if c.op == "!=":
            return provided_version != c.version
        return False

    return _apply_op(c.op, actual, required)


def dep_name(spec: str) -> str:
    """Extrai apenas o nome de uma especificação de dependência.

    "openssl>=3.0" → "openssl"
    "zlib"         → "zlib"
    """
    return parse_constraint(spec).name
