"""
zach-runner — Upload et analyse de projets complets (archives).

Formats : .zip, .tar, .tar.gz/.tgz (stdlib), .rar (si outil systeme
present via rarfile), .7z (via py7zr).

Securite : protection zip-slip, quotas (taille archive, nb fichiers,
taille totale), ignore les liens symboliques des tars.
"""
import shutil
import tarfile
import zipfile
from pathlib import Path

from runtimes import detect_runtime

ARCHIVE_EXTS = (".zip", ".tar", ".tgz", ".tar.gz", ".rar", ".7z")

MAX_ARCHIVE_MB = 64
MAX_FILES = 2000
MAX_TOTAL_MB = 256
MAX_TREE_FILES = 600

SKIP_DIRS = {"__MACOSX", ".git", ".hg", ".svn", "__pycache__", ".pytest_cache"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}

# Candidats point d'entree, par priorite (basename exact, minuscules)
ENTRY_PRIORITY = [
    "main.py", "app.py", "bot.py", "run.py", "start.py", "cli.py",
    "index.js", "server.js", "app.js", "main.js", "bot.js", "run.js",
    "main.sh", "run.sh", "start.sh",
]

MANIFEST_NAMES = {"requirements.txt", "package.json", "Procfile",
                  "runtime.txt", "Pipfile", "pyproject.toml"}


def archive_kind(filename: str) -> str | None:
    name = (filename or "").lower()
    if name.endswith(".tar.gz"):
        return "tar"
    suf = Path(name).suffix
    if suf == ".zip":
        return "zip"
    if suf in (".tar", ".tgz"):
        return "tar"
    if suf == ".rar":
        return "rar"
    if suf == ".7z":
        return "7z"
    return None


def _safe_dest(root: Path, member: str) -> Path | None:
    """Resout un chemin de membre en restant confine a root (anti zip-slip)."""
    if not member or member.strip() == "":
        return None
    p = Path(member)
    if p.is_absolute():
        return None
    if ".." in p.parts:
        return None
    dest = root.joinpath(*p.parts).resolve()
    try:
        dest.relative_to(root.resolve())
    except ValueError:
        return None
    return dest


def _quota_guard(count: int, total: int):
    if count > MAX_FILES:
        return f"Archive refusee : plus de {MAX_FILES} fichiers"
    if total > MAX_TOTAL_MB * 1024 * 1024:
        return f"Archive refusee : contenu superieur a {MAX_TOTAL_MB} Mo"
    return None


def _extract_zip(archive: Path, dest: Path) -> tuple[bool, str, int]:
    count, total = 0, 0
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                target = _safe_dest(dest, info.filename)
                if target is None:
                    continue
                if any(part in SKIP_DIRS for part in target.parts):
                    continue
                if target.name in SKIP_NAMES:
                    continue
                count += 1
                total += info.file_size
                err = _quota_guard(count, total)
                if err:
                    return False, err, count
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, length=1024 * 64)
    except zipfile.BadZipFile:
        return False, "Archive .zip illisible ou corrompue", 0
    except Exception as e:
        return False, f"Extraction .zip impossible : {e}", 0
    if count == 0:
        return False, "Archive vide (aucun fichier exploitable)", 0
    return True, f"{count} fichier(s) extrait(s)", count


def _extract_tar(archive: Path, dest: Path) -> tuple[bool, str, int]:
    count, total = 0, 0
    try:
        with tarfile.open(archive, "r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue  # ignore dossiers, liens, devices...
                target = _safe_dest(dest, member.name)
                if target is None:
                    continue
                if any(part in SKIP_DIRS for part in target.parts):
                    continue
                if target.name in SKIP_NAMES:
                    continue
                count += 1
                total += member.size
                err = _quota_guard(count, total)
                if err:
                    return False, err, count
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, length=1024 * 64)
    except (tarfile.TarError, OSError) as e:
        return False, f"Archive tar illisible : {e}", 0
    except Exception as e:
        return False, f"Extraction tar impossible : {e}", 0
    if count == 0:
        return False, "Archive vide (aucun fichier exploitable)", 0
    return True, f"{count} fichier(s) extrait(s)", count


def _extract_rar(archive: Path, dest: Path) -> tuple[bool, str, int]:
    try:
        import rarfile
    except ImportError:
        return False, "Support .rar non installe sur le serveur — utilisez .zip", 0
    # rarfile exige un outil systeme (unrar / bsdtar / unar)
    try:
        with rarfile.RarFile(archive) as rf:
            names = [n for n in rf.namelist() if not n.endswith("/")]
    except rarfile.RarCannotExec:
        return (False, "Decompression .rar indisponible sur ce serveur "
                "(outil unrar absent) — convertissez en .zip", 0)
    except rarfile.Error as e:
        return False, f"Archive .rar illisible : {e}", 0
    except Exception as e:
        return False, f"Lecture .rar impossible : {e}", 0
    count, total = 0, 0
    try:
        with rarfile.RarFile(archive) as rf:
            for info in rf.infolist():
                if info.is_dir():
                    continue
                target = _safe_dest(dest, info.filename)
                if target is None:
                    continue
                if any(part in SKIP_DIRS for part in target.parts):
                    continue
                if target.name in SKIP_NAMES:
                    continue
                count += 1
                total += info.file_size
                err = _quota_guard(count, total)
                if err:
                    return False, err, count
                target.parent.mkdir(parents=True, exist_ok=True)
                with rf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, length=1024 * 64)
    except Exception as e:
        return False, f"Extraction .rar impossible : {e}", count
    if count == 0:
        return False, "Archive vide (aucun fichier exploitable)", 0
    return True, f"{count} fichier(s) extrait(s)", count


