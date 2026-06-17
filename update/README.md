# Project Waifu updater

`update.bat` runs `files/system_setup/update_app.py`.

This updater downloads the latest GitHub release source archive for
`Taytarisms/Project_Waifu`, extracts it to a temporary folder, and overlays
app-owned files into the local `files` folder.

Protected paths are skipped during the overlay:

- `characters/`
- `models/`
- `userdata/`
- `logs/`
- `python_embeded/`
- `ffmpeg/`
- `tcl/`
- `update/backups/`
- `fine_tune/fine-tune-file.jsonl`
- `memory/chroma_data/`
- `memory/chroma_memories/`
- `setup_log.txt`
- `closed_captions.txt`
- any `__pycache__` or `.pyc/.pyo` files

or basically anything that pertains to your userdata.

Before overwriting or deleting files, the updater writes a backup zip under
`files/update/backups`. It also stores an update manifest in
`files/userdata/update_state.json`. After the first successful update, the
manifest lets the updater safely remove files that disappeared upstream, but
only when the local copy still matches the previous updater-managed version.