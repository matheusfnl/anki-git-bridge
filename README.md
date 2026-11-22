# GitBridge 1.0.0

**Back up and restore your entire Anki2 folder using Git.** GitBridge is a Windows Anki add-on with a **Push to Git** toolbar button, configurable backup on exit, a restore wizard, and an independent progress window.

> [!IMPORTANT]
> GitBridge is a **backup tool**, not two-way synchronization. It never pulls automatically or force-pushes. Your complete Anki2 folder can contain personal data and account information. **Use a private Git repository** and protect its credentials.

## Features

- **Full Anki2 backup:** profiles, decks, collections, media, settings, and add-ons.
- **Incremental copy:** unchanged files are skipped; Git records changes and preserves history.
- **Push to Git:** confirmation, normal Anki shutdown, backup, commit, push, optional restart.
- **Backup on exit:** optional; does not reopen Anki.
- **Restore from Git:** download a separate copy, or restore the entire Anki2 folder with two confirmations.
- **Previous data preserved:** a full restore saves the prior Anki2 in a separate folder and never deletes it automatically.
- **Keep current GitBridge:** full restore preserves the installed GitBridge version and configuration; other add-ons come from the chosen Git commit.
- **AnkiWeb-safe:** Push and Restore are disabled during collection or media synchronization.
- **English default / Portuguese (Brazil):** interface language follows the Anki setting (some diagnostic logs still use Portuguese).
- **GitHub, GitLab, or any Git server:** HTTPS and SSH remotes are supported.

## Requirements

- Windows with a compatible recent desktop Anki version (tested by the project author on Windows; exact Anki version coverage has not been certified).
- [Git for Windows](https://git-scm.com/downloads/win), authenticated for the desired remote (for example, SSH or Git Credential Manager).
- The Windows **GitBridge.ankiaddon** release, which bundles the separate Qt progress application. Users do **not** need to install Python, PyQt6, or PyInstaller.

> **Security warning:** The included GUI binary may be unsigned until a trusted code-signing certificate is obtained. Windows Smart App Control can block it. Do not disable system security protections just to install an add-on.

## Install

1. Download `GitBridge.ankiaddon` from the [GitHub Releases](../../releases) page or AnkiWeb when the listing is live.
2. Open **Anki → Tools → Add-ons → Install from file**, choose the package, and restart Anki.
3. Open **Tools → GitBridge → Settings**.
4. Choose your Anki2 folder (usually `%APPDATA%\Anki2`), a **separate** Git backup folder, a remote URL, and a branch. Your remote should be **private** and ideally empty on first use.
5. Configure Git identity if needed:

   ```powershell
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```

6. Click **Push to Git** next to Anki's **Sync** button (or use the Tools menu). Read the confirmation: **Anki will close** to make the backup, then optionally reopen.

GitBridge uses the configured **remote and branch**, not a hardcoded GitHub account or repository. It never creates a public repository for you. If `origin` in the local backup differs, GitBridge requests approval before changing it; URLs that differ only by recognized formatting do not prompt.

## Restoring

From **Tools → GitBridge**:

- **Download backup from Git**: clones the configured branch into a newly named directory without changing the current Anki2.
- **Restore full backup**: **replaces the entire Anki2**, including all profiles, collections, history, media, other add-ons, and settings. Requires an explicit warning plus a randomly generated confirmation code. GitBridge first downloads and validates the data, then waits for Anki to close. The currently installed GitBridge version/settings are retained.

Your previous Anki2 is preserved as a directory like `Anki2-antes-da-restauracao-YYYYMMDD-HHMMSS-XXXXXXXX` next to your Anki2 folder. The restore UI shows the exact path. **GitBridge never deletes this previous installation.** Delete it manually only after verifying the restore and keeping another independent backup.

If a normal filesystem error happens during replacement, GitBridge attempts to copy the previous data back. Force-ending the process or losing power during directory replacement may require **manual recovery** from the preserved directory. Test restores with disposable Anki data before trusting them in production.

## Backup details and limitations

- The original Anki2 is read-only during a backup. Git operations and file deletion occur only inside the separate backup copy.
- A manual backup is performed **after Anki exits**. It is not triggered by the AnkiWeb Sync button.
- AnkiWeb collection and media synchronization blocks manual Push and Restore.
- GitBridge uses `git add -A`, commits changes when necessary, and pushes `HEAD` to the configured branch; a divergent remote produces an error. It never forces a push or pulls changes automatically.
- The default file-size preflight limit is 95 MiB (configurable, 0 disables checking). **GitHub rejects ordinary Git objects above 100 MiB**, including objects already present in Git history; consider Git LFS where appropriate.
- Git can fail because of permissions, antivirus locks, authentication, network problems, or server limits. Logs for external operations are in `%LOCALAPPDATA%\AnkiGitHubBackup\logs` and available from **Tools → GitBridge → Backup history**.
- Do not store access tokens in Git URLs; use SSH or the platform's credential manager.
- GitBridge does not provide encryption. A private Git remote is an access restriction, **not encryption**.
- macOS and Linux external-worker support is not implemented in this release.

## Build on Windows

Install Python **3.12** for Windows and use PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1
```

The script creates a local `.venv`, installs pinned PyInstaller/PyQt6 dependencies, builds the standalone `backup_gui.exe` in **onedir** mode, and packages **GitBridge.ankiaddon** (plus a `.sha256` checksum). This binary must be built on Windows; running PyInstaller on Linux does not produce the required Windows executable.

For automated builds, the `.github/workflows/build-windows.yml` workflow runs on GitHub-hosted Windows runners. Trigger it manually for artifacts, or push an intentional version tag such as `v1.0.0` to build and publish a GitHub Release. **Do not publish a release until you have tested that exact compiled artifact.**

## Publishing and signing

- See [RELEASE_NOTES.md](RELEASE_NOTES.md) for release notes and [PUBLISHING.md](PUBLISHING.md) for the GitHub/AnkiWeb checklist.
- See [SIGNING.md](SIGNING.md) for Windows code-signing choices and known constraints.

## Português

**GitBridge faz backup da pasta Anki2 inteira para o repositório Git configurado por cada usuário**, incluindo perfis, bancos, mídias, extensões e configurações. Ao clicar em **Push to Git**, o Anki fecha normalmente, o backup incremental é enviado ao Git e o Anki pode ser reaberto. A restauração completa substitui todos os dados, mas preserva uma cópia da pasta Anki2 anterior e mantém a versão atual do GitBridge. **Use sempre um repositório privado.** Os menus têm tradução PT-BR, e o inglês é o idioma padrão. Consulte as seções acima para as instruções detalhadas.
