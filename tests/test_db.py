# * Testes do módulo db.py — migração, metadados e funcionalidades existentes.
# * Cobre: migrate_db (compatibilidade com bancos antigos), record_install
# *   com novos metadados, find_dependents com deps versionadas, find_owner_of_file.
# * Arquivo: tests/test_db.py

"""Testes do módulo db.py."""

import json
import tempfile
import unittest
from pathlib import Path

from thornspkg.db import (
    REASON_DEPENDENCY,
    REASON_EXPLICIT,
    TransactionJournal,
    find_all_orphans_transitively,
    find_dependents,
    find_owner_of_file,
    get_metadata,
    load_db,
    migrate_db,
    record_install,
    save_db,
)
from thornspkg.recipe import Recipe


class TestDbMigration(unittest.TestCase):
    """Testa a migração automática de bancos antigos."""

    def test_migrate_old_db(self):
        """Banco no formato antigo (sem campos v0.4+) deve ser migrado."""
        old_db = {"packages": {
            "vim": {
                "version": "9.1",
                "depends": [],
                "reason": "explicit",
                "files": ["usr/bin/vim"],
                "installed_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "checked_at": None,
            }
        }}
        changed = migrate_db(old_db)
        self.assertTrue(changed)
        info = old_db["packages"]["vim"]
        # Campos novos devem estar presentes
        for field in ("install_date", "description", "homepage", "license",
                      "maintainer", "repository", "architecture", "build_date",
                      "install_size", "download_size"):
            self.assertIn(field, info, f"campo {field} deveria estar presente")
        # install_date deve ter sido populado com installed_at
        self.assertEqual(info["install_date"], "2024-01-01T00:00:00+00:00")
        # Defaults seguros
        self.assertEqual(info["description"], "")
        self.assertIsNone(info["license"])

    def test_migrate_idempotent(self):
        """Migrar um banco já migrado não deve mudar nada."""
        db = {"packages": {
            "vim": {
                "version": "9.1", "depends": [], "reason": "explicit",
                "files": [], "installed_at": "2024-01-01",
                "updated_at": "2024-01-01", "checked_at": None,
                "description": "Vi Improved", "license": "Vim",
            }
        }}
        migrate_db(db)
        snapshot = json.dumps(db, sort_keys=True)
        changed = migrate_db(db)
        self.assertFalse(changed)
        self.assertEqual(json.dumps(db, sort_keys=True), snapshot)

    def test_load_db_auto_migrates(self):
        """load_db() deve migrar automaticamente."""
        with tempfile.TemporaryDirectory() as td:
            db_dir = Path(td)
            old_db = {"packages": {
                "zlib": {
                    "version": "1.3.1", "depends": [], "reason": "explicit",
                    "files": ["usr/lib/libz.so"],
                    "installed_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                    "checked_at": None,
                }
            }}
            save_db(db_dir, old_db)
            db = load_db(db_dir)
            info = db["packages"]["zlib"]
            self.assertIn("install_date", info)
            self.assertIn("description", info)


