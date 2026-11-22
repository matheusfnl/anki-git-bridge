# GitBridge 1.0.0 — First stable release

GitBridge makes a complete, incremental Git backup of your local Anki2 directory and can restore it from a configured Git remote.

## Highlights

- Manual **Push to Git** toolbar and Tools-menu actions.
- Optional backup when Anki closes; optional restart after manual backup.
- Separate Git working directory: never initialize Git inside the live Anki2 folder.
- Explicit Git remote and branch settings; GitHub, GitLab, and other Git servers supported.
- Backup history, progress messages, and errors.
- Full restore from a Git branch with a two-step confirmation, previous Anki2 preservation, and current GitBridge retention.
- Disable backup and restore while AnkiWeb collection/media sync is running.
- English default and Portuguese (Brazil) interface options.

## Requirements

Windows desktop Anki, Git for Windows, and authentication for a Git repository **configured by the user**. Python is not required on the end user's PC.

## Important notices

1. A full restore replaces **all** users, profiles, collections, media, settings, and add-ons except the currently installed GitBridge. The previous Anki2 directory is preserved separately.
2. Keep your Git repository **private**: Anki2 can contain sensitive account information.
3. This release's separate Windows GUI binary is **unsigned** unless an independently signed artifact is provided. Windows Smart App Control may prevent execution. Do not disable Windows protections to work around this.
4. Always keep an independent copy of your Anki data, particularly before testing full restores.
5. No automatic Git pull, merge, history rewrite, or force-push is performed.

**Download:** `GitBridge.ankiaddon` from the release assets, not GitHub's automatic source-code ZIP.
