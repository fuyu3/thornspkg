# * Comando `thorn orphan-files` — lista arquivos não pertencentes a nenhum pacote.
# * Filosofia: apenas reporta, nunca apaga (igual à versão anterior).
# * Arquivo: thornspkg/commands/orphan.py

"""Comando `thorn orphan-files`."""

from __future__ import annotations

from .. import config as cfgmod
from .. import db as dbmod
from ..colors import c
from ..orphan import collect_managed_files, find_orphan_files


def cmd_orphan_files(args, recipes, pmap, cfg) -> int:
    """Lista arquivos no root que não pertencem a nenhum pacote instalado."""
    db = dbmod.load_db(cfg.db_dir)

    if not db["packages"]:
        print(c("Nenhum pacote instalado — todos os arquivos são órfãos.", "yellow"))
        return 0

    managed = collect_managed_files(db)

    # Exclusões: defaults + dirs próprios do thorn + extras do usuário
    exclude = list(cfgmod.DEFAULT_ORPHAN_EXCLUDE)
    for thorn_dir in [cfg.sources_dir, cfg.build_dir]:
        try:
            rel = str(thorn_dir.resolve().relative_to(cfg.root_dir.resolve()))
            exclude.append(rel)
        except ValueError:
            pass
    if args.exclude:
        exclude.extend(args.exclude)

    print(c(f"Percorrendo {cfg.root_dir} …", "dim"))
    orphans = find_orphan_files(cfg.root_dir, managed, exclude, db_dir=cfg.db_dir)

    if not orphans:
        print(c("Nenhum arquivo órfão encontrado.", "green"))
        return 0

    print(c(f"{len(orphans)} arquivo(s) órfão(s) (não pertencem a nenhum pacote):", "bold"))
    for f in orphans:
        print(f"  {f}")

    print(c(
        f"\n  {len(orphans)} arquivo(s) listados — nada foi removido.",
        "dim",
    ))
    return 0
