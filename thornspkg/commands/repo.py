# * Comando `thorn repo` — gerencia repositórios remotos.
# * Subcomandos: add, remove, list, refresh.
# * Mantém compatibilidade total com a versão anterior.
# * Arquivo: thornspkg/commands/repo.py

"""Comando `thorn repo` — gerencia repositórios remotos."""

from __future__ import annotations

from ..colors import c
from ..repo import (
    RepoError,
    repo_add,
    repo_list,
    repo_refresh,
    repo_remove,
)
from .common import err


def cmd_repo(args, recipes, pmap, cfg) -> int:
    """Gerencia repositórios remotos."""
    repo_action = args.repo_action

    try:
        if repo_action == "add":
            repo_add(cfg.repos_config, args.repo_name, args.repo_url)
        elif repo_action == "remove":
            repo_remove(cfg.repos_config, args.repo_name)
        elif repo_action == "list":
            repo_list(cfg.repos_config)
        elif repo_action == "refresh":
            # refresh é alias de sync neste contexto
            repo_refresh(cfg.repos_config, cfg.db_dir)
        else:
            return err(f"ação de repositório desconhecida: {repo_action}")
    except RepoError as e:
        return err(str(e))

    return 0
