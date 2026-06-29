# * Comandos `thorn upgrade`, `thorn list-upgrades`.
# * upgrade: atualiza todos os pacotes instalados (ou apenas um, se especificado).
# * list-upgrades: lista pacotes desatualizados sem instalar nada.
# * Fluxo: sync → comparar versões → detectar desatualizados → resolver deps →
# *   executar transação → permitir rollback em caso de erro.
# * Usa version.py para comparação robusta de versões.
# * Arquivo: thornspkg/commands/upgrade.py

"""Comandos `thorn upgrade` e `thorn list-upgrades`."""

from __future__ import annotations

from .. import builder as bld
from .. import db as dbmod
from ..colors import c, ce
from ..downloader import DownloadError
from ..fileconflict import FileConflictError
from ..hooks import HookError
from ..recipe import Recipe
from ..repo import find_package_in_repos
from ..version import Version, VersionError, compare, parse_version
from .common import (
    build_installed_versions,
    cleanup_obsolete_files,
    err,
    install_one_package,
    resolve_install_order,
    warn,
)


def _latest_available(name: str, recipes: dict, cfg) -> tuple[str, str | None]:
    """Retorna (versão, origem) da versão mais recente disponível.

    origem é "recipe" ou "repo" ou "?".
    """
    if name in recipes:
        return (recipes[name].version, "recipe")
    repo_pkg = find_package_in_repos(name, cfg.db_dir, cfg.repos_config)
    if repo_pkg is not None:
        return (repo_pkg.version, "repo")
    return ("0", None)


def _is_outdated(installed_ver: str, available_ver: str) -> bool:
    """True se available_ver > installed_ver (comparação robusta)."""
    if not installed_ver or not available_ver:
        return installed_ver != available_ver
    try:
        return compare(available_ver, installed_ver) > 0
    except VersionError:
        # Fallback: simples diferença de strings
        return installed_ver != available_ver


def find_upgrades(db: dict, recipes: dict, cfg) -> list[tuple[str, str, str, str | None]]:
    """Retorna lista de pacotes desatualizados.

    Returns:
        Lista de tuples (name, installed_version, available_version, source).
        Ordenada por nome.
    """
    outdated: list[tuple[str, str, str, str | None]] = []
    for name in sorted(db["packages"]):
        info = db["packages"][name]
        inst_ver = info["version"]
        avail_ver, source = _latest_available(name, recipes, cfg)
        if _is_outdated(inst_ver, avail_ver):
            outdated.append((name, inst_ver, avail_ver, source))
    return outdated


# ---------------------------------------------------------------------------
# list-upgrades
# ---------------------------------------------------------------------------

def cmd_list_upgrades(args, recipes, pmap, cfg) -> int:
    """Lista pacotes desatualizados sem instalar nada."""
    db = dbmod.load_db(cfg.db_dir)
    outdated = find_upgrades(db, recipes, cfg)

    if not outdated:
        print(c("Sistema atualizado — nenhum pacote desatualizado.", "green"))
        return 0

    print(c(f"Pacotes desatualizados ({len(outdated)}):", "bold"))
    print(f"  {'Pacote':<24} {'Instalado':<14} → {'Disponível':<14} {'Origem'}")
    print(f"  {'─' * 24} {'─' * 14}   {'─' * 14} {'─' * 8}")
    for name, inst, avail, src in outdated:
        src_label = c(src or "?", "cyan" if src == "repo" else "dim")
        print(f"  {name:<24} {c(inst, 'yellow'):<14} → {c(avail, 'bright_green'):<14} {src_label}")

    print(c(f"\n  Execute " + c("thorn upgrade", "cyan") + " para atualizar todos.", "dim"))
    return 0


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def cmd_upgrade(args, recipes, pmap, cfg) -> int:
    """Atualiza todos os pacotes instalados (ou apenas um, se especificado).

    Fluxo:
      1. Verifica versões instaladas vs disponíveis
      2. Se args.packages: apenas esses pacotes (e suas deps)
      3. Senão: todos os pacotes desatualizados
      4. Resolve ordem de instalação
      5. Executa transação (com rollback em caso de erro, se --atomic)
    """
    db = dbmod.load_db(cfg.db_dir)

    # Determina a lista de pacotes a atualizar
    if args.packages:
        from ..version import dep_name
        targets = []
        for p in args.packages:
            try:
                base = dep_name(p)
            except VersionError:
                base = p
            real = pmap.get(base, base)
            if not dbmod.is_installed(db, real):
                # Se não está instalado, faz instalação normal via install
                warn(f"'{real}' não está instalado — será instalado (não atualizado)")
            targets.append(real)
    else:
        outdated = find_upgrades(db, recipes, cfg)
        targets = [name for name, _, _, _ in outdated]

    if not targets:
        print(c("Nada a atualizar — sistema está em dia.", "green"))
        return 0

    print(c(f"Pacotes a atualizar ({len(targets)}):", "bold"))
    for name in targets:
        if dbmod.is_installed(db, name):
            inst_ver = db["packages"][name]["version"]
            avail_ver, _ = _latest_available(name, recipes, cfg)
            print(f"  {name:<24} {c(inst_ver, 'yellow')} → {c(avail_ver, 'bright_green')}")
        else:
            print(f"  {name:<24} {c('(novo)', 'cyan')}")

    if args.dry_run:
        print(c("\n[dry-run] nenhum pacote foi atualizado.", "yellow"))
        return 0

    # Resolve ordem de instalação (incluindo deps transitivas)
    inst = set(db["packages"])
    inst_ver = build_installed_versions(db)

    # Cria args-like para resolve_install_order
    class _Args:
        packages = targets
    order = resolve_install_order(_Args, recipes, pmap, inst, cfg, inst_ver)
    if order is None:
        return 1

    # Filtra apenas os que precisam de atualização (ou não estão instalados)
    todo: list[str] = []
    for name in order:
        if not dbmod.is_installed(db, name):
            todo.append(name)
            continue
        inst_v = db["packages"][name]["version"]
        avail_v, _ = _latest_available(name, recipes, cfg)
        if _is_outdated(inst_v, avail_v) or args.reinstall:
            todo.append(name)

    if not todo:
        print(c("Nada a fazer — todos os pacotes já estão na versão mais recente.", "green"))
        return 0

    print(c(f"\nResolvido: {len(todo)} pacote(s) para processar.", "dim"))

    # Executa transação
    if args.atomic:
        return _upgrade_atomic(args, todo, recipes, cfg, db)
    return _upgrade_normal(args, todo, recipes, cfg, db)


