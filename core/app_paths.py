"""Read-only application assets and per-user writable data paths."""
import os
from pathlib import Path
import sys


FROZEN = bool(getattr(sys, 'frozen', False))
RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])).resolve()


def _videos_folder() -> Path:
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes
            from uuid import UUID

            class GUID(ctypes.Structure):
                _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                            ('Data3', wintypes.WORD), ('Data4', ctypes.c_byte * 8)]

            raw = UUID('18989b1d-99b5-455b-841c-ab7c74e4ddfc').bytes_le
            guid = GUID.from_buffer_copy(raw)
            target = ctypes.c_wchar_p()
            shell = ctypes.windll.shell32
            if shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(target)) == 0:
                try:
                    return Path(target.value)
                finally:
                    free = ctypes.windll.ole32.CoTaskMemFree
                    free.argtypes = [ctypes.c_void_p]
                    free(ctypes.cast(target, ctypes.c_void_p))
        except (AttributeError, OSError, ValueError):
            pass
    return Path.home() / 'Videos'


if FROZEN:
    DATA_ROOT = Path(os.environ.get('NAMBEO_DATA_DIR') or
                     (Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'Nambeo')).resolve()
    DEFAULT_VIDEO_ROOT = _videos_folder() / 'Nambeo'
    try:
        DEFAULT_VIDEO_ROOT.mkdir(parents=True, exist_ok=True)
    except OSError:
        DEFAULT_VIDEO_ROOT = DATA_ROOT / 'videos'
else:
    # Preserve existing source-checkout behavior and user files.
    DATA_ROOT = RESOURCE_ROOT
    DEFAULT_VIDEO_ROOT = RESOURCE_ROOT / 'downloads'
