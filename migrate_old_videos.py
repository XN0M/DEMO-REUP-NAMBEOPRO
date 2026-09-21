import os
import json
import shutil
import re
from datetime import datetime

DOWNLOADS_DIR = "downloads"
COUNTERS_FILE = "counters.json"

def get_next_sequence(platform: str) -> int:
    counters = {}
    if os.path.exists(COUNTERS_FILE):
        try:
            with open(COUNTERS_FILE, "r", encoding="utf-8") as f:
                counters = json.load(f)
        except Exception:
            pass
    count = counters.get(platform, 0) + 1
    counters[platform] = count
    try:
        with open(COUNTERS_FILE, "w", encoding="utf-8") as f:
            json.dump(counters, f, indent=2)
    except Exception:
        pass
    return count

def main():
    if not os.path.exists(DOWNLOADS_DIR):
        print("No downloads directory found.")
        return

    # Find all flat files in downloads/
    flat_files = [f for f in os.listdir(DOWNLOADS_DIR) if os.path.isfile(os.path.join(DOWNLOADS_DIR, f))]
    
    derivative_suffixes = (
        ".dubbed.mp4",
        ".hardsub.vi.mp4",
        ".hardsub.bilingual.mp4",
        ".softsub.vi.mp4",
        ".softsub.bilingual.mp4",
        ".dubbed.hardsub.vi.mp4",
        ".dubbed.hardsub.bilingual.mp4",
        ".dubbed.softsub.vi.mp4",
        ".dubbed.softsub.bilingual.mp4",
        ".backup.mp4",
        ".final.mp4"
    )

    original_mp4s = []
    for f in flat_files:
        if f.endswith(".mp4") and not any(f.endswith(sfx) for sfx in derivative_suffixes) and not re.search(r'\.f\d+\.mp4$', f):
            original_mp4s.append(f)

    print(f"Found {len(original_mp4s)} legacy original videos to migrate.")

    for mp4_file in original_mp4s:
        base_name = mp4_file[:-4]
        json_path = os.path.join(DOWNLOADS_DIR, f"{base_name}.json")
        mp4_path = os.path.join(DOWNLOADS_DIR, mp4_file)

        platform = "douyin"
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as jf:
                    meta = json.load(jf)
                    platform = meta.get("platform", "douyin")
            except Exception:
                pass
        else:
            if "youtube" in mp4_file.lower() or "yt_" in mp4_file.lower():
                platform = "youtube"
            elif "facebook" in mp4_file.lower() or "fb_" in mp4_file.lower():
                platform = "facebook"

        platform = platform.lower()
        seq = get_next_sequence(platform)

        # Get file date (from mtime)
        try:
            mtime = os.stat(mp4_path).st_mtime
            date_str = datetime.fromtimestamp(mtime).strftime("%Y%m%d")
        except:
            date_str = datetime.now().strftime("%Y%m%d")

        folder_name = f"{platform}_{seq:03d}_{date_str}"
        target_dir = os.path.join(DOWNLOADS_DIR, "Chung", folder_name)
        os.makedirs(target_dir, exist_ok=True)

        # Find all files with the same base name
        moved_count = 0
        for f in flat_files:
            if f.startswith(base_name):
                src = os.path.join(DOWNLOADS_DIR, f)
                dst = os.path.join(target_dir, f)
                try:
                    shutil.move(src, dst)
                    moved_count += 1
                except Exception as e:
                    print(f"Failed to move {f}: {e}")
        
        print(f"Migrated {base_name} to {folder_name}/ ({moved_count} files)")

if __name__ == "__main__":
    main()
