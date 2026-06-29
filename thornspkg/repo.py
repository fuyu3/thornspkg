# * Gerenciamento de repositórios remotos — configuração, índice e cache.
# * Configuração: /etc/thornspkg/repos.json com lista de {name, url}.
# * Cache local: /var/lib/thornspkg/sync/<repo>.json (baixado por `thorn repo refresh`).
# * Índice do repositório: JSON em <url>/index.json listando pacotes (binary ou recipe).
# * Funções principais: repo_add(), repo_remove(), repo_list(), repo_refresh(),
# *   load_cached_index(), find_package_in_repos(), load_all_cached_indexes().
# * Arquivo: thornspkg/repo.py

"""Gerenciamento de repositórios remotos.

Estrutura:
  /etc/thornspkg/repos.json   — configuração dos repositórios
  /var/lib/thornspkg/sync/    — cache local dos índices baixados

Cada repositório possui:
  - name: nome identificador
  - url:  URL base do repositório

O índice do repositório é um JSON em <url>/index.json com o formato:

  {
      "packages": {
          "vim": {
              "version": "9.1",
              "type": "binary",
              "url": "packages/vim-9.1-x86_64.tar.zst",
              "sha256": "...",
              "depends": ["ncurses"]
          },
          "htop": {
              "version": "3.4",
              "type": "recipe",
              "recipe": "recipes/htop.yaml",
              "depends": ["ncurses"]
          }
      }
  }

Comandos CLI:
  thornspkg repo add <nome> <url>
  thornspkg repo remove <nome>
  thornspkg repo list
  thornspkg repo refresh
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .colors import c
from .downloader import DownloadError, download_to_temp


class RepoError(Exception):
    pass


# ---------------------------------------------------------------------------
# configuração dos repositórios
# ---------------------------------------------------------------------------

_REPOS_FILENAME = "repos.json"


@dataclass
class RepoEntry:
    """Um repositório remoto."""
    name: str
    url: str  # URL base, terminada com /


def _default_repos_config() -> dict:
    return {"repos": []}


def load_repos_config(config_path: Path) -> list[RepoEntry]:
    """Carrega a configuração de repositórios do disco.

    Returns:
        Lista de RepoEntry ordenada por nome.
    """
    if not config_path.exists():
        return []
    try:
        with open(config_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise RepoError(f"erro ao ler {config_path}: {e}") from e

    entries = []
    for r in data.get("repos", []):
        if "name" not in r or "url" not in r:
            raise RepoError(f"repositório sem 'name' ou 'url': {r}")
        url = r["url"]
        if not url.endswith("/"):
            url += "/"
        entries.append(RepoEntry(name=r["name"], url=url))
    return sorted(entries, key=lambda e: e.name)


def save_repos_config(config_path: Path, entries: list[RepoEntry]) -> None:
    """Salva a configuração de repositórios no disco."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "repos": [
            {"name": e.name, "url": e.url}
            for e in sorted(entries, key=lambda e: e.name)
        ]
    }
    tmp = config_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
    tmp.rename(config_path)


def repo_add(config_path: Path, name: str, url: str) -> None:
    """Adiciona um repositório à configuração."""
    if not url.endswith("/"):
        url += "/"
    entries = load_repos_config(config_path)
    for e in entries:
        if e.name == name:
            raise RepoError(f"repositório '{name}' já existe (url: {e.url})")
    entries.append(RepoEntry(name=name, url=url))
    save_repos_config(config_path, entries)
    print(c(f"  ✓  repositório '{name}' adicionado: {url}", "green"))


def repo_remove(config_path: Path, name: str) -> None:
    """Remove um repositório da configuração."""
    entries = load_repos_config(config_path)
    new_entries = [e for e in entries if e.name != name]
    if len(new_entries) == len(entries):
        raise RepoError(f"repositório '{name}' não encontrado")
    save_repos_config(config_path, new_entries)
    # Remove o cache local se existir
    print(c(f"  ✓  repositório '{name}' removido", "green"))


def repo_list(config_path: Path) -> list[RepoEntry]:
    """Lista os repositórios configurados."""
    entries = load_repos_config(config_path)
    if not entries:
        print(c("Nenhum repositório configurado.", "dim"))
    else:
        print(c("Repositórios configurados:", "bold"))
        for e in entries:
            print(f"  {e.name:<20} {e.url}")
    return entries


