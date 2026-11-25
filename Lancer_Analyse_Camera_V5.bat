@echo off
rem LAUNCHER_VERSION=2
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Verification et Lancement - Analyse Camera

echo ========================================================
echo   VERIFICATION DE L'ENVIRONNEMENT (ANALYSE CAMERA)
echo ========================================================
echo.

rem --- 1. Python AVEC tkinter (ignore ceux de Nuke, Maya, etc.) ---
set "PY_CMD="
for %%V in (3.13 3.12 3.11 3.10 3.9) do (
    if not defined PY_CMD (
        py -%%V -c "import tkinter" >nul 2>&1 && set "PY_CMD=py -%%V"
    )
)
if not defined PY_CMD (
    python -c "import tkinter" >nul 2>&1 && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [!] Aucun Python avec tkinter trouve.
    echo     Le "python" du PATH est ignore s'il vient d'un logiciel ^(Nuke...^).
    echo     Installation de Python 3.12 via winget...
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    echo.
    echo Fermez cette fenetre puis relancez le lanceur ^(le PATH doit etre recharge^).
    pause
    exit /b 1
)
echo [OK] Python detecte : %PY_CMD%

rem --- 2. Mise a jour (lanceur d'abord, puis outil) depuis GitHub ---
if exist "%~dp0updater.py" (
    %PY_CMD% "%~dp0updater.py"
    if errorlevel 10 exit /b 0
)

rem --- 3. Modules Python ---
%PY_CMD% -c "import tkinterdnd2" >nul 2>&1 || %PY_CMD% -m pip install tkinterdnd2
%PY_CMD% -c "import pymediainfo" >nul 2>&1 || %PY_CMD% -m pip install pymediainfo
echo [OK] Modules Python prets.

rem --- 4. ExifTool ---
set "ET_FOUND=0"
if exist "%~dp0exiftool.exe" set "ET_FOUND=1"
where exiftool >nul 2>&1 && set "ET_FOUND=1"
if "!ET_FOUND!"=="0" (
    echo [!] ExifTool introuvable. Installation via winget...
    winget install -e --id OliverBetz.ExifTool --accept-package-agreements --accept-source-agreements
)
echo [OK] Verification ExifTool terminee.

echo.
echo Lancement de Analyse_Camera_V5.py...
set "SCRIPT=%~dp0Analyse_Camera_V5.py"

if not exist "%SCRIPT%" (
    echo [ERREUR] Analyse_Camera_V5.py introuvable dans : %~dp0
    pause
    exit /b 1
)

%PY_CMD% "%SCRIPT%" %*
if errorlevel 1 (
    echo.
    echo [ERREUR] Le programme s'est ferme avec une erreur.
    pause
)
endlocal
