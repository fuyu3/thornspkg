# * Comando `thorn sync` — atualiza índices de repositórios.
# * Alias direto de `thorn repo refresh`, mas com nome mais curto (estilo pacman).
# * Mantém a saída consistente com a versão anterior.
# * Arquivo: thornspkg/commands/sync.py

"""Comando `thorn sync` — atualiza índices de repositórios."""

from __future__ import annotations

from ..colors import c
from ..repo import RepoError, repo_refresh
from .common import err


def cmd_sync(args, recipes, pmap, cfg) -> int:
    """Atualiza os índices de todos os repositórios configurados."""
    try:
        repo_refresh(cfg.repos_config, cfg.db_dir)
    except RepoError as e:
        return err(str(e))
    return 0
