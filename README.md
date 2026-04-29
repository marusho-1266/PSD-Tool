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

## 仕様

`docs/requirements-spec.md` を参照してください。
