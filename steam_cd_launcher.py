import ctypes
import json
import os
import re
import subprocess
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


def get_steam_path() -> Path | None:
    # 1) Steam install env var (if present)
    env_path = os.environ.get("STEAM_PATH")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    # 2) Common default locations
    candidates = [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ]

    for path in candidates:
        if path.exists():
            return path

    # 3) Registry lookup
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
            steam_path = Path(value)
            if steam_path.exists():
                return steam_path
    except Exception:
        pass

    return None


def parse_libraryfolders(library_file: Path) -> list[Path]:
    libraries = []
    if not library_file.exists():
        return libraries

    content = library_file.read_text(encoding="utf-8", errors="ignore")

    # Works with old/new VDF style where path appears as: "path" "D:\\SteamLibrary"
    for match in re.finditer(r'"path"\s+"([^"]+)"', content):
        raw = match.group(1)
        normalized = raw.replace("\\\\", "\\")
        path = Path(normalized)
        if path.exists():
            libraries.append(path)

    return libraries


def get_steam_libraries() -> list[Path]:
    steam_path = get_steam_path()
    if not steam_path:
        return []

    libraries = [steam_path]
    library_file = steam_path / "steamapps" / "libraryfolders.vdf"
    for lib in parse_libraryfolders(library_file):
        if lib not in libraries:
            libraries.append(lib)

    return libraries


def parse_manifest(manifest_file: Path) -> tuple[str, str] | None:
    try:
        text = manifest_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    appid_match = re.search(r'"appid"\s+"(\d+)"', text)
    name_match = re.search(r'"name"\s+"([^"]+)"', text)
    if not appid_match or not name_match:
        return None

    return appid_match.group(1), name_match.group(1)


def get_installed_games() -> list[dict]:
    games = []
    for library in get_steam_libraries():
        steamapps = library / "steamapps"
        if not steamapps.exists():
            continue

        for manifest in steamapps.glob("appmanifest_*.acf"):
            parsed = parse_manifest(manifest)
            if not parsed:
                continue
            appid, name = parsed
            games.append(
                {
                    "appid": appid,
                    "name": name,
                    "library": str(library),
                }
            )

    games.sort(key=lambda g: g["name"].lower())
    return games


def get_drive_type(path: str) -> int:
    # DRIVE_CDROM = 5
    return ctypes.windll.kernel32.GetDriveTypeW(path)


def get_optical_drives() -> list[str]:
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if bitmask & (1 << i):
            letter = f"{chr(ord('A') + i)}:\\"
            if get_drive_type(letter) == 5:
                drives.append(letter)
    return drives


