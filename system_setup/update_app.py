import argparse
import ast
import datetime as _dt
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
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

FILES_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = FILES_ROOT.parent
PROJECT_ROOT = FILES_ROOT
STATE_PATH = FILES_ROOT / "userdata" / "update_state.json"
BACKUP_DIR = FILES_ROOT / "update" / "backups"
SELF_UPDATE_PATH = FILES_ROOT / "update" / "update_app.new.py"
LAUNCHER_UPDATE_PATH = FILES_ROOT / "update" / "update_windows.new.bat"
MANAGED_POLICY_REL = "update/managed_paths.json"
DEFAULT_BRANCH = "main"
DEFAULT_SOURCE = "branch"
UPDATER_STATE_VERSION = 2
SELF_UPDATE_EXIT_CODE = 10
REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_PROTECTED_PATHS = (
    "characters/",
    "models/",
    "userdata/",
    "logs/",
    ".venv/",
    "python_embeded/",
    "ffmpeg/",
    "tcl/",
    "update/backups/",
    "update/update_app.new.py",
    "update/update_windows.bat",
    "update/update_windows.new.bat",
    "torch_lock.txt",
    "fine_tune/fine-tune-file.jsonl",
    "memory/chroma_data/",
    "memory/chroma_memories/",
    "tts/models/",
    "tts/output/",
    "moderation/bad_words_b64",
    "moderation/bad_words_64.txt",
    "twitch_chat/blacklist.txt",
)

USER_CONTENT_PATTERNS = ("llm/*.txt",)
STABLE_UPDATER_PROTECTED_PATHS = (
    "system_setup/update_app.py",
    "update/",
)
REQUIRED_SOURCE_FILES = (
    "requirements.txt",
    "system_setup/app_version.py",
    "system_setup/settings.py",
    "ui/app.py",
)
LATEST_REQUIRED_SOURCE_FILES = (
    "system_setup/update_app.py",
)
DEFAULT_MANAGED_POLICY = {
    "version": 1,
    "managed_roots": [
        "assets/",
        "closed_captions/",
        "fine_tune/",
        "llm/",
        "main_loop/",
        "memory/",
        "moderation/",
        "speech_to_text/",
        "stream_chat/",
        "system_setup/",
        "tests/",
        "tts/",
        "twitch_chat/",
        "ui/",
        "update/",
        "vision/",
        "vtube_studio/",
    ],
    "managed_files": [
        "provider_capabilities.py",
        "requirements.txt",
    ],
}
GENERATED_DIR_NAMES = {"__pycache__"}
GENERATED_SUFFIXES = (".pyc", ".pyo")
IGNORED_SOURCE_PATHS = (
    ".git/",
    ".github/",
    ".gitignore",
    ".gitattributes",
    "LICENSE",
    "LICENSE.md",
    "README.md",
)


class UpdateError(RuntimeError):
    pass


def _safe_project_path(rel_path: str | Path) -> Path:
    rel = _normalize_rel(rel_path)
    if not rel or rel == "." or ".." in Path(rel).parts:
        raise UpdateError(f"Unsafe project-relative path blocked: {rel_path}")
    root = PROJECT_ROOT.resolve()
    target = (root / rel).resolve()
    if os.path.commonpath([str(root), str(target)]) != str(root):
        raise UpdateError(f"Unsafe project-relative path blocked: {rel_path}")
    return target

def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat()

