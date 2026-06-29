# * Comando `thorn install` — resolve dependências, compila/baixa e instala.
# * Suporta: --reinstall, --keep-build, --dry-run, --atomic, --prefer-binary,
# *   --prefer-source, --force-overwrite (sobrescreve arquivos de outros pacotes).
# * Realiza detecção de conflitos de arquivos (fileconflict.py) antes de instalar.
# * Arquivo: thornspkg/commands/install.py

"""Comando `thorn install`."""

from __future__ import annotations

from .. import builder as bld
from .. import db as dbmod
from ..colors import c, ce
from ..downloader import DownloadError
from ..fileconflict import FileConflictError, build_file_index
from ..hooks import HookError
from ..recipe import Recipe
from ..repo import find_package_in_repos, resolve_package_url
from ..version import VersionError, dep_name
from .common import (
    build_one,
    build_installed_versions,
    cleanup_obsolete_files,
    err,
    get_package_type,
    get_repo_url,
    install_binary_one,
    install_one_package,
    resolve_install_order,
    run_install_hooks,
    warn,
)


def cmd_install(args, recipes, pmap, cfg) -> int:
    """Resolve dependências, compila e instala."""
    db = dbmod.load_db(cfg.db_dir)
    inst = set(db["packages"])
    inst_ver = build_installed_versions(db)

    # Avisa sobre transação pendente antes de qualquer coisa
    if dbmod.TransactionJournal.is_pending(cfg.db_dir):
        tx_path = cfg.db_dir / "transaction.json"
        warn(
            f"journal de transação incompleto encontrado: {tx_path}\n"
            "  Isso indica que uma instalação atômica anterior foi interrompida.\n"
            "  Execute 'thorn recover-tx' para desfazê-la, ou delete o arquivo "
            "manualmente se já foi resolvida."
        )
        if args.atomic:
            print(ce("  Não é possível iniciar nova transação com journal pendente.", "red"))
            return 1

    # Resolve dependências
    order, all_recipes = resolve_install_order(args, recipes, pmap, inst, cfg, inst_ver)
    if order is None:
        return 1
    # Usa all_recipes (inclui receitas remotas baixadas durante resolução)
    # em vez do dict `recipes` original (que só tem receitas locais)
    recipes = all_recipes

    # Determina quais são explícitos (argumentos diretos) vs dependências
    explicit_set: set[str] = set()
    for n in args.packages:
        try:
            explicit_set.add(pmap.get(dep_name(n), dep_name(n)))
        except VersionError:
            explicit_set.add(pmap.get(n, n))

    todo = [n for n in order if args.reinstall or n not in inst]

    if not todo:
        print(c("Nada a fazer — tudo já está instalado.", "green"))
        return 0

    print(c(f"Pacotes a instalar ({len(todo)}):", "bold"))
    for n in todo:
        marker = "" if n in explicit_set else c(" [dep]", "dim")
        pkg_type = get_package_type(n, recipes, cfg)
        type_marker = ""
        if pkg_type == "binary":
            type_marker = c(" [binário]", "cyan")
        elif pkg_type == "recipe-remote":
            type_marker = c(" [receita remota]", "magenta")
        print(f"  {c(n, 'cyan')}{marker}{type_marker}")

    if args.dry_run:
        print(c("\n[dry-run] nenhum pacote foi instalado.", "yellow"))
        return 0

    if args.atomic:
        return _install_atomic(args, todo, explicit_set, recipes, cfg, db)
    return _install_normal(args, todo, explicit_set, recipes, cfg, db)


# ---------------------------------------------------------------------------
# instalação normal (não-atômica)
# ---------------------------------------------------------------------------

