"""生产入口: python -m app.main (backend 目录下) 或 uvicorn app.main:app。"""
from .api import create_app, run

app = create_app()

if __name__ == "__main__":
    run()
