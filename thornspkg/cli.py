# * Interface de linha de comando do thornspkg — parser argparse e registro de subcomandos.
# * A lógica de cada comando está em thornspkg/commands/*.py.
# * Comandos suportados:
# *   deps, tree, install, remove, autoremove, recover-tx, fetch, list, info,
# *   search, why, files, owns, check, outdated, log, suggest-deps,
# *   orphan-files, repo, sync, upgrade, list-upgrades, cache.
# * Lock automático: operações de escrita (install, remove, autoremove, recover-tx,
# *   repo, sync, upgrade) adquirem lock exclusivo via PackageLock.
# * Arquivo: thornspkg/cli.py

"""thorn — interface de linha de comando do thornspkg."""

from __future__ import annotations

import argparse

from . import config as cfgmod
from .colors import ce
from .commands import (
    cache_cmd,
    check as check_cmd,
    inspect as inspect_cmd,
    install as install_cmd,
    orphan as orphan_cmd,
    remove as remove_cmd,
    repo as repo_cmd,
    search as search_cmd,
    selfupdate as selfupdate_cmd,
    sync as sync_cmd,
    upgrade as upgrade_cmd,
)
from .lock import LockError, PackageLock
from .recipe import RecipeError, build_provides_map, load_all_recipes


# ---------------------------------------------------------------------------
# mensagens
# ---------------------------------------------------------------------------

