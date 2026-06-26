# * Testes do módulo selfupdate.py.
# * Cobre: is_newer_available, ReleaseInfo parsing, fetch_release com servidor
# *   GitHub API fake, normalização de repositório, _select_sdist_asset,
# *   _parse_sha256_digest.
# * Arquivo: tests/test_selfupdate.py

"""Testes do módulo selfupdate.py."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from thornspkg.selfupdate import (
    DEFAULT_GITHUB_REPO,
    ReleaseInfo,
    SelfUpdateError,
    _normalize_repo,
    _parse_sha256_digest,
    _select_sdist_asset,
    fetch_latest,
    fetch_release,
    get_github_repo,
    is_newer_available,
    is_pip_available,
    is_user_install,
    perform_self_update,
)


class TestIsNewerAvailable(unittest.TestCase):

    def test_newer_version(self):
        self.assertTrue(is_newer_available("0.4.0", "0.5.0"))
        self.assertTrue(is_newer_available("1.0", "1.0.1"))
        self.assertTrue(is_newer_available("1.2.3", "1.2.4"))

    def test_same_version(self):
        self.assertFalse(is_newer_available("0.4.2", "0.4.2"))
        self.assertFalse(is_newer_available("1.0", "1.0.0"))

    def test_older_version(self):
        self.assertFalse(is_newer_available("1.0", "0.9"))
        self.assertFalse(is_newer_available("2.0", "1.99"))

    def test_with_epoch(self):
        self.assertTrue(is_newer_available("5.0", "1:5.0"))

    def test_invalid_version_fallback(self):
        self.assertTrue(is_newer_available("invalid", "0.5.0"))


class TestNormalizeRepo(unittest.TestCase):

    def test_simple_owner_repo(self):
        self.assertEqual(_normalize_repo("thornspkg/thornspkg"), "thornspkg/thornspkg")

    def test_https_url(self):
        self.assertEqual(
            _normalize_repo("https://github.com/thornspkg/thornspkg"),
            "thornspkg/thornspkg",
        )

    def test_ssh_url(self):
        self.assertEqual(
            _normalize_repo("git@github.com:thornspkg/thornspkg.git"),
            "thornspkg/thornspkg",
        )

    def test_trailing_slash(self):
        self.assertEqual(_normalize_repo("thornspkg/thornspkg/"), "thornspkg/thornspkg")

    def test_invalid_no_slash(self):
        with self.assertRaises(SelfUpdateError):
            _normalize_repo("thornspkg")

    def test_invalid_too_many_slashes(self):
        with self.assertRaises(SelfUpdateError):
            _normalize_repo("a/b/c")


class TestGetGithubRepo(unittest.TestCase):

    def test_default(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("THORN_SELFUPDATE_REPO", None)
            self.assertEqual(get_github_repo(), DEFAULT_GITHUB_REPO)

    def test_override_argument(self):
        self.assertEqual(get_github_repo("foo/bar"), "foo/bar")

    def test_env_variable(self):
        with patch.dict("os.environ", {"THORN_SELFUPDATE_REPO": "env/repo"}):
            self.assertEqual(get_github_repo(), "env/repo")

    def test_override_takes_precedence_over_env(self):
        with patch.dict("os.environ", {"THORN_SELFUPDATE_REPO": "env/repo"}):
            self.assertEqual(get_github_repo("arg/repo"), "arg/repo")


class TestParseSha256Digest(unittest.TestCase):

    def test_sha256_prefix(self):
        self.assertEqual(
            _parse_sha256_digest("sha256:abcdef1234567890"),
            "abcdef1234567890",
        )

    def test_plain_hex(self):
        self.assertEqual(
            _parse_sha256_digest("abcdef1234567890"),
            "abcdef1234567890",
        )

    def test_none(self):
        self.assertIsNone(_parse_sha256_digest(None))
        self.assertIsNone(_parse_sha256_digest(""))

    def test_other_algorithm(self):
        # sha512 não é suportado — retorna None
        self.assertIsNone(_parse_sha256_digest("sha512:abcdef"))


class TestSelectSdistAsset(unittest.TestCase):

    def test_prefers_thornspkg_prefix(self):
        assets = [
            {"name": "random-1.0.tar.gz"},
            {"name": "thornspkg-0.4.3.tar.gz"},
        ]
        selected = _select_sdist_asset(assets)
        self.assertEqual(selected["name"], "thornspkg-0.4.3.tar.gz")

    def test_any_tar_gz(self):
        assets = [{"name": "release-1.0.tar.gz"}]
        selected = _select_sdist_asset(assets)
        self.assertEqual(selected["name"], "release-1.0.tar.gz")

    def test_zip_fallback(self):
        assets = [{"name": "thornspkg-0.4.3.zip"}]
        selected = _select_sdist_asset(assets)
        self.assertEqual(selected["name"], "thornspkg-0.4.3.zip")

    def test_no_suitable_asset(self):
        assets = [
            {"name": "README.md"},
            {"name": "release.exe"},
        ]
        self.assertIsNone(_select_sdist_asset(assets))

    def test_empty_list(self):
        self.assertIsNone(_select_sdist_asset([]))


class TestReleaseInfo(unittest.TestCase):

    def test_default_fields(self):
        r = ReleaseInfo(version="1.0", download_url="https://example.org/t.tar.gz")
        self.assertEqual(r.version, "1.0")
        self.assertIsNone(r.sha256)
        self.assertIsNone(r.size)
        self.assertEqual(r.source, "github")


class TestFetchReleaseWithFakeServer(unittest.TestCase):
    """Testa fetch_release contra um servidor HTTP que simula a GitHub API."""

    def setUp(self):
        import http.server
        import socketserver
        import threading
        import os

        self.tmpdir = tempfile.mkdtemp(prefix="thorn-test-gh-")
        self.tmpdir_path = Path(self.tmpdir)

        # Tarball fake (criado antes de saber a porta, mas com nome fixo)
        import tarfile
        import io
        tarball = self.tmpdir_path / "thornspkg-99.0.0.tar.gz"
        with tarfile.open(tarball, "w:gz") as tf:
            info = tarfile.TarInfo(name="thornspkg-99.0.0/README")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"hello"))

        # Aloca porta efêmera automaticamente (porta 0 = SO escolhe)
        os.chdir(self.tmpdir)
        handler = http.server.SimpleHTTPRequestHandler
        self.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

        # JSON de resposta do GitHub API (com URL dinâmica do servidor fake)
        self.github_response = {
            "tag_name": "v99.0.0",
            "name": "thornspkg 99.0.0",
            "body": "Release notes de teste",
            "assets": [
                {
                    "name": "thornspkg-99.0.0.tar.gz",
                    "browser_download_url": f"http://127.0.0.1:{self.port}/thornspkg-99.0.0.tar.gz",
                    "size": 1024,
                    "digest": "sha256:abcdef0123456789",
                }
            ],
        }
        (self.tmpdir_path / "release.json").write_text(json.dumps(self.github_response))

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_fetch_release_parses_correctly(self):
        """Mockamos a URL do GitHub API para apontar para o servidor fake."""
        with patch("thornspkg.selfupdate.github_api_url") as mock_url:
            mock_url.return_value = f"http://127.0.0.1:{self.port}/release.json"
            release = fetch_release("test/repo", tag=None)

        self.assertEqual(release.version, "99.0.0")
        self.assertEqual(release.tag_name, "v99.0.0")
        self.assertEqual(release.release_name, "thornspkg 99.0.0")
        self.assertEqual(release.release_notes, "Release notes de teste")
        self.assertEqual(release.download_url,
                         f"http://127.0.0.1:{self.port}/thornspkg-99.0.0.tar.gz")
        self.assertEqual(release.sha256, "abcdef0123456789")
        self.assertEqual(release.size, 1024)
        self.assertEqual(release.source, "github")
        self.assertEqual(release.repo, "test/repo")

    def test_fetch_release_specific_tag(self):
        """Busca por tag específica."""
        with patch("thornspkg.selfupdate.github_api_url") as mock_url:
            mock_url.return_value = f"http://127.0.0.1:{self.port}/release.json"
            release = fetch_release("test/repo", tag="v99.0.0")

        self.assertEqual(release.version, "99.0.0")
        # Verifica que a URL foi chamada com a tag (sem prefixo 'v')
        mock_url.assert_called_once_with("test/repo", tag="99.0.0")

    def test_fetch_release_no_assets_raises(self):
        """Release sem assets deve gerar erro claro."""
        no_assets = {"tag_name": "v1.0", "assets": []}
        (self.tmpdir_path / "empty.json").write_text(json.dumps(no_assets))
        with patch("thornspkg.selfupdate.github_api_url") as mock_url:
            mock_url.return_value = f"http://127.0.0.1:{self.port}/empty.json"
            with self.assertRaises(SelfUpdateError) as ctx:
                fetch_release("test/repo")
        self.assertIn("não tem assets", str(ctx.exception))

    def test_fetch_release_wrong_tag_name_raises(self):
        """Release sem tag_name deve gerar erro."""
        no_tag = {"assets": [{"name": "thornspkg-1.0.tar.gz", "browser_download_url": "x"}]}
        (self.tmpdir_path / "notag.json").write_text(json.dumps(no_tag))
        with patch("thornspkg.selfupdate.github_api_url") as mock_url:
            mock_url.return_value = f"http://127.0.0.1:{self.port}/notag.json"
            with self.assertRaises(SelfUpdateError) as ctx:
                fetch_release("test/repo")
        self.assertIn("tag_name", str(ctx.exception))


class TestPerformSelfUpdateDryRun(unittest.TestCase):

    def test_dry_run_with_url(self):
        """Dry-run com URL customizada deve funcionar sem rede."""
        rc = perform_self_update(
            url="https://example.org/thornspkg-0.99.0.tar.gz",
            dry_run=True,
            yes=True,
        )
        self.assertEqual(rc, 0)

    def test_dry_run_with_force_same_version(self):
        """--force + --dry-run deve mostrar reinstalação."""
        # Usa URL com versão menor que a atual para testar --force
        rc = perform_self_update(
            url="https://example.org/thornspkg-0.0.1.tar.gz",
            dry_run=True,
            force=True,
            yes=True,
        )
        self.assertEqual(rc, 0)


class TestPipDetection(unittest.TestCase):

    def test_is_pip_available_returns_bool(self):
        self.assertIsInstance(is_pip_available(), bool)

    def test_is_user_install_returns_bool(self):
        self.assertIsInstance(is_user_install(), bool)


if __name__ == "__main__":
    unittest.main()
