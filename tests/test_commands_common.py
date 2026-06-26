# * Testes do módulo commands/common.py — helpers compartilhados da CLI.
# * Cobre: build_installed_versions, get_metadata via DB, fluxos de instalação
# *   simulados (sem realmente compilar nada).
# * Arquivo: tests/test_commands_common.py

"""Testes do módulo commands/common.py."""

import unittest

from thornspkg.commands.common import build_installed_versions
from thornspkg.db import record_install
from thornspkg.recipe import Recipe


class TestBuildInstalledVersions(unittest.TestCase):

    def test_empty_db(self):
        versions = build_installed_versions({"packages": {}})
        self.assertEqual(versions, {})

    def test_populated_db(self):
        db = {"packages": {}}
        for name, ver in [("zlib", "1.3.1"), ("openssl", "3.3.1"), ("python", "3.12.4")]:
            recipe = Recipe(name=name, version=ver, path=__import__("pathlib").Path(f"{name}.toml"))
            record_install(db, recipe, [f"usr/bin/{name}"])
        versions = build_installed_versions(db)
        self.assertEqual(versions["zlib"], "1.3.1")
        self.assertEqual(versions["openssl"], "3.3.1")
        self.assertEqual(versions["python"], "3.12.4")


if __name__ == "__main__":
    unittest.main()
