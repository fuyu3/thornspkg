# * Comandos de inspeção: `thorn files`, `thorn owns`, `thorn deps`, `thorn tree`,
# *   `thorn log`, `thorn fetch`, `thorn suggest-deps`.
# * files:        lista arquivos instalados de um pacote.
# * owns:         descobre qual pacote possui um arquivo.
# * deps:         mostra ordem de build/instalação (sem instalar).
# * tree:         exibe árvore ASCII de dependências.
# * log:          exibe log de build de um pacote.
# * fetch:        baixa sources sem compilar.
# * suggest-deps: sugere dependências analisando o source.
# * Arquivo: thornspkg/commands/inspect.py

"""Comandos de inspeção: files, owns, deps, tree, log, fetch, suggest-deps."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .. import builder as bld
from .. import db as dbmod
from ..colors import c
from ..depgraph import dep_tree_lines
from ..recipe import RecipeError
from ..version import VersionError, dep_name
from .common import err, resolve_install_order, warn


# ---------------------------------------------------------------------------
# deps
# ---------------------------------------------------------------------------

def cmd_deps(args, recipes, pmap, cfg) -> int:
    """Mostra a ordem de instalação sem compilar nada."""
    db = dbmod.load_db(cfg.db_dir)
    inst = set(db["packages"])

    # Constrói {nome: versão} dos instalados para verificação de constraints
    inst_ver = {
        name: info.get("version", "0")
        for name, info in db["packages"].items()
    }

    order = resolve_install_order(args, recipes, pmap, inst, cfg, inst_ver)
    if order is None:
        return 1

    print(c("Ordem de build/instalação:", "bold"))
    for i, name in enumerate(order, 1):
        r = recipes.get(name)
        if dbmod.is_installed(db, name):
            ver_i = db["packages"][name]["version"]
            stale = c(f" ↑ {ver_i}", "dim") if r and ver_i != r.version else ""
            tag = c("instalado", "green") + stale
        else:
            tag = c("pendente", "yellow")
        ver_r = r.version if r else "?"
        print(f"  {i:3d}.  {name:<24} {ver_r:<12} [{tag}]")
    return 0


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------

def cmd_tree(args, recipes, pmap, cfg) -> int:
    """Exibe a árvore de dependências de um ou mais pacotes."""
    for target in args.packages:
        try:
            base = dep_name(target)
        except VersionError:
            base = target
        real = pmap.get(base, base)
        if real not in recipes:
            return err(f"pacote desconhecido: '{target}'")
        r = recipes[real]
        print(c(f"{r.name} {r.version}", "bold"))
        seen: set[str] = {real}
        for i, dep in enumerate(r.depends):
            is_last = i == len(r.depends) - 1
            for line in dep_tree_lines(recipes, dep, pmap, seen, "", is_last):
                print(line)
    return 0


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def cmd_fetch(args, recipes, pmap, cfg) -> int:
    """Baixa sources sem compilar."""
    for name in args.packages:
        try:
            base = dep_name(name)
        except VersionError:
            base = name
        real = pmap.get(base, base)
        if real not in recipes:
            return err(f"pacote desconhecido: '{real}'")
        r = recipes[real]
        if not r.sources:
            warn(f"'{real}' não tem source definido, pulando")
            continue
        print(c(f"→ {real}-{r.version}", "bold"))
        for url, chk in zip(r.sources, r.checksums):
            bld.fetch_source(url, cfg.sources_dir, chk)
    return 0


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def cmd_files(args, recipes, pmap, cfg) -> int:
    """Lista os arquivos instalados por um pacote."""
    db = dbmod.load_db(cfg.db_dir)
    try:
        name = pmap.get(dep_name(args.package), dep_name(args.package))
    except VersionError:
        name = pmap.get(args.package, args.package)
    if not dbmod.is_installed(db, name):
        return err(f"'{name}' não está instalado")
    files = db["packages"][name]["files"]
    print(c(f"{name}: {len(files)} arquivo(s)", "bold"))
    for f in sorted(files):
        print(f"  /{f}")
    return 0


# ---------------------------------------------------------------------------
# owns
# ---------------------------------------------------------------------------

def cmd_owns(args, recipes, pmap, cfg) -> int:
    """Descobre qual pacote instalado possui um arquivo."""
    db = dbmod.load_db(cfg.db_dir)
    target = Path(args.path).resolve()
    root_r = cfg.root_dir.resolve()

    try:
        rel = str(target.relative_to(root_r))
    except ValueError:
        rel = str(target).lstrip("/")

    # Remove barras iniciais duplicadas
    rel = rel.lstrip("/")

    owner = dbmod.find_owner_of_file(db, rel)
    if owner is not None:
        info = db["packages"][owner]
        print(c(f"/{rel}", "bold") + f"  →  {owner} {info['version']}")
        return 0

    print(c(f"/{rel}", "dim") + "  não pertence a nenhum pacote instalado")
    return 1


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

def cmd_log(args, recipes, pmap, cfg) -> int:
    """Exibe o log de build de um pacote."""
    try:
        name = dep_name(args.package)
    except VersionError:
        name = args.package
    name = pmap.get(name, name)
    if name not in recipes:
        return err(f"pacote desconhecido: '{name}'")
    r = recipes[name]
    log_path = cfg.build_dir / f"{r.name}-{r.version}" / "build.log"
    if not log_path.exists():
        return err(f"log não encontrado: {log_path}")

    if args.tail:
        subprocess.run(["tail", "-n", str(args.tail), str(log_path)])
    else:
        print(log_path.read_text())
    return 0


# ---------------------------------------------------------------------------
# suggest-deps
# ---------------------------------------------------------------------------

def cmd_suggest_deps(args, recipes, pmap, cfg) -> int:
    """Analisa o source de um pacote e sugere dependências potenciais."""
    from ..suggest import analyze_source

    try:
        name = dep_name(args.package)
    except VersionError:
        name = args.package
    name = pmap.get(name, name)
    if name not in recipes:
        return err(f"pacote desconhecido: '{name}'")

    r = recipes[name]
    if not r.sources:
        return err(f"'{name}' não tem source definido na receita")

    suggest_dir = cfg.build_dir / f"{r.name}-{r.version}-suggest"

    if suggest_dir.is_dir() and any(suggest_dir.iterdir()):
        entries = [p for p in suggest_dir.iterdir()]
        src_dir = entries[0] if len(entries) == 1 and entries[0].is_dir() else suggest_dir
        print(c(f"→ reutilizando source em {src_dir}", "dim"))
    else:
        print(c(f"→ {r.name}-{r.version}: baixando e extraindo source…", "cyan"))
        try:
            archive = bld.fetch_source(r.sources[0], cfg.sources_dir, r.checksums[0])
            suggest_dir.mkdir(parents=True, exist_ok=True)
            src_dir = bld.extract_archive(archive, suggest_dir)
        except bld.BuildError as e:
            return err(str(e))

    print(c(f"→ analisando {src_dir} …\n", "cyan"))

    required, optional = analyze_source(src_dir)

    if not required and not optional:
        print(c("Nenhuma dependência detectada nos arquivos de build.", "dim"))
        print("  (Arquivos procurados: configure.ac, meson.build, CMakeLists.txt, configure)")
    else:
        if required:
            print(c("Dependências sugeridas (obrigatórias):", "bold"))
            for s in required:
                in_dep = s.name in r.depends
                marker = c("  ✓ já em depends", "dim") if in_dep else ""
                print(f"  {s.name:<28} {c('de: ' + s.origin, 'dim')}{marker}")

        if optional:
            print(c("\nDependências sugeridas (opcionais):", "bold"))
            for s in optional:
                in_dep = s.name in (r.depends + r.optional_deps)
                marker = c("  ✓ já em depends/optional_deps", "dim") if in_dep else ""
                print(f"  {s.name:<28} {c('de: ' + s.origin, 'dim')}{marker}")

    print(c(
        "\nNota: sugestões baseadas em análise estática — revise antes de "
        "adicionar ao campo 'depends' da receita.",
        "dim",
    ))

    if not args.keep:
        shutil.rmtree(suggest_dir, ignore_errors=True)

    return 0