# ---------------------------------------------------------------------------
# índice do repositório
# ---------------------------------------------------------------------------

@dataclass
class RepoPackage:
    """Informações sobre um pacote no índice do repositório."""
    name: str
    version: str
    pkg_type: str       # "binary" ou "recipe"
    url: str | None = None          # URL relativa do binário (type=binary)
    sha256: str | None = None       # checksum do binário (type=binary)
    recipe: str | None = None       # URL relativa da receita (type=recipe)
    depends: list[str] = field(default_factory=list)


def parse_repo_index(data: dict) -> dict[str, RepoPackage]:
    """Converte o JSON do índice em dict de RepoPackage.

    Valida a estrutura e rejeita entradas malformadas.
    """
    packages = {}
    for name, info in data.get("packages", {}).items():
        pkg_type = info.get("type", "binary")
        if pkg_type not in ("binary", "recipe"):
            raise RepoError(
                f"pacote '{name}': tipo inválido '{pkg_type}' "
                "(deve ser 'binary' ou 'recipe')"
            )
        if pkg_type == "binary" and "url" not in info:
            raise RepoError(f"pacote '{name}': tipo binary sem campo 'url'")
        if pkg_type == "recipe" and "recipe" not in info:
            raise RepoError(f"pacote '{name}': tipo recipe sem campo 'recipe'")

        packages[name] = RepoPackage(
            name=name,
            version=info.get("version", "0"),
            pkg_type=pkg_type,
            url=info.get("url"),
            sha256=info.get("sha256"),
            recipe=info.get("recipe"),
            depends=info.get("depends", []),
        )
    return packages


# ---------------------------------------------------------------------------
# cache local de índices (sync)
# ---------------------------------------------------------------------------

def sync_dir(db_dir: Path) -> Path:
    """Retorna o diretório de cache de índices: <db_dir>/sync/"""
    return db_dir / "sync"


def repo_refresh(config_path: Path, db_dir: Path) -> None:
    """Baixa os índices de todos os repositórios e salva no cache local.

    Fluxo:
      repo refresh → baixa índices remotos → salva em /var/lib/thornspkg/sync/<repo>.json
    """
    entries = load_repos_config(config_path)
    if not entries:
        print(c("Nenhum repositório configurado. Use 'thorn repo add <nome> <url>'.", "yellow"))
        return

    sdir = sync_dir(db_dir)
    sdir.mkdir(parents=True, exist_ok=True)

    for entry in entries:
        index_url = entry.url + "index.json"
        dest = sdir / f"{entry.name}.json"
        print(c(f"  ↓  {entry.name}: {index_url}", "cyan"))
        try:
            download_to_temp(index_url, dest)
        except DownloadError as e:
            print(c(f"  ✗  falha ao baixar índice de '{entry.name}': {e}", "red"))
            continue

        # Valida o índice baixado
        try:
            with open(dest) as f:
                data = json.load(f)
            parse_repo_index(data)
            pkg_count = len(data.get("packages", {}))
            print(c(f"  ✓  {entry.name}: {pkg_count} pacote(s) no índice", "green"))
        except (json.JSONDecodeError, RepoError) as e:
            print(c(f"  ✗  índice de '{entry.name}' inválido: {e}", "red"))
            # Remove arquivo corrompido
            if dest.exists():
                dest.unlink()

    print(c("\n✓ refresh concluído.", "green"))


def load_cached_index(db_dir: Path, repo_name: str) -> dict[str, RepoPackage] | None:
    """Carrega o índice em cache de um repositório.

    Returns:
        Dict de RepoPackage, ou None se não houver cache.
    """
    path = sync_dir(db_dir) / f"{repo_name}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return parse_repo_index(data)
    except (json.JSONDecodeError, RepoError) as e:
        print(c(f"  aviso: índice em cache de '{repo_name}' inválido: {e}", "yellow"))
        return None