def _extract_7z(archive: Path, dest: Path) -> tuple[bool, str, int]:
    import tempfile
    try:
        import py7zr
    except ImportError:
        return False, "Support .7z non installe sur le serveur — utilisez .zip", 0
    try:
        with py7zr.SevenZipFile(archive, "r") as zf:
            infos = [i for i in zf.list() if not i.is_directory]
            if not infos:
                return False, "Archive vide (aucun fichier exploitable)", 0
            if len(infos) > MAX_FILES:
                return False, f"Archive refusee : plus de {MAX_FILES} fichiers", 0
            total = sum(i.uncompressed or 0 for i in infos)
            if total > MAX_TOTAL_MB * 1024 * 1024:
                return False, f"Archive refusee : contenu superieur a {MAX_TOTAL_MB} Mo", 0
            # Refuse toute archive contenant un chemin dangereux AVANT extraction
            for info in infos:
                if _safe_dest(dest, info.filename) is None:
                    return False, "Archive refusee : chemin de fichier dangereux", 0
            with tempfile.TemporaryDirectory(prefix="zr7z") as tmp:
                zf.extractall(path=tmp)
                count, moved = 0, 0
                for src in sorted(Path(tmp).rglob("*")):
                    if not src.is_file():
                        continue
                    try:
                        rel = src.relative_to(tmp).as_posix()
                    except ValueError:
                        continue
                    target = _safe_dest(dest, rel)
                    if target is None:
                        continue
                    if any(part in SKIP_DIRS for part in target.parts):
                        continue
                    if target.name in SKIP_NAMES:
                        continue
                    count += 1
                    err = _quota_guard(count, total)
                    if err:
                        return False, err, count
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(target))
                    moved += 1
    except Exception as e:
        return False, f"Extraction .7z impossible : {e}", 0
    if moved == 0:
        return False, "Archive vide (aucun fichier exploitable)", 0
    return True, f"{moved} fichier(s) extrait(s)", moved


def extract_archive(archive: Path, dest: Path) -> tuple[bool, str, int]:
    """Extrait une archive vers dest. Retourne (ok, message, nb_fichiers)."""
    kind = archive_kind(archive.name)
    if kind is None:
        return False, "Format non supporte (attendus : .zip, .tar.gz, .rar, .7z)", 0
    if archive.stat().st_size > MAX_ARCHIVE_MB * 1024 * 1024:
        return False, f"Archive trop volumineuse (max {MAX_ARCHIVE_MB} Mo)", 0
    dest.mkdir(parents=True, exist_ok=True)
    if kind == "zip":
        return _extract_zip(archive, dest)
    if kind == "tar":
        return _extract_tar(archive, dest)
    if kind == "rar":
        return _extract_rar(archive, dest)
    if kind == "7z":
        return _extract_7z(archive, dest)
    return False, "Format non supporte", 0


def _entry_score(rel: str, depth: int) -> tuple:
    """Score de pertinence comme point d'entree (plus petit = mieux)."""
    base = Path(rel).name.lower()
    try:
        prio = ENTRY_PRIORITY.index(base)
    except ValueError:
        prio = 100
    rt = detect_runtime(base)
    rt_bonus = 0 if rt in ("python", "node", "bash") else 50
    return (prio, rt_bonus, depth, len(rel), rel)


def scan_project(root: Path) -> dict:
    """Inventorie un projet : fichiers, entrees candidates, manifestes."""
    files: list = []
    truncated = False
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        if len(files) >= MAX_TREE_FILES:
            truncated = True
            break
        try:
            size = p.stat().st_size
        except OSError:
            continue
        depth = rel.count("/")
        files.append({"path": rel, "size": size, "depth": depth,
                      "runtime": detect_runtime(p.name)})
    runnable = [f for f in files if f["runtime"] in ("python", "node", "bash")]
    runnable.sort(key=lambda f: _entry_score(f["path"], f["depth"]))
    entries = [{"path": f["path"], "runtime": f["runtime"], "size": f["size"]}
               for f in runnable[:20]]
    manifests = {}
    for f in files:
        name = Path(f["path"]).name
        if name in MANIFEST_NAMES and name not in manifests:
            manifests[name] = f["path"]
    readme = next((f["path"] for f in files
                   if Path(f["path"]).name.lower().startswith("readme")), None)
    total_size = sum(f["size"] for f in files)
    return {"files": files, "truncated": truncated,
            "total_files": len(files), "total_size": total_size,
            "entries": entries, "manifests": manifests, "readme": readme,
            "suggested_entry": entries[0] if entries else None}