def _err(msg: str) -> int:
    import sys
    print(ce(f"erro: {msg}", "red"), file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# construção da Config
# ---------------------------------------------------------------------------

def build_config(args: argparse.Namespace) -> cfgmod.Config:
    return cfgmod.Config(
        recipes_dir  = __import__("pathlib").Path(args.recipes_dir),
        patches_dir  = __import__("pathlib").Path(args.patches_dir),
        sources_dir  = __import__("pathlib").Path(args.sources_dir),
        build_dir    = __import__("pathlib").Path(args.build_dir),
        db_dir       = __import__("pathlib").Path(args.db_dir),
        root_dir     = __import__("pathlib").Path(args.root),
        prefix       = args.prefix,
        hooks_dir    = __import__("pathlib").Path(args.hooks_dir),
        jobs         = args.jobs,
        repos_config = __import__("pathlib").Path(getattr(args, 'repos_config', cfgmod.DEFAULT_REPOS_CONFIG)),
    )


# ---------------------------------------------------------------------------
# argumentos globais
# ---------------------------------------------------------------------------

def _add_global_args(parser: argparse.ArgumentParser) -> None:
    d = cfgmod
    from pathlib import Path
    parser.add_argument("--recipes-dir", default=str(d.DEFAULT_RECIPES_DIR), metavar="DIR")
    parser.add_argument("--patches-dir", default=str(d.DEFAULT_PATCHES_DIR), metavar="DIR")
    parser.add_argument("--sources-dir", default=str(d.DEFAULT_SOURCES_DIR), metavar="DIR")
    parser.add_argument("--build-dir",   default=str(d.DEFAULT_BUILD_DIR),   metavar="DIR")
    parser.add_argument("--db-dir",      default=str(d.DEFAULT_DB_DIR),      metavar="DIR")
    parser.add_argument(
        "--root", default=str(d.DEFAULT_ROOT_DIR), metavar="DIR",
        help="raiz de instalação (use $LFS antes do chroot)",
    )
    parser.add_argument(
        "--prefix", default=d.DEFAULT_PREFIX, metavar="PATH",
        help="prefixo de instalação dentro do root (default: /usr)",
    )
    parser.add_argument(
        "--hooks-dir", default=str(d.DEFAULT_HOOKS_DIR), metavar="DIR",
        help="diretório de hooks globais (default: /etc/thornspkg/hooks)",
    )
    parser.add_argument(
        "--repos-config", default=str(d.DEFAULT_REPOS_CONFIG), metavar="FILE",
        help="arquivo de configuração dos repositórios (default: /etc/thornspkg/repos.json)",
    )
    parser.add_argument("-j", "--jobs", type=int, default=d.DEFAULT_JOBS, metavar="N")


# ---------------------------------------------------------------------------
# parser principal
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="thorn",
        description="thornspkg — gerenciador de pacotes source-based para LFS/BLFS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
comandos:
  deps            ordem de build/dependências (sem instalar)
  tree            árvore ASCII de dependências
  install         compila e instala (resolvendo deps)
  remove          remove um pacote instalado
  autoremove      remove dependências órfãs
  recover-tx      desfaz transação atômica interrompida
  fetch           baixa sources sem compilar
  list            lista pacotes instalados
  info            detalhes de receita e status de instalação
  search          busca receitas por nome/descrição
  why             quem depende de um pacote
  files           arquivos instalados de um pacote
  owns            qual pacote possui um arquivo
  check           verifica integridade (sha256) dos arquivos
  outdated        pacotes com versão instalada ≠ disponível
  log             exibe log de build
  suggest-deps    sugere dependências analisando o source
  orphan-files    lista arquivos não pertencentes a nenhum pacote
  repo            gerencia repositórios remotos (add/remove/list/refresh)
  sync            atualiza índices dos repositórios (alias: repo refresh)
  upgrade         atualiza todos os pacotes (ou apenas um)
  list-upgrades   lista atualizações disponíveis
  cache           gerencia cache de downloads (stats/clean/list)
  version         mostra a versão instalada do thornspkg
  self-update     atualiza o próprio thornspkg (do PyPI/GitHub/URL)
        """,
    )
    _add_global_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    # deps
    p = sub.add_parser("deps", help="ordem de build sem instalar")
    p.add_argument("packages", nargs="+")
    p.set_defaults(func=inspect_cmd.cmd_deps)

    # tree
    p = sub.add_parser("tree", help="árvore ASCII de dependências")
    p.add_argument("packages", nargs="+")
    p.set_defaults(func=inspect_cmd.cmd_tree)

    # install
    p = sub.add_parser("install", aliases=["build"], help="compila e instala")
    p.add_argument("packages", nargs="+")
    p.add_argument("--reinstall",  action="store_true", help="reconstrói mesmo se já instalado")
    p.add_argument("--keep-build", action="store_true", help="preserva o diretório de build")
    p.add_argument("-n", "--dry-run", action="store_true", help="simula sem instalar")
    p.add_argument(
        "--atomic", action="store_true",
        help="só grava o banco se TODOS os pacotes instalarem com sucesso; "
             "em caso de falha, remove os arquivos já instalados nesta sessão",
    )
    p.add_argument(
        "--prefer-binary", action="store_true", default=True,
        help="prefere pacotes binários quando disponíveis (padrão)",
    )
    p.add_argument(
        "--prefer-source", action="store_true",
        help="força compilação a partir da receita, mesmo se houver binário",
    )
    p.add_argument(
        "--force-overwrite", action="store_true",
        help="sobrescreve arquivos que pertencem a outros pacotes (perigoso)",
    )
    p.set_defaults(func=install_cmd.cmd_install)

    # remove
    p = sub.add_parser("remove", aliases=["rm"], help="remove um pacote instalado")
    p.add_argument("package")
    p.add_argument("--force", action="store_true", help="remove mesmo com dependentes instalados")
    p.set_defaults(func=remove_cmd.cmd_remove)

    # autoremove
    p = sub.add_parser("autoremove", help="remove dependências órfãs")
    p.add_argument("-y", "--yes", action="store_true", help="confirma automaticamente")
    p.set_defaults(func=remove_cmd.cmd_autoremove)

    # recover-tx
    p = sub.add_parser("recover-tx", help="desfaz transação atômica interrompida")
    p.add_argument("-y", "--yes", action="store_true", help="confirma automaticamente")
    p.set_defaults(func=remove_cmd.cmd_recover_tx)

    # fetch
    p = sub.add_parser("fetch", help="baixa sources sem compilar")
    p.add_argument("packages", nargs="+")
    p.set_defaults(func=inspect_cmd.cmd_fetch)

    # list
    p = sub.add_parser("list", aliases=["ls"], help="lista pacotes instalados")
    p.set_defaults(func=search_cmd.cmd_list)

    # info
    p = sub.add_parser("info", help="detalhes de um pacote")
    p.add_argument("package")
    p.set_defaults(func=search_cmd.cmd_info)

    # search
    p = sub.add_parser("search", help="busca receitas por nome/descrição")
    p.add_argument("pattern")
    p.set_defaults(func=search_cmd.cmd_search)

    # why
    p = sub.add_parser("why", help="quem depende de um pacote")
    p.add_argument("package")
    p.set_defaults(func=search_cmd.cmd_why)

    # files
    p = sub.add_parser("files", help="arquivos instalados de um pacote")
    p.add_argument("package")
    p.set_defaults(func=inspect_cmd.cmd_files)

    # owns
    p = sub.add_parser("owns", help="qual pacote possui um arquivo")
    p.add_argument("path")
    p.set_defaults(func=inspect_cmd.cmd_owns)

    # check
    p = sub.add_parser("check", help="verifica integridade dos arquivos")
    p.add_argument("packages", nargs="*", metavar="PACKAGE",
                   help="pacotes a verificar (default: todos)")
    p.set_defaults(func=check_cmd.cmd_check)

    # outdated
    p = sub.add_parser("outdated", help="pacotes com versão ≠ disponível")
    p.set_defaults(func=search_cmd.cmd_outdated)

    # log
    p = sub.add_parser("log", help="exibe log de build")
    p.add_argument("package")
    p.add_argument("--tail", type=int, metavar="N", help="últimas N linhas")
    p.set_defaults(func=inspect_cmd.cmd_log)

    # suggest-deps
    p = sub.add_parser("suggest-deps", help="sugere deps analisando o source")
    p.add_argument("package")
    p.add_argument("--keep", action="store_true",
                   help="mantém o source extraído após a análise")
    p.set_defaults(func=inspect_cmd.cmd_suggest_deps)

    # orphan-files
    p = sub.add_parser("orphan-files", help="lista arquivos não pertencentes a nenhum pacote")
    p.add_argument(
        "--exclude", action="append", metavar="PATH",
        help="caminho relativo ao root a ignorar (pode repetir)",
    )
    p.set_defaults(func=orphan_cmd.cmd_orphan_files)

    # repo
    p = sub.add_parser("repo", help="gerencia repositórios remotos")
    p.add_argument(
        "repo_action",
        choices=["add", "remove", "list", "refresh"],
        help="ação: add/remove/list/refresh",
    )
    p.add_argument("repo_name", nargs="?", help="nome do repositório (add/remove)")
    p.add_argument("repo_url", nargs="?", help="URL do repositório (add)")
    p.set_defaults(func=repo_cmd.cmd_repo)

    # sync (alias curto para repo refresh)
    p = sub.add_parser("sync", help="atualiza índices dos repositórios")
    p.set_defaults(func=sync_cmd.cmd_sync)

    # upgrade
    p = sub.add_parser("upgrade", help="atualiza todos os pacotes (ou apenas um)")
    p.add_argument("packages", nargs="*", help="pacotes específicos a atualizar (default: todos)")
    p.add_argument("--reinstall", action="store_true",
                   help="reinstala mesmo se já está na versão mais recente")
    p.add_argument("-n", "--dry-run", action="store_true", help="simula sem instalar")
    p.add_argument(
        "--atomic", action="store_true",
        help="só grava o banco se TODOS os upgrades tiverem sucesso (com rollback)",
    )
    p.add_argument(
        "--prefer-binary", action="store_true", default=True,
        help="prefere pacotes binários quando disponíveis (padrão)",
    )
    p.add_argument(
        "--prefer-source", action="store_true",
        help="força compilação a partir da receita, mesmo se houver binário",
    )
    p.add_argument(
        "--force-overwrite", action="store_true",
        help="sobrescreve arquivos que pertencem a outros pacotes",
    )
    p.set_defaults(func=upgrade_cmd.cmd_upgrade)

    # list-upgrades
    p = sub.add_parser("list-upgrades", aliases=["list-upgradable"],
                       help="lista atualizações disponíveis")
    p.set_defaults(func=upgrade_cmd.cmd_list_upgrades)

    # cache
    p = sub.add_parser("cache", help="gerencia cache de downloads")
    p.add_argument(
        "cache_action",
        choices=["stats", "clean", "list"],
        help="ação: stats (estatísticas) | clean (limpar) | list (listar)",
    )
    p.add_argument("--no-sources",  action="store_true", help="não limpar sources/")
    p.add_argument("--no-packages", action="store_true", help="não limpar packages/")
    p.add_argument("--indexes",     action="store_true",
                   help="limpar também indexes/ (default: não limpa)")
    p.set_defaults(func=cache_cmd.cmd_cache)

    # version
    p = sub.add_parser("version", help="mostra a versão instalada do thornspkg")
    p.add_argument("--check", action="store_true",
                   help="verifica se há versão mais recente disponível no GitHub")
    p.add_argument("--repo", metavar="OWNER/REPO",
                   help=f"repositório GitHub (default: {selfupdate_cmd.get_github_repo()})")
    p.set_defaults(func=selfupdate_cmd.cmd_version)

    # self-update
    p = sub.add_parser("self-update", aliases=["selfupdate"],
                       help="atualiza o próprio thornspkg via GitHub Releases")
    p.add_argument("--repo", metavar="OWNER/REPO",
                   help=f"repositório GitHub (default: {selfupdate_cmd.get_github_repo()})")
    p.add_argument("--tag", metavar="TAG",
                   help="instala uma tag específica (ex: v0.4.3); default: latest")
    p.add_argument("--url", metavar="URL",
                   help="URL customizada do tarball (sobrescreve --repo/--tag)")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="só mostra o que seria feito, sem instalar")
    p.add_argument("--force", action="store_true",
                   help="reinstala mesmo se já está na versão mais recente")
    p.add_argument("-y", "--yes", action="store_true",
                   help="não pede confirmação")
    p.set_defaults(func=selfupdate_cmd.cmd_self_update)

    args = parser.parse_args(argv)
    cfg = build_config(args)

    # Validação do subcomando repo
    if args.command == "repo":
        if args.repo_action == "add" and (not args.repo_name or not args.repo_url):
            parser.error("repo add requer <nome> e <url>")
        if args.repo_action == "remove" and not args.repo_name:
            parser.error("repo remove requer <nome>")

    # Adquire lock para operações de escrita
    needs_lock = args.command in (
        "install", "remove", "autoremove", "recover-tx",
        "repo", "sync", "upgrade",
    )
    lock = PackageLock(cfg.db_dir)

    if needs_lock:
        try:
            lock.acquire()
        except LockError as e:
            return _err(str(e))

    try:
        # Carrega receitas de forma tolerante: comandos como `repo add`,
        # `sync`, `cache` não precisam delas, e o diretório default pode
        # não existir em sistemas sem receitas locais.
        try:
            recipes = load_all_recipes(cfg.recipes_dir)
        except RecipeError as e:
            # Se o erro for apenas "diretório não encontrado", usa dict vazio.
            # Outros erros (TOML inválido, etc.) continuam sendo fatais.
            if "não encontrado" in str(e) or "not found" in str(e).lower():
                recipes = {}
            else:
                return _err(f"ao carregar receitas: {e}")

        try:
            pmap = build_provides_map(recipes)
        except RecipeError as e:
            return _err(str(e))

        return args.func(args, recipes, pmap, cfg) or 0
    finally:
        if needs_lock:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
