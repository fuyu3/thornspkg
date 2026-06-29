# * Configuração global do thornspkg — todos os paths e defaults.
# * Define constantes DEFAULT_* que podem ser sobrescritas por variáveis de ambiente
# * (ex: THORN_ROOT, THORN_DB) ou por flags de CLI (que têm prioridade máxima).
# * A dataclass Config é o objeto passado para todas as funções que precisam de paths.
# * Campos importantes: recipes_dir, db_dir, root_dir, prefix, jobs, repos_config.
# * Arquivo: thornspkg/config.py

"""Configuração global do thornspkg.

Todos os paths têm um default razoável para um sistema LFS já em uso,
mas podem ser sobrescritos via variável de ambiente ou flag de CLI.
A flag tem sempre prioridade sobre a variável de ambiente.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_RECIPES_DIR  = Path(os.environ.get("THORN_RECIPES",  "/etc/thornspkg/recipes"))
DEFAULT_PATCHES_DIR  = Path(os.environ.get("THORN_PATCHES",  "/etc/thornspkg/patches"))
DEFAULT_SOURCES_DIR  = Path(os.environ.get("THORN_SOURCES",  "/var/cache/thornspkg/sources"))
DEFAULT_BUILD_DIR    = Path(os.environ.get("THORN_BUILD",    "/var/tmp/thornspkg/build"))
DEFAULT_DB_DIR       = Path(os.environ.get("THORN_DB",       "/var/lib/thornspkg"))
DEFAULT_ROOT_DIR     = Path(os.environ.get("THORN_ROOT",     "/"))
DEFAULT_PREFIX       = os.environ.get("THORN_PREFIX",        "/usr")
DEFAULT_HOOKS_DIR    = Path(os.environ.get("THORN_HOOKS",    "/etc/thornspkg/hooks"))
DEFAULT_JOBS         = int(os.environ.get("THORN_JOBS",      os.cpu_count() or 1))

# Arquivo de configuração dos repositórios remotos
DEFAULT_REPOS_CONFIG = Path(os.environ.get("THORN_REPOS_CONFIG", "/etc/thornspkg/repos.json"))

# Prefixes relativos ao root ignorados por 'thorn orphan-files'
DEFAULT_ORPHAN_EXCLUDE: list[str] = [
    "proc", "sys", "dev", "run", "tmp", "lost+found",
    "var/tmp", "var/run",
]


@dataclass
class Config:
    recipes_dir: Path
    patches_dir: Path
    sources_dir: Path
    build_dir: Path
    db_dir: Path
    root_dir: Path
    prefix: str
    hooks_dir: Path
    jobs: int
    extra_env: dict[str, str] = field(default_factory=dict)
    repos_config: Path = field(default_factory=lambda: DEFAULT_REPOS_CONFIG)
