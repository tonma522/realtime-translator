@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"
python --version > nul 2>&1
if errorlevel 1 ( echo [エラー] Python が見つかりません。 & pause & exit /b 1 )
python -c "import pyaudiowpatch" > nul 2>&1
if errorlevel 1 ( echo 依存パッケージをインストールします... & pip install -r requirements.txt )
python -c "import google.genai" > nul 2>&1
if errorlevel 1 ( pip install -r requirements.txt )
python translator.py
if errorlevel 1 ( echo [エラー] アプリがエラーで終了しました。 & pause )
