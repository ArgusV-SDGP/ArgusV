@echo off
title FFmpeg Auto-Installer
echo ===================================================
echo      FFmpeg Auto-Installer for Windows
echo ===================================================
echo.
echo Downloading and installing FFmpeg via Winget...
echo This might take a minute or two. Please wait.
echo @author kaveendrankavee@gmail.com
echo.


winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements

echo.
echo ===================================================
echo Installation Complete!
echo ===================================================
echo.
echo IMPORTANT: If you have VS Code, Command Prompt, or
echo PowerShell currently open, you MUST restart them
echo before Python will recognize FFmpeg.
echo.
pause