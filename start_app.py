import os
import sys
import time
import webbrowser
import threading
import subprocess
import uvicorn

# Force UTF-8 on Windows console
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure working directory is project dir and in sys.path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

def free_port(port: int):
    try:
        out = subprocess.check_output(f"netstat -ano | findstr :{port}", shell=True).decode()
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 5 and "LISTENING" in line:
                pid = parts[-1]
                if pid != str(os.getpid()):
                    print(f"[*] Đang giải phóng cổng {port} từ tiến trình cũ (PID: {pid})...")
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
                    time.sleep(0.5)
    except Exception:
        pass

def open_browser():
    time.sleep(1.5)
    print("\n[+] Đang mở giao diện trên trình duyệt web...")
    webbrowser.open("http://127.0.0.1:8000")

if __name__ == "__main__":
    free_port(8000)

    print("=" * 60)
    print("   DOUYIN & FACEBOOK VIDEO DOWNLOADER MVP - WEB UI")
    print("=" * 60)
    print(f"[*] Thư mục dự án: {PROJECT_DIR}")
    print("[*] Khởi động máy chủ cục bộ tại: http://127.0.0.1:8000")
    print("[*] Nhấn Ctrl+C trong cửa sổ này để tắt công cụ.")
    print("=" * 60)

    threading.Thread(target=open_browser, daemon=True).start()
    
    from server import app

    # Run uvicorn cleanly in single process mode (prevents zombie workers on Windows)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")



