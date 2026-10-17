import sys
import py_compile
import ast
from pathlib import Path

# Fix console printing encoding on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def check_syntax():
    base_dir = Path(__file__).resolve().parent.parent
    
    # Các file cần kiểm tra
    files_to_check = []
    
    # Thêm các file ở thư mục gốc
    for f in ["server.py", "start_app.py"]:
        p = base_dir / f
        if p.exists():
            files_to_check.append(p)
            
    # Thêm toàn bộ các file Python trong thư mục core/
    core_dir = base_dir / "core"
    if core_dir.exists():
        for p in core_dir.glob("*.py"):
            files_to_check.append(p)

    has_errors = False
    
    print("=== Nambeo Pro: Trình kiểm tra cú pháp (Syntax Linter) ===")
    
    for filepath in files_to_check:
        rel_path = filepath.relative_to(base_dir)
        try:
            # Phân tích chuỗi AST để bắt chính xác IndentationError và SyntaxError
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            ast.parse(source, filename=str(filepath))
            
            # Củng cố thêm bằng py_compile
            py_compile.compile(str(filepath), doraise=True)
            
            print(f"[OK]   {rel_path}")
        except IndentationError as e:
            has_errors = True
            print(f"[FAIL] {rel_path} - Lỗi thụt lề (IndentationError) tại dòng {e.lineno}")
            print(f"       Chi tiết: {e.msg}")
        except SyntaxError as e:
            has_errors = True
            print(f"[FAIL] {rel_path} - Lỗi cú pháp (SyntaxError) tại dòng {e.lineno}")
            print(f"       Chi tiết: {e.msg}")
        except Exception as e:
            has_errors = True
            print(f"[FAIL] {rel_path} - Lỗi không xác định: {e}")

    print("==========================================================")
    if has_errors:
        print("KẾT LUẬN: PHÁT HIỆN LỖI. Vui lòng sửa các lỗi trên trước khi tiếp tục.")
        sys.exit(1)
    else:
        print("KẾT LUẬN: CODE SẠCH. Không phát hiện lỗi cú pháp.")
        sys.exit(0)

if __name__ == "__main__":
    check_syntax()
