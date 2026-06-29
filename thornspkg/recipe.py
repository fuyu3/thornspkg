# * Leitura e validação de receitas TOML — cada pacote é um arquivo .toml em --recipes-dir.
# * Campos: name (obrig), version (obrig), depends, optional_deps, provides, source/sha256,
# *   patches, build_system (autotools|make|cmake|meson|custom), configure_args,
# *   prefix, [env], pre_build, steps, install_steps, post_install, pre_remove, post_remove.
# * Metadados extras (v0.4+): description, homepage, license, maintainer, repository,
# *   architecture, build_date, install_size, download_size.
# * depends/optional_deps aceitam operadores de versão: "openssl>=3.0", "python<3.15".
# * Parser TOML: usa tomllib (Python ≥3.11) ou tomli (fallback para Python <3.11).
# * Funções principais: load_recipe() → Recipe, load_all_recipes() → dict[str, Recipe],
# *   build_provides_map() → {nome_virtual: nome_real} para resolução de provides.
# * Arquivo: thornspkg/recipe.py

"""Leitura e validação das receitas TOML.

Campos suportados por receita:

  name            (obrigatório) nome canônico do pacote
  version         (obrigatório) versão
  description     descrição de uma linha
  homepage        URL do site do projeto
  license         licença (ex: "MIT", "GPL-3.0-or-later", "Vim")
  maintainer      nome/email do mantenedor da receita
  repository      nome do repositório de onde veio (core, extra, …)
  architecture    arquitetura alvo (ex: x86_64, aarch64, any)
  build_date      data de build do pacote binário (ISO 8601)
  install_size    tamanho instalado em bytes
  download_size   tamanho do download em bytes

  depends         lista de deps obrigatórias (aceita operadores: "openssl>=3.0")
  optional_deps   deps instaladas automaticamente só se já estiverem no sistema
  provides        nomes virtuais que este pacote satisfaz (ex: bash provides ["sh"])
  source          URL (http/https/ftp) ou caminho local; pode ser lista de URLs
  sha256          checksum do tarball principal (ou lista, se source for lista)
  patches         lista de nomes de patch em <patches_dir>/<name>/ ou paths absolutos
  build_system    autotools | make | cmake | meson | custom  (default: autotools)
  configure_args  args extras para configure/cmake/meson (--prefix já é adicionado)
  prefix          sobrescreve o prefix global apenas para este pacote

  [env]           variáveis de ambiente extras durante o build deste pacote
    CFLAGS = "-O2"

  pre_build       comandos shell antes do build (ex: autoreconf)
  steps           comandos de build; se presente, ignora build_system/configure_args
  install_steps   comandos de install; default ["make install"]
  post_install    comandos shell após copiar para o root (ex: ldconfig, mandb)
  pre_remove      comandos shell antes de remover arquivos
  post_remove     comandos shell após remover arquivos
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

VALID_BUILD_SYSTEMS = {"autotools", "make", "cmake", "meson", "custom"}


class RecipeError(Exception):
    pass


@dataclass
class Recipe:
    name: str
    version: str
    path: Path
    description: str = ""
    # Metadados extras (v0.4+) — valores padrão seguros para receitas antigas
    homepage: str | None = None
    license: str | None = None
    maintainer: str | None = None
    repository: str | None = None
    architecture: str | None = None
    build_date: str | None = None           # ISO 8601 — preenchido para pacotes binários
    install_size: int | None = None         # bytes
    download_size: int | None = None        # bytes
    depends: list[str] = field(default_factory=list)
    optional_deps: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    # source pode ser uma string ou lista de strings
    sources: list[str] = field(default_factory=list)
    checksums: list[str | None] = field(default_factory=list)   # sha256 por source
    patches: list[str] = field(default_factory=list)
    build_system: str = "autotools"
    configure_args: list[str] = field(default_factory=list)
    prefix: str | None = None                                    # sobrescreve config.prefix
    env: dict[str, str] = field(default_factory=dict)
    pre_build: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    install_steps: list[str] = field(default_factory=list)
    post_install: list[str] = field(default_factory=list)
    pre_remove: list[str] = field(default_factory=list)
    post_remove: list[str] = field(default_factory=list)

    @property
    def source(self) -> str | None:
        return self.sources[0] if self.sources else None

    @property
    def sha256(self) -> str | None:
        return self.checksums[0] if self.checksums else None

    def to_metadata_dict(self) -> dict:
        """Retorna os metadados como dict, pronto para gravar no DB.

        Útil para serialização consistente entre receitas locais e
        pacotes binários de repositório.
        """
        return {
            "description":   self.description,
            "homepage":      self.homepage,
            "license":       self.license,
            "maintainer":    self.maintainer,
            "repository":    self.repository,
            "architecture":  self.architecture,
            "build_date":    self.build_date,
            "install_size":  self.install_size,
            "download_size": self.download_size,
        }


def _check_tomllib() -> None:
    if tomllib is None:
        raise RecipeError(
            "nenhum parser TOML disponível — use Python ≥ 3.11 (tem tomllib "
            "embutido) ou instale 'tomli'."
        )


def _to_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def load_recipe(path: Path) -> Recipe:
    _check_tomllib()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise RecipeError(f"{path}: TOML inválido: {e}") from e

    for req in ("name", "version"):
        if req not in data:
            raise RecipeError(f"{path}: campo obrigatório '{req}' ausente")

    bs = data.get("build_system", "autotools")
    if bs not in VALID_BUILD_SYSTEMS:
        raise RecipeError(
            f"{path}: build_system '{bs}' inválido "
            f"(opções: {', '.join(sorted(VALID_BUILD_SYSTEMS))})"
        )

    steps = _to_list(data.get("steps"))
    if bs == "custom" and not steps:
        raise RecipeError(f"{path}: build_system='custom' exige 'steps' explícito")

    # source pode ser string ou lista
    raw_sources = data.get("sources", data.get("source"))
    sources = _to_list(raw_sources)

    raw_checksums = data.get("sha256")
    if raw_checksums is None:
        checksums: list[str | None] = [None] * len(sources)
    elif isinstance(raw_checksums, list):
        checksums = raw_checksums
    else:
        checksums = [raw_checksums]

    # normaliza comprimento
    while len(checksums) < len(sources):
        checksums.append(None)

    env = dict(data.get("env", {}))
    if not isinstance(env, dict):
        raise RecipeError(f"{path}: [env] deve ser uma tabela TOML")
    for k, v in env.items():
        if not isinstance(v, str):
            raise RecipeError(f"{path}: env.{k} deve ser string")

    return Recipe(
        name=data["name"],
        version=str(data["version"]),
        path=path,
        description=data.get("description", ""),
        homepage=data.get("homepage"),
        license=data.get("license"),
        maintainer=data.get("maintainer"),
        repository=data.get("repository"),
        architecture=data.get("architecture"),
        build_date=data.get("build_date"),
        install_size=data.get("install_size"),
        download_size=data.get("download_size"),
        depends=_to_list(data.get("depends")),
        optional_deps=_to_list(data.get("optional_deps")),
        provides=_to_list(data.get("provides")),
        sources=sources,
        checksums=checksums,
        patches=_to_list(data.get("patches")),
        build_system=bs,
        configure_args=_to_list(data.get("configure_args")),
        prefix=data.get("prefix"),
        env=env,
        pre_build=_to_list(data.get("pre_build")),
        steps=steps,
        install_steps=_to_list(data.get("install_steps")),
        post_install=_to_list(data.get("post_install")),
        pre_remove=_to_list(data.get("pre_remove")),
        post_remove=_to_list(data.get("post_remove")),
    )


def load_all_recipes(recipes_dir: Path) -> dict[str, Recipe]:
    if not recipes_dir.is_dir():
        raise RecipeError(f"diretório de receitas não encontrado: {recipes_dir}")

    recipes: dict[str, Recipe] = {}
    for toml_path in sorted(recipes_dir.glob("*.toml")):
        recipe = load_recipe(toml_path)
        if recipe.name in recipes:
            raise RecipeError(
                f"nome duplicado '{recipe.name}': "
                f"{toml_path} e {recipes[recipe.name].path}"
            )
        recipes[recipe.name] = recipe
    return recipes


def build_provides_map(recipes: dict[str, Recipe]) -> dict[str, str]:
    """Retorna {nome_virtual: nome_real} para resolução de provides."""
    pmap: dict[str, str] = {}
    for name, r in recipes.items():
        for vname in r.provides:
            if vname in pmap and pmap[vname] != name:
                raise RecipeError(
                    f"conflito de provides: '{vname}' é fornecido por "
                    f"'{name}' e '{pmap[vname]}'"
                )
            pmap[vname] = name
    return pmap
