import argparse
import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

try:
    from app_version import APP_VERSION, PUBLIC_REPOSITORY
except Exception:
    APP_VERSION = "unknown"
    PUBLIC_REPOSITORY = "Taytarisms/Project_Waifu"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT.parent
STATE_PATH = PROJECT_ROOT / "userdata" / "update_state.json"
BACKUP_DIR = PROJECT_ROOT / "update" / "backups"
DEFAULT_BRANCH = "main"
REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_PROTECTED_PATHS = (
    "characters/",
    "models/",
    "userdata/",
    "logs/",
    "python_embeded/",
    "ffmpeg/",
    "tcl/",
    "update/backups/",
    "fine_tune/fine-tune-file.jsonl",
    "memory/chroma_data/",
    "memory/chroma_memories/",
    "setup_log.txt",
    "closed_captions.txt",
)
GENERATED_DIR_NAMES = {"__pycache__"}
GENERATED_SUFFIXES = (".pyc", ".pyo")


class UpdateError(RuntimeError):
    pass

def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat()

def _normalize_rel(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip("/")

def _is_protected(rel_path: str | Path, protected_paths: tuple[str, ...]) -> bool:
    rel = _normalize_rel(rel_path)
    parts = set(rel.split("/"))
    if parts.intersection(GENERATED_DIR_NAMES) or rel.endswith(GENERATED_SUFFIXES):
        return True
    for protected in protected_paths:
        protected = _normalize_rel(protected)
        if not protected:
            continue
        if protected.endswith("/"):
            base = protected.rstrip("/")
            if rel == base or rel.startswith(base + "/"):
                return True
        elif rel == protected:
            return True
    return False

def _load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default

def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Project-Waifu-Updater",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def _github_json(url: str):
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))

def _github_json_or_none(url: str):
    try:
        return _github_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise

def _source_from_release(repo: str, allow_prerelease: bool) -> dict | None:
    if allow_prerelease:
        releases = _github_json_or_none(f"https://api.github.com/repos/{repo}/releases")
        if releases:
            release = releases[0]
        else:
            return None
    else:
        release = _github_json_or_none(f"https://api.github.com/repos/{repo}/releases/latest")
        if not release:
            return None

    return {
        "kind": "release",
        "name": release.get("name") or release.get("tag_name") or "latest release",
        "version": release.get("tag_name") or release.get("name") or "unknown",
        "commit": release.get("target_commitish") or "",
        "published_at": release.get("published_at") or "",
        "zip_url": release.get("zipball_url"),
    }

def _source_from_tag(repo: str) -> dict | None:
    tags = _github_json_or_none(f"https://api.github.com/repos/{repo}/tags")
    if not tags:
        return None
    tag = tags[0]
    commit = (tag.get("commit") or {}).get("sha", "")
    return {
        "kind": "tag",
        "name": tag.get("name") or "latest tag",
        "version": tag.get("name") or commit[:12] or "unknown",
        "commit": commit,
        "published_at": "",
        "zip_url": tag.get("zipball_url"),
    }

def _source_from_branch(repo: str, branch: str) -> dict:
    branch_data = _github_json(f"https://api.github.com/repos/{repo}/branches/{branch}")
    commit = ((branch_data.get("commit") or {}).get("sha") or "").strip()
    if not commit:
        raise UpdateError(f"Could not resolve branch commit for {repo}:{branch}")
    return {
        "kind": "branch",
        "name": branch,
        "version": f"{branch}@{commit[:12]}",
        "commit": commit,
        "published_at": "",
        "zip_url": f"https://api.github.com/repos/{repo}/zipball/{commit}",
    }

def _select_source(repo: str, source: str, branch: str, allow_prerelease: bool) -> dict:
    if source == "release":
        result = _source_from_release(repo, allow_prerelease)
        if result:
            return result
        result = _source_from_tag(repo)
        if result:
            return result
        return _source_from_branch(repo, branch)
    if source == "tag":
        result = _source_from_tag(repo)
        if result:
            return result
        return _source_from_branch(repo, branch)
    return _source_from_branch(repo, branch)

def _download(url: str, destination: Path) -> None:
    if not url:
        raise UpdateError("Selected update source does not have a downloadable archive.")
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        with destination.open("wb") as handle:
            shutil.copyfileobj(response, handle)

def _safe_extract(zip_path: Path, destination: Path) -> Path:
    destination = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if os.path.commonpath([str(destination), str(target)]) != str(destination):
                raise UpdateError(f"Unsafe archive path blocked: {member.filename}")
        archive.extractall(destination)

    children = [item for item in destination.iterdir() if item.is_dir()]
    if len(children) == 1:
        return children[0]
    return destination

