# * Cache persistente de sources, pacotes binários e índices de repositório.
# * Estrutura: /var/cache/thornspkg/{sources,packages,indexes}/.
# * Verificação por checksum (SHA256) — reusa arquivos válidos sem rebaixar.
# * Funções principais: get_cached_source(), get_cached_package(), put_cache(),
# *   cache_stats(), cache_clean().
# * Arquivo: thornspkg/cache.py

"""Cache persistente de downloads (sources, pacotes binários, índices).

Estrutura em disco:

  /var/cache/thornspkg/
    sources/   — tarballs de código-fonte (ex: openssl-3.3.1.tar.gz)
    packages/  — pacotes binários baixados (ex: vim-9.1-x86_64.tar.zst)
    indexes/   — índices de repositórios (ex: core.json)

Funcionamento:
  - Antes de baixar, verifica se o arquivo já existe no cache.
  - Se existir E o checksum bater, usa o arquivo local.
  - Se existir mas o checksum divergir, re-baixa.
  - Se não existir, baixa e armazena.

Comandos CLI:
  thorn cache stats   — mostra uso do cache
  thorn cache clean   — remove todo o cache
  thorn cache list    — lista arquivos no cache
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .builder import sha256_file
from .colors import c
from .downloader import DownloadError, download_to_temp


# ---------------------------------------------------------------------------
# constantes
# ---------------------------------------------------------------------------

SOURCES_SUBDIR  = "sources"
PACKAGES_SUBDIR = "packages"
INDEXES_SUBDIR  = "indexes"


# ---------------------------------------------------------------------------
# exceções
# ---------------------------------------------------------------------------

class CacheError(Exception):
    """Erro genérico do cache."""
    pass


# ---------------------------------------------------------------------------
# helpers de path
# ---------------------------------------------------------------------------

def _cache_root_from_config(cfg) -> Path:
    """Extrai o diretório raiz do cache da configuração.

    Por padrão, é o mesmo que cfg.sources_dir.parent (que normalmente é
    /var/cache/thornspkg/sources). Mas se a config foi sobrescrita,
    tentamos adivinhar a raiz a partir de qualquer dos subdiretórios.
    """
    # Heurística: se sources_dir termina com /sources, parent é a raiz
    sources = Path(cfg.sources_dir)
    if sources.name == SOURCES_SUBDIR:
        return sources.parent
    # Fallback: usa o próprio sources_dir como raiz (modo legacy)
    return sources


def sources_cache_dir(cfg) -> Path:
    return Path(cfg.sources_dir)


def packages_cache_dir(cfg) -> Path:
    root = _cache_root_from_config(cfg)
    return root / PACKAGES_SUBDIR


def indexes_cache_dir(cfg) -> Path:
    """Diretório de cache para índices de repositórios.

    Mantém compatibilidade com o sync_dir() do repo.py, que usa
    <db_dir>/sync/. Preferimos o cache dedicado quando disponível,
    mas mantemos o sync/ como fallback para não invalidar caches antigos.
    """
    root = _cache_root_from_config(cfg)
    return root / INDEXES_SUBDIR


# ---------------------------------------------------------------------------
# operações de cache
# ---------------------------------------------------------------------------

def _verify_checksum(path: Path, expected_sha256: str | None) -> bool:
    """True se o arquivo existe e o checksum bate (ou checksum é None)."""
    if not path.exists():
        return False
    if expected_sha256 is None:
        return True
    return sha256_file(path) == expected_sha256


@dataclass
class CacheResult:
    """Resultado de uma operação de cache."""
    path: Path
    from_cache: bool       # True se reaproveitado do cache
    downloaded: bool       # True se baixado agora


def get_cached_source(
    url: str,
    cfg,
    expected_sha256: str | None = None,
) -> CacheResult:
    """Retorna o caminho de um source, usando cache quando possível.

    Args:
        url:              URL do source (http/https/ftp)
        cfg:              configuração do thornspkg
        expected_sha256:  checksum esperado (None = não verificar)

    Returns:
        CacheResult com path, from_cache, downloaded

    Raises:
        DownloadError:    se o download falhar
        CacheError:       se o checksum não bater após download
    """
    sources_dir = sources_cache_dir(cfg)
    sources_dir.mkdir(parents=True, exist_ok=True)

    filename = url.rsplit("/", 1)[-1]
    dest = sources_dir / filename

    # Tenta usar o cache
    if _verify_checksum(dest, expected_sha256):
        print(c(f"  ↓  {filename} (cache)", "dim"))
        return CacheResult(path=dest, from_cache=True, downloaded=False)

    # Baixa
    print(c(f"  ↓  {url}", "cyan"))
    download_to_temp(url, dest)

    # Verifica checksum pós-download
    if expected_sha256 is not None:
        actual = sha256_file(dest)
        if actual != expected_sha256:
            dest.unlink(missing_ok=True)
            raise CacheError(
                f"sha256 diverge para '{filename}'\n"
                f"  esperado: {expected_sha256}\n"
                f"  obtido:   {actual}"
            )
        print(c("  ✓  checksum OK", "green"))

    return CacheResult(path=dest, from_cache=False, downloaded=True)


def get_cached_package(
    url: str,
    cfg,
    expected_sha256: str | None = None,
) -> CacheResult:
    """Igual a get_cached_source, mas para pacotes binários (subdir packages/)."""
    packages_dir = packages_cache_dir(cfg)
    packages_dir.mkdir(parents=True, exist_ok=True)

    filename = url.rsplit("/", 1)[-1]
    dest = packages_dir / filename

    if _verify_checksum(dest, expected_sha256):
        print(c(f"  ↓  {filename} (cache binário)", "dim"))
        return CacheResult(path=dest, from_cache=True, downloaded=False)

    print(c(f"  ↓  {url}", "cyan"))
    download_to_temp(url, dest)

    if expected_sha256 is not None:
        actual = sha256_file(dest)
        if actual != expected_sha256:
            dest.unlink(missing_ok=True)
            raise CacheError(
                f"sha256 diverge para binário '{filename}'\n"
                f"  esperado: {expected_sha256}\n"
                f"  obtido:   {actual}"
            )
        print(c("  ✓  checksum OK", "green"))

    return CacheResult(path=dest, from_cache=False, downloaded=True)


def put_in_cache(local_path: Path, cache_dir: Path, filename: str | None = None) -> Path:
    """Copia um arquivo local para o cache, retornando o caminho no cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    fname = filename or local_path.name
    dest = cache_dir / fname
    if not dest.exists() or sha256_file(local_path) != sha256_file(dest):
        shutil.copy2(local_path, dest)
    return dest


