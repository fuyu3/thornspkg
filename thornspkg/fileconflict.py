# * Rastreamento de propriedade de arquivos e detecção de conflitos.
# * Mantém um índice {file_path: package_name} para consulta rápida de ownership.
# * Antes de instalar qualquer pacote, verifica se algum arquivo já pertence
# *   a outro pacote — em caso afirmativo, levanta FileConflictError.
# * Funções principais: build_file_index(), check_conflicts(), find_owner().
# * Arquivo: thornspkg/fileconflict.py

"""Rastreamento de propriedade de arquivos e detecção de conflitos.

Funciona em conjunto com o banco de dados (db.py): mantém um índice
em memória {caminho_relativo: nome_pacote} construído a partir de
db["packages"][name]["files"].

Fluxo de uso:
  1. Antes de instalar um pacote, chame check_conflicts(manifest, db, pkg_name)
  2. Se retornar lista não-vazia, aborte a instalação com FileConflictError
  3. Para consultas ad-hoc, use find_owner(db, path) — é O(N) mas o cache
     de índice pode ser construído uma vez com build_file_index()
"""

from __future__ import annotations

from pathlib import Path


class FileConflictError(Exception):
    """Levantada quando um arquivo a instalar já pertence a outro pacote.

    Attributes:
        package:     nome do pacote que está tentando instalar
        conflicts:   lista de tuples (file_path, owner_package)
    """

    def __init__(self, package: str, conflicts: list[tuple[str, str]]) -> None:
        self.package = package
        self.conflicts = conflicts
        lines = [f"conflito de arquivos ao instalar '{package}':"]
        for fpath, owner in conflicts:
            lines.append(f"  /{fpath}  →  já pertence a '{owner}'")
        lines.append(
            "  Use --force-overwrite para sobrescrever (não recomendado)."
        )
        super().__init__("\n".join(lines))


# ---------------------------------------------------------------------------
# índice de ownership
# ---------------------------------------------------------------------------

def build_file_index(db: dict) -> dict[str, str]:
    """Constrói um índice {caminho_relativo: nome_do_pacote}.

    Em caso de arquivos duplicados no banco (estado inconsistente,
    só deve ocorrer por corrupção manual), o primeiro pacote encontrado
    prevalece e os subsequentes são reportados como aviso via stderr.
    """
    index: dict[str, str] = {}
    for pkg_name, info in db.get("packages", {}).items():
        for f in info.get("files", []):
            if f in index:
                # Estado inconsistente — não deveria acontecer em condições normais
                from .colors import c
                import sys
                print(
                    c(f"  aviso: arquivo /{f} aparece em dois pacotes "
                      f"('{index[f]}' e '{pkg_name}') — banco pode estar corrompido",
                      "yellow"),
                    file=sys.stderr,
                )
                continue
            index[f] = pkg_name
    return index


def find_owner(db: dict, rel_path: str) -> str | None:
    """Retorna o nome do pacote que possui o arquivo `rel_path`.

    `rel_path` é o caminho relativo ao root, sem barra inicial,
    exatamente como armazenado no manifest (ex: "usr/bin/vim").
    """
    for pkg_name, info in db.get("packages", {}).items():
        if rel_path in info.get("files", []):
            return pkg_name
    return None


def find_owner_indexed(index: dict[str, str], rel_path: str) -> str | None:
    """Versão O(1) de find_owner() — requer índice pré-construído."""
    return index.get(rel_path)


# ---------------------------------------------------------------------------
# detecção de conflitos
# ---------------------------------------------------------------------------

def check_conflicts(
    manifest: list[str],
    db: dict,
    new_package: str,
    *,
    index: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Verifica se algum arquivo de `manifest` já pertence a outro pacote.

    Args:
        manifest:     lista de caminhos relativos (sem /) a instalar
        db:           banco de dados de pacotes instalados
        new_package:  nome do pacote que está sendo instalado
        index:        índice pré-construído (opcional, evita rebuild)

    Returns:
        Lista de tuples (file_path, owner_package) para cada conflito.
        Lista vazia = nenhum conflito.
    """
    if index is None:
        index = build_file_index(db)

    conflicts: list[tuple[str, str]] = []
    for fpath in manifest:
        owner = index.get(fpath)
        # Owner é None OU é o próprio pacote (reinstall) → OK
        if owner is not None and owner != new_package:
            conflicts.append((fpath, owner))
    return conflicts


def assert_no_conflicts(
    manifest: list[str],
    db: dict,
    new_package: str,
    *,
    index: dict[str, str] | None = None,
    allow_overwrite: bool = False,
) -> None:
    """Verifica conflitos e levanta FileConflictError se houver.

    Args:
        manifest:         lista de caminhos relativos a instalar
        db:               banco de dados
        new_package:      nome do pacote sendo instalado
        index:            índice pré-construído (opcional)
        allow_overwrite:  se True, não levanta erro (modo --force-overwrite)

    Raises:
        FileConflictError: se houver conflito e allow_overwrite=False
    """
    if allow_overwrite:
        return
    conflicts = check_conflicts(manifest, db, new_package, index=index)
    if conflicts:
        raise FileConflictError(new_package, conflicts)


# ---------------------------------------------------------------------------
# helpers de caminho
# ---------------------------------------------------------------------------

def to_rel_path(absolute_or_rel: str, root_dir: Path) -> str:
    """Normaliza um caminho para a forma relativa usada no manifest.

    "/usr/bin/vim"   → "usr/bin/vim"
    "usr/bin/vim"    → "usr/bin/vim"
    """
    s = absolute_or_rel
    if s.startswith("/"):
        s = s.lstrip("/")
    return s


def to_abs_path(rel_path: str, root_dir: Path) -> str:
    """Converte caminho relativo do manifest para caminho absoluto no root."""
    return str(root_dir / rel_path)
