#!/usr/bin/env python3
# * Teste crítico do fluxo de upgrade: verifica se arquivos obsoletos
# * (presentes na versão antiga mas não na nova) são removidos corretamente.
# * Cenário: hello v1.0 instala 3 arquivos; hello v1.1 instala apenas 2
# * (remove o /usr/share/examples/legacy.txt). Após upgrade, esse arquivo
# * não deveria mais existir no root.
# * Arquivo: scripts/test_upgrade_cleanup.py

"""Teste crítico: arquivos obsoletos devem ser removidos no upgrade."""

import json
import os
import shutil
import sys
import tempfile
import hashlib
import tarfile
from io import BytesIO
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_tarball(files: dict[str, bytes]) -> bytes:
    """Cria um tarball .tar.gz em memória com os arquivos dados."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, content in files.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(content)
            tf.addfile(info, BytesIO(content))
    return buf.getvalue()


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    from thornspkg.config import Config
    from thornspkg.cli import main as thorn_main

    tmpdir = Path(tempfile.mkdtemp(prefix="thorn-upgrade-"))
    print(f"Test dir: {tmpdir}")

    # Estrutura de diretórios de teste
    db_dir = tmpdir / "db"
    cache_dir = tmpdir / "cache"
    root_dir = tmpdir / "root"
    repo_dir = tmpdir / "repo"
    pkgs_dir = repo_dir / "packages"
    recipes_dir = project_root / "recipes"  # usa receitas reais do projeto
    repos_config = tmpdir / "repos.json"

    for d in [db_dir, cache_dir, root_dir, pkgs_dir]:
        d.mkdir(parents=True, exist_ok=True)

    cfg = Config(
        recipes_dir=recipes_dir,
        patches_dir=tmpdir / "patches",
        sources_dir=cache_dir / "sources",
        build_dir=tmpdir / "build",
        db_dir=db_dir,
        root_dir=root_dir,
        prefix="/usr",
        hooks_dir=tmpdir / "hooks",
        jobs=1,
        repos_config=repos_config,
    )

    # ── Cenário: pacote "demo" v1.0 com 3 arquivos ──────────────────────
    v1_files = {
        "usr/bin/demo": b"#!/bin/sh\necho demo v1.0\n",
        "usr/share/demo/data.txt": b"v1.0 data\n",
        "usr/share/demo/legacy.txt": b"v1.0 legacy (sera removido em v2)\n",
    }
    v1_tarball = pkgs_dir / "demo-1.0-x86_64.tar.gz"
    v1_tarball.write_bytes(make_tarball(v1_files))
    v1_sha = sha256_bytes(v1_tarball.read_bytes())

    # ── Cenário: pacote "demo" v2.0 com 2 arquivos (legacy.txt removido) ─
    v2_files = {
        "usr/bin/demo": b"#!/bin/sh\necho demo v2.0\n",
        "usr/share/demo/data.txt": b"v2.0 data (atualizado)\n",
        # usr/share/demo/legacy.txt foi removido em v2
    }
    v2_tarball = pkgs_dir / "demo-2.0-x86_64.tar.gz"
    v2_tarball.write_bytes(make_tarball(v2_files))
    v2_sha = sha256_bytes(v2_tarball.read_bytes())

    # ── Gerar index.json v1 ──────────────────────────────────────────────
    index_v1 = {
        "schema_version": 1,
        "packages": {
            "demo": {
                "version": "1.0",
                "type": "binary",
                "url": "packages/demo-1.0-x86_64.tar.gz",
                "sha256": v1_sha,
                "depends": [],
                "description": "Demo package v1",
            }
        }
    }
    (repo_dir / "index.json").write_text(json.dumps(index_v1, indent=2))

    # ── Helper para chamar thorn ─────────────────────────────────────────
    def thorn(*args):
        argv = [
            "--recipes-dir", str(recipes_dir),
            "--db-dir", str(db_dir),
            "--repos-config", str(repos_config),
            "--root", str(root_dir),
            "--sources-dir", str(cache_dir / "sources"),
            *args,
        ]
        return thorn_main(argv)

    print("\n=== PASSO 1: instalar demo v1.0 ===")
    rc = thorn("repo", "add", "local", f"file://{repo_dir}/")
    rc = thorn("sync")
    rc = thorn("install", "demo")
    assert rc == 0, f"install v1 falhou (rc={rc})"

    # Verificar que 3 arquivos foram instalados
    installed = sorted(p.relative_to(root_dir).as_posix() for p in root_dir.rglob("*") if p.is_file())
    print(f"  Arquivos após v1.0: {installed}")
    assert "usr/bin/demo" in installed
    assert "usr/share/demo/data.txt" in installed
    assert "usr/share/demo/legacy.txt" in installed, "legacy.txt deveria estar instalado em v1"

    # ── Atualizar repositório para v2.0 ──────────────────────────────────
    index_v2 = {
        "schema_version": 1,
        "packages": {
            "demo": {
                "version": "2.0",
                "type": "binary",
                "url": "packages/demo-2.0-x86_64.tar.gz",
                "sha256": v2_sha,
                "depends": [],
                "description": "Demo package v2 (legacy.txt removido)",
            }
        }
    }
    (repo_dir / "index.json").write_text(json.dumps(index_v2, indent=2))
    print("\n=== PASSO 2: repositório atualizado para demo v2.0 ===")
    print(f"  Novo index.json: version=2.0, legacy.txt REMOVIDO do tarball")

    print("\n=== PASSO 3: thorn sync (baixar novo índice) ===")
    thorn("sync")

    print("\n=== PASSO 4: thorn list-upgrades ===")
    thorn("list-upgrades")

    print("\n=== PASSO 5: thorn upgrade demo ===")
    rc = thorn("upgrade", "demo")
    assert rc == 0, f"upgrade falhou (rc={rc})"

    # ── Verificar resultado ──────────────────────────────────────────────
    installed_after = sorted(p.relative_to(root_dir).as_posix() for p in root_dir.rglob("*") if p.is_file())
    print(f"\n  Arquivos após v2.0: {installed_after}")

    legacy_path = root_dir / "usr/share/demo/legacy.txt"
    if legacy_path.exists():
        print("\n  ✗ FALHA: legacy.txt ainda existe após upgrade!")
        print("  O thornspkg NÃO remove arquivos obsoletos durante upgrade.")
        print("  Isso é um bug que precisa ser corrigido.")
        result_code = 1
    else:
        print("\n  ✓ OK: legacy.txt foi removido corretamente durante upgrade.")
        result_code = 0

    # Verificar que usr/bin/demo tem o conteúdo novo (v2.0)
    bin_content = (root_dir / "usr/bin/demo").read_text()
    if "v2.0" in bin_content:
        print("  ✓ OK: usr/bin/demo foi atualizado para v2.0")
    else:
        print(f"  ✗ FALHA: usr/bin/demo não foi atualizado. Conteúdo: {bin_content!r}")
        result_code = 1

    # Verificar data.txt foi atualizado
    data_content = (root_dir / "usr/share/demo/data.txt").read_text()
    if "v2.0" in data_content:
        print("  ✓ OK: data.txt foi atualizado para v2.0")
    else:
        print(f"  ✗ FALHA: data.txt não foi atualizado. Conteúdo: {data_content!r}")
        result_code = 1

    # Verificar DB reflete v2.0
    db = json.loads((db_dir / "installed.json").read_text())
    demo_info = db["packages"]["demo"]
    print(f"\n  DB: demo version={demo_info['version']}, files={len(demo_info['files'])}")
    if demo_info["version"] == "2.0":
        print("  ✓ OK: banco atualizado para v2.0")
    else:
        print(f"  ✗ FALHA: banco ainda tem versão {demo_info['version']}")
        result_code = 1

    if len(demo_info["files"]) == 2:
        print("  ✓ OK: manifest tem 2 arquivos (legacy.txt removido)")
    else:
        print(f"  ✗ FALHA: manifest tem {len(demo_info['files'])} arquivos (esperado 2)")

    print(f"\n{'='*60}")
    if result_code == 0:
        print("TESTE PASSOU: upgrade funciona corretamente")
    else:
        print("TESTE FALHOU: upgrade tem bugs (arquivos obsoletos não removidos)")
    print(f"{'='*60}")

    # Cleanup
    shutil.rmtree(tmpdir, ignore_errors=True)
    return result_code


if __name__ == "__main__":
    sys.exit(main())