def _normalize_rel(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip("/")

def _is_ignored_source(rel_path: str | Path) -> bool:
    rel = _normalize_rel(rel_path)
    for ignored in IGNORED_SOURCE_PATHS:
        norm = _normalize_rel(ignored)
        if not norm:
            continue
        if ignored.endswith("/"):
            if rel == norm or rel.startswith(norm + "/"):
                return True
        elif rel == norm:
            return True
    return False

def _is_protected(rel_path: str | Path, protected_paths: tuple[str, ...]) -> bool:
    rel = _normalize_rel(rel_path).casefold()
    parts = set(rel.split("/"))
    if parts.intersection(GENERATED_DIR_NAMES) or rel.endswith(GENERATED_SUFFIXES):
        return True
    if any(fnmatchcase(rel, pattern) for pattern in USER_CONTENT_PATTERNS):
        return True
    for protected_entry in protected_paths:
        is_directory = str(protected_entry).replace("\\", "/").endswith("/")
        protected = _normalize_rel(protected_entry).casefold()
        if not protected:
            continue
        if is_directory:
            if rel == protected or rel.startswith(protected + "/"):
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
    temporary = path.with_name(f".{path.name}.{os.getpid()}.update_tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

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
        if _is_ignored_source(rel):
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


def _validated_policy_entries(values, *, directories: bool) -> list[str]:
    result: list[str] = []
    for raw in values if isinstance(values, list) else []:
        rel = _normalize_rel(raw)
        if not rel or rel == "." or ".." in Path(rel).parts or Path(rel).is_absolute():
            raise UpdateError(f"Unsafe managed-path policy entry blocked: {raw!r}")
        result.append(rel + "/" if directories else rel)
    return sorted(set(result))


def _load_managed_policy(source_root: Path) -> dict:
    policy_path = source_root / MANAGED_POLICY_REL
    if policy_path.is_file():
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UpdateError(f"Invalid managed-path policy: {exc}") from exc
    else:
        # Older stable releases predate the policy. The built-in policy keeps
        # those releases usable without allowing them to downgrade this updater.
        policy = DEFAULT_MANAGED_POLICY
    if not isinstance(policy, dict):
        raise UpdateError("The managed-path policy is not a JSON object.")
    try:
        version = int(policy.get("version") or 1)
    except (TypeError, ValueError) as exc:
        raise UpdateError("Managed-path policy version must be an integer.") from exc
    return {
        "version": version,
        "managed_roots": _validated_policy_entries(
            policy.get("managed_roots", DEFAULT_MANAGED_POLICY["managed_roots"]),
            directories=True,
        ),
        "managed_files": _validated_policy_entries(
            policy.get("managed_files", DEFAULT_MANAGED_POLICY["managed_files"]),
            directories=False,
        ),
    }


def _source_app_version(source_root: Path) -> str:
    version_file = source_root / "system_setup" / "app_version.py"
    try:
        tree = ast.parse(version_file.read_text(encoding="utf-8"), filename=str(version_file))
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in targets):
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    except Exception:
        pass
    return "unknown"


def _canonical_version_label(value: str) -> str:
    label = re.sub(r"\s+", "", str(value or "").strip().lower())
    return label[1:] if label.startswith("v") else label


def _validate_release_identity(source: dict, app_version: str) -> None:
    release_version = str(source.get("version") or "unknown").strip()
    if "unknown" in {release_version.lower(), str(app_version).lower()}:
        raise UpdateError(
            "Published release does not expose a verifiable application version; "
            "use the latest/main updater or publish a corrected release."
        )
    if _canonical_version_label(release_version) != _canonical_version_label(app_version):
        raise UpdateError(
            "Published release metadata does not match its source archive: "
            f"release/tag={release_version!r}, source APP_VERSION={app_version!r}. "
            "Refusing a potentially mixed-version downgrade. Use the latest/main "
            "updater or publish a corrected release."
        )


def _validate_source_tree(
    source_root: Path,
    source_manifest: dict[str, dict],
    policy: dict,
    *,
    stable_channel: bool = False,
) -> None:
    required_files = REQUIRED_SOURCE_FILES
    if not stable_channel:
        required_files += LATEST_REQUIRED_SOURCE_FILES
    missing = [rel for rel in required_files if not (source_root / rel).is_file()]
    if missing:
        raise UpdateError(
            "Downloaded archive does not match the expected Project Waifu layout; "
            f"missing: {', '.join(missing)}"
        )
    if (source_root / "files" / "system_setup" / "update_app.py").is_file():
        raise UpdateError(
            "Downloaded archive contains an unexpected nested files directory; refusing "
            "to create a mixed installation."
        )
    if len(source_manifest) < 25:
        raise UpdateError(
            f"Downloaded archive contains only {len(source_manifest)} managed files; "
            "refusing an incomplete update."
        )
    if not policy.get("managed_roots"):
        raise UpdateError("Managed-path policy contains no application roots.")

    syntax_errors: list[str] = []
    for rel in sorted(source_manifest):
        if not rel.lower().endswith(".py"):
            continue
        path = source_root / rel
        try:
            compile(path.read_bytes(), str(path), "exec")
        except (SyntaxError, ValueError, UnicodeError) as exc:
            syntax_errors.append(f"{rel}: {exc}")
            if len(syntax_errors) >= 5:
                break
    if syntax_errors:
        raise UpdateError(
            "Downloaded Python sources failed syntax validation:\n  "
            + "\n  ".join(syntax_errors)
        )


def _iter_managed_local_files(policy: dict, protected_paths: tuple[str, ...]):
    seen: set[str] = set()
    for root_entry in policy.get("managed_roots", []):
        root_rel = _normalize_rel(root_entry)
        root = PROJECT_ROOT / root_rel
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = _normalize_rel(path.relative_to(PROJECT_ROOT))
            if rel in seen or _is_protected(rel, protected_paths) or _is_ignored_source(rel):
                continue
            seen.add(rel)
            yield rel, path
    for file_entry in policy.get("managed_files", []):
        rel = _normalize_rel(file_entry)
        path = PROJECT_ROOT / rel
        if (
            rel not in seen
            and path.is_file()
            and not _is_protected(rel, protected_paths)
            and not _is_ignored_source(rel)
        ):
            yield rel, path

def _make_plan(
    source_root: Path,
    source_manifest: dict[str, dict],
    previous_manifest: dict[str, dict],
    protected_paths: tuple[str, ...],
    managed_policy: dict | None = None,
) -> list[dict]:
    actions: list[dict] = []
    planned_paths: set[str] = set()
    for rel, meta in sorted(source_manifest.items()):
        if _is_protected(rel, protected_paths):
            continue
        source_path = source_root / rel
        target_path = _safe_project_path(rel)
        if not target_path.exists():
            actions.append(
                {
                    "action": "add",
                    "path": rel,
                    "source": str(source_path),
                    "after_sha256": meta["sha256"],
                }
            )
            planned_paths.add(rel)
            continue
        if not target_path.is_file():
            actions.append({"action": "conflict", "path": rel, "reason": "target is not a file"})
            planned_paths.add(rel)
            continue
        current_hash = _sha256_file(target_path)
        if current_hash != meta["sha256"]:
            actions.append(
                {
                    "action": "update",
                    "path": rel,
                    "source": str(source_path),
                    "before_sha256": current_hash,
                    "after_sha256": meta["sha256"],
                }
            )
        else:
            actions.append(
                {"action": "unchanged", "path": rel, "after_sha256": meta["sha256"]}
            )
        planned_paths.add(rel)

    if previous_manifest:
        removed_paths = sorted(set(previous_manifest) - set(source_manifest))
        for rel in removed_paths:
            if _is_protected(rel, protected_paths):
                continue
            target_path = _safe_project_path(rel)
            if not target_path.exists() or not target_path.is_file():
                planned_paths.add(rel)
                continue
            previous_hash = previous_manifest.get(rel, {}).get("sha256")
            current_hash = _sha256_file(target_path)
            if previous_hash and current_hash == previous_hash:
                actions.append(
                    {
                        "action": "delete",
                        "path": rel,
                        "before_sha256": current_hash,
                        "reason": "removed upstream",
                    }
                )
            else:
                actions.append(
                    {
                        "action": "skip_delete_modified",
                        "path": rel,
                        "reason": "local file differs from previous update manifest",
                    }
                )
            planned_paths.add(rel)

    if managed_policy:
        for rel, target_path in _iter_managed_local_files(managed_policy, protected_paths):
            if rel in source_manifest or rel in planned_paths:
                continue
            current_hash = _sha256_file(target_path)
            actions.append(
                {
                    "action": "prune",
                    "path": rel,
                    "before_sha256": current_hash,
                    "reason": "managed application file is absent from the selected source",
                }
            )
            planned_paths.add(rel)
    return actions

def _summarize_actions(actions: list[dict]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in actions:
        summary[item["action"]] = summary.get(item["action"], 0) + 1
    return summary

def _print_summary(summary: dict[str, int]) -> None:
    for key in (
        "add",
        "update",
        "delete",
        "prune",
        "unchanged",
        "conflict",
        "skip_delete_modified",
    ):
        if summary.get(key):
            print(f"{key}: {summary[key]}")


def _print_removal_plan(actions: list[dict]) -> None:
    removals = [item for item in actions if item["action"] in {"delete", "prune"}]
    if removals:
        print("Files scheduled for removal:")
        for item in removals[:50]:
            print(f"  {item['action']}: {item['path']}")
        if len(removals) > 50:
            print(f"  ...and {len(removals) - 50} more")
    preserved = [item for item in actions if item["action"] == "skip_delete_modified"]
    if preserved:
        print("Locally modified removed-upstream files preserved:")
        for item in preserved[:50]:
            print(f"  {item['path']}")
        if len(preserved) > 50:
            print(f"  ...and {len(preserved) - 50} more")

def _create_backup(actions: list[dict], source: dict, previous_state: dict) -> Path | None:
    backup_actions = [
        item for item in actions if item["action"] in {"add", "update", "delete", "prune"}
    ]
    if not backup_actions:
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"update_backup_{_now_stamp()}.zip"
    suffix = 1
    while backup_path.exists():
        backup_path = BACKUP_DIR / f"update_backup_{_now_stamp()}_{suffix}.zip"
        suffix += 1
    manifest = {
        "format_version": 2,
        "created_at": _now_iso(),
        "source": source,
        "previous_state": previous_state,
        "actions": [
            {
                key: item.get(key)
                for key in ("action", "path", "before_sha256", "after_sha256", "reason")
                if item.get(key) is not None
            }
            for item in backup_actions
        ],
        "files": [],
    }
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in backup_actions:
            rel = item["path"]
            target = _safe_project_path(rel)
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

def _atomic_copy(source: Path, target: Path, expected_sha256: str | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.update_tmp")
    try:
        shutil.copy2(source, temporary)
        if expected_sha256 and _sha256_file(temporary) != expected_sha256:
            raise UpdateError(f"Staged file hash changed while copying: {target}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _apply_actions(actions: list[dict]) -> None:
    for item in actions:
        rel = item["path"]
        target = _safe_project_path(rel)
        if item["action"] in {"add", "update"}:
            source = Path(item["source"])
            _atomic_copy(source, target, item.get("after_sha256"))
        elif item["action"] in {"delete", "prune"}:
            target.unlink(missing_ok=True)
            _remove_empty_parents(target)


def _read_backup_manifest(backup_path: Path) -> dict:
    try:
        with zipfile.ZipFile(backup_path) as archive:
            raw = archive.read("_update_backup_manifest.json")
        manifest = json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise UpdateError(f"Invalid update backup {backup_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise UpdateError(f"Invalid update backup manifest in {backup_path}")
    return manifest


def _backup_actions(manifest: dict) -> list[dict]:
    actions = manifest.get("actions")
    if isinstance(actions, list):
        return [item for item in actions if isinstance(item, dict)]
    # Compatibility with backups made by the first archive updater.
    return [
        {"action": "update", "path": rel, "reason": "legacy backup restore"}
        for rel in manifest.get("files", [])
        if isinstance(rel, str)
    ]


def _rollback_conflicts(actions: list[dict]) -> list[str]:
    conflicts: list[str] = []
    for item in actions:
        rel = _normalize_rel(item.get("path", ""))
        target = _safe_project_path(rel)
        action = item.get("action")
        expected_after = item.get("after_sha256")
        if action == "add":
            if target.exists() and (
                not target.is_file()
                or (expected_after and _sha256_file(target) != expected_after)
            ):
                conflicts.append(f"{rel} was modified after the update")
        elif action == "update":
            if not target.is_file():
                conflicts.append(f"{rel} is missing")
            elif expected_after and _sha256_file(target) != expected_after:
                conflicts.append(f"{rel} was modified after the update")
        elif action in {"delete", "prune"} and target.exists():
            conflicts.append(f"{rel} was recreated after the update")
    return conflicts


def _restore_backup(backup_path: Path, *, force: bool = False) -> dict:
    manifest = _read_backup_manifest(backup_path)
    actions = _backup_actions(manifest)
    if not force:
        conflicts = _rollback_conflicts(actions)
        if conflicts:
            raise UpdateError(
                "Rollback stopped to protect newer local changes:\n  "
                + "\n  ".join(conflicts[:20])
                + (f"\n  ...and {len(conflicts) - 20} more." if len(conflicts) > 20 else "")
                + "\nUse --force to overwrite them."
            )

    archived_files = set(manifest.get("files", []))
    with zipfile.ZipFile(backup_path) as archive:
        for item in reversed(actions):
            rel = _normalize_rel(item.get("path", ""))
            target = _safe_project_path(rel)
            action = item.get("action")
            if action == "add" and rel not in archived_files:
                target.unlink(missing_ok=True)
                _remove_empty_parents(target)
                continue
            if rel not in archived_files:
                continue
            try:
                data = archive.read(rel)
            except KeyError as exc:
                raise UpdateError(f"Backup is missing archived file {rel}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{os.getpid()}.rollback_tmp")
            try:
                temporary.write_bytes(data)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

    if "previous_state" in manifest:
        previous_state = manifest.get("previous_state")
        if isinstance(previous_state, dict) and previous_state:
            _write_json(STATE_PATH, previous_state)
        else:
            STATE_PATH.unlink(missing_ok=True)
    return manifest

def _venv_python() -> str:
    win = FILES_ROOT / ".venv" / "Scripts" / "python.exe"
    if win.is_file():
        return str(win)
    return sys.executable

def _install_requirements() -> str | None:
    requirements = FILES_ROOT / "requirements.txt"
    if not requirements.is_file():
        print("No requirements.txt found after update; skipping dependency install.")
        return None

    python = _venv_python()
    if python == sys.executable:
        print(
            "WARNING: app venv (.venv) not found; installing requirements into the "
            "current interpreter (inside python_embeded). Run setup.bat to rebuild the venv if the app "
            "fails to import its dependencies."
        )
    print(f"Installing Python dependencies with {python}")
    subprocess.check_call([python, "-s", "-m", "pip", "install", "-r", str(requirements)])
    return _sha256_file(requirements)


def _read_state() -> dict:
    return _load_json(STATE_PATH, {})


def _write_state(
    source: dict,
    manifest: dict[str, dict],
    protected_paths: tuple[str, ...],
    *,
    dependencies_installed_sha256: str | None = None,
) -> dict:
    requirements = FILES_ROOT / "requirements.txt"
    required_hash = _sha256_file(requirements) if requirements.is_file() else None
    state = {
        "updater_state_version": UPDATER_STATE_VERSION,
        "updated_at": _now_iso(),
        "repo": source.get("repo"),
        "source_kind": source.get("kind"),
        "source_name": source.get("name"),
        "installed_version": source.get("version"),
        "installed_commit": source.get("commit", ""),
        "app_version": source.get("app_version", "unknown"),
        "published_at": source.get("published_at", ""),
        "protected_paths": list(protected_paths),
        "requirements_sha256": required_hash,
        "dependencies_installed_sha256": dependencies_installed_sha256,
        "dependencies_pending": bool(
            required_hash and required_hash != dependencies_installed_sha256
        ),
        "manifest": manifest,
    }
    _write_json(STATE_PATH, state)
    return state


def _installed_version(state: dict) -> str:
    return state.get("installed_version") or APP_VERSION or "unknown"


def _noupdate_exists() -> bool:
    return (APP_ROOT / ".noupdate").exists() or (FILES_ROOT / ".noupdate").exists()


def _protected_paths(
    extra_paths: list[str] | None,
    *,
    stable_channel: bool = False,
) -> tuple[str, ...]:
    combined = list(DEFAULT_PROTECTED_PATHS)
    if stable_channel:
        combined.extend(STABLE_UPDATER_PROTECTED_PATHS)
    if extra_paths:
        combined.extend(extra_paths)
    return tuple(combined)


def _stage_self_update(source_root: Path, source: dict, *, skip: bool) -> bool:
    if skip or source.get("kind") != "branch":
        return False
    candidates = (
        (
            source_root / "system_setup" / "update_app.py",
            Path(__file__).resolve(),
            SELF_UPDATE_PATH,
        ),
        (
            source_root / "update" / "update_windows.bat",
            FILES_ROOT / "update" / "update_windows.bat",
            LAUNCHER_UPDATE_PATH,
        ),
    )
    staged = False
    for source_path, current_path, pending_path in candidates:
        if not source_path.is_file():
            continue
        if current_path.is_file() and _sha256_file(source_path) == _sha256_file(current_path):
            continue
        _atomic_copy(source_path, pending_path, _sha256_file(source_path))
        staged = True
        print(f"Staged updater component: {pending_path.name}")
    if staged:
        print("Updater components changed; the launcher will install them and resume the update.")
    return staged


def _resolve_backup(requested: str | None = None) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    root = BACKUP_DIR.resolve()
    if requested:
        candidate = Path(requested)
        if not candidate.is_absolute():
            candidate = BACKUP_DIR / candidate
        candidate = candidate.resolve()
        if os.path.commonpath([str(root), str(candidate)]) != str(root):
            raise UpdateError("Rollback backup must be inside files/update/backups.")
        if not candidate.is_file():
            raise UpdateError(f"Rollback backup not found: {candidate}")
        return candidate
    backups = sorted(BACKUP_DIR.glob("update_backup_*.zip"), key=lambda path: path.stat().st_mtime)
    if not backups:
        raise UpdateError("No updater backups are available.")
    return backups[-1]


def command_backups(_args) -> int:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = sorted(BACKUP_DIR.glob("update_backup_*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not backups:
        print("No updater backups are available.")
        return 0
    for path in backups:
        try:
            manifest = _read_backup_manifest(path)
            source = manifest.get("source") or {}
            version = source.get("version") or source.get("name") or "unknown"
            created = manifest.get("created_at") or "unknown time"
            print(f"{path.name}  |  {created}  |  update to {version}")
        except UpdateError as exc:
            print(f"{path.name}  |  invalid: {exc}")
    return 0


def command_rollback(args) -> int:
    backup_path = _resolve_backup(args.backup)
    manifest = _read_backup_manifest(backup_path)
    actions = _backup_actions(manifest)
    print(f"Backup: {backup_path}")
    print(f"Files affected: {len(actions)}")
    if not args.yes:
        answer = input("Roll back this update now? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Rollback cancelled.")
            return 0
    _restore_backup(backup_path, force=args.force)
    print("Rollback completed. Restart the app before using it.")
    return 0


def command_check(args) -> int:
    if _noupdate_exists() and not args.ignore_noupdate:
        print("Updates are disabled because a .noupdate file exists.")
        return 0

    source = _select_source(args.repo, args.source, args.branch, args.allow_prerelease)
    state = _read_state()
    installed = _installed_version(state)
    maintenance_required = int(state.get("updater_state_version") or 0) < UPDATER_STATE_VERSION
    dependencies_pending = bool(state.get("dependencies_pending"))
    update_available = (
        installed == "unknown"
        or installed != source["version"]
        or maintenance_required
        or dependencies_pending
    )

    print(f"Repository: {args.repo}")
    print(f"Installed:  {installed}")
    print(f"Available:  {source['version']} ({source['kind']}: {source['name']})")
    if source.get("published_at"):
        print(f"Published:  {source['published_at']}")
    if maintenance_required:
        print("Maintenance: updater manifest migration / stale-file repair required")
    if dependencies_pending:
        print("Dependencies: requirements installation is pending")
    print(f"Update available: {'yes' if update_available else 'no'}")
    return 0


def command_update(args) -> int:
    if _noupdate_exists() and not args.ignore_noupdate:
        print("Updates are disabled because a .noupdate file exists.")
        return 0

    source = _select_source(args.repo, args.source, args.branch, args.allow_prerelease)
    source["repo"] = args.repo
    stable_channel = source.get("kind") in {"release", "tag"}
    protected_paths = _protected_paths(args.preserve, stable_channel=stable_channel)
    state = _read_state()
    installed = _installed_version(state)

    state_is_current = int(state.get("updater_state_version") or 0) >= UPDATER_STATE_VERSION
    if installed == source["version"] and state_is_current and not args.force:
        if args.install_deps and state.get("dependencies_pending"):
            installed_hash = _install_requirements()
            state["dependencies_installed_sha256"] = installed_hash
            state["dependencies_pending"] = bool(
                state.get("requirements_sha256")
                and state.get("requirements_sha256") != installed_hash
            )
            state["updated_at"] = _now_iso()
            _write_json(STATE_PATH, state)
            print("Pending dependency installation completed.")
            return 0
        if state.get("dependencies_pending"):
            print(
                f"Application files are already on {source['version']}, but dependency "
                "installation is pending. Rerun with --install-deps or run setup.bat."
            )
            return 0
        print(f"Already on {source['version']}. Use --force to verify and reinstall this version.")
        return 0

    requirements = FILES_ROOT / "requirements.txt"
    before_requirements_hash = _sha256_file(requirements) if requirements.is_file() else None

    with tempfile.TemporaryDirectory(prefix="project_waifu_update_") as temp_dir:
        temp_root = Path(temp_dir)
        archive_path = temp_root / "source.zip"
        extract_root = temp_root / "extract"
        print(f"Downloading {args.repo} {source['version']}...")
        _download(source["zip_url"], archive_path)
        source_root = _safe_extract(archive_path, extract_root)

        source_manifest = _build_manifest(source_root, protected_paths)
        managed_policy = _load_managed_policy(source_root)
        source["app_version"] = _source_app_version(source_root)
        _validate_source_tree(
            source_root,
            source_manifest,
            managed_policy,
            stable_channel=stable_channel,
        )
        if stable_channel:
            _validate_release_identity(source, source["app_version"])

        if not args.dry_run and _stage_self_update(
            source_root,
            source,
            skip=args.skip_self_update,
        ):
            return SELF_UPDATE_EXIT_CODE

        previous_manifest = state.get("manifest") if state.get("repo") == args.repo else {}
        actions = _make_plan(
            source_root,
            source_manifest,
            previous_manifest,
            protected_paths,
            managed_policy,
        )
        summary = _summarize_actions(actions)

        print(f"Selected channel: {source['kind']} ({source['name']})")
        print(f"Application version: {source['app_version']}")
        print(f"Protected paths: {', '.join(protected_paths)}")
        _print_summary(summary)
        _print_removal_plan(actions)

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

        backup_path = _create_backup(actions, source, state)
        if backup_path:
            print(f"Backup created: {backup_path}")

        try:
            _apply_actions(actions)
            previously_installed_requirements = (
                state.get("dependencies_installed_sha256") or before_requirements_hash
            )
            new_state = _write_state(
                source,
                source_manifest,
                protected_paths,
                dependencies_installed_sha256=previously_installed_requirements,
            )
        except Exception as exc:
            if backup_path:
                print("Update apply failed; restoring the pre-update backup...")
                _restore_backup(backup_path, force=True)
            raise UpdateError(f"Update was rolled back after an apply failure: {exc}") from exc
        print(f"Update applied: {source['version']}")

    if args.install_deps and new_state.get("dependencies_pending"):
        installed_hash = _install_requirements()
        new_state["dependencies_installed_sha256"] = installed_hash
        new_state["dependencies_pending"] = bool(
            new_state.get("requirements_sha256")
            and new_state.get("requirements_sha256") != installed_hash
        )
        new_state["updated_at"] = _now_iso()
        _write_json(STATE_PATH, new_state)
    elif new_state.get("dependencies_pending"):
        print(
            "requirements.txt changed. Dependency installation remains pending; "
            "run setup.bat or rerun this updater with --install-deps."
        )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project Waifu source-archive updater.")
    parser.add_argument("--repo", default=PUBLIC_REPOSITORY, help="GitHub repository in owner/name form.")
    parser.add_argument(
        "--source",
        choices=("release", "tag", "branch"),
        default=DEFAULT_SOURCE,
        help=(
            "Update channel. The default follows the main branch because it is the "
            "authoritative current app build; release/tag channels are opt-in."
        ),
    )
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--allow-prerelease", action="store_true")
    parser.add_argument("--ignore-noupdate", action="store_true")
    parser.add_argument("--skip-self-update", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--preserve", action="append", help="Additional relative file/folder path to preserve.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Check GitHub for an available update.")

    update_parser = subparsers.add_parser("update", help="Download and apply an update.")
    update_parser.add_argument("--dry-run", action="store_true")
    update_parser.add_argument("--force", action="store_true")
    update_parser.add_argument("--yes", action="store_true")
    update_parser.add_argument("--install-deps", action="store_true")
    rollback_parser = subparsers.add_parser(
        "rollback",
        help="Restore an updater backup (the newest backup by default).",
    )
    rollback_parser.add_argument("--backup", help="Backup filename from files/update/backups.")
    rollback_parser.add_argument("--yes", action="store_true")
    rollback_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files modified after the update.",
    )
    subparsers.add_parser("backups", help="List available updater backups.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return command_check(args)
        if args.command == "update":
            return command_update(args)
        if args.command == "rollback":
            return command_rollback(args)
        if args.command == "backups":
            return command_backups(args)
    except (UpdateError, urllib.error.URLError, urllib.error.HTTPError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Update failed: {exc}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
