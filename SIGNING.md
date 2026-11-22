# Windows code-signing decision — GitBridge 1.0.0

The Anki add-on archive contains a separate `backup_gui/backup_gui.exe` built with PyInstaller. AnkiWeb or GitHub hosting **does not automatically sign this executable**. A self-signed certificate generally will **not** satisfy Windows Smart App Control.

## Options

1. **SignPath Foundation — free for qualifying open-source projects.** Application and approval required. The project must meet the foundation's open-source and publishing rules. <https://signpath.org/>
2. **OV code-signing certificate — traditional CA.** Available internationally with identity verification and protected private-key storage. Cost varies; Microsoft cites approximately US$150–300/year. <https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options>
3. **Azure Artifact Signing (Public Trust).** Microsoft's recommended managed service, but its current individual-developer eligibility is restricted to the USA and Canada; individual developers based in Brazil do not currently qualify. <https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options>

## Signing a built executable

After obtaining a **CA-trusted** signing certificate and installing Windows SDK SignTool, sign the actual `dist/backup_gui/backup_gui.exe` **before packaging** the `.ankiaddon`:

```powershell
signtool sign /a /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "dist\backup_gui\backup_gui.exe"
signtool verify /pa /v "dist\backup_gui\backup_gui.exe"
```

`/a` chooses an available eligible certificate in the Windows certificate store; an HSM, cloud certificate, or signing service may require a different provider-specific command. Also review other distributed binaries (`.dll`, helpers), since Smart App Control evaluates loaded code.

**Never commit or share private signing keys, `.pfx` files, passwords, or access tokens.** Sign in an isolated, trusted build/release environment. Timestamp signatures. Verify signatures and full installer behavior on a separate Windows 11 test device.

## Release status

No production trusted code-signing identity has been supplied for GitBridge yet. The packaging script **does not claim or apply a trusted signature**. A future signed release requires author approval of the signing method, identity verification, and Windows verification. Do not disable Smart App Control to install an unsigned add-on.
