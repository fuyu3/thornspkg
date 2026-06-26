# * Módulo centralizado de download — curl preferencial, urllib como fallback.
# * Detecção automática do curl no sistema (cacheada após primeira verificação).
# * Quando curl está disponível: retries, timeout, follow redirects, mensagens amigáveis.
# * Quando curl não está disponível: fallback para urllib.request da stdlib.
# * Download atômico via download_to_temp(): escreve em .part e renomeia ao concluir.
# * Toda a lógica de download do thornspkg deve passar por este módulo.
# * Arquivo: thornspkg/downloader.py

"""Download centralizado com suporte a curl e fallback para urllib.

Preferência:
  1. curl (se disponível no sistema) — com retries, timeout e tratamento de erros
  2. urllib.request — fallback para sistemas sem curl

Toda a lógica de download do thornspkg deve passar por este módulo.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from .colors import c


class DownloadError(Exception):
    """Falha durante download."""
    pass


# ---------------------------------------------------------------------------
# detecção do curl
# ---------------------------------------------------------------------------

_curl_available: bool | None = None


def is_curl_available() -> bool:
    """Verifica se o comando curl está disponível no PATH."""
    global _curl_available
    if _curl_available is None:
        _curl_available = shutil.which("curl") is not None
    return _curl_available


def reset_curl_detection() -> None:
    """Reseta a detecção de curl (útil para testes)."""
    global _curl_available
    _curl_available = None


# ---------------------------------------------------------------------------
# download com curl
# ---------------------------------------------------------------------------

def download_curl(
    url: str,
    dest: Path,
    *,
    retries: int = 3,
    timeout: int = 120,
    follow_redirects: bool = True,
    headers: dict[str, str] | None = None,
) -> Path:
    """Baixa URL via curl.

    Args:
        url:              URL a baixar
        dest:             caminho de destino (arquivo)
        retries:          número de tentativas
        timeout:          timeout em segundos
        follow_redirects: se True, segue redirecionamentos (-L)
        headers:          headers HTTP adicionais (ex: {"User-Agent": "..."})

    Raises:
        DownloadError: se o download falhar após todas as tentativas
    """
    cmd = ["curl"]
    if follow_redirects:
        cmd.append("-L")
    cmd.extend([
        "--fail",              # retorna erro HTTP como falha
        "--retry", str(retries),
        "--retry-delay", "2",
        "--connect-timeout", str(timeout),
        "--max-time", str(timeout * 5),  # tempo total limitado
    ])
    # User-Agent default — alguns servidores (notably GitHub API) exigem
    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
        if "User-Agent" not in headers:
            cmd.extend(["-H", "User-Agent: thornspkg-downloader/1.0"])
    else:
        cmd.extend(["-H", "User-Agent: thornspkg-downloader/1.0"])
    cmd.extend(["-o", str(dest), url])

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        raise DownloadError("curl não encontrado no sistema")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Mensagens amigáveis para erros comuns
        if result.returncode == 22:
            detail = f"código de erro HTTP (curl exit 22): {stderr}"
        elif result.returncode == 28:
            detail = f"timeout atingido após {timeout}s (curl exit 28)"
        elif result.returncode == 6:
            detail = f"não foi possível resolver o host (curl exit 6)"
        elif result.returncode == 7:
            detail = f"não foi possível conectar ao servidor (curl exit 7)"
        else:
            detail = f"curl exit {result.returncode}: {stderr}"

        raise DownloadError(f"falha ao baixar '{url}': {detail}")


# ---------------------------------------------------------------------------
# download com urllib (fallback)
# ---------------------------------------------------------------------------

def download_urllib(url: str, dest: Path, *, headers: dict[str, str] | None = None) -> Path:
    """Baixa URL via urllib.request (fallback sem curl).

    Args:
        url:      URL a baixar
        dest:     caminho de destino
        headers:  headers HTTP adicionais (ex: {"User-Agent": "..."})

    Raises:
        DownloadError: se o download falhar
    """
    import urllib.request
    import urllib.error

    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    # User-Agent default — alguns servidores (notably GitHub API) exigem
    if "User-Agent" not in (headers or {}):
        req.add_header("User-Agent", "thornspkg-downloader/1.0")

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            with open(dest, "wb") as f:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
    except urllib.error.HTTPError as e:
        raise DownloadError(
            f"falha ao baixar '{url}': HTTP {e.code} ({e.reason})"
        ) from e
    except urllib.error.URLError as e:
        raise DownloadError(
            f"falha ao baixar '{url}': {e.reason}"
        ) from e
    except Exception as e:
        raise DownloadError(
            f"falha ao baixar '{url}': {e}"
        ) from e

    return dest


# ---------------------------------------------------------------------------
# interface unificada
# ---------------------------------------------------------------------------

def download(
    url: str,
    dest: Path,
    *,
    prefer_curl: bool = True,
    retries: int = 3,
    timeout: int = 120,
    headers: dict[str, str] | None = None,
) -> Path:
    """Baixa um arquivo, usando curl se disponível ou urllib como fallback.

    Args:
        url:         URL a baixar
        dest:        caminho de destino (arquivo)
        prefer_curl: se True, tenta curl antes de urllib
        retries:     número de retries (apenas curl)
        timeout:     timeout em segundos (apenas curl)
        headers:     headers HTTP adicionais

    Returns:
        Path para o arquivo baixado

    Raises:
        DownloadError: se o download falhar por todos os métodos
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    if prefer_curl and is_curl_available():
        try:
            return download_curl(
                url, dest, retries=retries, timeout=timeout, headers=headers,
            )
        except DownloadError:
            print(c("  aviso: curl falhou, tentando urllib…", "yellow"))

    return download_urllib(url, dest, headers=headers)


def download_to_temp(
    url: str,
    dest: Path,
    *,
    prefer_curl: bool = True,
    retries: int = 3,
    timeout: int = 120,
    headers: dict[str, str] | None = None,
) -> Path:
    """Baixa para arquivo temporário e renomeia ao concluir (download atômico).

    Evita arquivos corrompidos por downloads interrompidos.
    """
    tmp = dest.with_name(dest.name + ".part")
    try:
        download(
            url, tmp,
            prefer_curl=prefer_curl, retries=retries, timeout=timeout,
            headers=headers,
        )
    except DownloadError:
        if tmp.exists():
            tmp.unlink()
        raise
    tmp.rename(dest)
    return dest
