# * Comandos `thorn remove` e `thorn autoremove`.
# * remove: remove um pacote e reporta órfãos resultantes.
# * autoremove: remove todas as dependências órfãs transitivamente.
# * Ambos usam do_remove_one() de common.py para hooks + remoção de arquivos.
# * Arquivo: thornspkg/commands/remove.py

"""Comandos `thorn remove` e `thorn autoremove`."""

from __future__ import annotations

from .. import db as dbmod
from ..colors import c, ce
from .common import confirm, do_remove_one, err


def cmd_remove(args, recipes, pmap, cfg) -> int:
    """Remove um pacote instalado e reporta órfãos resultantes."""
    from ..version import dep_name, VersionError

    db = dbmod.load_db(cfg.db_dir)
    try:
        name = pmap.get(dep_name(args.package), dep_name(args.package))
    except VersionError:
        name = pmap.get(args.package, args.package)

    if not dbmod.is_installed(db, name):
        return err(f"'{name}' não está instalado")

    # Proteção contra remoção de algo que outros precisam
    dependents = dbmod.find_dependents(db, name)
    if dependents and not args.force:
        print(
            ce(f"erro: '{name}' ainda é requerido por: ", "red") +
            ", ".join(dependents),
        )
        print("  use --force para forçar a remoção")
        return 1

    version = db["packages"][name]["version"]

    # Calcula órfãos ANTES de remover (simula remoção numa cópia)
    temp_pkgs = {k: v for k, v in db["packages"].items() if k != name}
    orphans = dbmod.find_all_orphans_transitively(temp_pkgs)

    # Remove o pacote
    recipe_opt = recipes.get(name)
    removed, skipped = do_remove_one(name, version, recipe_opt, db, cfg)

    print(c(f"✓ '{name}' removido ({removed} arq., {skipped} ignorados).", "green"))

    # Informa sobre órfãos
    if orphans:
        orphans = [o for o in orphans if dbmod.is_installed(db, o)]

    if orphans:
        print(c(
            f"\n  {len(orphans)} dependência(s) ficaram sem uso "
            "(instaladas apenas como dep):",
            "yellow",
        ))
        for o in orphans:
            print(f"    {o}  {db['packages'][o]['version']}")
        print("  Execute " + c("thorn autoremove", "cyan") + " para removê-las.")

    return 0


def cmd_autoremove(args, recipes, pmap, cfg) -> int:
    """Remove todas as dependências órfãs (reason='dependency', sem mais usuários)."""
    db = dbmod.load_db(cfg.db_dir)
    orphans = dbmod.find_all_orphans_transitively(db["packages"])

    if not orphans:
        print(c("Nenhuma dependência órfã encontrada.", "green"))
        return 0

    print(c(f"Dependências órfãs ({len(orphans)}):", "bold"))
    for o in orphans:
        info = db["packages"].get(o, {})
        print(f"  {o:<24} {info.get('version', '?')}")

    if not confirm(
        f"\nRemover os {len(orphans)} pacote(s) acima?",
        default=True,
        yes=args.yes,
    ):
        print("Operação cancelada.")
        return 0

    errors = 0
    for i, name in enumerate(orphans, 1):
        if not dbmod.is_installed(db, name):
            continue

        version = db["packages"][name]["version"]
        recipe_opt = recipes.get(name)
        print(c(f"\n[{i}/{len(orphans)}] removendo {name}…", "dim"))
        try:
            removed, skipped = do_remove_one(name, version, recipe_opt, db, cfg)
            print(c(f"  ✓ {removed} arq. removidos", "green"))
        except Exception as e:
            print(ce(f"  ✗ falha ao remover '{name}': {e}", "red"))
            errors += 1

    if errors:
        print(ce(f"\n{errors} erro(s) durante autoremove.", "red"))
        return 1

    print(c(f"\n✓ {len(orphans)} pacote(s) órfão(s) removidos.", "bright_green"))
    return 0


def cmd_recover_tx(args, recipes, pmap, cfg) -> int:
    """Desfaz uma transação atômica interrompida (journal em disco)."""
    if not dbmod.TransactionJournal.is_pending(cfg.db_dir):
        print(c("Nenhuma transação incompleta encontrada.", "green"))
        return 0

    journal = dbmod.TransactionJournal.load_from_disk(cfg.db_dir)
    names = journal.package_names

    if not names:
        print("Journal vazio. Apagando.")
        journal.clear()
        return 0

    print(c("Transação incompleta encontrada. Pacotes afetados:", "bold"))
    for n in names:
        print(f"  {n}")

    if not confirm(
        f"\nRemover os {len(names)} pacote(s) listados do root?",
        default=True,
        yes=args.yes,
    ):
        print("Operação cancelada. Journal mantido.")
        return 0

    rolled = journal.rollback(cfg.root_dir)
    print(c(f"✓ Rollback concluído: {', '.join(rolled)} removidos.", "green"))
    print("  Banco de dados não foi modificado.")
    return 0
