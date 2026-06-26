# * Testes do módulo fileconflict.py.
# * Cobre: build_file_index, find_owner, check_conflicts, assert_no_conflicts,
# *   FileConflictError (atributos e mensagem).
# * Arquivo: tests/test_fileconflict.py

"""Testes do módulo fileconflict.py."""

import unittest

from thornspkg.fileconflict import (
    FileConflictError,
    assert_no_conflicts,
    build_file_index,
    check_conflicts,
    find_owner,
    to_rel_path,
)


class TestFileConflict(unittest.TestCase):

    def setUp(self):
        self.db = {"packages": {
            "vim": {
                "files": ["usr/bin/vim", "usr/share/vim/vimrc"],
                "version": "9.1",
            },
            "curl": {
                "files": ["usr/bin/curl", "usr/lib/libcurl.so"],
                "version": "8.9.1",
            },
        }}

    def test_build_file_index(self):
        index = build_file_index(self.db)
        self.assertEqual(index["usr/bin/vim"], "vim")
        self.assertEqual(index["usr/bin/curl"], "curl")
        self.assertEqual(len(index), 4)

    def test_find_owner_existing(self):
        self.assertEqual(find_owner(self.db, "usr/bin/vim"), "vim")
        self.assertEqual(find_owner(self.db, "usr/bin/curl"), "curl")

    def test_find_owner_nonexistent(self):
        self.assertIsNone(find_owner(self.db, "usr/bin/foo"))

    def test_check_conflicts_no_conflict(self):
        manifest = ["usr/bin/new-tool", "usr/share/new-tool/data"]
        conflicts = check_conflicts(manifest, self.db, "new-tool")
        self.assertEqual(conflicts, [])

    def test_check_conflicts_with_conflict(self):
        manifest = ["usr/bin/vim", "usr/bin/new-tool"]
        conflicts = check_conflicts(manifest, self.db, "my-vim")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0], ("usr/bin/vim", "vim"))

    def test_check_conflicts_same_package_reinstall(self):
        # Reinstalar o mesmo pacote não é conflito
        manifest = ["usr/bin/vim", "usr/share/vim/vimrc"]
        conflicts = check_conflicts(manifest, self.db, "vim")
        self.assertEqual(conflicts, [])

    def test_assert_no_conflicts_raises(self):
        manifest = ["usr/bin/vim"]
        with self.assertRaises(FileConflictError) as ctx:
            assert_no_conflicts(manifest, self.db, "my-vim")
        self.assertEqual(ctx.exception.package, "my-vim")
        self.assertEqual(len(ctx.exception.conflicts), 1)
        self.assertIn("vim", str(ctx.exception))

    def test_assert_no_conflicts_allow_overwrite(self):
        manifest = ["usr/bin/vim"]
        # Com allow_overwrite=True, não levanta
        assert_no_conflicts(manifest, self.db, "my-vim", allow_overwrite=True)

    def test_to_rel_path(self):
        from pathlib import Path
        self.assertEqual(to_rel_path("/usr/bin/vim", Path("/")), "usr/bin/vim")
        self.assertEqual(to_rel_path("usr/bin/vim", Path("/")), "usr/bin/vim")
        self.assertEqual(to_rel_path("/usr/bin/vim/", Path("/")), "usr/bin/vim/")


if __name__ == "__main__":
    unittest.main()