def run_powershell(script: str, args: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    if args:
        cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def query_recorders() -> list[dict]:
    script = r"""
$ErrorActionPreference = 'Stop'
$master = New-Object -ComObject IMAPI2.MsftDiscMaster2
$result = @()
foreach ($id in $master) {
  $rec = New-Object -ComObject IMAPI2.MsftDiscRecorder2
  $rec.InitializeDiscRecorder($id)
  $vols = @($rec.VolumePathNames)
  $result += [pscustomobject]@{
    Id = $id
    ProductId = $rec.ProductId
    VendorId = $rec.VendorId
    Volumes = $vols
  }
}
$result | ConvertTo-Json -Depth 4
""".strip()

    proc = run_powershell(script)
    if proc.returncode != 0 or not proc.stdout.strip():
        return []

    try:
        parsed = json.loads(proc.stdout)
        if isinstance(parsed, dict):
            return [parsed]
        return parsed
    except json.JSONDecodeError:
        return []


def build_disc_payload(staging_dir: Path, appid: str, game_name: str):
    autorun_inf = staging_dir / "autorun.inf"
    launcher_bat = staging_dir / "SteamDiscLauncher.bat"
    readme = staging_dir / "README.txt"
    marker_json = staging_dir / "steam_disc_payload.json"

    autorun_inf.write_text(
        "[autorun]\n"
        "open=SteamDiscLauncher.bat\n"
        "action=Launch Steam Game\n"
        "label=Steam Quick Launch Disc\n",
        encoding="utf-8",
    )

    launcher_bat.write_text(
        "@echo off\n"
        "start \"\" \"steam://rungameid/{appid}\"\n".format(appid=appid),
        encoding="utf-8",
    )

    readme.write_text(
        f"This disc launches: {game_name} (AppID {appid})\n"
        "\n"
        "If the game does not auto-launch when inserted, open this disc and run SteamDiscLauncher.bat manually.\n"
        "Steam must be installed and the game must already be installed on this PC.\n",
        encoding="utf-8",
    )

    marker_json.write_text(
        json.dumps(
            {
                "format": "steam-physical-launcher",
                "version": 1,
                "appid": appid,
                "game_name": game_name,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def burn_with_imapi(staging_dir: Path, drive: str, disc_title: str) -> tuple[bool, str]:
    # PowerShell script that burns the staging folder to the selected optical drive using IMAPI2 COM.
    script = r"""
param(
    [string]$StagePath,
    [string]$Drive,
    [string]$DiscTitle
)

$ErrorActionPreference = 'Stop'

$master = New-Object -ComObject IMAPI2.MsftDiscMaster2
$recorder = $null

foreach ($id in $master) {
    $r = New-Object -ComObject IMAPI2.MsftDiscRecorder2
    $r.InitializeDiscRecorder($id)
    $vols = @($r.VolumePathNames)
    if ($vols -contains $Drive) {
        $recorder = $r
        break
    }
}

if ($null -eq $recorder) {
    throw "Could not find recorder for drive $Drive"
}

$discFormat = New-Object -ComObject IMAPI2.MsftDiscFormat2Data
$discFormat.Recorder = $recorder
$discFormat.ClientName = 'Steam Disc Launcher'

if (-not $discFormat.IsCurrentMediaSupported($recorder)) {
    throw 'Inserted media is not supported by this recorder.'
}

$fs = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
$fs.ChooseImageDefaultsForMediaType($discFormat.CurrentPhysicalMediaType)
$fs.FileSystemsToCreate = 4 # ISO9660
$fs.VolumeName = $DiscTitle
$root = $fs.Root

Get-ChildItem -LiteralPath $StagePath -File | ForEach-Object {
    $root.AddFile($_.Name, $_.FullName)
}

$resultImage = $fs.CreateResultImage()
$discFormat.Write($resultImage.ImageStream)
Write-Output 'Burn complete.'
""".strip()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8") as ps1:
        ps1.write(script)
        script_path = ps1.name

    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_path,
                "-StagePath",
                str(staging_dir),
                "-Drive",
                drive,
                "-DiscTitle",
                disc_title,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return True, proc.stdout.strip() or "Burn complete."
        return False, (proc.stderr.strip() or proc.stdout.strip() or "Burn failed.")
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Steam Physical Games")
        self.geometry("860x560")

        self.games: list[dict] = []
        self.filtered_games: list[dict] = []
        self.recorders: list[dict] = []

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.filter_games())

        self.drive_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready.")

        self._build_ui()
        self.refresh_all()

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Search installed Steam games:").pack(anchor="w")
        ttk.Entry(top, textvariable=self.search_var).pack(fill="x", pady=(2, 8))

        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        self.listbox = tk.Listbox(main)
        self.listbox.pack(fill="both", expand=True, side="left")

        scroll = ttk.Scrollbar(main, orient="vertical", command=self.listbox.yview)
        scroll.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scroll.set)

        controls = ttk.Frame(self, padding=10)
        controls.pack(fill="x")

        ttk.Label(controls, text="CD/DVD drive:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.drive_combo = ttk.Combobox(controls, textvariable=self.drive_var, state="readonly", width=30)
        self.drive_combo.grid(row=0, column=1, sticky="w")

        ttk.Button(controls, text="Refresh", command=self.refresh_all).grid(row=0, column=2, padx=8)
        ttk.Button(controls, text="Burn launch disc", command=self.burn_selected).grid(row=0, column=3, padx=8)

        ttk.Label(
            self,
            text=(
                "Note: Modern Windows often blocks true autorun from optical media for security. "
                "If auto-launch does not occur, open the disc and run SteamDiscLauncher.bat."
            ),
            wraplength=830,
            foreground="#555",
            padding=(10, 0),
        ).pack(anchor="w")

        status = ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=6)
        status.pack(fill="x", side="bottom")

    def refresh_all(self):
        self.status_var.set("Scanning Steam libraries and optical drives...")
        self.update_idletasks()

        self.games = get_installed_games()
        self.filter_games()

        self.recorders = query_recorders()
        self.populate_drives()

        self.status_var.set(f"Found {len(self.games)} games and {len(self.recorders)} burner device(s).")

    def filter_games(self):
        query = self.search_var.get().strip().lower()
        if not query:
            self.filtered_games = self.games[:]
        else:
            self.filtered_games = [
                g for g in self.games if query in g["name"].lower() or query in g["appid"]
            ]

        self.listbox.delete(0, tk.END)
        for game in self.filtered_games:
            self.listbox.insert(tk.END, f"{game['name']}  (AppID: {game['appid']})")

    def populate_drives(self):
        drive_map = []
        for rec in self.recorders:
            vols = rec.get("Volumes") or []
            vol_text = ", ".join(vols) if vols else "(no volume path)"
            label = f"{vol_text} - {rec.get('VendorId', '')} {rec.get('ProductId', '')}".strip()
            if vols:
                drive_map.append((label, vols[0]))

        if not drive_map:
            # Fallback: show detected optical drives even if recorder metadata failed.
            for drive in get_optical_drives():
                drive_map.append((f"{drive} - Optical drive", drive))

        self.drive_lookup = {label: drive for label, drive in drive_map}
        self.drive_combo["values"] = list(self.drive_lookup.keys())
        if drive_map:
            self.drive_combo.current(0)
            self.drive_var.set(drive_map[0][0])
        else:
            self.drive_var.set("")

    def burn_selected(self):
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showwarning("No game selected", "Select a Steam game first.")
            return

        drive_label = self.drive_var.get()
        if not drive_label or drive_label not in self.drive_lookup:
            messagebox.showwarning("No burner selected", "Select a writable CD/DVD drive.")
            return

        game = self.filtered_games[selection[0]]
        drive = self.drive_lookup[drive_label]

        confirm = messagebox.askyesno(
            "Confirm burn",
            f"Burn launch disc for:\n\n{game['name']} (AppID {game['appid']})\n\nTarget drive: {drive}\n\nInsert a blank writable disc before continuing.",
        )
        if not confirm:
            return

        self.status_var.set(f"Preparing launch payload for {game['name']}...")
        self.update_idletasks()

        with tempfile.TemporaryDirectory() as temp_dir:
            staging_dir = Path(temp_dir)
            build_disc_payload(staging_dir, game["appid"], game["name"])

            self.status_var.set(f"Burning disc in {drive}...")
            self.update_idletasks()

            ok, msg = burn_with_imapi(staging_dir, drive, "STEAM_LAUNCH")
            if ok:
                self.status_var.set("Burn completed successfully.")
                messagebox.showinfo("Success", f"Launch disc burned successfully.\n\n{msg}")
            else:
                self.status_var.set("Burn failed.")
                messagebox.showerror("Burn failed", msg)


if __name__ == "__main__":
    app = App()
    app.mainloop()
