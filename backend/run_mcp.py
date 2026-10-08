"""MCP 启动器(包外): claude mcp add taskmirror -s user -- cmd /c <python> D:\\projects\\taskmirror\\backend\\run_mcp.py
绝对路径 + 自设 sys.path, 任意 cwd 可跑; UTF-8 防 Windows GBK。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ["PYTHONUTF8"] = "1"
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except AttributeError:
    pass

from app.mcp_server import mcp

if __name__ == "__main__":
    mcp.run()