def _install_normal(args, todo, explicit_set, recipes, cfg, db) -> int:
    """Instala pacotes um a um, gravando no banco após cada um."""
    # Constrói índice de ownership uma vez para checagem de conflitos
    file_index = build_file_index(db)
    allow_overwrite = getattr(args, 'force_overwrite', False)

    for i, name in enumerate(todo, 1):
        reason = (
            dbmod.REASON_EXPLICIT
            if name in explicit_set
            else dbmod.REASON_DEPENDENCY
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
            print(ce(f"\n✗ falha em '{name}': {e}", "red"))
            remaining = len(todo) - i
            if remaining:
                print(ce(f"  ({remaining} pacote(s) não processados)", "dim"))
            return 1

        # Verifica conflitos APÓS obter o manifest (mas antes de gravar no DB)
        try:
            from ..fileconflict import assert_no_conflicts
            assert_no_conflicts(
                manifest, db, name,
                index=file_index,
                allow_overwrite=allow_overwrite,
            )
        except FileConflictError as e:
            # Rollback: remove os arquivos que acabamos de instalar
            print(ce(str(e), "red"))
            print(ce(f"  revertendo instalação de '{name}'…", "yellow"))
            bld.remove_installed_files(cfg.root_dir, manifest)
            return 1

        # Se o pacote já estava instalado (reinstall/upgrade), remove arquivos
        # que existiam na versão antiga mas não constam no novo manifest.
        cleanup_obsolete_files(name, manifest, db, cfg)

        # Atualiza índice com os arquivos do novo pacote
        for f in manifest:
            file_index[f] = name

        # Registra no banco
        recipe = recipes.get(name)
        if recipe:
            dbmod.record_install(db, recipe, manifest, reason=reason)
        else:
            # Pacote de repositório sem receita local — registro manual
            _record_repo_install(db, name, cfg, manifest, reason)

        dbmod.save_checksums(cfg.db_dir, name, checksums)
        dbmod.save_db(cfg.db_dir, db)

    print(c(f"\n✓ {len(todo)} pacote(s) instalado(s).", "bright_green"))
    return 0


def _install_atomic(args, todo, explicit_set, recipes, cfg, db) -> int:
    """Instala todos os pacotes e só grava o banco se TODOS tiverem sucesso."""
    journal = dbmod.TransactionJournal(cfg.db_dir)
    collected: list[tuple] = []
    file_index = build_file_index(db)
    allow_overwrite = getattr(args, 'force_overwrite', False)

    print(c(f"\n[atomic] transação iniciada para {len(todo)} pacote(s)", "dim"))

    try:
        for i, name in enumerate(todo, 1):
            reason = (
                dbmod.REASON_EXPLICIT
                if name in explicit_set
                else dbmod.REASON_DEPENDENCY
            )
            manifest, checksums = install_one_package(
                name, recipes, cfg, args,
                current=i, total=len(todo),
                allow_overwrite=allow_overwrite,
            )

            # Verifica conflitos APÓS instalar
            try:
                from ..fileconflict import assert_no_conflicts
                assert_no_conflicts(
                    manifest, db, name,
                    index=file_index,
                    allow_overwrite=allow_overwrite,
                )
            except FileConflictError as e:
                raise bld.BuildError(str(e)) from e

            # Remove arquivos obsoletos da versão anterior (se reinstalando)
            cleanup_obsolete_files(name, manifest, db, cfg)

            # Atualiza índice
            for f in manifest:
                file_index[f] = name

            version = _get_package_version(name, cfg)
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

    # ── commit: grava todos os registros no banco de uma vez ──
    print(c("\n[atomic] commit — gravando banco de dados…", "dim"))

    for name, version, manifest, checksums, reason in collected:
        recipe = recipes.get(name)
        if recipe:
            dbmod.record_install(db, recipe, manifest, reason=reason)
        else:
            _record_repo_install(db, name, cfg, manifest, reason)
        dbmod.save_checksums(cfg.db_dir, name, checksums)
    dbmod.save_db(cfg.db_dir, db)
    journal.clear()

    print(c(f"✓ {len(todo)} pacote(s) instalados atomicamente.", "bright_green"))
    return 0


def _get_package_version(name: str, cfg) -> str:
    """Obtém a versão de um pacote (receita local ou repositório)."""
    repo_pkg = find_package_in_repos(name, cfg.db_dir, cfg.repos_config)
    if repo_pkg is not None:
        return repo_pkg.version
    return "0"


def _record_repo_install(db: dict, name: str, cfg, manifest: list[str], reason: str) -> None:
    """Registra um pacote de repositório (sem receita local) no banco."""
    repo_pkg = find_package_in_repos(name, cfg.db_dir, cfg.repos_config)
    now = dbmod._now()
    db["packages"][name] = {
        "version":       repo_pkg.version if repo_pkg else "0",
        "depends":       repo_pkg.depends if repo_pkg else [],
        "optional_deps": [],
        "provides":      [],
        "reason":        reason,
        "files":         manifest,
        "installed_at":  db["packages"].get(name, {}).get("installed_at") or now,
        "install_date":  db["packages"].get(name, {}).get("install_date") or now,
        "updated_at":    now,
        "checked_at":    None,
        # Metadados (v0.4+)
        "description":   db["packages"].get(name, {}).get("description", ""),
        "homepage":      db["packages"].get(name, {}).get("homepage"),
        "license":       db["packages"].get(name, {}).get("license"),
        "maintainer":    db["packages"].get(name, {}).get("maintainer"),
        "repository":    db["packages"].get(name, {}).get("repository"),
        "architecture":  db["packages"].get(name, {}).get("architecture"),
        "build_date":    db["packages"].get(name, {}).get("build_date"),
        "install_size":  db["packages"].get(name, {}).get("install_size"),
        "download_size": db["packages"].get(name, {}).get("download_size"),
    }
