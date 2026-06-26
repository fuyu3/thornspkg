# * Comandos `thorn self-update` e `thorn version`.
# * self-update: verifica, baixa e instala a versão mais recente do thornspkg
# *   a partir do GitHub Releases (default) ou URL customizada.
# * version:     mostra a versão instalada e (com --check) a versão mais recente disponível.
# * Estes comandos funcionam INDEPENDENTEMENTE do banco de dados do thornspkg,
# *   para poder se recuperar mesmo se o DB estiver corrompido.
# * Arquivo: thornspkg/commands/selfupdate.py

"""Comandos `thorn self-update` e `thorn version`."""

from __future__ import annotations

from .. import __version__ as CURRENT_VERSION
from ..colors import c, ce
from ..selfupdate import (
    SelfUpdateError,
    fetch_latest,
    get_github_repo,
    is_newer_available,
    perform_self_update,
)


def cmd_version(args, recipes, pmap, cfg) -> int:
    """Mostra a versão instalada do thornspkg."""
    print(c(f"thornspkg {CURRENT_VERSION}", "bold"))

    import thornspkg
    import sys
    print(f"  Python:    {sys.executable}")
    print(f"  Pacote:    {thornspkg.__file__}")
    print(f"  Repo:      github.com/{get_github_repo(getattr(args, 'repo', None))}")

    if getattr(args, 'check', False):
        print()
        print(c("→ verificando versão mais recente no GitHub…", "cyan"))
        try:
            repo = get_github_repo(getattr(args, 'repo', None))
            release = fetch_latest(repo)
        except SelfUpdateError as e:
            print(ce(f"  erro: {e}", "red"))
            return 1

        print(f"  Remota:    {release.version} (tag {release.tag_name})")
        if release.release_name:
            print(f"  Título:    {release.release_name}")
        if is_newer_available(CURRENT_VERSION, release.version):
            print(c(
                f"\n  ✗ versão mais recente disponível: {release.version}",
                "bright_yellow",
            ))
            print(c("    execute 'sudo thorn self-update' para atualizar", "dim"))
            return 1
        else:
            print(c("\n  ✓ já está na versão mais recente", "green"))
            return 0

    return 0


def cmd_self_update(args, recipes, pmap, cfg) -> int:
    """Atualiza o thornspkg para a versão mais recente via GitHub Releases.

    Funciona de forma autônoma — não usa o banco de dados de pacotes,
    para poder se recuperar mesmo se o DB estiver corrompido.
    """
    return perform_self_update(
        repo=getattr(args, 'repo', None),
        tag=getattr(args, 'tag', None),
        url=getattr(args, 'url', None),
        dry_run=args.dry_run,
        force=args.force,
        yes=args.yes,
    )
