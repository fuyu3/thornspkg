# * Mecanismo de lock para impedir múltiplas instâncias simultâneas do thornspkg.
# * Usa fcntl.flock() (Unix) no arquivo /var/lib/thornspkg/db.lock.
# * Por padrão é não-bloqueante: se outra instância está rodando, falha imediatamente
# * com mensagem amigável. O lock é liberado automaticamente ao processo encerrar.
# * Pode ser usado como context manager: `with PackageLock(db_dir): ...`
# * Arquivo: thornspkg/lock.py

"""Mecanismo de lock para evitar múltiplas instâncias simultâneas do thornspkg.

Utiliza fcntl.flock() em sistemas Unix para obter um lock exclusivo
sobre /var/lib/thornspkg/db.lock. Se outra instância estiver ativa,
exibe mensagem amigável e aborta.

O lock é liberado automaticamente quando o processo encerra (mesmo por
sinal ou crash), pois flock() é associado ao file descriptor.
"""

from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path

from .colors import ce


class LockError(Exception):
    """Levantada quando não foi possível obter o lock."""
    pass


class PackageLock:
    """Lock exclusivo para o thornspkg.

    Uso como context manager:

        with PackageLock(db_dir):
            # operações protegidas
            ...

    Ou manual:

        lock = PackageLock(db_dir)
        lock.acquire()
        try:
            ...
        finally:
            lock.release()
    """

    def __init__(self, db_dir: Path, timeout_msg: str | None = None) -> None:
        self._db_dir = db_dir
        self._lock_path = db_dir / "db.lock"
        self._fd: int | None = None
        self._timeout_msg = timeout_msg or (
            "Outra instância do thornspkg está em execução.\n"
            "  Se isso for um erro, remova manualmente o lock em: "
            f"{self._lock_path}"
        )

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def acquire(self, blocking: bool = False) -> None:
        """Tenta obter o lock exclusivo.

        Args:
            blocking: se True, espera até que o lock seja liberado.
                     se False (padrão), falha imediatamente se já estiver travado.

        Raises:
            LockError: se o lock não puder ser obtido.
        """
        self._db_dir.mkdir(parents=True, exist_ok=True)

        # Abre (ou cria) o arquivo de lock
        self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)

        # Escreve o PID no arquivo para informação (não é usado para lock,
        # apenas para diagnóstico manual)
        try:
            os.ftruncate(self._fd, 0)
            os.write(self._fd, f"{os.getpid()}\n".encode())
        except OSError:
            pass  # não é crítico

        # Tenta obter o lock exclusivo
        operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if not blocking else 0)
        try:
            fcntl.flock(self._fd, operation)
        except (OSError, BlockingIOError):
            self._cleanup()
            raise LockError(self._timeout_msg)

    def release(self) -> None:
        """Libera o lock e fecha o arquivo."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            self._cleanup()

    def _cleanup(self) -> None:
        """Fecha o file descriptor."""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> "PackageLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
        return None  # não suprime exceções

    def __del__(self) -> None:
        # Segurança: garante liberação em caso de esquecimento
        self.release()
