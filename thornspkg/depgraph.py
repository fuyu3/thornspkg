# * Resolução de ordem de build por ordenação topológica (DFS).
# * Resolve dependências transitivas, detecta ciclos (com caminho completo),
# *   suporta provides (pacotes virtuais: bash satisfaz "sh") e optional_deps.
# * Suporta operadores de versão: "openssl>=3.0", "python<3.15", "curl=8.9.1".
# * Verifica versões instaladas contra constraints quando `installed_versions` é passado.
# * Funções principais: resolve_order() → lista linearizada de instalação,
# *   reverse_deps() → quem depende de um pacote,
# *   dep_tree_lines() → árvore ASCII para o comando `thorn tree`.
# * Arquivo: thornspkg/depgraph.py

"""Resolução de ordem de build por ordenação topológica (DFS).

Suporta:
  - dependências transitivas
  - deduplicação (cada pacote aparece só uma vez)
  - detecção de ciclos (com o caminho completo)
  - dependências faltando com hint de quem pediu
  - provides (pacotes virtuais: bash satisfaz "sh")
  - optional_deps (instaladas só se já estiverem no sistema)
  - operadores de versão: "openssl>=3.0", "python<3.15", "curl=8.9.1"
  - verificação de versões instaladas contra constraints
"""

from __future__ import annotations

from .colors import c
from .recipe import Recipe
from .version import (
    Constraint,
    VersionError,
    dep_name,
    parse_constraint,
    satisfies,
)


class DependencyCycleError(Exception):
    pass


class MissingDependencyError(Exception):
    pass


class VersionConflictError(Exception):
    """Levantada quando uma versão instalada não satisfaz uma constraint."""
    pass


def resolve_order(
    recipes: dict[str, Recipe],
    targets: list[str],
    provides_map: dict[str, str] | None = None,
    installed: set[str] | None = None,
    installed_versions: dict[str, str] | None = None,
) -> list[str]:
    """Retorna a ordem de instalação linearizada para `targets` e todas as
    suas dependências transitivas (sem duplicatas, todas as deps antes do
    pacote que depende delas).

    Args:
        recipes:             dicionário de receitas conhecidas
        targets:             lista de pacotes a instalar (com ou sem constraint)
        provides_map:        {nome_virtual: nome_real} para resolução de provides
        installed:           conjunto de pacotes já no sistema — usado para
                             incluir optional_deps que já estão instaladas
        installed_versions:  {nome: versão} dos pacotes instalados — usado para
                             verificar se versões instaladas satisfazem as
                             constraints das novas receitas

    Raises:
        MissingDependencyError:  dependência não encontrada
        DependencyCycleError:    ciclo detectado
        VersionConflictError:    versão instalada não satisfaz constraint
    """
    pmap = provides_map or {}
    inst = installed or set()
    inst_ver = installed_versions or {}

    visited: set[str] = set()
    in_progress: set[str] = set()
    order: list[str] = []

    def canonical(name: str) -> str:
        """Resolve nome virtual → nome real."""
        return pmap.get(name, name)

    def visit(spec: str, path: list[str]) -> None:
        """Visita uma dependência (que pode ter constraint de versão)."""
        # Tenta fazer parse como constraint; se falhar, usa como nome puro
        try:
            constraint = parse_constraint(spec)
            name = constraint.name
        except VersionError:
            name = spec
            constraint = None

        real = canonical(name)

        # Verifica versão instalada contra a constraint
        if constraint and constraint.op and real in inst_ver:
            inst_v = inst_ver[real]
            if not satisfies(real, inst_v, constraint):
                raise VersionConflictError(
                    f"versão instalada de '{real}' ({inst_v}) não satisfaz "
                    f"constraint '{constraint}'"
                )

        if real in visited:
            return
        if real not in recipes:
            hint = f" (requerido por '{path[-1]}')" if path else ""
            raise MissingDependencyError(
                f"pacote desconhecido '{name}'{hint}"
            )
        if real in in_progress:
            cycle = " → ".join(path + [real])
            raise DependencyCycleError(f"ciclo de dependências: {cycle}")

        in_progress.add(real)
        recipe = recipes[real]

        # dependências obrigatórias
        for dep in recipe.depends:
            visit(dep, path + [real])

        # dependências opcionais: só inclui se já estão instaladas
        for dep in recipe.optional_deps:
            try:
                dep_real_name = dep_name(dep)
            except VersionError:
                dep_real_name = dep
            dep_real = canonical(dep_real_name)
            if dep_real in inst:
                visit(dep, path + [real])

        in_progress.discard(real)
        visited.add(real)
        order.append(real)

    for target in targets:
        visit(target, [])

    return order


def reverse_deps(
    recipes: dict[str, Recipe],
    name: str,
    provides_map: dict[str, str] | None = None,
) -> list[str]:
    """Quais receitas têm `name` (ou um virtual que ele satisfaz) em depends?

    Suporta depends com operadores de versão: "openssl>=3.0" ainda conta
    como referência a "openssl".
    """
    pmap = provides_map or {}
    real = pmap.get(name, name)
    # nomes pelos quais esse pacote pode ser referenciado
    aliases: set[str] = {name, real}
    if real in recipes:
        aliases.update(recipes[real].provides)

    result = []
    for pkg_name, recipe in sorted(recipes.items()):
        if pkg_name == real:
            continue
        # Extrai nomes canônicos das dependências (sem operador/versão)
        dep_names: set[str] = set()
        for d in list(recipe.depends) + list(recipe.optional_deps):
            try:
                dep_names.add(dep_name(d))
            except VersionError:
                dep_names.add(d)
        if aliases & dep_names:
            result.append(pkg_name)
    return result


def dep_tree_lines(
    recipes: dict[str, Recipe],
    name: str,
    provides_map: dict[str, str] | None = None,
    _seen: set[str] | None = None,
    _prefix: str = "",
    _last: bool = True,
) -> list[str]:
    """Gera linhas de texto da árvore de dependências (estilo `tree`).

    Suporta depends com operadores de versão: "openssl>=3.0" é exibido
    como "openssl>=3.0" na árvore (mais informativo).
    """
    pmap = provides_map or {}

    # Tenta extrair o nome base de uma possível constraint
    try:
        constraint = parse_constraint(name)
        base_name = constraint.name
        suffix = f"{constraint.op}{constraint.version}" if constraint.op else ""
    except VersionError:
        base_name = name
        suffix = ""

    real = pmap.get(base_name, base_name)
    seen = _seen if _seen is not None else set()

    connector = "└── " if _last else "├── "
    if real not in recipes:
        label = f"{name} (desconhecido)"
        return [f"{_prefix}{connector}{label}"]

    r = recipes[real]
    already = real in seen
    label = f"{r.name} {r.version}{('  ' + suffix) if suffix else ''}"
    if already:
        label += c(" [já listado]", "dim")

    lines = [f"{_prefix}{connector}{label}"]
    seen.add(real)  # marca antes de descer para que deps compartilhadas sejam detectadas

    if not already and r.depends:
        child_prefix = _prefix + ("    " if _last else "│   ")
        for i, dep in enumerate(r.depends):
            is_last = i == len(r.depends) - 1
            lines += dep_tree_lines(
                recipes, dep, pmap, seen, child_prefix, is_last
            )

    return lines
