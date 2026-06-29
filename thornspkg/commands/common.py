# * Funções utilitárias compartilhadas entre os subcomandos da CLI.
# * Centraliza: mensagens de erro/aviso, confirmação, resolução de ordem,
# *   helpers de instalação (hooks, build_one, install_binary_one, do_remove_one),
# *   helpers de repositório (get_repo_url, get_package_version, get_package_type).
# * Usado por todos os módulos em thornspkg/commands/.
# * Arquivo: thornspkg/commands/common.py

"""Funções utilitárias compartilhadas entre os subcomandos.

Centraliza:
  - Mensagens de erro/aviso (com cores)
  - Confirmação sim/não
  - Resolução de ordem de instalação (com fallback para repositórios)
  - Helpers de instalação: build_one, install_binary_one, do_remove_one
  - Helpers de repositório: get_repo_url, get_package_version, get_package_type
"""

from __future__ import annotations

import sys
from pathlib import Path

from .. import builder as bld
from .. import config as cfgmod
from .. import db as dbmod
from ..colors import c, ce
from ..depgraph import (
    DependencyCycleError,
    MissingDependencyError,
    VersionConflictError,
    resolve_order,
)
from ..downloader import DownloadError
from ..fileconflict import FileConflictError, assert_no_conflicts, build_file_index
from ..hooks import HookError, run_global_hooks
from ..recipe import Recipe, RecipeError, build_provides_map, load_recipe
from ..repo import (
    RepoError,
    download_remote_recipe,
    find_package_in_repos,
    load_cached_index,
    load_repos_config,
    resolve_package_url,
)
from ..version import VersionError, dep_name


# ---------------------------------------------------------------------------
# mensagens e confirmações
# ---------------------------------------------------------------------------

def err(msg: str) -> int:
    """Imprime mensagem de erro em stderr e retorna código 1."""
    print(ce(f"erro: {msg}", "red"), file=sys.stderr)
    return 1


def warn(msg: str) -> None:
    """Imprime aviso em stderr."""
    print(ce(f"aviso: {msg}", "yellow"), file=sys.stderr)


def info(msg: str) -> None:
    """Imprime mensagem informativa em stdout."""
    print(c(msg, "dim"))