def load_all_cached_indexes(db_dir: Path, config_path: Path) -> dict[str, RepoPackage]:
    """Carrega os índices em cache de todos os repositórios configurados.

    Retorna um dict unificado {nome_pacote: RepoPackage}.
    Se o mesmo pacote existe em múltiplos repositórios, o primeiro encontrado
    (em ordem alfabética de repo) prevalece.
    """
    entries = load_repos_config(config_path)
    merged: dict[str, RepoPackage] = {}

    for entry in entries:
        index = load_cached_index(db_dir, entry.name)
        if index is None:
            continue
        for pkg_name, pkg in index.items():
            if pkg_name not in merged:
                merged[pkg_name] = pkg

    return merged


def find_package_in_repos(
    name: str,
    db_dir: Path,
    config_path: Path,
) -> RepoPackage | None:
    """Procura um pacote nos índices em cache.

    Percorre os repositórios em ordem alfabética e retorna o primeiro
    pacote encontrado com o nome dado.
    """
    entries = load_repos_config(config_path)
    for entry in entries:
        index = load_cached_index(db_dir, entry.name)
        if index is None:
            continue
        if name in index:
            return index[name]
    return None


def resolve_package_url(base_url: str, relative_path: str) -> str:
    """Combina a URL base do repo com um caminho relativo."""
    return base_url + relative_path


# ---------------------------------------------------------------------------
# download de receita remota (.toml)
# ---------------------------------------------------------------------------

def get_repo_url_for_package(
    name: str,
    db_dir: Path,
    config_path: Path,
) -> str | None:
    """Retorna a URL base do repositório que contém o pacote, ou None."""
    entries = load_repos_config(config_path)
    for entry in entries:
        index = load_cached_index(db_dir, entry.name)
        if index is not None and name in index:
            return entry.url
    return None


def download_remote_recipe(
    name: str,
    db_dir: Path,
    config_path: Path,
    dest_dir: Path,
) -> Path | None:
    """Baixa a receita .toml de um pacote do repositório remoto.

    Procura o pacote nos índices em cache, baixa o arquivo .toml referenciado
    em repo_pkg.recipe, verifica SHA256 se disponível, e salva em dest_dir.

    O arquivo é salvo com nome `<name>-<version>.toml` para evitar colisões.
    Se já existir no destino com o SHA256 correto, é reutilizado (cache).

    Args:
        name:         nome canônico do pacote
        db_dir:       diretório do banco do thornspkg
        config_path:  path do repos.json
        dest_dir:     diretório onde salvar o .toml (criado se não existir)

    Returns:
        Path para o arquivo .toml baixado, ou None se o pacote não estiver
        em nenhum repositório ou não for do tipo "recipe".

    Raises:
        RepoError: em caso de falha no download ou SHA256 divergente.
    """
    repo_pkg = find_package_in_repos(name, db_dir, config_path)
    if repo_pkg is None or repo_pkg.pkg_type != "recipe":
        return None

    base_url = get_repo_url_for_package(name, db_dir, config_path)
    if not base_url:
        return None

    if not repo_pkg.recipe:
        raise RepoError(
            f"pacote '{name}': tipo recipe sem campo 'recipe' no índice"
        )

    full_url = resolve_package_url(base_url, repo_pkg.recipe)
    filename = repo_pkg.recipe.rsplit("/", 1)[-1]
    # Normaliza nome para incluir versão se não tiver (facilita cache)
    if not filename.endswith(".toml"):
        filename = f"{name}-{repo_pkg.version}.toml"

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename

    # Reutiliza cache se SHA256 bater
    if repo_pkg.sha256 and dest.exists():
        import hashlib
        h = hashlib.sha256()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() == repo_pkg.sha256:
            return dest

    # Baixa para arquivo temporário e renomeia (atômico)
    tmp = dest.with_suffix(".toml.tmp")
    try:
        download_to_temp(full_url, tmp)
    except DownloadError as e:
        raise RepoError(
            f"falha ao baixar receita de '{name}': {e}"
        ) from e

    # Verifica SHA256 se declarado no índice
    if repo_pkg.sha256:
        import hashlib
        h = hashlib.sha256()
        with open(tmp, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
        if digest != repo_pkg.sha256:
            tmp.unlink(missing_ok=True)
            raise RepoError(
                f"sha256 diverge para receita de '{name}':\n"
                f"  esperado: {repo_pkg.sha256}\n"
                f"  obtido:   {digest}"
            )

    # Renomeia atomically
    tmp.rename(dest)
    return dest