class TestRecordInstall(unittest.TestCase):
    """Testa record_install com novos metadados."""

    def test_record_install_with_metadata(self):
        recipe = Recipe(
            name="newpkg", version="1.0", path=Path("test.toml"),
            description="Test package",
            homepage="https://example.com",
            license="MIT",
            maintainer="dev@example.com",
            repository="core",
            architecture="x86_64",
            install_size=1024,
            download_size=512,
        )
        db = {"packages": {}}
        record_install(db, recipe, ["usr/bin/newpkg"], reason=REASON_EXPLICIT)
        info = db["packages"]["newpkg"]
        self.assertEqual(info["version"], "1.0")
        self.assertEqual(info["description"], "Test package")
        self.assertEqual(info["homepage"], "https://example.com")
        self.assertEqual(info["license"], "MIT")
        self.assertEqual(info["maintainer"], "dev@example.com")
        self.assertEqual(info["repository"], "core")
        self.assertEqual(info["architecture"], "x86_64")
        self.assertEqual(info["install_size"], 1024)
        self.assertEqual(info["download_size"], 512)
        self.assertIn("install_date", info)
        self.assertIn("installed_at", info)

    def test_record_install_preserves_explicit_reason(self):
        """Pacote explicit não vira dependency em reinstall."""
        recipe = Recipe(name="pkg", version="1.0", path=Path("t.toml"))
        db = {"packages": {"pkg": {
            "version": "0.9", "reason": REASON_EXPLICIT, "depends": [],
            "files": ["usr/bin/pkg"], "installed_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }}}
        record_install(db, recipe, ["usr/bin/pkg"], reason=REASON_DEPENDENCY)
        self.assertEqual(db["packages"]["pkg"]["reason"], REASON_EXPLICIT)

    def test_record_install_extra_metadata(self):
        """extra_metadata sobrescreve valores da receita."""
        recipe = Recipe(name="pkg", version="1.0", path=Path("t.toml"),
                        description="from recipe")
        db = {"packages": {}}
        record_install(
            db, recipe, ["usr/bin/pkg"],
            extra_metadata={"description": "from repo", "repository": "extra"},
        )
        self.assertEqual(db["packages"]["pkg"]["description"], "from repo")
        self.assertEqual(db["packages"]["pkg"]["repository"], "extra")


class TestDependentsAndOrphans(unittest.TestCase):
    """Testa find_dependents e find_all_orphans_transitively com deps versionadas."""

    def test_find_dependents_with_versioned_deps(self):
        """'openssl>=3.0' deve contar como dependente de 'openssl'."""
        db = {"packages": {
            "openssl": {"version": "3.3.1", "depends": [], "reason": "explicit", "files": []},
            "curl": {"version": "8.9.1", "depends": ["openssl>=3.0"], "reason": "explicit", "files": []},
        }}
        deps = find_dependents(db, "openssl")
        self.assertIn("curl", deps)

    def test_find_orphans_with_versioned_deps(self):
        """Pacote que só é referenciado com versionamento não é órfão."""
        packages = {
            "openssl": {"version": "3.3.1", "depends": [], "reason": REASON_DEPENDENCY, "files": []},
            "curl": {"version": "8.9.1", "depends": ["openssl>=3.0"], "reason": REASON_EXPLICIT, "files": []},
        }
        orphans = find_all_orphans_transitively(packages)
        # openssl NÃO é órfão porque curl depende dele (mesmo com constraint)
        self.assertNotIn("openssl", orphans)


class TestFileOwner(unittest.TestCase):

    def test_find_owner_of_file(self):
        db = {"packages": {
            "vim": {"files": ["usr/bin/vim", "usr/share/vim/vimrc"], "version": "9.1"},
        }}
        self.assertEqual(find_owner_of_file(db, "usr/bin/vim"), "vim")
        self.assertIsNone(find_owner_of_file(db, "usr/bin/foo"))

    def test_get_metadata(self):
        db = {"packages": {"vim": {"version": "9.1", "license": "Vim"}}}
        meta = get_metadata(db, "vim")
        self.assertEqual(meta["license"], "Vim")
        # Campos ausentes devem ter defaults
        self.assertIsNone(meta.get("homepage"))


class TestTransactionJournal(unittest.TestCase):
    """Smoke test do TransactionJournal."""

    def test_journal_record_and_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            db_dir = Path(td)
            journal = TransactionJournal(db_dir)
            journal.record("pkg1", "1.0", "explicit",
                          ["usr/bin/pkg1"], {"usr/bin/pkg1": "abc"})
            self.assertEqual(journal.package_names, ["pkg1"])
            self.assertTrue(TransactionJournal.is_pending(db_dir))
            journal.clear()
            self.assertFalse(TransactionJournal.is_pending(db_dir))


if __name__ == "__main__":
    unittest.main()
