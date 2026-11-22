# Publishing GitBridge 1.0.0

The **Git repository used to publish GitBridge** is separate from the private **backup repository each end user configures**. Never publish a personal Anki2 folder, an Anki backup checkout, Git credentials, or the Anki2 Git history.

## 1. Review release source

- `manifest.json` must say `human_version: 1.0.0`, while keeping package ID `anki_github_backup` so previous local installations upgrade cleanly.
- Confirm the code contains no real backup URLs, credentials, customer data, or user profiles.
- Decide and add an appropriate source-code license before publicly inviting redistribution. **No license has been chosen on the author's behalf.**
- Test manual backup, automatic backup on exit, cancellation, normal restoration, and preservation of the installed GitBridge with a disposable Anki2.

## 2. GitHub (public source and downloads)

1. Create a **new public Git repository** for the GitBridge source. Do **not** use any repository that stores Anki backups.
2. Commit the contents of this project directory at the repository root. The workflow is in `.github/workflows/build-windows.yml`.
3. In **Actions → Build GitBridge for Windows**, manually run the workflow and download the `GitBridge-Windows` artifact.
4. Inspect and test its `GitBridge.ankiaddon` on Windows. The package must include `backup_gui/backup_gui.exe`.
5. When that exact revision is ready, create/push an annotated Git tag `v1.0.0`. The workflow builds from the tag and creates the GitHub Release with `GitBridge.ankiaddon`, its SHA-256 checksum, and `RELEASE_NOTES.md`.
6. Verify the published release and test its **actual downloaded** `.ankiaddon` in a disposable Anki profile. Keep any pre-release builds clearly labeled.

GitHub's automatically generated source ZIP **does not contain** the compiled Qt executable and is not the installable add-on.

## 3. AnkiWeb

1. Sign in to your AnkiWeb account and open <https://ankiweb.net/shared/addons>.
2. Use **Upload**, and select the **same tested `GitBridge.ankiaddon`**, not the source archive.
3. Use a descriptive title, an English description, Windows-only requirements, warning about complete Anki2 replacement, private-Git recommendation, and a support/source URL pointing to GitBridge's separate public repository.
4. Verify compatibility settings and the uploaded add-on package. If an existing listing is to be updated, update that listing rather than creating a duplicate.
5. Copy the AnkiWeb add-on code and test installing using **Tools → Add-ons → Get Add-ons**.

Per Anki's add-on packaging documentation, the `.ankiaddon` ZIP must contain files at its root (not a wrapping `anki_github_backup` folder) and must not contain Python `__pycache__` directories. The build script follows this layout.

## 4. Signing

See `SIGNING.md`. Publishing an unsigned release is technically possible, but devices enforcing Smart App Control may block the included EXE. The code-signing step requires a trusted signing identity or acceptance by an open-source signing program; it cannot be completed by a generic build without authorized keys.

## 5. Suggested AnkiWeb description

**GitBridge — Full Anki2 Backup to Git (Windows)**

> Back up your entire Anki2 data directory to **your own private Git repository** (GitHub, GitLab, or another Git server). Manual "Push to Git" closes Anki safely, makes an incremental copy, commits changes, and pushes to your configured remote/branch, then optionally reopens Anki. Optional backup on exit, progress logs, and full restore with two confirmation steps are included. Full restore **replaces all Anki profiles and data**, while preserving the previous Anki2 folder and your current GitBridge installation. Requires Windows and Git for Windows. **Not a two-way sync tool.** This package includes an unsigned separate GUI executable; Windows Smart App Control may block it. Keep your backups private.