def _iter_source_files(source_root: Path, protected_paths: tuple[str, ...]):
    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        rel = _normalize_rel(path.relative_to(source_root))
        if _is_protected(rel, protected_paths):
            continue
        yield rel, path

def _build_manifest(source_root: Path, protected_paths: tuple[str, ...]) -> dict[str, dict]:
    manifest = {}
    for rel, path in _iter_source_files(source_root, protected_paths):
        manifest[rel] = {
            "sha256": _sha256_file(path),
            "size": path.stat().st_size,
        }
    return manifest

def _make_plan(
    source_root: Path,
    source_manifest: dict[str, dict],
    previous_manifest: dict[str, dict],
    protected_paths: tuple[str, ...],
) -> list[dict]:
    actions: list[dict] = []
    for rel, meta in sorted(source_manifest.items()):
        source_path = source_root / rel
        target_path = PROJECT_ROOT / rel
        if not target_path.exists():
            actions.append({"action": "add", "path": rel, "source": str(source_path)})
            continue
        if not target_path.is_file():
            actions.append({"action": "conflict", "path": rel, "reason": "target is not a file"})
            continue
        current_hash = _sha256_file(target_path)
        if current_hash != meta["sha256"]:
            actions.append({"action": "update", "path": rel, "source": str(source_path)})
        else:
            actions.append({"action": "unchanged", "path": rel})

    if previous_manifest:
        removed_paths = sorted(set(previous_manifest) - set(source_manifest))
        for rel in removed_paths:
            if _is_protected(rel, protected_paths):
                continue
            target_path = PROJECT_ROOT / rel
            if not target_path.exists() or not target_path.is_file():
                continue
            previous_hash = previous_manifest.get(rel, {}).get("sha256")
            current_hash = _sha256_file(target_path)
            if previous_hash and current_hash == previous_hash:
                actions.append({"action": "delete", "path": rel})
            else:
                actions.append(
                    {
                        "action": "skip_delete_modified",
                        "path": rel,
                        "reason": "local file differs from previous update manifest",
                    }
                )
    return actions

