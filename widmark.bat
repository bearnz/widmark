@echo off
rem Double-click to open Widmark. uvw runs it without a console window.
cd /d "%~dp0"
start "" uvw run app.py
