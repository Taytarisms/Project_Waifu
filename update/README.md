# Project Waifu updater

Use one of the launchers in the application root:

- `update.bat` follows the current `main` branch (the recommended/latest build).
- `update_stable.bat` follows the newest published GitHub release. A release can
  intentionally be older than `main`.
- `rollback_update.bat` restores the newest updater backup.

The public repository root corresponds to the local `files` directory. The
small root launchers call the repository-managed `update/update_windows.bat`,
which lets future updates improve the launcher without trying to overwrite a
batch file while it is running.

## Safety model

Before changing application files, the updater:

1. downloads the selected source into a temporary directory;
2. verifies the expected repository layout, managed-path policy, and Python
   syntax;
3. stages updater changes and restarts the update in a second pass when using
   the latest channel;
4. computes a complete add/update/delete/prune plan;
5. writes a rollback zip under `files/update/backups`; and
6. atomically replaces individual files.

If applying a file or writing the new state fails, the updater automatically
restores the pre-update backup. The explicit rollback launcher refuses to
overwrite files changed after an update unless the command-line `--force`
option is used.

The managed-path policy in `managed_paths.json` lets the first run remove stale
application files left by older updater implementations. Pruning is limited to
declared application-owned paths. User content and runtime data remain
protected, including:

- `characters/`, `models/`, `userdata/`, and `logs/`
- `.venv/`, `python_embeded/`, `ffmpeg/`, and `tcl/`
- the installer-specific `torch_lock.txt`
- `update/backups/`
- `fine_tune/fine-tune-file.jsonl`
- `memory/chroma_data/` and `memory/chroma_memories/`
- `tts/models/` and `tts/output/`
- all `.txt` prompts under `llm/`, including custom names and subdirectories
- `moderation/bad_words_b64`, `moderation/bad_words_64.txt`, and
  `twitch_chat/blacklist.txt`
- generated `__pycache__`, `.pyc`, and `.pyo` files

Protected user content is excluded from upstream replacements and stale-file
pruning, even on the first update or when an older manifest tracked it.
For a custom prompt stored elsewhere or with a different extension, pass
`--preserve RELATIVE_PATH` to the Python updater (paths are relative to `files/`).

Stable release updates additionally protect `system_setup/update_app.py` and
the entire `update/` directory, so choosing an older release cannot downgrade
the newer updater infrastructure. A release/tag label must also match the
`APP_VERSION` inside its source archive. Mismatched release metadata is rejected
instead of risking a mixed-version installation.

## Command-line recovery

From the `files` directory, the embedded Python can also be used directly:

```text
python_embeded\python.exe system_setup\update_app.py backups
python_embeded\python.exe system_setup\update_app.py rollback
python_embeded\python.exe system_setup\update_app.py rollback --backup NAME.zip
```

Update state is stored in `userdata/update_state.json`. If `requirements.txt`
changes and dependency installation is declined, the state records that work
as pending and the updater will continue to report it until dependencies are
installed.
