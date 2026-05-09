"""PyInstaller 用エントリポイント（配布 exe ビルド）。開発時は `PYTHONPATH=src python -m psd_tool` を使ってください。"""
from psd_tool.app.main import run

if __name__ == "__main__":
    raise SystemExit(run())
