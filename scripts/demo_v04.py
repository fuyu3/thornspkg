#!/usr/bin/env python3
# * Demostra todos os novos recursos do thornspkg v0.4+.
# * Executa uma série de cenários contra um banco de teste, sem precisar
# *   compilar nada de verdade. Útil como smoke test de integração.
# * Uso: python /home/z/my-project/download/thornspkg/scripts/demo_v04.py
# * Arquivo: scripts/demo_v04.py

"""Demostra os novos recursos do thornspkg v0.4+."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main() -> int:
    # Adiciona o diretório do projeto ao PYTHONPATH
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    from thornspkg.version import (
        compare, parse_constraint, satisfies, VersionError,
    )
    from thornspkg.fileconflict import (
        FileConflictError, assert_no_conflicts, build_file_index, find_owner,
    )
    from thornspkg.cache import cache_stats, cache_clean, cache_list
    from thornspkg.db import (
        migrate_db, record_install, find_dependents, find_owner_of_file,
        get_metadata, load_db, save_db,
    )
    from thornspkg.depgraph import (
        resolve_order, VersionConflictError,
    )
    from thornspkg.recipe import Recipe
    from thornspkg.config import Config

    print("=" * 70)
    print("DEMONSTRAÇÃO DOS NOVOS RECURSOS DO thornspkg v0.4+")
    print("=" * 70)

    # 1. Dependências com versão
    print("\n[1] Dependências com operadores de versão")
    print("-" * 70)
    print(f"  satisfies('openssl', '3.5', 'openssl>=3.0') = {satisfies('openssl', '3.5', 'openssl>=3.0')}")
    print(f"  satisfies('python', '3.15', 'python<3.15')  = {satisfies('python', '3.15', 'python<3.15')}")
    print(f"  satisfies('curl', '8.9.1', 'curl=8.9.1')    = {satisfies('curl', '8.9.1', 'curl=8.9.1')}")
    print(f"  satisfies('curl', '8.9.2', 'curl!=8.9.1')   = {satisfies('curl', '8.9.2', 'curl!=8.9.1')}")
    print(f"  compare('3.12.4', '3.12.10')                = {compare('3.12.4', '3.12.10')} (<0 = older)")
    print(f"  compare('1:5.0', '5.0')                     = {compare('1:5.0', '5.0')} (>0 = epoch wins)")
    print(f"  compare('1.2.3rc1', '1.2.3beta1')           = {compare('1.2.3rc1', '1.2.3beta1')} (>0 = rc>beta)")

    # 2. Resolvedor com constraints
    print("\n[2] Resolvedor de dependências com versionamento")
    print("-" * 70)
    recipes = {
        "zlib": Recipe(name="zlib", version="1.3.1", path=Path("zlib.toml")),
        "openssl": Recipe(name="openssl", version="3.3.1", path=Path("openssl.toml"),
                          depends=["zlib>=1.0"]),
        "python": Recipe(name="python", version="3.12.4", path=Path("python.toml"),
                         depends=["openssl>=3.0", "zlib"]),
    }
    order = resolve_order(recipes, ["python"], installed_versions={})
    print(f"  Ordem para instalar python: {order}")

    try:
        resolve_order(recipes, ["python"],
                      installed={"zlib"},
                      installed_versions={"zlib": "0.5"})
    except VersionConflictError as e:
        print(f"  OK VersionConflictError disparado corretamente: {e}")

    # 3. Conflito de arquivos
    print("\n[3] Detecção de conflito de arquivos")
    print("-" * 70)
    db = {"packages": {
        "vim": {"files": ["usr/bin/vim", "usr/share/vim/vimrc"], "version": "9.1"},
        "curl": {"files": ["usr/bin/curl"], "version": "8.9.1"},
    }}
    print(f"  find_owner(db, 'usr/bin/vim')  = {find_owner(db, 'usr/bin/vim')!r}")
    print(f"  find_owner(db, 'usr/bin/foo')  = {find_owner(db, 'usr/bin/foo')!r}")

    manifest = ["usr/bin/vim", "usr/bin/new-tool"]
    try:
        assert_no_conflicts(manifest, db, "my-vim")
    except FileConflictError as e:
        print(f"  OK FileConflictError disparado:")
        for line in str(e).split("\n"):
            print(f"    {line}")

    # 4. Migração de banco antigo
    print("\n[4] Migração automática de banco antigo")
    print("-" * 70)
    old_db = {"packages": {
        "vim": {
            "version": "9.1", "depends": [], "reason": "explicit",
            "files": ["usr/bin/vim"],
            "installed_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "checked_at": None,
        }
    }}
    print(f"  Antes da migração, campos em 'vim': {len(old_db['packages']['vim'])}")
    migrate_db(old_db)
    new_fields = ["install_date", "description", "homepage", "license",
                  "maintainer", "repository", "architecture", "build_date",
                  "install_size", "download_size"]
    print(f"  Depois da migração, campos em 'vim': {len(old_db['packages']['vim'])}")
    print(f"  Novos campos presentes: {all(f in old_db['packages']['vim'] for f in new_fields)}")

    # 5. Metadados expandidos
    print("\n[5] Metadados expandidos via record_install")
    print("-" * 70)
    recipe = Recipe(
        name="vim", version="9.1", path=Path("vim.toml"),
        description="Vi Improved",
        homepage="https://www.vim.org/",
        license="Vim",
        maintainer="Bram Moolenaar <bram@vim.org>",
        repository="core",
        architecture="x86_64",
        install_size=44040192,
        download_size=11000000,
    )
    record_install(old_db, recipe, ["usr/bin/vim"], reason="explicit")
    meta = get_metadata(old_db, "vim")
    print(f"  Descrição:    {meta['description']}")
    print(f"  Homepage:     {meta['homepage']}")
    print(f"  Licença:      {meta['license']}")
    print(f"  Mantenedor:   {meta['maintainer']}")
    print(f"  Repositório:  {meta['repository']}")
    print(f"  Arquitetura:  {meta['architecture']}")
    print(f"  Tam. instal.: {meta['install_size']} bytes")
    print(f"  Tam. download:{meta['download_size']} bytes")

    # 6. Cache persistente
    print("\n[6] Cache persistente de downloads")
    print("-" * 70)
    with tempfile.TemporaryDirectory() as td:
        cache_root = Path(td) / "cache"
        cfg = Config(
            recipes_dir=Path(td) / "recipes",
            patches_dir=Path(td) / "patches",
            sources_dir=cache_root / "sources",
            build_dir=Path(td) / "build",
            db_dir=Path(td) / "db",
            root_dir=Path(td) / "root",
            prefix="/usr",
            hooks_dir=Path(td) / "hooks",
            jobs=1,
        )
        from thornspkg.cache import sources_cache_dir, packages_cache_dir, indexes_cache_dir
        for d in [sources_cache_dir(cfg), packages_cache_dir(cfg), indexes_cache_dir(cfg)]:
            d.mkdir(parents=True, exist_ok=True)
        (sources_cache_dir(cfg) / "openssl-3.3.1.tar.gz").write_bytes(b"x" * 100000)
        (packages_cache_dir(cfg) / "vim-9.1-x86_64.tar.zst").write_bytes(b"x" * 50000)
        (indexes_cache_dir(cfg) / "core.json").write_text("{}")

        stats = cache_stats(cfg)
        print(f"  sources/   {stats.sources_count} arquivo(s)   {stats.human_size(stats.sources_size)}")
        print(f"  packages/  {stats.packages_count} arquivo(s)   {stats.human_size(stats.packages_size)}")
        print(f"  indexes/   {stats.indexes_count} arquivo(s)   {stats.human_size(stats.indexes_size)}")
        print(f"  total      {stats.total_count} arquivo(s)   {stats.human_size(stats.total_size)}")

        removed = cache_clean(cfg, sources=True, packages=True, indexes=False)
        stats2 = cache_stats(cfg)
        print(f"\n  Apos cache_clean (sem indexes): removidos {removed} arquivos")
        print(f"  indexes/ preservado: {stats2.indexes_count} arquivo(s)")

    # 7. find_dependents com versionamento
    print("\n[7] find_dependents com depends versionadas")
    print("-" * 70)
    db2 = {"packages": {
        "openssl": {"version": "3.3.1", "depends": [], "reason": "explicit", "files": []},
        "curl": {"version": "8.9.1", "depends": ["openssl>=3.0"], "reason": "explicit", "files": []},
        "git": {"version": "2.45", "depends": ["openssl>=3.0", "curl"], "reason": "explicit", "files": []},
    }}
    deps = find_dependents(db2, "openssl")
    print(f"  Pacotes que dependem de 'openssl' (com constraints): {deps}")

    # 8. find_owner_of_file
    print("\n[8] find_owner_of_file (equivalente a 'thorn owns')")
    print("-" * 70)
    db3 = {"packages": {
        "vim": {"files": ["usr/bin/vim", "usr/share/vim/vimrc"], "version": "9.1"},
        "bash": {"files": ["usr/bin/bash", "usr/bin/sh"], "version": "5.2"},
    }}
    print(f"  Owner de 'usr/bin/vim'  = {find_owner_of_file(db3, 'usr/bin/vim')!r}")
    print(f"  Owner de 'usr/bin/sh'   = {find_owner_of_file(db3, 'usr/bin/sh')!r}")
    print(f"  Owner de 'usr/bin/foo'  = {find_owner_of_file(db3, 'usr/bin/foo')!r}")

    print("\n" + "=" * 70)
    print("DEMONSTRACAO CONCLUIDA COM SUCESSO")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
