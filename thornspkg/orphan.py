# * Detecção de arquivos órfãos — lista arquivos no root não gerenciados por nenhum pacote.
# * Filosofia: apenas reporta, nunca apaga. O usuário decide o que fazer.
# * Algoritmo: os.walk topdown com pruning agressivo (diretórios excluídos são ignorados).
# * Funções principais: collect_managed_files() → set de paths gerenciados,
# *   find_orphan_files() → lista de arquivos órfãos no root.
# * Arquivo: thornspkg/orphan.py

"""Detecção de arquivos órfãos — arquivos no root não gerenciados pelo thorn.

Filosofia: reportar apenas, nunca apagar. O usuário decide o que fazer.

O algoritmo é um os.walk topdown com pruning agressivo:
  - diretórios excluídos são ignorados por completo (sem entrar neles)
  - symlinks para diretório aparecem em dirnames mas NÃO são percorridos
    pelo walk; tratamos como "arquivo" para fins de rastreamento
  - diretórios reais sem arquivos rastreados são silenciosamente ignorados
    (criados pelos pacotes mas não listados no manifest, comportamento normal)
"""

from __future__ import annotations

import os
from pathlib import Path


def collect_managed_files(db: dict) -> set[str]:
    """Retorna o conjunto de todos os caminhos relativos gerenciados.

    Os caminhos são relativos ao root (sem barra inicial), exatamente como
    gravados no manifest por builder.install_from_staging().
    Ex: "usr/lib/libz.so", "usr/include/zlib.h"
    """
    managed: set[str] = set()
    for info in db["packages"].values():
        for f in info.get("files", []):
            managed.add(f)
    return managed


def _rel(base: Path, child: Path) -> str:
    """Caminho relativo de child em relação a base, como string POSIX.

    Retorna "" se child == base.
    """
    rel = child.relative_to(base)
    s = str(rel)
    return "" if s == "." else s


def find_orphan_files(
    root: Path,
    managed: set[str],
    exclude_prefixes: list[str],
    db_dir: Path | None = None,
) -> list[str]:
    """Percorre root e lista arquivos/symlinks não presentes em `managed`.

    Args:
        root:             diretório raiz a percorrer (normalmente cfg.root_dir)
        managed:          conjunto retornado por collect_managed_files()
        exclude_prefixes: lista de prefixos relativos ao root a ignorar
                          ex: ["proc", "sys", "var/tmp"]
        db_dir:           se fornecido e estiver dentro de root, é excluído
                          automaticamente (o próprio banco de dados do thorn)

    Returns:
        Lista ordenada de caminhos absolutos (com "/" inicial) de órfãos.
    """
    root_r = root.resolve()

    # Normaliza exclusões para strings POSIX sem barra inicial
    excl: set[str] = {p.strip("/") for p in exclude_prefixes if p.strip("/")}

    # Adiciona o diretório do banco se estiver dentro do root
    if db_dir is not None:
        try:
            rel_db = _rel(root_r, db_dir.resolve())
            if rel_db:
                excl.add(rel_db)
        except ValueError:
            pass   # db_dir fora do root — não precisa excluir

    def _is_excluded(rel: str) -> bool:
        """Verdadeiro se `rel` é ou está dentro de algum prefixo excluído."""
        if rel in excl:
            return True
        return any(rel.startswith(e + "/") for e in excl)

    orphans: list[str] = []

    for dirpath_str, dirnames, filenames in os.walk(root_r, followlinks=False, topdown=True):
        dirpath = Path(dirpath_str)
        rel_dir = _rel(root_r, dirpath)

        # ----- pruning de diretórios excluídos -----
        # Filtra dirnames IN-PLACE para que os.walk não entre neles.
        # Precisamos verificar tanto o dir atual quanto o caminho composto.
        kept: list[str] = []
        for d in dirnames:
            rel_d = (rel_dir + "/" + d) if rel_dir else d
            if _is_excluded(rel_d):
                continue   # poda este subdiretório
            src = dirpath / d
            if src.is_symlink():
                # Symlink para diretório: trata como arquivo rastreável,
                # mas NÃO entra nele (followlinks=False já garante isso,
                # porém ainda aparece em dirnames — precisamos tirar para
                # o walk não tentar entrar).
                rel_file = rel_d
                if rel_file not in managed:
                    orphans.append("/" + rel_file)
            else:
                kept.append(d)
        dirnames[:] = kept

        # ----- arquivos e symlinks para arquivo -----
        for fname in filenames:
            rel_file = (rel_dir + "/" + fname) if rel_dir else fname
            if rel_file not in managed:
                orphans.append("/" + rel_file)

    return sorted(orphans)