def _upgrade_normal(args, todo, recipes, cfg, db) -> int:
    """Atualiza pacotes um a um, gravando no banco após cada um."""
    from ..fileconflict import build_file_index, assert_no_conflicts
    file_index = build_file_index(db)
    allow_overwrite = getattr(args, 'force_overwrite', False)

    success_count = 0
    for i, name in enumerate(todo, 1):
        reason = db["packages"].get(name, {}).get(
            "reason", dbmod.REASON_EXPLICIT
        )
        try:
            manifest, checksums = install_one_package(
                name, recipes, cfg, args,
                current=i, total=len(todo),
                allow_overwrite=allow_overwrite,
            )
        except FileConflictError as e:
            print(ce(str(e), "red"))
            return 1
        except (bld.BuildError, HookError, DownloadError) as e:
            print(ce(f"\n✗ falha ao atualizar '{name}': {e}", "red"))
            remaining = len(todo) - i
            if remaining:
                warn(f"  ({remaining} pacote(s) restantes não processados)")
            return 1

        # Verifica conflitos
        try:
            assert_no_conflicts(
                manifest, db, name,
                index=file_index,
                allow_overwrite=allow_overwrite,
            )
        except FileConflictError as e:
            print(ce(str(e), "red"))
            print(ce(f"  revertendo instalação de '{name}'…", "yellow"))
            bld.remove_installed_files(cfg.root_dir, manifest)
            return 1

        # Remove arquivos obsoletos da versão anterior
        cleanup_obsolete_files(name, manifest, db, cfg)

        # Atualiza índice
        for f in manifest:
            file_index[f] = name

        # Registra no banco
        recipe = recipes.get(name)
        if recipe:
            dbmod.record_install(db, recipe, manifest, reason=reason)
        else:
            from .install import _record_repo_install
            _record_repo_install(db, name, cfg, manifest, reason)

        dbmod.save_checksums(cfg.db_dir, name, checksums)
        dbmod.save_db(cfg.db_dir, db)
        success_count += 1

    print(c(f"\n✓ {success_count} pacote(s) atualizado(s).", "bright_green"))
    return 0


def _upgrade_atomic(args, todo, recipes, cfg, db) -> int:
    """Atualiza todos atomicamente. Em caso de erro, faz rollback."""
    journal = dbmod.TransactionJournal(cfg.db_dir)
    collected: list[tuple] = []
    from ..fileconflict import build_file_index, assert_no_conflicts
    file_index = build_file_index(db)
    allow_overwrite = getattr(args, 'force_overwrite', False)

    print(c(f"\n[atomic] transação de upgrade iniciada para {len(todo)} pacote(s)", "dim"))

    try:
        for i, name in enumerate(todo, 1):
            reason = db["packages"].get(name, {}).get(
                "reason", dbmod.REASON_EXPLICIT
            )
            manifest, checksums = install_one_package(
                name, recipes, cfg, args,
                current=i, total=len(todo),
                allow_overwrite=allow_overwrite,
            )

            # Verifica conflitos
            try:
                assert_no_conflicts(
                    manifest, db, name,
                    index=file_index,
                    allow_overwrite=allow_overwrite,
                )
            except FileConflictError as e:
                raise bld.BuildError(str(e)) from e

            # Remove arquivos obsoletos da versão anterior
            cleanup_obsolete_files(name, manifest, db, cfg)

            for f in manifest:
                file_index[f] = name

            version = db["packages"].get(name, {}).get("version", "0")
            journal.record(name, version, reason, manifest, checksums)
            collected.append((name, version, manifest, checksums, reason))

    except (bld.BuildError, HookError, DownloadError, FileConflictError) as e:
        print(ce(f"\n✗ falha em transação atômica: {e}", "red"))
        print(ce("  Revertendo arquivos instalados nesta transação…", "yellow"))
        rolled = journal.rollback(cfg.root_dir)
        if rolled:
            print(ce(f"  Rollback: {', '.join(rolled)} removidos do root.", "dim"))
        else:
            print(ce("  Nenhum arquivo para reverter.", "dim"))
        print(ce("  Banco de dados não foi modificado.", "green"))
        return 1

    # Commit
    print(c("\n[atomic] commit — gravando banco de dados…", "dim"))
    for name, version, manifest, checksums, reason in collected:
        recipe = recipes.get(name)
        if recipe:
            dbmod.record_install(db, recipe, manifest, reason=reason)
        else:
            from .install import _record_repo_install
            _record_repo_install(db, name, cfg, manifest, reason)
        dbmod.save_checksums(cfg.db_dir, name, checksums)
    dbmod.save_db(cfg.db_dir, db)
    journal.clear()

    print(c(f"✓ {len(todo)} pacote(s) atualizados atomicamente.", "bright_green"))
    return 0
