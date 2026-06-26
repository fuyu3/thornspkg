# * Comando `thorn check` — verifica integridade (sha256) dos arquivos instalados.
# * Mantém a mesma saída da versão anterior, mas separado em seu próprio módulo.
# * Arquivo: thornspkg/commands/check.py

"""Comando `thorn check` — verifica integridade dos arquivos instalados."""

from __future__ import annotations

import sys

from .. import builder as bld
from .. import db as dbmod
from ..colors import c, ce
from .common import warn


def cmd_check(args, recipes, pmap, cfg) -> int:
    """Verifica integridade (sha256) dos arquivos instalados."""
    db = dbmod.load_db(cfg.db_dir)
    targets = args.packages or list(db["packages"])
    has_error = False

    for name in targets:
        if not dbmod.is_installed(db, name):
            warn(f"'{name}' não está instalado, pulando")
            continue

        stored = dbmod.load_checksums(cfg.db_dir, name)
        files = db["packages"][name]["files"]
        ok = fail = missing = 0

        print(c(f"→ {name}", "bold"))
        for rel in sorted(files):
            p = cfg.root_dir / rel
            if not p.exists() and not p.is_symlink():
                print(f"  {c('MISS', 'red')}  /{rel}")
                missing += 1
                has_error = True
                continue
            if p.is_symlink():
                continue
            expected = stored.get(rel)
            if expected is None:
                print(f"  {c('?   ', 'dim')}  /{rel}  (sem checksum)")
                continue
            actual = bld.sha256_file(p)
            if actual == expected:
                ok += 1
            else:
                print(f"  {c('FAIL', 'bright_red')}  /{rel}")
                has_error = True
                fail += 1

        parts = []
        if ok:      parts.append(c(f"{ok} ok", "green"))
        if fail:    parts.append(c(f"{fail} diverge", "red"))
        if missing: parts.append(c(f"{missing} ausente", "red"))
        print("  " + "  ".join(parts) if parts else c("  (sem checksums registrados)", "dim"))
        dbmod.update_check_ts(db, name)

    dbmod.save_db(cfg.db_dir, db)
    return 1 if has_error else 0
