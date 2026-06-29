# * Comandos `thorn search`, `thorn list`, `thorn why`, `thorn info`, `thorn outdated`.
# * search: busca receitas e pacotes de repositório por nome/descrição.
# * list:   lista pacotes instalados.
# * why:    mostra quem depende de um pacote.
# * info:   exibe metadados detalhados de um pacote (com novos campos v0.4+).
# * outdated: lista pacotes com versão instalada diferente da receita/repositório.
# * Arquivo: thornspkg/commands/search.py

"""Comandos de consulta: search, list, why, info, outdated."""

from __future__ import annotations

from .. import builder as bld
from .. import db as dbmod
from ..colors import c, ce
from ..depgraph import reverse_deps
from ..recipe import Recipe
from ..repo import find_package_in_repos, load_all_cached_indexes
from ..version import VersionError, dep_name, parse_constraint, satisfies
from .common import err


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def cmd_search(args, recipes, pmap, cfg) -> int:
    """Busca receitas pelo nome ou descrição."""
    pattern = args.pattern.lower()
    db = dbmod.load_db(cfg.db_dir)
    matches = [
        (n, r) for n, r in sorted(recipes.items())
        if pattern in n.lower() or pattern in r.description.lower()
    ]

    repo_indexes = load_all_cached_indexes(cfg.db_dir, cfg.repos_config)
    repo_matches = [
        (n, p) for n, p in sorted(repo_indexes.items())
        if pattern in n.lower()
    ]

    if not matches and not repo_matches:
        print("Nenhum resultado.")
        return 0

    for n, r in matches:
        inst = dbmod.is_installed(db, n)
        reason = dbmod.reason_of(db, n) if inst else None
        tag_i = c("I", "green") if inst else c("-", "dim")
        tag_d = c("D", "dim") if reason == dbmod.REASON_DEPENDENCY else " "
        desc = f"  {c(r.description, 'dim')}" if r.description else ""
        deps = f"  deps: {', '.join(r.depends)}" if r.depends else ""
        print(f"  [{tag_i}{tag_d}]  {n:<22} {r.version:<12}{desc}{deps}")

    for n, p in repo_matches:
        if n in dict(matches):
            continue
        inst = dbmod.is_installed(db, n)
        tag_i = c("I", "green") if inst else c("-", "dim")
        tag_r = c("R", "cyan") if p.pkg_type == "recipe" else c("B", "blue")
        desc = f"  {c('repositório', 'dim')}"
        print(f"  [{tag_i} {tag_r}]  {n:<22} {p.version:<12}{desc}")

    print(c("\n  [I] = instalado  [D] = como dependência  [B] = binário  [R] = receita remota", "dim"))
    return 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def cmd_list(args, recipes, pmap, cfg) -> int:
    """Lista pacotes instalados."""
    db = dbmod.load_db(cfg.db_dir)
    if not db["packages"]:
        print("Nenhum pacote instalado.")
        return 0

    pkgs = sorted(db["packages"])
    name_w = max(len(n) for n in pkgs)

    for name in pkgs:
        info = db["packages"][name]
        ver_i = info["version"]
        ver_r = recipes[name].version if name in recipes else None
        outdated = ver_r and ver_r != ver_i

        ver_col = c(f"{ver_i:<7}", "yellow") + c(f" → {ver_r}", "bright_yellow") \
            if outdated else f"{ver_i:<14}"

        reason = info.get("reason", dbmod.REASON_EXPLICIT)
        dep_marker = c(" [D]", "dim") if reason == dbmod.REASON_DEPENDENCY else ""
        provides_col = c(f"  provides: {', '.join(info['provides'])}", "dim") \
            if info.get("provides") else ""

        print(f"  {name:<{name_w}}  {ver_col}  {len(info['files'])} arq.{dep_marker}{provides_col}")

    has_dep = any(
        info.get("reason") == dbmod.REASON_DEPENDENCY
        for info in db["packages"].values()
    )
    if has_dep:
        print(c("\n  [D] = instalado automaticamente como dependência", "dim"))
    return 0


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------

