# Steam Physical Launcher Disc

This Windows desktop app creates a **launcher-only** CD/DVD for your installed Steam games.

- It scans installed Steam libraries.
- Lets you search/select a game.
- Burns a small disc payload with:
  - `autorun.inf`
  - `SteamDiscLauncher.bat`
  - `README.txt`
- The disc does **not** contain game files. It only launches the game via Steam AppID.

## Requirements

- Windows
- Python 3.10+
- Steam installed
- Optical writer drive + blank writable disc


## Optional: auto-launch watcher at startup

This project includes a background watcher that checks optical drives on insert and runs launcher discs created by this app.

- It only runs discs that contain both:
  - `SteamDiscLauncher.bat`
  - `steam_disc_payload.json`
- It runs once per insert, then waits for eject/reinsert.

Install startup watcher (current user):

```powershell
powershell -ExecutionPolicy Bypass -File .\install_startup_watcher.ps1
```

Remove startup watcher:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_startup_watcher.ps1
```

## Important behavior note

Modern versions of Windows often block full autorun for removable media. If the game does not auto-launch when inserted:

1. Open the disc in File Explorer
2. Run `SteamDiscLauncher.bat`

Steam and the selected game must already be installed on that machine.
