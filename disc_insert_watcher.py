import os
import subprocess
import time
from pathlib import Path


POLL_SECONDS = 2.0
LAUNCHER_NAME = "SteamDiscLauncher.bat"
MARKER_NAME = "steam_disc_payload.json"


def get_drive_type(path: str) -> int:
    # Windows API: DRIVE_CDROM = 5
    import ctypes

    return ctypes.windll.kernel32.GetDriveTypeW(path)


def list_optical_drives() -> list[str]:
    drives = []
    import ctypes

    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if bitmask & (1 << i):
            letter = f"{chr(ord('A') + i)}:\\"
            if get_drive_type(letter) == 5:
                drives.append(letter)
    return drives


def media_signature(drive: str) -> str:
    """
    Best-effort signature for inserted media so we only auto-run once per insert.
    Uses volume serial + launcher mtime if available.
    """
    try:
        import ctypes

        serial = ctypes.c_uint32()
        max_component = ctypes.c_uint32()
        fs_flags = ctypes.c_uint32()
        fs_name = ctypes.create_unicode_buffer(261)
        vol_name = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            drive,
            vol_name,
            261,
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(fs_flags),
            fs_name,
            261,
        )
        if ok:
            return f"{drive}|{serial.value}"
    except Exception:
        pass

    launcher = Path(drive) / LAUNCHER_NAME
    if launcher.exists():
        try:
            stat = launcher.stat()
            return f"{drive}|{int(stat.st_mtime)}|{stat.st_size}"
        except OSError:
            pass

    return f"{drive}|unknown"


def is_our_disc(drive: str) -> bool:
    root = Path(drive)
    launcher = root / LAUNCHER_NAME
    marker = root / MARKER_NAME
    return launcher.exists() and marker.exists()


def launch_disc(drive: str):
    launcher = Path(drive) / LAUNCHER_NAME
    # Use cmd /c start so the batch runs detached and watcher keeps running.
    subprocess.Popen(
        ["cmd", "/c", "start", "", str(launcher)],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


def main():
    seen: set[str] = set()

    while True:
        current = set()
        for drive in list_optical_drives():
            try:
                if not os.path.isdir(drive):
                    continue

                sig = media_signature(drive)
                current.add(sig)

                if sig in seen:
                    continue

                if is_our_disc(drive):
                    launch_disc(drive)
                    seen.add(sig)
            except Exception:
                # Keep watcher alive even if one drive errors.
                continue

        # Remove signatures for ejected media so reinserting can launch again.
        seen.intersection_update(current)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
