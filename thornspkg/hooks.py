# * Hooks globais do sistema — scripts executáveis em <hooks_dir>/<fase>.d/.
# * Fases: pre-install, post-install, pre-remove, post-remove.
# * Scripts são chamados em ordem lexicográfica (00-ldconfig, 10-mandb...).
# * Códigos de saída: 0=continua, 75=EX_TEMPFAIL (falha ignorável), outro=erro crítico.
# * Variáveis de ambiente nos scripts: THORN_PACKAGE, THORN_VERSION, THORN_ROOT, THORN_PREFIX.
# * Função principal: run_global_hooks() — usada por cli.py durante install/remove.
# * Arquivo: thornspkg/hooks.py

"""Hooks globais do sistema para o thornspkg.

Estrutura esperada em disco:
  <hooks_dir>/
    pre-install.d/    — executados antes de instalar cada pacote
    post-install.d/   — executados após instalar cada pacote
    pre-remove.d/     — executados antes de remover cada pacote
    post-remove.d/    — executados após remover cada pacote

Cada diretório pode conter qualquer número de scripts executáveis.
Eles são chamados em ordem lexicográfica (00-ldconfig, 10-mandb...).

Códigos de saída:
  0    sucesso — continua normalmente
  75   EX_TEMPFAIL — falha não-crítica, hook pede para ser ignorado
       (útil para hooks opcionais como "regenerar initramfs se instalado")
  outro — erro crítico — aborta a operação (lança HookError)

Variáveis de ambiente disponíveis nos scripts:
  THORN_PACKAGE   nome do pacote
  THORN_VERSION   versão do pacote
  THORN_ROOT      raiz de instalação (--root)
  THORN_PREFIX    prefixo de instalação (--prefix)

Exemplo de hook que nunca aborta:
  #!/bin/sh
  update-desktop-database 2>/dev/null || exit 75

Exemplo de hook crítico:
  #!/bin/sh
  ldconfig || exit 1
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# EX_TEMPFAIL (sysexits.h): "temporary failure, user is invited to retry"
# Usado aqui como "hook falhou mas a operação pode continuar"
SKIP_EXIT_CODE = 75


class HookError(Exception):
    pass


def run_global_hooks(
    hooks_dir: Path,
    phase: str,
    pkg_name: str,
    pkg_version: str,
    root_dir: str,
    prefix: str,
) -> None:
    """Executa todos os scripts executáveis em <hooks_dir>/<phase>.d/.

    `phase` deve ser um de: pre-install, post-install, pre-remove, post-remove.

    Lança HookError se qualquer script retornar um código != 0 e != 75.
    Scripts que retornam 75 são silenciosamente ignorados.
    """
    phase_dir = hooks_dir / f"{phase}.d"
    if not phase_dir.is_dir():
        return

    scripts = sorted(
        p for p in phase_dir.iterdir()
        if p.is_file() and os.access(p, os.X_OK)
    )
    if not scripts:
        return

    env = os.environ.copy()
    env.update({
        "THORN_PACKAGE": pkg_name,
        "THORN_VERSION": pkg_version,
        "THORN_ROOT":    root_dir,
        "THORN_PREFIX":  prefix,
    })

    for script in scripts:
        result = subprocess.run([str(script)], env=env)
        if result.returncode in (0, SKIP_EXIT_CODE):
            continue
        raise HookError(
            f"hook '{script.name}' ({phase}.d) falhou com exit {result.returncode}"
        )
