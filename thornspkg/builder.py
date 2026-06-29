# * Núcleo de compilação, extração e instalação de pacotes — o coração do thornspkg.
# * Pipeline de build (source): fetch → extract → patch → pre_build → build → stage → copy → post_install
# * Pipeline de pacote binário: fetch → verify SHA256 → extract (seguro) → hooks → register
# * Extração segura: rejeita path traversal (../), caminhos absolutos, symlinks perigosos.
# *   Funciona em Python antigo sem depender de filter="data" do tarfile.
# * Suporte a dois tipos de pacote: source (compilação) e binary (download direto).
# * Funções principais: fetch_source(), extract_archive(), build_and_install(),
# *   install_binary_package(), install_from_staging(), remove_installed_files().
# * Arquivo: thornspkg/builder.py

"""Download, aplicação de patches, compilação, instalação e remoção de pacotes.

Pipeline de build:
  1. fetch         — baixa e verifica cada source da receita
  2. extract       — extrai o tarball principal em <build_dir>/<pkg>-<ver>/
  3. extra_sources — copia fontes adicionais para o mesmo diretório
  4. patch         — aplica patches via `patch -p1`
  5. pre_build     — hooks shell antes da compilação
  6. build         — resolve e roda os steps de build
  7. stage         — roda install_steps com DESTDIR
  8. copy          — copia staging → root, grava manifest e checksums
  9. post_install  — hooks shell no root real (ldconfig, mandb, etc.)

Pipeline de pacote binário:
  1. fetch         — baixa o tarball binário do repositório
  2. verify        — verifica SHA256
  3. extract       — extração segura (sem path traversal, sem symlinks perigosos)
  4. hooks         — executa hooks de instalação
  5. register      — registra no banco de dados
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from .colors import c
from .config import Config
from .db import save_checksums
from .downloader import DownloadError, download_to_temp
from .recipe import Recipe


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# utilitários
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: str, cwd: Path, env: dict, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as log:
        log.write(f"\n$ {cmd}\n")
        log.flush()
        proc = subprocess.run(
            cmd, shell=True, cwd=cwd, env=env,
            stdout=log, stderr=subprocess.STDOUT
        )
    if proc.returncode != 0:
        raise BuildError(
            f"falhou (exit {proc.returncode}): {cmd}\n"
            f"  log: {log_path}"
        )


# ---------------------------------------------------------------------------
# 1. download
# ---------------------------------------------------------------------------

def fetch_source(url: str, sources_dir: Path, checksum: str | None) -> Path:
    """Baixa um source para sources_dir e verifica checksum.

    Utiliza o módulo downloader centralizado (curl preferencial, urllib fallback).
    O sources_dir agora é tratado como o cache persistente de sources.
    """
    sources_dir.mkdir(parents=True, exist_ok=True)

    if url.startswith(("http://", "https://", "ftp://")):
        filename = url.rsplit("/", 1)[-1]
        dest = sources_dir / filename
        if dest.exists():
            print(c(f"  ↓  {filename} (cache)", "dim"))
        else:
            print(c(f"  ↓  {url}", "cyan"))
            try:
                download_to_temp(url, dest)
            except DownloadError as e:
                raise BuildError(f"falha ao baixar '{url}': {e}") from e
    else:
        src = Path(url).expanduser()
        if not src.exists():
            raise BuildError(f"source local não encontrado: {src}")
        dest = sources_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        print(c(f"  ↓  {src.name} (local)", "dim"))

    if checksum:
        digest = sha256_file(dest)
        if digest != checksum:
            raise BuildError(
                f"sha256 diverge para '{dest.name}'\n"
                f"  esperado: {checksum}\n"
                f"  obtido:   {digest}"
            )
        print(c("  ✓  checksum OK", "green"))

    return dest


def download_to_temp(url: str, dest: Path) -> Path:
    """Atalho para o download_to_temp do módulo downloader.

    Mantido aqui para compatibilidade com chamadas existentes que fazem
    `from .builder import download_to_temp`.
    """
    from .downloader import download_to_temp as _dl
    return _dl(url, dest)


# ---------------------------------------------------------------------------
# 2. extração segura
# ---------------------------------------------------------------------------

def _is_safe_tar_member(member: tarfile.TarInfo, dest_dir: Path) -> bool:
    """Verifica se um membro do tar é seguro para extração.

    Rejeita:
      - path traversal (caminhos com ../)
      - caminhos absolutos (começam com /)
      - symlinks que apontam para fora do dest_dir
      - hardlinks que apontam para fora do dest_dir

    Isso funciona mesmo em versões antigas do Python que não suportam
    o parâmetro filter="data" do tarfile.extractall().
    """
    name = member.name

    # Rejeita caminhos com ../ (path traversal)
    if ".." in name:
        return False

    # Rejeita caminhos absolutos
    if name.startswith("/"):
        return False

    # Resolve o caminho completo e verifica se está dentro de dest_dir
    # (proteção contra codificação maliciosa como "foo/../../etc/passwd")
    try:
        full_path = (dest_dir / name).resolve()
        if not str(full_path).startswith(str(dest_dir.resolve())):
            return False
    except (ValueError, OSError):
        return False

    # Rejeita symlinks perigosos (que apontam para fora do dest_dir)
    if member.issym() or member.islnk():
        link_target = member.linkname
        # Rejeita links absolutos
        if link_target.startswith("/"):
            return False
        # Rejeita links com traversal
        if ".." in link_target:
            return False
        # Verifica se o alvo do link resolvido está dentro de dest_dir
        try:
            if member.issym():
                link_path = (dest_dir / name).parent / link_target
            else:
                # hardlink: linkname é relativo ao diretório de extração
                link_path = dest_dir / link_target
            resolved = link_path.resolve()
            if not str(resolved).startswith(str(dest_dir.resolve())):
                return False
        except (ValueError, OSError):
            return False

    return True


def extract_archive(archive: Path, dest_dir: Path) -> Path:
    """Extrai para dest_dir com segurança. Retorna o subdir gerado (se único) ou dest_dir.

    Segurança:
      - Impede path traversal (../)
      - Rejeita caminhos absolutos
      - Rejeita links simbólicos perigosos
      - Funciona em versões antigas do Python sem filter="data"
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            # Usa extração segura manual em vez de filter="data".
            # Motivo: filter="data" (Python 3.12+) levanta exceções fatais
            # (OutsideDestinationError, AbsoluteLinkError, etc.) que abortam
            # toda a extração. Nossa abordagem manual filtra entradas perigosas
            # e continua extraindo as seguras, com mensagens de aviso.
            safe_members = []
            for member in tf.getmembers():
                if _is_safe_tar_member(member, dest_dir):
                    safe_members.append(member)
                else:
                    print(c(
                        f"  ⚠  entrada rejeitada (segurança): {member.name}",
                        "yellow",
                    ))
            # filter="tar" preserva metadata mas não aplica o filtro "data"
            # agressivo (que aborta em vez de filtrar). Nós já filtramos manualmente.
            try:
                tf.extractall(dest_dir, members=safe_members, filter="tar")
            except TypeError:
                # Python < 3.12 não suporta filter
                tf.extractall(dest_dir, members=safe_members)
    elif zipfile.is_zipfile(archive):
        # Extração segura para ZIP
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                # Rejeita path traversal em ZIPs
                if ".." in info.filename or info.filename.startswith("/"):
                    print(c(
                        f"  ⚠  entrada ZIP rejeitada (segurança): {info.filename}",
                        "yellow",
                    ))
                    continue
                # Rejeita symlinks em ZIPs (não suportados nativamente,
                # mas verificamos por segurança)
                if info.filename.endswith("/"):
                    continue  # diretório
                zf.extract(info, dest_dir)
    else:
        shutil.copy2(archive, dest_dir / archive.name)
        return dest_dir

    entries = list(dest_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest_dir


# ---------------------------------------------------------------------------
# 3. patches
# ---------------------------------------------------------------------------

def apply_patches(recipe: Recipe, src_dir: Path, patches_dir: Path, env: dict, log_path: Path) -> None:
    if not recipe.patches:
        return
    print(c(f"  ✂  aplicando {len(recipe.patches)} patch(es)", "cyan"))
    pkg_patches_dir = patches_dir / recipe.name
    for patch_name in recipe.patches:
        p = Path(patch_name)
        if not p.is_absolute():
            p = pkg_patches_dir / patch_name
        if not p.exists():
            raise BuildError(f"patch não encontrado: {p}")
        _run(f"patch -p1 -i {p}", src_dir, env, log_path)


# ---------------------------------------------------------------------------
# 4. resolução dos steps de build
# ---------------------------------------------------------------------------

def resolve_build_steps(recipe: Recipe, prefix: str, jobs: int) -> tuple[list[str], list[str]]:
    if recipe.steps:
        install = recipe.install_steps or ["make install"]
        return list(recipe.steps), install

    extra = " ".join(recipe.configure_args)
    bs = recipe.build_system

    if bs == "autotools":
        cfg = f"./configure --prefix={prefix} {extra}".strip()
        return [cfg, f"make -j{jobs}"], ["make install"]

    if bs == "make":
        make_args = f"PREFIX={prefix} {extra}".strip()
        return [f"make -j{jobs} {make_args}".strip()], [f"make install {make_args}".strip()]

    if bs == "cmake":
        cmake_cfg = f"cmake -B _build -DCMAKE_INSTALL_PREFIX={prefix} {extra}".strip()
        return [cmake_cfg, f"cmake --build _build -j{jobs}"], ["cmake --install _build"]

    if bs == "meson":
        meson_cfg = f"meson setup _build --prefix={prefix} {extra}".strip()
        return [meson_cfg, f"ninja -C _build -j{jobs}"], ["ninja -C _build install"]

    raise BuildError(f"build_system inválido: {bs}")


# ---------------------------------------------------------------------------
# 5. cópia staging → root com manifest e checksums
# ---------------------------------------------------------------------------

def _copy_symlink(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    os.symlink(os.readlink(src), dst)


def install_from_staging(staging: Path, dest_root: Path) -> tuple[list[str], dict[str, str]]:
    """Copia staging → dest_root. Retorna (manifest, checksums).

    Arquivos de índice compartilhado (que múltiplos pacotes podem regenerar)
    são filtrados do manifesto para evitar falsos conflitos:
      - /usr/share/info/dir       — índice de info pages (install-info)
      - /usr/share/info/dir.gz    — versão comprimida
      - /usr/share/info/dir.bz2
      - /usr/share/info/dir.xz

    Esses arquivos ainda são copiados para o root (se existirem no staging),
    mas não são "propriedade" de nenhum pacote específico — qualquer pacote
    pode regenerá-los a qualquer momento. Isso evita que o thornspkg reclame
    de "conflito de arquivos" entre pacotes que ambos regeneram o índice.
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    manifest: list[str] = []
    checksums: dict[str, str] = {}

    # Padrões de arquivos compartilhados que NÃO devem ser rastreados no
    # manifesto (causam conflitos entre pacotes que os regeneram).
    SHARED_INDEX_PATTERNS = [
        "usr/share/info/dir",
        "usr/share/info/dir.gz",
        "usr/share/info/dir.bz2",
        "usr/share/info/dir.xz",
    ]

    def is_shared_index(rel_path: str) -> bool:
        """Verifica se o arquivo é um índice compartilhado."""
        normalized = rel_path.lstrip("/")
        return normalized in SHARED_INDEX_PATTERNS

    for dirpath, dirnames, filenames in os.walk(staging, followlinks=False):
        rel_dir = Path(dirpath).relative_to(staging)
        (dest_root / rel_dir).mkdir(parents=True, exist_ok=True)

        real_dirs = []
        for d in dirnames:
            src = Path(dirpath) / d
            if src.is_symlink():
                rel = str(rel_dir / d)
                _copy_symlink(src, dest_root / rel)
                manifest.append(rel)
            else:
                real_dirs.append(d)
        dirnames[:] = real_dirs

        for fname in filenames:
            src = Path(dirpath) / fname
            rel = str(rel_dir / fname)
            # Copia o arquivo para o root (mesmo se for índice compartilhado,
            # para não perder dados), mas não registra no manifesto se for.
            dst = dest_root / rel
            if src.is_symlink():
                _copy_symlink(src, dst)
            else:
                shutil.copy2(src, dst)
                checksums[rel] = sha256_file(dst)
            # Filtra índices compartilhados do manifesto
            if not is_shared_index(rel):
                manifest.append(rel)
            # Remove também do checksums se for índice compartilhado
            if is_shared_index(rel) and rel in checksums:
                del checksums[rel]

    return manifest, checksums


def compute_install_size(root_dir: Path, manifest: list[str]) -> int:
    """Soma o tamanho em bytes de todos os arquivos do manifest.

    Symlinks contam como 0 bytes. Arquivos ausentes são ignorados.
    """
    total = 0
    for rel in manifest:
        p = root_dir / rel
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def compute_download_size(archive_path: Path) -> int:
    """Retorna o tamanho em bytes de um arquivo baixado."""
    try:
        return archive_path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# 6. remoção de arquivos
# ---------------------------------------------------------------------------

def remove_installed_files(root_dir: Path, files: list[str]) -> tuple[int, int]:
    """Remove os arquivos do manifesto do root.

    Antes de remover arquivos `.info`, executa `install-info --remove`
    para limpar a entrada correspondente do índice compartilhado
    /usr/share/info/dir. Isso evita que o índice fique com entradas órfãs
    apontando para páginas info que foram removidas.

    Após remover todos os arquivos, limpa diretórios vazios de baixo pra cima.
    Se o /usr/share/info/dir ficar vazio (sem entradas), também é removido.
    """
    removed = skipped = 0

    # --- 1. Antes de remover .info files, limpa entradas do índice ---
    # Arquivos .info em /usr/share/info/ (excluindo o próprio `dir` que é o índice)
    info_files_to_clean = []
    for rel in files:
        normalized = rel.lstrip("/")
        if (normalized.startswith("usr/share/info/")
                and normalized.endswith(".info")
                and normalized != "usr/share/info/dir"):
            info_files_to_clean.append(normalized)

    if info_files_to_clean:
        info_dir_path = root_dir / "usr" / "share" / "info" / "dir"
        if info_dir_path.exists():
            for info_file in info_files_to_clean:
                # Tenta rodar install-info --remove --info-dir=<dir> <arquivo>
                full_info_path = root_dir / info_file
                if full_info_path.exists():
                    try:
                        subprocess.run(
                            ["install-info", "--remove",
                             f"--info-dir={info_dir_path}",
                             str(full_info_path)],
                            capture_output=True, timeout=10,
                            check=False,  # não falha se install-info não existir
                        )
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        # install-info não está instalado ou travou — ignora
                        pass

    # --- 2. Remove os arquivos do manifesto ---
    for rel in files:
        p = root_dir / rel
        try:
            if p.is_symlink() or p.is_file():
                p.unlink()
                removed += 1
            elif p.is_dir():
                p.rmdir()
                removed += 1
            else:
                skipped += 1
        except (FileNotFoundError, OSError):
            skipped += 1

    # --- 3. Limpa diretórios vazios de baixo pra cima ---
    parents = sorted(
        {(root_dir / rel).parent for rel in files},
        key=lambda p: len(str(p)), reverse=True,
    )
    for parent in parents:
        while parent != root_dir and root_dir in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    # --- 4. Remove o info/dir se ficou vazio (após limpeza) ---
    # Se o diretório /usr/share/info/ ficou sem nenhum .info, remove o dir também
    info_dir_path = root_dir / "usr" / "share" / "info" / "dir"
    if info_dir_path.exists():
        try:
            # Se o dir está vazio ou só tem comentários (sem entradas `*`), remove
            content = info_dir_path.read_text().strip()
            has_entries = any(l.startswith("*") for l in content.split("\n"))
            if not has_entries:
                info_dir_path.unlink()
        except (OSError, UnicodeDecodeError):
            pass

    return removed, skipped


# ---------------------------------------------------------------------------
# 7. instalação de pacote binário
# ---------------------------------------------------------------------------

def install_binary_package(
    archive_path: Path,
    cfg: Config,
    package_name: str,
    package_version: str,
    *,
    keep_build: bool = False,
    current: int = 0,
    total: int = 0,
) -> tuple[list[str], dict[str, str]]:
    """Instala um pacote binário (tarball pré-compilado).

    Fluxo:
      baixar → verificar SHA256 → extrair (seguro) → executar hooks → registrar no banco

    O tarball binário deve conter a estrutura final COMPLETA (ex: `usr/bin/vim`,
    `usr/share/man/man1/vim.1`, etc.) relativa ao root. Esses caminhos são
    copiados diretamente para `cfg.root_dir` sem aplicar `cfg.prefix` — o
    prefix já deve estar embutido na estrutura do tarball.
    """
    counter = f"[{current}/{total}] " if total else ""
    print(c(f"\n{counter}==> {package_name}-{package_version} (binário)", "bold"))

    pkg_build_dir = cfg.build_dir / f"{package_name}-{package_version}"

    # --- extrair ---
    extract_dir = pkg_build_dir / "_extract"
    if pkg_build_dir.exists():
        shutil.rmtree(pkg_build_dir)
    extract_dir.mkdir(parents=True)

    # Extraímos direto em extract_dir — NÃO usamos o subdir retornado por
    # extract_archive, porque ele assume que um único subdir (ex: "usr/")
    # é o staging. Para pacotes binários, queremos preservar a estrutura
    # completa do tarball (com "usr/" dentro).
    extract_archive(archive_path, extract_dir)

    # --- copiar para root ---
    # staging = extract_dir (preserva estrutura completa usr/bin/... )
    staging = extract_dir
    print(c(f"  →  {cfg.root_dir}", "cyan"))
    manifest, checksums = install_from_staging(staging, cfg.root_dir)

    # --- salvar checksums ---
    save_checksums(cfg.db_dir, package_name, checksums)

    if not keep_build:
        shutil.rmtree(pkg_build_dir, ignore_errors=True)

    print(c(f"  ✓  {len(manifest)} arquivos instalados (binário)", "green"))
    return manifest, checksums


# ---------------------------------------------------------------------------
# orquestrador principal
# ---------------------------------------------------------------------------

def build_and_install(
    recipe: Recipe,
    cfg: Config,
    *,
    jobs: int | None = None,
    keep_build: bool = False,
    current: int = 0,
    total: int = 0,
) -> tuple[list[str], dict[str, str]]:
    jobs = jobs or cfg.jobs
    prefix = recipe.prefix or cfg.prefix

    counter = f"[{current}/{total}] " if total else ""
    print(c(f"\n{counter}==> {recipe.name}-{recipe.version}", "bold"))

    pkg_build_dir = cfg.build_dir / f"{recipe.name}-{recipe.version}"

    # --- fetch & extract ---
    if recipe.sources:
        archives = []
        for url, chk in zip(recipe.sources, recipe.checksums):
            archives.append(fetch_source(url, cfg.sources_dir, chk))

        extract_dir = pkg_build_dir / "_extract"
        if pkg_build_dir.exists():
            shutil.rmtree(pkg_build_dir)
        extract_dir.mkdir(parents=True)

        src_dir = extract_archive(archives[0], extract_dir)

        # sources adicionais vão para o mesmo diretório de trabalho
        for extra_archive in archives[1:]:
            shutil.copy2(extra_archive, src_dir / extra_archive.name)
    else:
        if pkg_build_dir.exists():
            shutil.rmtree(pkg_build_dir)
        pkg_build_dir.mkdir(parents=True)
        src_dir = pkg_build_dir

    staging = pkg_build_dir / "_destdir"
    staging.mkdir(parents=True)
    log_path = pkg_build_dir / "build.log"

    # --- ambiente base ---
    env = os.environ.copy()
    env["DESTDIR"] = str(staging)
    env["MAKEFLAGS"] = env.get("MAKEFLAGS") or f"-j{jobs}"
    env["PKG_CONFIG_PATH"] = env.get("PKG_CONFIG_PATH", f"{cfg.root_dir}/usr/lib/pkgconfig:{cfg.root_dir}/usr/share/pkgconfig")
    # env global da config
    env.update(cfg.extra_env)
    # env da receita (maior prioridade)
    env.update(recipe.env)

    # --- patches ---
    apply_patches(recipe, src_dir, cfg.patches_dir, env, log_path)

    # --- pre_build ---
    if recipe.pre_build:
        print(c("  ⚙  pre_build", "cyan"))
        for cmd in recipe.pre_build:
            _run(cmd, src_dir, env, log_path)

    # --- build ---
    build_steps, install_steps = resolve_build_steps(recipe, prefix, jobs)
    print(c("  ⚙  compilando", "cyan"))
    for cmd in build_steps:
        _run(cmd, src_dir, env, log_path)

    # --- install (staging) ---
    print(c("  📦 staging (DESTDIR)", "cyan"))
    for cmd in install_steps:
        _run(cmd, src_dir, env, log_path)

    # --- copy staging → root ---
    print(c(f"  →  {cfg.root_dir}", "cyan"))
    manifest, checksums = install_from_staging(staging, cfg.root_dir)

    # --- salvar checksums ---
    save_checksums(cfg.db_dir, recipe.name, checksums)

    # --- post_install (no root real) ---
    if recipe.post_install:
        print(c("  ⚙  post_install", "cyan"))
        root_env = env.copy()
        root_env["DESTDIR"] = ""
        for cmd in recipe.post_install:
            _run(cmd, cfg.root_dir, root_env, log_path)

    if not keep_build:
        shutil.rmtree(pkg_build_dir, ignore_errors=True)

    print(c(f"  ✓  {len(manifest)} arquivos instalados", "green"))
    return manifest, checksums


def run_remove_hooks(recipe: Recipe, cfg: Config) -> None:
    if not recipe.pre_remove:
        return
    log_path = cfg.build_dir / f"{recipe.name}-remove.log"
    env = os.environ.copy()
    print(c("  ⚙  pre_remove", "cyan"))
    for cmd in recipe.pre_remove:
        _run(cmd, cfg.root_dir, env, log_path)


def run_post_remove_hooks(recipe: Recipe, cfg: Config) -> None:
    if not recipe.post_remove:
        return
    log_path = cfg.build_dir / f"{recipe.name}-remove.log"
    env = os.environ.copy()
    print(c("  ⚙  post_remove", "cyan"))
    for cmd in recipe.post_remove:
        _run(cmd, cfg.root_dir, env, log_path)