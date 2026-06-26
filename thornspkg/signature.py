# * Interface para verificação futura de assinaturas GPG — PLACEHOLDER.
# * Este módulo NÃO implementa GPG. Apenas define a interface (verify_signature,
# * is_signature_verification_enabled, download_signature) para que o restante
# * do código possa chamar essas funções sem saber dos detalhes de implementação.
# * Quando GPG for implementado de verdade, basta modificar este arquivo.
# * Arquivo: thornspkg/signature.py

"""Interface para verificação futura de assinaturas GPG.

Este módulo NÃO implementa GPG completo. Apenas define a interface
que será usada pelo restante do código, permitindo que a verificação
de assinaturas seja adicionada no futuro sem alterar a arquitetura.

Quando a implementação GPG for adicionada, basta modificar as funções
neste módulo. O restante do código chama verify_signature() e
is_signature_verification_enabled() sem saber dos detalhes.
"""

from __future__ import annotations

from pathlib import Path


class SignatureError(Exception):
    """Falha na verificação de assinatura."""
    pass


def is_signature_verification_enabled() -> bool:
    """Verifica se a verificação de assinaturas está habilitada.

    Por enquanto, retorna sempre False (funcionalidade não implementada).
    No futuro, pode ler de configuração ou detectar gpg no sistema.
    """
    return False


def verify_signature(file_path: Path, signature_path: Path, keyring: Path | None = None) -> bool:
    """Verifica a assinatura GPG de um arquivo.

    Args:
        file_path:       caminho do arquivo a verificar
        signature_path:  caminho do arquivo de assinatura (.sig ou .asc)
        keyring:         keyring GPG específico (opcional)

    Returns:
        True se a assinatura for válida.

    Raises:
        SignatureError: se a verificação falhar ou não estiver implementada.

    NOTA: Esta função é um placeholder. A implementação real usará
    gpgv ou a biblioteca gnupg para verificar a assinatura.
    """
    # Placeholder — quando implementado, algo como:
    #   result = subprocess.run(
    #       ["gpgv", "--keyring", str(keyring or default_keyring),
    #        str(signature_path), str(file_path)],
    #       capture_output=True,
    #   )
    #   if result.returncode != 0:
    #       raise SignatureError(f"assinatura inválida para {file_path}")
    #   return True

    raise SignatureError(
        "verificação de assinaturas GPG ainda não implementada. "
        "Use --no-verify-signature para pular a verificação."
    )


def download_signature(file_url: str) -> str | None:
    """Retorna a URL esperada para a assinatura de um arquivo.

    Convenção: <url>.sig ou <url>.asc

    No futuro, esta função pode tentar baixar a assinatura e retornar
    o caminho local. Por enquanto, apenas retorna a URL esperada.
    """
    # Tenta .sig primeiro, depois .asc
    for ext in (".sig", ".asc"):
        sig_url = file_url + ext
        # No futuro: verificar se a URL existe
        # Por enquanto, apenas retorna a URL presumida
        return sig_url
    return None