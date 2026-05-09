# PSDリサイズ出力ツール

印刷所向けの PSD テンプレートのキャンバスに合わせ、入力画像を **contain**（内側に収める）配置し、白背景の**簡易 PSD**を書き出すデスクトップアプリです（Photoshop 不要）。

## 必要環境

- Windows 10/11
- Python 3.10 以上推奨

## セットアップ

### コマンドプロンプト（cmd.exe）

```bat
cd PSD-Tool
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

### PowerShell

```powershell
cd PSD-Tool
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Git Bash

`source .venv/Scripts/activate` のあと `sed: command not found` が出る場合、PATH に Git の `usr\bin`（`sed` がある）が通っていないことがあります。`(.venv)` が付いていれば仮想環境は有効なので、無視してよいことも多いです。回避策は **cmd / PowerShell** で有効化するか、次のように **activate なし**で実行します:

```bash
.venv/Scripts/pip install -r requirements.txt
PYTHONPATH=src .venv/Scripts/python -m psd_tool
```

## 起動

**cmd** では:

```bat
set PYTHONPATH=src
python -m psd_tool
```

**PowerShell** では: `$env:PYTHONPATH="src"; python -m psd_tool`

**Git Bash** では: `PYTHONPATH=src python -m psd_tool`（上記で仮想環境の `python` を使う）

## HEIC/HEIF（任意）

`pillow-heif` を入れると、HEIC を開けます。

```bash
pip install pillow-heif
```

## 配布用 exe のビルド（Windows）

[PyInstaller](https://pyinstaller.org/) で単一の `dist\PSDResizeTool.exe` を作成します（GUI・コンソールなし）。

1. 上記と同様に仮想環境を作り、`requirements.txt` をインストール済みであること。
2. ビルド用依存を追加: `pip install -r requirements-build.txt`
3. リポジトリルートで spec を実行:

```bat
pyinstaller --clean --noconfirm PSDTool.spec
```

またはスクリプトから一括（`.venv` が既にある前提）:

- **cmd**: `scripts\build_exe.bat`
- **PowerShell**: `.\scripts\build_exe.ps1`

成果物は `dist\PSDResizeTool.exe` です。初回起動時はウイルス対策ソフトのスキャンで数十秒かかることがあります。

### メモ

- エントリはルートの `run_exe_entry.py`（凍結時も `src` 配下の `psd_tool` をパスに含めて解析しています）。
- PySide6 は `PSDTool.spec` 内で `collect_all` によりバンドルしています。
- HEIC 対応を exe に含める場合は、ビルド環境に `pillow-heif` を入れたうえで再度ビルドしてください（オプション）。

## 仕様

`docs/requirements-spec.md` を参照してください。

## 個人開発ロードマップ

販売・拡張方針・連載計画・判断基準は `docs/個人開発ロードマップ.md` を参照してください。