def confirm(prompt: str, default: bool = True, yes: bool = False) -> bool:
    """Pergunta sim/não ao usuário. Se não for TTY, retorna `default`."""
    if yes:
        return True
    if not sys.stdin.isatty():
        return default
    hint = "[S/n]" if default else "[s/N]"
    try:
        resp = input(f"{prompt} {hint}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not resp:
        return default
    return resp in ("s", "y", "sim", "yes")


# ---------------------------------------------------------------------------
# resolução de ordem de instalação
# ---------------------------------------------------------------------------

def _fetch_repo_recipe(name: str, cfg) -> Recipe | None:
    """Busca um pacote nos repositórios e cria uma Recipe virtual.

    Retorna None se o pacote não estiver em nenhum repositório.

    A Recipe virtual tem apenas name/version/depends (sem sources/build_system)
    — é usada só para resolver a árvore de dependências. A receita completa
    (.toml com sources, build_system, etc.) é baixada depois por
    _load_remote_recipe() no momento do build.
    """
    repo_pkg = find_package_in_repos(name, cfg.db_dir, cfg.repos_config)
    if repo_pkg is None:
        return None
    return Recipe(
        name=name,
        version=repo_pkg.version,
        path=Path(f"repo:{name}"),
        depends=list(repo_pkg.depends),
    )


def _load_remote_recipe(name: str, cfg) -> Recipe | None:
    """Baixa a receita .toml remota e a carrega como Recipe completa.

    Usado quando install_one_package() detecta uma receita virtual (criada
    por _fetch_repo_recipe para resolver deps) e precisa da receita real
    com sources/build_system/steps para efetivamente compilar.

    A receita é cacheada em <db_dir>/remote-recipes/ para reuso.

    Returns:
        Recipe completa, ou None se não foi possível baixar/carregar.
    """
    cache_dir = cfg.db_dir / "remote-recipes"
    try:
        recipe_path = download_remote_recipe(
            name, cfg.db_dir, cfg.repos_config, cache_dir
        )
    except RepoError as e:
        warn(f"falha ao baixar receita remota de '{name}': {e}")
        return None

    if recipe_path is None:
        return None

    print(c(f"  ↓  receita remota: {recipe_path.name}", "cyan"))
    try:
        return load_recipe(recipe_path)
    except RecipeError as e:
        warn(f"receita remota de '{name}' inválida: {e}")
        return None


def _fetch_all_repo_recipes(
    targets: list[str],
    local_recipes: dict,
    pmap: dict,
    cfg,
) -> dict[str, Recipe]:
    """Busca recursivamente todas as receitas necessárias nos repositórios.

    Para cada target e suas dependências transitivas, se a receita não existe
    localmente, busca no repositório. Para pacotes do tipo "recipe", baixa o
    .toml real (via _load_remote_recipe) e usa suas depends + optional_deps
    para resolver a árvore completa. Para pacotes do tipo "binary", usa apenas
    as depends declaradas no index.json.

    Args:
        targets:        lista de specs de pacotes (ex: ["bar", "foo>=1.0"])
        local_recipes:  receitas locais já carregadas
        pmap:           mapa de provides
        cfg:            configuração

    Returns:
        Dict com todas as receitas (locais + reais/virtuais do repositório).
    """
    virtual = dict(local_recipes)
    # Fila de nomes base a buscar (sem operador de versão)
    to_check: list[str] = []
    seen: set[str] = set()

    for spec in targets:
        try:
            base = dep_name(spec)
        except VersionError:
            base = spec
        to_check.append(base)

    while to_check:
        name = to_check.pop(0)
        if name in seen:
            continue
        seen.add(name)

        # Resolve provides
        real = pmap.get(name, name)

        # Se já temos localmente, usa e explora suas deps
        if real in virtual:
            for dep in virtual[real].depends:
                try:
                    dep_base = dep_name(dep)
                except VersionError:
                    dep_base = dep
                if dep_base not in seen:
                    to_check.append(dep_base)
            continue

        # Senão, verifica se está no repositório
        repo_pkg = find_package_in_repos(real, cfg.db_dir, cfg.repos_config)
        if repo_pkg is None:
            continue  # deixa resolve_order() reclamar de missing dep

        if repo_pkg.pkg_type == "recipe":
            # Baixa a receita .toml real (com sources, build_system, etc.)
            # — assim temos acesso a depends + optional_deps completas.
            # A receita é cacheada em <db_dir>/remote-recipes/.
            real_recipe = _load_remote_recipe(real, cfg)
            if real_recipe is not None:
                virtual[real] = real_recipe
                # Adiciona depends + optional_deps à fila para busca recursiva
                for dep in list(real_recipe.depends) + list(real_recipe.optional_deps):
                    try:
                        dep_base = dep_name(dep)
                    except VersionError:
                        dep_base = dep
                    if dep_base not in seen:
                        to_check.append(dep_base)
            else:
                # Fallback: usa apenas as depends do index.json
                recipe = _fetch_repo_recipe(real, cfg)
                if recipe is not None:
                    virtual[real] = recipe
                    for dep in recipe.depends:
                        try:
                            dep_base = dep_name(dep)
                        except VersionError:
                            dep_base = dep
                        if dep_base not in seen:
                            to_check.append(dep_base)
        else:
            # binary: usa apenas depends do index.json (não há .toml)
            recipe = _fetch_repo_recipe(real, cfg)
            if recipe is not None:
                virtual[real] = recipe
                for dep in recipe.depends:
                    try:
                        dep_base = dep_name(dep)
                    except VersionError:
                        dep_base = dep
                    if dep_base not in seen:
                        to_check.append(dep_base)

    return virtual


def resolve_install_order(args, recipes, pmap, inst, cfg, inst_versions=None):
    """Resolve a ordem de instalação, considerando receitas locais e pacotes
    de repositório.

    Estratégia:
      1. Busca recursivamente todas as receitas necessárias (locais + repo)
      2. Resolve a ordem com resolve_order()

    Retorna lista linearizada ou None em caso de erro (mensagem já impressa).
    """
    try:
        all_recipes = _fetch_all_repo_recipes(args.packages, recipes, pmap, cfg)
        return resolve_order(
            all_recipes, args.packages, pmap, inst,
            installed_versions=inst_versions,
        )
    except (DependencyCycleError, MissingDependencyError, VersionConflictError) as e:
        err(str(e))
        return None
    except Exception as e:
        err(f"erro inesperado ao resolver dependências: {e}")
        return None


# ---------------------------------------------------------------------------
# helpers de instalação
# ---------------------------------------------------------------------------

def run_install_hooks(phase: str, name: str, version: str, cfg: cfgmod.Config) -> None:
    """Roda hooks globais de install (pre-install / post-install)."""
    run_global_hooks(
        cfg.hooks_dir, phase, name, version,
        str(cfg.root_dir), cfg.prefix,
    )


def run_remove_hooks(phase: str, name: str, version: str, cfg: cfgmod.Config) -> None:
    """Roda hooks globais de remove (pre-remove / post-remove)."""
    run_global_hooks(
        cfg.hooks_dir, phase, name, version,
        str(cfg.root_dir), cfg.prefix,
    )


def build_one(
    name: str,
    recipe,
    cfg: cfgmod.Config,
    *,
    jobs: int,
    keep_build: bool,
    current: int,
    total: int,
) -> tuple[list[str], dict[str, str]]:
    """Compila e instala um único pacote, rodando hooks globais."""
    version = recipe.version
    run_install_hooks("pre-install", name, version, cfg)
    manifest, checksums = bld.build_and_install(
        recipe, cfg, jobs=jobs, keep_build=keep_build,
        current=current, total=total,
    )
    run_install_hooks("post-install", name, version, cfg)
    return manifest, checksums


def install_binary_one(
    name: str,
    version: str,
    binary_url: str,
    sha256: str,
    cfg: cfgmod.Config,
    *,
    current: int = 0,
    total: int = 0,
    repository: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Baixa e instala um pacote binário do repositório.

    Usa o cache persistente de pacotes (cache.py) quando disponível.
    """
    run_install_hooks("pre-install", name, version, cfg)

    # Baixar via cache de pacotes binários
    from ..cache import get_cached_package, CacheError
    filename = binary_url.rsplit("/", 1)[-1]
    try:
        result = get_cached_package(binary_url, cfg, expected_sha256=sha256 or None)
        dest = result.path
    except CacheError as e:
        raise bld.BuildError(f"falha ao obter binário '{name}': {e}") from e
    except DownloadError as e:
        raise bld.BuildError(f"falha ao baixar binário '{name}': {e}") from e

    manifest, checksums = bld.install_binary_package(
        dest, cfg, name, version,
        current=current, total=total,
    )

    run_install_hooks("post-install", name, version, cfg)
    return manifest, checksums


def do_remove_one(name: str, version: str, recipe_opt, db: dict,
                  cfg: cfgmod.Config) -> tuple[int, int]:
    """Remove um único pacote: hooks de receita, hooks globais, arquivos, DB."""
    # Hooks de receita (pre_remove)
    if recipe_opt:
        bld.run_remove_hooks(recipe_opt, cfg)

    # Hooks globais pre-remove
    run_remove_hooks("pre-remove", name, version, cfg)

    # Remove arquivos do root
    files = db["packages"][name]["files"]
    removed, skipped = bld.remove_installed_files(cfg.root_dir, files)

    # Atualiza banco
    dbmod.delete_checksums(cfg.db_dir, name)
    dbmod.remove_record(db, name)
    dbmod.save_db(cfg.db_dir, db)

    # Hooks pós-remoção
    if recipe_opt:
        bld.run_post_remove_hooks(recipe_opt, cfg)
    run_remove_hooks("post-remove", name, version, cfg)

    return removed, skipped


# ---------------------------------------------------------------------------
# helpers de repositório
# ---------------------------------------------------------------------------

def get_repo_url(name: str, cfg: cfgmod.Config) -> str:
    """Retorna a URL base do repositório que contém o pacote."""
    entries = load_repos_config(cfg.repos_config)
    for entry in entries:
        index = load_cached_index(cfg.db_dir, entry.name)
        if index is not None and name in index:
            return entry.url
    return ""


def get_package_version(name: str, cfg: cfgmod.Config) -> str:
    """Obtém a versão de um pacote (receita local ou repositório)."""
    repo_pkg = find_package_in_repos(name, cfg.db_dir, cfg.repos_config)
    if repo_pkg is not None:
        return repo_pkg.version
    return "0"


def get_package_type(name: str, recipes: dict, cfg: cfgmod.Config) -> str:
    """Determina o tipo de instalação para um pacote.

    Returns:
        "local"         — receita local (compilação)
        "binary"        — pacote binário do repositório
        "recipe-remote" — receita do repositório
    """
    if name in recipes:
        repo_pkg = find_package_in_repos(name, cfg.db_dir, cfg.repos_config)
        if repo_pkg is not None and repo_pkg.pkg_type == "binary":
            return "binary"
        return "local"

    repo_pkg = find_package_in_repos(name, cfg.db_dir, cfg.repos_config)
    if repo_pkg is not None:
        return repo_pkg.pkg_type if repo_pkg.pkg_type == "binary" else "recipe-remote"
    return "local"


def install_one_package(
    name: str,
    recipes: dict,
    cfg: cfgmod.Config,
    args,
    current: int,
    total: int,
    *,
    allow_overwrite: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Instala um único pacote, escolhendo a melhor estratégia.

    Prioridade (padrão): binário > receita local > receita remota
    Com --prefer-source: receita local > binário > receita remota

    Receitas remotas (type="recipe" no index.json do repositório) são baixadas
    em <db_dir>/remote-recipes/ e cacheadas para reuso. O SHA256 declarado no
    índice é verificado após o download.

    Realiza detecção de conflitos de arquivos ANTES de instalar.
    """
    prefer_source = getattr(args, 'prefer_source', False)

    recipe = recipes.get(name)
    repo_pkg = find_package_in_repos(name, cfg.db_dir, cfg.repos_config)

    # Detecta receita virtual (criada pelo resolver para dep resolution).
    # Receitas virtuais têm path="repo:<name>" e não têm sources/build_system.
    # Se o pacote está no repo como type="recipe", baixamos o .toml real agora.
    is_virtual = (
        recipe is not None
        and not recipe.sources
        and str(recipe.path).startswith("repo:")
    )
    if is_virtual and repo_pkg is not None and repo_pkg.pkg_type == "recipe":
        real_recipe = _load_remote_recipe(name, cfg)
        if real_recipe is not None:
            recipe = real_recipe
            recipes[name] = real_recipe  # substitui no dict para reuso
            is_virtual = False

    # Determina o modo de instalação
    if prefer_source:
        if recipe is not None and not is_virtual:
            return build_one(
                name, recipe, cfg,
                jobs=args.jobs, keep_build=args.keep_build,
                current=current, total=total,
            )
        if repo_pkg is not None and repo_pkg.pkg_type == "binary":
            return install_binary_one(
                name, repo_pkg.version,
                resolve_package_url(get_repo_url(name, cfg), repo_pkg.url),
                repo_pkg.sha256 or "",
                cfg, current=current, total=total,
                repository=repo_pkg.name,
            )
    else:
        # Padrão: tenta binário primeiro
        if repo_pkg is not None and repo_pkg.pkg_type == "binary":
            return install_binary_one(
                name, repo_pkg.version,
                resolve_package_url(get_repo_url(name, cfg), repo_pkg.url),
                repo_pkg.sha256 or "",
                cfg, current=current, total=total,
                repository=repo_pkg.name,
            )
        if recipe is not None and not is_virtual:
            return build_one(
                name, recipe, cfg,
                jobs=args.jobs, keep_build=args.keep_build,
                current=current, total=total,
            )

    # Fallback: receita do repositório remoto (type="recipe")
    # Já tentamos baixar acima; se chegamos aqui com receita real, builda.
    if repo_pkg is not None and repo_pkg.pkg_type == "recipe":
        if recipe is not None and not is_virtual:
            return build_one(
                name, recipe, cfg,
                jobs=args.jobs, keep_build=args.keep_build,
                current=current, total=total,
            )
        raise bld.BuildError(
            f"não foi possível carregar a receita remota de '{name}' "
            f"(verifique se o arquivo .toml existe no repositório)"
        )

    raise bld.BuildError(f"não foi possível determinar como instalar '{name}'")


# ---------------------------------------------------------------------------
# helper de conflito de arquivos
# ---------------------------------------------------------------------------

def pre_check_conflicts(
    manifest: list[str],
    db: dict,
    new_package: str,
    *,
    allow_overwrite: bool = False,
) -> None:
    """Verifica conflitos de arquivos antes de instalar.

    Em modo normal, levanta FileConflictError se houver conflito.
    Em modo --force-overwrite, apenas avisa.
    """
    if allow_overwrite:
        return
    try:
        assert_no_conflicts(manifest, db, new_package, allow_overwrite=False)
    except FileConflictError:
        # Repassa para o chamador decidir
        raise


def build_installed_versions(db: dict) -> dict[str, str]:
    """Constrói {nome: versão} de todos os pacotes instalados."""
    return {
        name: info.get("version", "0")
        for name, info in db.get("packages", {}).items()
    }


def cleanup_obsolete_files(
    name: str,
    new_manifest: list[str],
    db: dict,
    cfg: cfgmod.Config,
) -> int:
    """Remove arquivos que existiam na versão antiga mas não na nova.

    Deve ser chamado APÓS instalar os novos arquivos e ANTES de atualizar o
    banco com o novo manifest. Caso contrário, arquivos que foram removidos
    do pacote na nova versão ficariam órfãos no root.

    Retorna o número de arquivos removidos.
    """
    if name not in db.get("packages", {}):
        return 0  # instalação nova, nada a limpar
    old_files = set(db["packages"][name].get("files", []))
    new_files = set(new_manifest)
    obsolete = old_files - new_files
    if not obsolete:
        return 0
    removed, _ = bld.remove_installed_files(cfg.root_dir, list(obsolete))
    if removed:
        print(c(f"  ♻  {removed} arquivo(s) obsoleto(s) removido(s) da versão anterior", "dim"))
    return removed