def _summarize_actions(actions: list[dict]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in actions:
        summary[item["action"]] = summary.get(item["action"], 0) + 1
    return summary

def _print_summary(summary: dict[str, int]) -> None:
    for key in ("add", "update", "delete", "unchanged", "conflict", "skip_delete_modified"):
        if summary.get(key):
            print(f"{key}: {summary[key]}")

def _create_backup(actions: list[dict], source: dict) -> Path | None:
    backup_actions = [item for item in actions if item["action"] in {"update", "delete"}]
    if not backup_actions:
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"update_backup_{_now_stamp()}.zip"
    manifest = {
        "created_at": _now_iso(),
        "source": source,
        "files": [],
    }
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in backup_actions:
            rel = item["path"]
            target = PROJECT_ROOT / rel
            if target.is_file():
                archive.write(target, rel)
                manifest["files"].append(rel)
        archive.writestr("_update_backup_manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    return backup_path

def _remove_empty_parents(path: Path) -> None:
    current = path.parent
    while current != PROJECT_ROOT and PROJECT_ROOT in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent

def _apply_actions(actions: list[dict]) -> None:
    for item in actions:
        rel = item["path"]
        target = PROJECT_ROOT / rel
        if item["action"] in {"add", "update"}:
            source = Path(item["source"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif item["action"] == "delete":
            target.unlink(missing_ok=True)
            _remove_empty_parents(target)

def _venv_python() -> str:
    win = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    posix = PROJECT_ROOT / ".venv" / "bin" / "python"
    if win.is_file():
        return str(win)
    if posix.is_file():
        return str(posix)
    return sys.executable

def _install_requirements_if_changed(before_hash: str | None) -> None:
    requirements = PROJECT_ROOT / "requirements.txt"
    if not requirements.is_file():
        print("No requirements.txt found after update; skipping dependency install.")
        return
    after_hash = _sha256_file(requirements)
    if before_hash == after_hash:
        print("requirements.txt unchanged; skipping dependency install.")
        return

    python = _venv_python()
    if python == sys.executable:
        print(
            "WARNING: app venv (.venv) not found; installing requirements into the "
            "current interpreter (inside python_embedded). Run setup.bat to rebuild the venv if the app "
            "fails to import its dependencies."
        )
    print(f"requirements.txt changed; installing Python dependencies with {python}")
    subprocess.check_call([python, "-s", "-m", "pip", "install", "-r", str(requirements)])


def _read_state() -> dict:
    return _load_json(STATE_PATH, {})


def _write_state(source: dict, manifest: dict[str, dict], protected_paths: tuple[str, ...]) -> None:
    state = {
        "updated_at": _now_iso(),
        "repo": source.get("repo"),
        "source_kind": source.get("kind"),
        "installed_version": source.get("version"),
        "installed_commit": source.get("commit", ""),
        "published_at": source.get("published_at", ""),
        "protected_paths": list(protected_paths),
        "manifest": manifest,
    }
    _write_json(STATE_PATH, state)


def _installed_version(state: dict) -> str:
    return state.get("installed_version") or APP_VERSION or "unknown"


def _noupdate_exists() -> bool:
    return (APP_ROOT / ".noupdate").exists() or (PROJECT_ROOT / ".noupdate").exists()


def _protected_paths(extra_paths: list[str] | None) -> tuple[str, ...]:
    combined = list(DEFAULT_PROTECTED_PATHS)
    if extra_paths:
        combined.extend(extra_paths)
    return tuple(combined)


def command_check(args) -> int:
    if _noupdate_exists() and not args.ignore_noupdate:
        print("Updates are disabled because a .noupdate file exists.")
        return 0

    source = _select_source(args.repo, args.source, args.branch, args.allow_prerelease)
    state = _read_state()
    installed = _installed_version(state)
    update_available = installed == "unknown" or installed != source["version"]

    print(f"Repository: {args.repo}")
    print(f"Installed:  {installed}")
    print(f"Available:  {source['version']} ({source['kind']}: {source['name']})")
    if source.get("published_at"):
        print(f"Published:  {source['published_at']}")
    print(f"Update available: {'yes' if update_available else 'no'}")
    return 0


def command_update(args) -> int:
    if _noupdate_exists() and not args.ignore_noupdate:
        print("Updates are disabled because a .noupdate file exists.")
        return 0

    protected_paths = _protected_paths(args.preserve)
    source = _select_source(args.repo, args.source, args.branch, args.allow_prerelease)
    source["repo"] = args.repo
    state = _read_state()
    installed = _installed_version(state)

    if installed == source["version"] and not args.force:
        print(f"Already on {source['version']}. Use --force to reinstall this version.")
        return 0

    requirements = PROJECT_ROOT / "requirements.txt"
    before_requirements_hash = _sha256_file(requirements) if requirements.is_file() else None

    with tempfile.TemporaryDirectory(prefix="project_waifu_update_") as temp_dir:
        temp_root = Path(temp_dir)
        archive_path = temp_root / "source.zip"
        extract_root = temp_root / "extract"
        print(f"Downloading {args.repo} {source['version']}...")
        _download(source["zip_url"], archive_path)
        source_root = _safe_extract(archive_path, extract_root)

        source_manifest = _build_manifest(source_root, protected_paths)
        previous_manifest = state.get("manifest") if state.get("repo") == args.repo else {}
        actions = _make_plan(source_root, source_manifest, previous_manifest, protected_paths)
        summary = _summarize_actions(actions)

        print(f"Protected paths: {', '.join(protected_paths)}")
        _print_summary(summary)

        if summary.get("conflict"):
            print("Conflicting paths were found. Resolve them or use a clean install before updating.")
            return 2

        if args.dry_run:
            print("Dry run complete; no files were changed.")
            return 0

        if not args.yes:
            answer = input("Apply this update now? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Update cancelled.")
                return 0

        backup_path = _create_backup(actions, source)
        if backup_path:
            print(f"Backup created: {backup_path}")

        _apply_actions(actions)
        _write_state(source, source_manifest, protected_paths)
        print(f"Update applied: {source['version']}")

    if args.install_deps:
        _install_requirements_if_changed(before_requirements_hash)
    elif before_requirements_hash and (PROJECT_ROOT / "requirements.txt").is_file():
        after_hash = _sha256_file(PROJECT_ROOT / "requirements.txt")
        if before_requirements_hash != after_hash:
            print("requirements.txt changed. Run setup.bat or rerun this updater with --install-deps.")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project Waifu source-archive updater.")
    parser.add_argument("--repo", default=PUBLIC_REPOSITORY, help="GitHub repository in owner/name form.")
    parser.add_argument("--source", choices=("release", "tag", "branch"), default="release")
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--allow-prerelease", action="store_true")
    parser.add_argument("--ignore-noupdate", action="store_true")
    parser.add_argument("--preserve", action="append", help="Additional relative file/folder path to preserve.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Check GitHub for an available update.")

    update_parser = subparsers.add_parser("update", help="Download and apply an update.")
    update_parser.add_argument("--dry-run", action="store_true")
    update_parser.add_argument("--force", action="store_true")
    update_parser.add_argument("--yes", action="store_true")
    update_parser.add_argument("--install-deps", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return command_check(args)
        if args.command == "update":
            return command_update(args)
    except (UpdateError, urllib.error.URLError, urllib.error.HTTPError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Update failed: {exc}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())