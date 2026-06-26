# * Self-update do thornspkg — verifica, baixa e instala novas versões de si mesmo.
# * Funciona INDEPENDENTEMENTE do banco de dados do thornspkg (para poder
# *   se recuperar mesmo se o DB estiver corrompido ou se a versão instalada
# *   estiver quebrada).
# * Fonte única: GitHub Releases API (sem PyPI).
# *   Endpoint: https://api.github.com/repos/<owner>/<repo>/releases/latest
# *   O release deve ter um asset .tar.gz (sdist). O campo "digest" do asset
# *   (formato "sha256:hex") é usado para verificação.
# * Estratégias de install (em ordem de preferência):
# *   1. pip install <sdist.tar.gz>     — se pip disponível
# *   2. pip install --user <sdist>     — se não tiver perms de root
# *   3. extrair diretamente no sys.prefix — fallback sem pip (arriscado)
# * Arquivo: thornspkg/selfupdate.py

"""Self-update do thornspkg via GitHub Releases.

Este módulo permite que o thornspkg se atualize independentemente do seu
próprio banco de dados de pacotes. Isso é importante porque:

1. Se o DB estiver corrompido, `thorn upgrade thornspkg` não funcionaria
2. Se a versão instalada tiver um bug no resolvedor de deps, o upgrade
   via `thorn upgrade` pode falhar
3. Para bootstrapping inicial em sistemas novos

O comando `thorn self-update` faz tudo de forma autônoma:
  - consulta o GitHub Releases API
  - compara com a versão instalada
  - baixa o asset .tar.gz
  - verifica SHA256 (do campo "digest" do asset)
  - instala via pip (ou fallback)

Configuração do repositório GitHub
-----------------------------------
Por padrão, consulta:
  https://api.github.com/repos/fuyu3/thornspkg/releases/latest

Você pode sobrescrever o owner/repo via variável de ambiente
THORN_SELFUPDATE_REPO (formato "owner/repo") ou pela flag --repo.

Para funcionar, o release mais recente no GitHub deve ter:
  - tag_name: "v0.4.3" (com prefixo 'v')
  - um asset chamado "thornspkg-<versão>.tar.gz" (o sdist)
  - o campo digest do asset deve ser "sha256:<hex>" (GitHub calcula
    automaticamente quando você faz upload via API)

Consulte docs/ATUALIZAR_O_THORN.md para o passo a passo de como
publicar um release correto.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__ as CURRENT_VERSION
from .colors import c, ce
from .downloader import DownloadError, download_to_temp
from .version import compare


# ---------------------------------------------------------------------------
# exceções
# ---------------------------------------------------------------------------

class SelfUpdateError(Exception):
    """Erro durante self-update."""
    pass


# ---------------------------------------------------------------------------
# configuração do repositório GitHub
# ---------------------------------------------------------------------------

# Default: owner/repo no GitHub. Pode ser sobrescrito via:
#   - Variável de ambiente THORN_SELFUPDATE_REPO="owner/repo"
#   - Flag --repo owner/repo no CLI
DEFAULT_GITHUB_REPO = "fuyu3/thornspkg"


def get_github_repo(override: str | None = None) -> str:
    """Retorna o repositório GitHub no formato 'owner/repo'.

    Prioridade: override > env > default.
    """
    if override:
        return _normalize_repo(override)
    env = os.environ.get("THORN_SELFUPDATE_REPO")
    if env:
        return _normalize_repo(env)
    return DEFAULT_GITHUB_REPO


def _normalize_repo(s: str) -> str:
    """Normaliza para 'owner/repo', removendo URLs completas se necessário."""
    s = s.strip().strip("/")
    # Remove prefixos de URL
    s = re.sub(r"^https?://github\.com/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^git@github\.com:", "", s, flags=re.IGNORECASE)
    s = s.removesuffix(".git")
    # Valida formato owner/repo
    if "/" not in s or s.count("/") > 1:
        raise SelfUpdateError(
            f"repositório inválido: {s!r} (use formato 'owner/repo')"
        )
    return s


def github_api_url(repo: str, *, tag: str | None = None) -> str:
    """Constrói a URL do GitHub Releases API.

    Args:
        repo: "owner/repo"
        tag:  se None, busca /releases/latest; senão /releases/tags/<tag>
    """
    base = f"https://api.github.com/repos/{repo}/releases"
    if tag is None:
        return f"{base}/latest"
    return f"{base}/tags/{tag}"


# ---------------------------------------------------------------------------
# fetch do release
# ---------------------------------------------------------------------------

@dataclass
class ReleaseInfo:
    """Informações sobre uma release disponível para download."""
    version: str
    download_url: str
    sha256: str | None = None
    size: int | None = None
    source: str = "github"
    tag_name: str | None = None      # ex: "v0.4.3"
    release_name: str | None = None  # título do release
    release_notes: str | None = None # body (markdown) do release
    repo: str | None = None          # "owner/repo" usado


def _parse_sha256_digest(digest: str | None) -> str | None:
    """Converte 'sha256:hex...' para apenas 'hex...'.

    GitHub API retorna digests no formato "sha256:hexadecimal...".
    """
    if not digest:
        return None
    if digest.startswith("sha256:"):
        return digest[len("sha256:"):]
    # Outros algoritmos (sha512, md5) não suportados — retorna None
    if ":" in digest:
        algo = digest.split(":", 1)[0]
        if algo != "sha256":
            return None
    return digest


def _select_sdist_asset(assets: list[dict]) -> dict | None:
    """Seleciona o asset .tar.gz (sdist) da lista de assets do release.

    Preferência:
      1. Asset chamado "thornspkg-<versão>.tar.gz"
      2. Qualquer asset .tar.gz
      3. Qualquer asset .zip (último recurso)
    """
    # Primeiro: thornspkg-*.tar.gz
    for a in assets:
        name = a.get("name", "")
        if name.startswith("thornspkg-") and name.endswith(".tar.gz"):
            return a
    # Depois: qualquer .tar.gz
    for a in assets:
        if a.get("name", "").endswith(".tar.gz"):
            return a
    # Por último: .zip
    for a in assets:
        if a.get("name", "").endswith(".zip"):
            return a
    return None


def fetch_release(
    repo: str = DEFAULT_GITHUB_REPO,
    *,
    tag: str | None = None,
) -> ReleaseInfo:
    """Consulta o GitHub Releases API para um release.

    Args:
        repo: "owner/repo" (ex: "fuyu3/thornspkg")
        tag:  se None, busca o release mais recente;
              senão, busca a tag específica (sem prefixo 'v').

    Returns:
        ReleaseInfo com versão, URL do asset, SHA256 (do digest), etc.

    Raises:
        SelfUpdateError: se a consulta falhar ou o release não tiver asset.

    Autenticação:
        Se a variável de ambiente GITHUB_TOKEN estiver definida, ela é usada
        como Bearer token. Isso aumenta o rate limit de 60 para 5000 req/hora
        e permite acessar repositórios privados.
    """
    # Normaliza tag (remove prefixo 'v' se presente para a URL da API)
    api_tag = tag.lstrip("v") if tag else None
    url = github_api_url(repo, tag=api_tag)

    # GitHub API exige User-Agent e recomenda Accept: application/vnd.github+json
    gh_headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Autenticação opcional via GITHUB_TOKEN (aumenta rate limit)
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        gh_headers["Authorization"] = f"Bearer {github_token}"

    tmp = Path(tempfile.mkstemp(suffix=".json")[1])
    try:
        try:
            download_to_temp(url, tmp, timeout=30, headers=gh_headers)
        except DownloadError as e:
            msg = str(e)
            if "403" in msg or "401" in msg:
                # Tenta distinguir rate limit de repo privado
                if "rate limit" in msg.lower():
                    raise SelfUpdateError(
                        f"GitHub API rate limit excedido: {e}\n"
                        f"  aguarde alguns minutos ou defina GITHUB_TOKEN para autenticar"
                    ) from e
                raise SelfUpdateError(
                    f"GitHub API rejeitou a consulta (403): {e}\n"
                    f"  o repositório pode ser privado — defina GITHUB_TOKEN"
                ) from e
            if "404" in msg:
                raise SelfUpdateError(
                    f"release não encontrado no repositório '{repo}'"
                    + (f" tag '{tag}'" if tag else "")
                    + f".\n  verifique se o repositório existe e tem releases publicados."
                ) from e
            raise SelfUpdateError(
                f"não foi possível consultar GitHub API: {e}\n"
                f"  verifique sua conexão ou use --url para uma fonte customizada"
            ) from e

        try:
            data = json.loads(tmp.read_text())
        except json.JSONDecodeError as e:
            raise SelfUpdateError(f"resposta do GitHub inválida: {e}") from e

        tag_name = data.get("tag_name")
        if not tag_name:
            raise SelfUpdateError("GitHub não retornou 'tag_name'")

        # Versão = tag sem prefixo 'v'
        version = tag_name.lstrip("v")

        # Procura pelo asset sdist
        assets = data.get("assets", [])
        if not assets:
            raise SelfUpdateError(
                f"release '{tag_name}' não tem assets. "
                f"faça upload do sdist .tar.gz ao publicar o release."
            )

        asset = _select_sdist_asset(assets)
        if asset is None:
            available = ", ".join(a.get("name", "?") for a in assets)
            raise SelfUpdateError(
                f"release '{tag_name}' não tem asset .tar.gz. "
                f"assets encontrados: {available}"
            )

        download_url = asset.get("browser_download_url")
        if not download_url:
            raise SelfUpdateError(f"asset sem 'browser_download_url': {asset}")

        # SHA256 do campo digest (GitHub calcula automaticamente no upload)
        sha256 = _parse_sha256_digest(asset.get("digest"))
        size = asset.get("size")

        return ReleaseInfo(
            version=version,
            download_url=download_url,
            sha256=sha256,
            size=size,
            source="github",
            tag_name=tag_name,
            release_name=data.get("name"),
            release_notes=data.get("body"),
            repo=repo,
        )
    finally:
        tmp.unlink(missing_ok=True)


def fetch_latest(repo: str = DEFAULT_GITHUB_REPO) -> ReleaseInfo:
    """Atalho para fetch_release(repo, tag=None)."""
    return fetch_release(repo, tag=None)


# ---------------------------------------------------------------------------
# verificação de versão
# ---------------------------------------------------------------------------

def is_newer_available(current: str, latest: str) -> bool:
    """True se `latest` > `current`."""
    try:
        return compare(latest, current) > 0
    except Exception:
        # Fallback: simples diferença de strings
        return latest != current


# ---------------------------------------------------------------------------
# instalação
# ---------------------------------------------------------------------------

def is_pip_available() -> bool:
    """Verifica se pip está disponível no Python atual."""
    try:
        import importlib.util
        return importlib.util.find_spec("pip") is not None
    except ImportError:
        return False


def is_user_install() -> bool:
    """Detecta se thornspkg foi instalado com --user."""
    import thornspkg
    pkg_path = Path(thornspkg.__file__).resolve()
    return ".local" in str(pkg_path)


def install_via_pip(tarball: Path, *, user: bool = False, upgrade: bool = True) -> None:
    """Instala o tarball via pip.

    Args:
        tarball:  caminho do sdist .tar.gz
        user:     se True, usa --user (instala em ~/.local/)
        upgrade:  se True, usa --upgrade
    """
    cmd = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")
    if user:
        cmd.append("--user")
    cmd.append("--no-deps")  # o thornspkg não tem deps externas
    cmd.append(str(tarball))

    print(c(f"  $ {' '.join(cmd)}", "dim"))
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as e:
        raise SelfUpdateError("pip install timed out após 5 minutos") from e

    if result.returncode != 0:
        output_lines = result.stdout.strip().split("\n")[-20:]
        raise SelfUpdateError(
            f"pip install falhou (exit {result.returncode}):\n"
            + "\n".join(f"  {line}" for line in output_lines)
        )


def install_via_extract(tarball: Path, dest: Path) -> None:
    """Fallback sem pip: extrai o tarball diretamente no destino.

    ARRISCADO: pode deixar o sistema inconsistente se a extração for
    interrompida. Use só se pip não estiver disponível.
    """
    import tarfile
    print(c(f"  ⚠  instalando sem pip (extração direta em {dest})", "yellow"))
    print(c("     isso é arriscado — considere instalar pip", "yellow"))
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as tf:
        members = tf.getmembers()
        if not members:
            raise SelfUpdateError("tarball vazio")
        prefix = members[0].name.split("/")[0]
        for member in members:
            if member.name.startswith(prefix + "/"):
                member.name = member.name[len(prefix) + 1:]
                if member.name:
                    tf.extract(member, dest)


# ---------------------------------------------------------------------------
# orquestrador principal
# ---------------------------------------------------------------------------

def perform_self_update(
    *,
    repo: str | None = None,
    tag: str | None = None,
    url: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    yes: bool = False,
) -> int:
    """Executa o self-update completo via GitHub Releases.

    Args:
        repo:    "owner/repo" do GitHub (default: THORN_SELFUPDATE_REPO env ou
                 DEFAULT_GITHUB_REPO)
        tag:     tag específica para instalar (ex: "v0.4.3" ou "0.4.3").
                 Se None, instala o release mais recente.
        url:     URL customizada do tarball (sobrescreve repo/tag).
        dry_run: só mostra o que seria feito.
        force:   reinstala mesmo se já está na versão mais recente.
        yes:     não pede confirmação.

    Returns:
        0 em sucesso, 1 em erro (mensagem já impressa).
    """
    print(c(f"thornspkg self-update (versão atual: {CURRENT_VERSION})", "bold"))
    print()

    # 1. Buscar informações do release
    try:
        if url:
            # URL customizada — extrai versão do nome do arquivo
            m = re.search(r"thornspkg[_-]v?(\d+\.\d+\.\d+(?:[\.\-+]\w+)?)", url)
            version = m.group(1) if m else "0.0.0"
            release = ReleaseInfo(
                version=version,
                download_url=url,
                sha256=None,
                source="custom-url",
            )
            print(f"  Fonte:          {release.source}")
        else:
            github_repo = get_github_repo(repo)
            release = fetch_release(github_repo, tag=tag)
            print(f"  Fonte:          GitHub Releases")
            print(f"  Repositório:    github.com/{github_repo}")
            if release.tag_name:
                print(f"  Tag:            {release.tag_name}")
            if release.release_name:
                print(f"  Título:         {release.release_name}")
    except SelfUpdateError as e:
        print(ce(f"erro: {e}", "red"))
        return 1

    print(f"  Versão remota:  {release.version}")
    print(f"  URL:            {release.download_url}")
    if release.sha256:
        print(f"  SHA256:         {release.sha256[:16]}…")
    else:
        print(c("  SHA256:         (não disponível — sem verificação)", "yellow"))
    if release.size:
        print(f"  Tamanho:        {release.size} bytes")
    print()

    # 2. Comparar versões
    is_newer = is_newer_available(CURRENT_VERSION, release.version)
    if not is_newer and not force:
        print(c(f"✓ já está na versão mais recente ({CURRENT_VERSION})", "green"))
        print(c("  use --force para reinstalar a mesma versão", "dim"))
        return 0

    if is_newer:
        print(c(f"  → atualização disponível: {CURRENT_VERSION} → {release.version}", "bright_yellow"))
    else:
        print(c(f"  → reinstalando versão {release.version} (--force)", "yellow"))

    # Mostra release notes se disponível (primeiras 5 linhas)
    if release.release_notes:
        notes_lines = release.release_notes.strip().split("\n")[:5]
        if notes_lines:
            print(c("\n  Notas do release:", "dim"))
            for line in notes_lines:
                print(f"    {line}")
            if len(release.release_notes.strip().split("\n")) > 5:
                print(c("    ... (veja mais no GitHub)", "dim"))
            print()

    if dry_run:
        print(c("\n[dry-run] nenhum download foi feito.", "yellow"))
        return 0

    # 3. Confirmar
    if not yes:
        try:
            resp = input(f"\n  Confirmar atualização para {release.version}? [s/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            print("Operação cancelada.")
            return 0
        if resp not in ("s", "y", "sim", "yes"):
            print("Operação cancelada.")
            return 0

    # 4. Baixar tarball
    print()
    print(c("→ baixando tarball…", "cyan"))
    tmpdir = Path(tempfile.mkdtemp(prefix="thorn-selfupdate-"))
    try:
        # Nome do arquivo = basename da URL
        filename = release.download_url.rsplit("/", 1)[-1]
        tarball = tmpdir / filename
        try:
            download_to_temp(release.download_url, tarball, timeout=600)
        except DownloadError as e:
            print(ce(f"erro: falha no download: {e}", "red"))
            return 1
        print(c(f"  ✓ baixado: {tarball.name} ({tarball.stat().st_size} bytes)", "green"))

        # 5. Verificar SHA256 se disponível
        if release.sha256:
            import hashlib
            h = hashlib.sha256()
            with open(tarball, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            actual = h.hexdigest()
            if actual != release.sha256:
                print(ce(f"erro: SHA256 diverge!", "red"))
                print(f"  esperado: {release.sha256}")
                print(f"  obtido:   {actual}")
                return 1
            print(c("  ✓ checksum OK", "green"))
        else:
            print(c("  ⚠ sem SHA256 para verificar (asset não tem digest)", "yellow"))
            print(c("    para ativar verificação, faça upload do asset via API", "dim"))

        # 6. Instalar
        print()
        print(c("→ instalando…", "cyan"))
        if is_pip_available():
            user = is_user_install()
            try:
                install_via_pip(tarball, user=user, upgrade=True)
            except SelfUpdateError as e:
                print(ce(f"erro: {e}", "red"))
                return 1
            print(c("  ✓ pip install concluído", "green"))
        else:
            import site
            dest = Path(site.getsitepackages()[0])
            try:
                install_via_extract(tarball, dest)
            except SelfUpdateError as e:
                print(ce(f"erro: {e}", "red"))
                return 1
            print(c("  ✓ extração concluída (sem pip)", "green"))

        # 7. Verificar
        print()
        print(c(f"✓ self-update concluído: {CURRENT_VERSION} → {release.version}", "bright_green"))
        print(c("  execute 'thorn version' para confirmar", "dim"))
        print(c("  ⚠ se havia instância do thorn em execução, ela ainda usa a versão antiga;", "yellow"))
        print(c("    abra um novo shell ou reexecute o comando.", "yellow"))
        return 0

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
