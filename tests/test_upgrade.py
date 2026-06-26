# * Testes do fluxo de upgrade — garante que arquivos obsoletos sejam removidos.
# * Cobre: cleanup_obsolete_files() helper + fluxo end-to-end de install v1 → upgrade v2.
# * Arquivo: tests/test_upgrade.py

"""Testes do fluxo de upgrade."""

import json
import tempfile
import unittest
from pathlib import Path

from thornspkg.commands.common import cleanup_obsolete_files
from thornspkg.config import Config


def make_cfg(td: Path) -> Config:
    """Cria Config com todos os paths dentro de td."""
    return Config(
        recipes_dir=td / "recipes",
        patches_dir=td / "patches",
        sources_dir=td / "cache" / "sources",
        build_dir=td / "build",
        db_dir=td / "db",
        root_dir=td / "root",
        prefix="/usr",
        hooks_dir=td / "hooks",
        jobs=1,
    )


class TestCleanupObsoleteFiles(unittest.TestCase):

    def test_no_cleanup_for_new_package(self):
        """Pacote novo (não instalado) → não remove nada."""
        with tempfile.TemporaryDirectory() as td:
            cfg = make_cfg(Path(td))
            cfg.root_dir.mkdir(parents=True, exist_ok=True)
            db = {"packages": {}}
            # Não deve crashar nem remover nada
            removed = cleanup_obsolete_files("newpkg", ["usr/bin/newpkg"], db, cfg)
            self.assertEqual(removed, 0)

    def test_no_cleanup_when_manifest_unchanged(self):
        """Manifest idêntico → não remove nada."""
        with tempfile.TemporaryDirectory() as td:
            cfg = make_cfg(Path(td))
            (cfg.root_dir / "usr/bin").mkdir(parents=True, exist_ok=True)
            (cfg.root_dir / "usr/bin/demo").write_text("v1")
            db = {"packages": {"demo": {
                "version": "1.0",
                "files": ["usr/bin/demo"],
            }}}
            removed = cleanup_obsolete_files(
                "demo", ["usr/bin/demo"], db, cfg
            )
            self.assertEqual(removed, 0)
            # Arquivo ainda existe
            self.assertTrue((cfg.root_dir / "usr/bin/demo").exists())

    def test_removes_obsolete_files(self):
        """Arquivos no manifest antigo mas não no novo devem ser removidos."""
        with tempfile.TemporaryDirectory() as td:
            cfg = make_cfg(Path(td))
            # Criar diretórios pai para os 3 arquivos
            for p in ["usr/bin", "usr/share/demo"]:
                (cfg.root_dir / p).mkdir(parents=True, exist_ok=True)
            (cfg.root_dir / "usr/bin/demo").write_text("v1")
            (cfg.root_dir / "usr/share/demo/data.txt").write_text("v1")
            (cfg.root_dir / "usr/share/demo/legacy.txt").write_text("v1")
            db = {"packages": {"demo": {
                "version": "1.0",
                "files": [
                    "usr/bin/demo",
                    "usr/share/demo/data.txt",
                    "usr/share/demo/legacy.txt",
                ],
            }}}
            # Novo manifest v2 não tem legacy.txt
            new_manifest = [
                "usr/bin/demo",
                "usr/share/demo/data.txt",
            ]
            removed = cleanup_obsolete_files("demo", new_manifest, db, cfg)
            self.assertEqual(removed, 1)
            # legacy.txt foi removido
            self.assertFalse((cfg.root_dir / "usr/share/demo/legacy.txt").exists())
            # Outros arquivos continuam
            self.assertTrue((cfg.root_dir / "usr/bin/demo").exists())
            self.assertTrue((cfg.root_dir / "usr/share/demo/data.txt").exists())

    def test_removes_multiple_obsolete_files(self):
        """Múltiplos arquivos obsoletos são removidos."""
        with tempfile.TemporaryDirectory() as td:
            cfg = make_cfg(Path(td))
            (cfg.root_dir / "usr/bin").mkdir(parents=True, exist_ok=True)
            for p in ["usr/bin/old1", "usr/bin/old2", "usr/bin/keep"]:
                (cfg.root_dir / p).write_text("x")
            db = {"packages": {"pkg": {
                "version": "1.0",
                "files": ["usr/bin/old1", "usr/bin/old2", "usr/bin/keep"],
            }}}
            removed = cleanup_obsolete_files(
                "pkg", ["usr/bin/keep"], db, cfg
            )
            self.assertEqual(removed, 2)
            self.assertFalse((cfg.root_dir / "usr/bin/old1").exists())
            self.assertFalse((cfg.root_dir / "usr/bin/old2").exists())
            self.assertTrue((cfg.root_dir / "usr/bin/keep").exists())


if __name__ == "__main__":
    unittest.main()