# ---------------------------------------------------------------------------
# estatísticas e limpeza
# ---------------------------------------------------------------------------

@dataclass
class CacheStats:
    """Estatísticas do cache."""
    sources_count: int = 0
    sources_size: int = 0
    packages_count: int = 0
    packages_size: int = 0
    indexes_count: int = 0
    indexes_size: int = 0

    @property
    def total_count(self) -> int:
        return self.sources_count + self.packages_count + self.indexes_count

    @property
    def total_size(self) -> int:
        return self.sources_size + self.packages_size + self.indexes_size

    def human_size(self, n: int) -> str:
        """Converte bytes para forma legível."""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    def format(self) -> str:
        lines = [
            c("Cache do thornspkg:", "bold"),
            f"  sources/   {self.sources_count:5d} arquivo(s)   {self.human_size(self.sources_size)}",
            f"  packages/  {self.packages_count:5d} arquivo(s)   {self.human_size(self.packages_size)}",
            f"  indexes/   {self.indexes_count:5d} arquivo(s)   {self.human_size(self.indexes_size)}",
            f"  {'─' * 50}",
            f"  total      {self.total_count:5d} arquivo(s)   {self.human_size(self.total_size)}",
        ]
        return "\n".join(lines)


def _dir_stats(d: Path) -> tuple[int, int]:
    """Retorna (n_arquivos, bytes) para um diretório."""
    if not d.exists():
        return (0, 0)
    count = 0
    size = 0
    for p in d.rglob("*"):
        if p.is_file():
            count += 1
            size += p.stat().st_size
    return (count, size)


def cache_stats(cfg) -> CacheStats:
    """Calcula estatísticas do cache."""
    sc, ss = _dir_stats(sources_cache_dir(cfg))
    pc, ps = _dir_stats(packages_cache_dir(cfg))
    ic, is_ = _dir_stats(indexes_cache_dir(cfg))
    return CacheStats(
        sources_count=sc, sources_size=ss,
        packages_count=pc, packages_size=ps,
        indexes_count=ic, indexes_size=is_,
    )


def cache_clean(cfg, *, sources: bool = True, packages: bool = True,
                indexes: bool = False) -> int:
    """Remove conteúdo do cache.

    Args:
        sources:  limpar sources/
        packages: limpar packages/
        indexes:  limpar indexes/ (default False — índices são baratos)

    Returns:
        Número de arquivos removidos.
    """
    removed = 0
    targets: list[Path] = []
    if sources:
        targets.append(sources_cache_dir(cfg))
    if packages:
        targets.append(packages_cache_dir(cfg))
    if indexes:
        targets.append(indexes_cache_dir(cfg))

    for d in targets:
        if not d.exists():
            continue
        for p in d.iterdir():
            if p.is_file():
                p.unlink()
                removed += 1
            elif p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
    return removed


def cache_list(cfg) -> dict[str, list[Path]]:
    """Lista arquivos no cache, agrupados por categoria."""
    result: dict[str, list[Path]] = {
        "sources": [],
        "packages": [],
        "indexes": [],
    }
    for label, d in [
        ("sources", sources_cache_dir(cfg)),
        ("packages", packages_cache_dir(cfg)),
        ("indexes", indexes_cache_dir(cfg)),
    ]:
        if d.exists():
            result[label] = sorted(p for p in d.iterdir() if p.is_file())
    return result