def cmd_info(args, recipes, pmap, cfg) -> int:
    """Exibe informações detalhadas de um pacote (incluindo novos metadados)."""
    db = dbmod.load_db(cfg.db_dir)
    try:
        name = pmap.get(dep_name(args.package), dep_name(args.package))
    except VersionError:
        name = pmap.get(args.package, args.package)

    in_recipe = name in recipes
    installed = dbmod.is_installed(db, name)
    repo_pkg = find_package_in_repos(name, cfg.db_dir, cfg.repos_config)

    if not in_recipe and not installed and repo_pkg is None:
        return err(f"pacote desconhecido: '{args.package}'")

    # Cabeçalho
    display_name = name
    display_version = "?"
    if in_recipe:
        display_version = recipes[name].version
    elif repo_pkg is not None:
        display_version = repo_pkg.version
    elif installed:
        display_version = db["packages"][name]["version"]

    print(c(f"{display_name}  {display_version}", "bold"))

    # Metadados da receita local
    if in_recipe:
        r = recipes[name]
        if r.description:
            print(f"  Descrição:      {r.description}")
        if r.homepage:
            print(f"  Homepage:       {r.homepage}")
        if r.license:
            print(f"  Licença:        {r.license}")
        if r.maintainer:
            print(f"  Mantenedor:     {r.maintainer}")
        if r.repository:
            print(f"  Repositório:    {r.repository}")
        if r.architecture:
            print(f"  Arquitetura:    {r.architecture}")
        if r.build_date:
            print(f"  Build date:     {r.build_date}")
        if r.install_size is not None:
            print(f"  Tam. Instalado: {_human_size(r.install_size)}")
        if r.download_size is not None:
            print(f"  Tam. Download:  {_human_size(r.download_size)}")
        print(f"  Receita:        {r.path}")
        print(f"  Build system:   {r.build_system}")
        if r.sources:
            print(f"  Source(s):")
            for url in r.sources:
                print(f"    {url}")
        print(f"  Depends:        {', '.join(r.depends) or '(nenhuma)'}")
        if r.optional_deps:
            print(f"  Optional deps:  {', '.join(r.optional_deps)}")
        if r.provides:
            print(f"  Provides:       {', '.join(r.provides)}")
        if r.patches:
            print(f"  Patches:        {', '.join(r.patches)}")
        if r.env:
            print(f"  Env:            " + "  ".join(f"{k}={v}" for k, v in r.env.items()))
        if r.post_install:
            print(f"  Post-install:   {'; '.join(r.post_install)}")

    # Info do repositório
    if repo_pkg is not None:
        print()
        print(c("  [repositório]", "cyan"))
        print(f"  Tipo:           {repo_pkg.pkg_type}")
        print(f"  Versão (repo):  {repo_pkg.version}")
        if repo_pkg.pkg_type == "binary":
            print(f"  URL:            {repo_pkg.url}")
            if repo_pkg.sha256:
                print(f"  SHA256:         {repo_pkg.sha256}")
        elif repo_pkg.pkg_type == "recipe":
            print(f"  Recipe:         {repo_pkg.recipe}")
        if repo_pkg.depends:
            print(f"  Depends (repo): {', '.join(repo_pkg.depends)}")

    # Info do banco (instalado)
    if installed:
        info = db["packages"][name]
        reason = info.get("reason", dbmod.REASON_EXPLICIT)
        reason_str = c("explícito", "green") if reason == dbmod.REASON_EXPLICIT \
            else c("dependência (automático)", "dim")
        print()
        print(c("  [instalado]", "bright_green"))
        print(f"  Versão:         {info['version']}")
        print(f"  Razão:          {reason_str}")
        print(f"  Arquivos:       {len(info['files'])}")
        print(f"  Instalado em:   {info.get('installed_at', '?')}")
        if info.get("updated_at") and info["updated_at"] != info.get("installed_at"):
            print(f"  Atualizado em:  {info['updated_at']}")
        if info.get("checked_at"):
            print(f"  Verificado em:  {info['checked_at']}")

        # Metadados extras do DB
        meta = dbmod.get_metadata(db, name)
        if meta.get("install_size") is not None:
            print(f"  Tam. instalado: {_human_size(meta['install_size'])}")
        if meta.get("download_size") is not None:
            print(f"  Tam. download:  {_human_size(meta['download_size'])}")
        if meta.get("architecture"):
            print(f"  Arquitetura:    {meta['architecture']}")
        if meta.get("license"):
            print(f"  Licença:        {meta['license']}")
        if meta.get("repository"):
            print(f"  Repositório:    {meta['repository']}")
        if meta.get("build_date"):
            print(f"  Build date:     {meta['build_date']}")

        dependents = dbmod.find_dependents(db, name)
        if dependents:
            print(f"  Requerido por:  {', '.join(dependents)}")
    else:
        print(c("\n  [não instalado]", "dim"))

    return 0


def _human_size(n: int) -> str:
    """Converte bytes para forma legível."""
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ---------------------------------------------------------------------------
# why
# ---------------------------------------------------------------------------

def cmd_why(args, recipes, pmap, cfg) -> int:
    """Mostra quais receitas dependem de um pacote."""
    try:
        name = dep_name(args.package)
    except VersionError:
        name = args.package
    name = pmap.get(name, name)

    if name not in recipes:
        return err(f"pacote desconhecido: '{name}'")

    rdeps = reverse_deps(recipes, name, pmap)
    if not rdeps:
        print(f"Nenhuma receita depende de '{name}'.")
        return 0

    print(c(f"'{name}' é requerido por:", "bold"))
    db = dbmod.load_db(cfg.db_dir)
    for dep in rdeps:
        inst = dbmod.is_installed(db, dep)
        tag = c("instalado", "green") if inst else c("não instalado", "dim")
        print(f"  {dep:<24} [{tag}]")
    return 0


# ---------------------------------------------------------------------------
# outdated
# ---------------------------------------------------------------------------

def cmd_outdated(args, recipes, pmap, cfg) -> int:
    """Pacotes com versão instalada diferente da receita."""
    db = dbmod.load_db(cfg.db_dir)
    found = False
    for name in sorted(db["packages"]):
        info = db["packages"][name]
        if name in recipes:
            ver_r = recipes[name].version
        else:
            # Tenta repositório
            repo_pkg = find_package_in_repos(name, cfg.db_dir, cfg.repos_config)
            if repo_pkg is None:
                continue
            ver_r = repo_pkg.version

        ver_i = info["version"]
        if ver_i != ver_r:
            found = True
            reason = info.get("reason", dbmod.REASON_EXPLICIT)
            tag = c(" [D]", "dim") if reason == dbmod.REASON_DEPENDENCY else ""
            print(f"  {name:<24}{tag}  instalado: {c(ver_i,'yellow'):<20}  disponível: {c(ver_r,'bright_green')}")

    if not found:
        print(c("Todos os pacotes estão na versão mais recente.", "green"))
    return 0
