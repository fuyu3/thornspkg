# * Comando `thorn cache` — gerencia o cache persistente de downloads.
# * Subcomandos: stats, clean, list.
# * stats: mostra uso do cache (arquivos, espaço, reaproveitamento).
# * clean: remove todo o cache (ou subconjunto via flags).
# * list:  lista arquivos no cache, agrupados por categoria.
# * Arquivo: thornspkg/commands/cache_cmd.py

"""Comando `thorn cache` — gerencia o cache persistente."""

from __future__ import annotations

from .. import cache
from ..colors import c


def cmd_cache(args, recipes, pmap, cfg) -> int:
    """Gerencia o cache de downloads."""
    action = args.cache_action

    if action == "stats":
        return _cmd_cache_stats(args, cfg)
    if action == "clean":
        return _cmd_cache_clean(args, cfg)
    if action == "list":
        return _cmd_cache_list(args, cfg)

    print(c(f"ação de cache desconhecida: {action}", "red"))
    return 1


def _cmd_cache_stats(args, cfg) -> int:
    """Mostra estatísticas do cache."""
    stats = cache.cache_stats(cfg)
    print(stats.format())
    return 0


def _cmd_cache_clean(args, cfg) -> int:
    """Remove conteúdo do cache."""
    sources = not getattr(args, 'no_sources', False)
    packages = not getattr(args, 'no_packages', False)
    indexes = getattr(args, 'indexes', False)

    if not (sources or packages or indexes):
        print(c("Nada para limpar (nenhuma categoria selecionada).", "yellow"))
        return 0

    removed = cache.cache_clean(
        cfg,
        sources=sources,
        packages=packages,
        indexes=indexes,
    )
    print(c(f"✓ {removed} arquivo(s) removido(s) do cache.", "green"))
    return 0


def _cmd_cache_list(args, cfg) -> int:
    """Lista arquivos no cache."""
    files = cache.cache_list(cfg)
    any_files = False
    for category in ("sources", "packages", "indexes"):
        items = files.get(category, [])
        if not items:
            continue
        any_files = True
        print(c(f"\n{category}/ ({len(items)} arquivo(s)):", "bold"))
        for p in items:
            size = p.stat().st_size if p.exists() else 0
            print(f"  {p.name:<40}  {_human_size(size)}")

    if not any_files:
        print(c("Cache vazio.", "dim"))
    return 0


def _human_size(n: int) -> str:
    """Converte bytes para forma legível."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
