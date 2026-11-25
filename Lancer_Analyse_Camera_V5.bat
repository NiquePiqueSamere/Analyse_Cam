@echo off
rem LAUNCHER_VERSION=4
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Verification et Lancement - Analyse Camera
set "LOG=%~dp0lancement.log"
>> "%LOG%" echo ==== %date% %time% ====

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
>> "%LOG%" echo PY_CMD=%PY_CMD%

rem --- 2. Mise a jour depuis GitHub (l'outil, et le lanceur en .bat.new) ---
if exist "%~dp0updater.py" (
    %PY_CMD% "%~dp0updater.py"
    >> "%LOG%" echo updater errorlevel=!errorlevel!
)

rem --- 2b. Application d'un nouveau lanceur : sur une seule ligne logique, puis relance en direct ---
if exist "%~f0.new" (
    echo [*] Nouveau lanceur recu : application et relance...
    >> "%LOG%" echo application du nouveau lanceur
    copy /y "%~f0" "%~f0.bak" >nul
    copy /y "%~f0.new" "%~f0" >nul && (
        del "%~f0.new" >nul 2>&1
        set "LAUNCHER_JUST_UPDATED=1"
        call "%~f0" %*
        exit /b !errorlevel!
    )
    echo [!] Impossible d'appliquer le nouveau lanceur : on continue avec l'ancien.
)

rem --- 3. Modules Python ---
%PY_CMD% -c "import tkinterdnd2" >nul 2>&1 || %PY_CMD% -m pip install tkinterdnd2
%PY_CMD% -c "import pymediainfo" >nul 2>&1 || %PY_CMD% -m pip install pymediainfo
echo [OK] Modules Python prets.

rem --- 4. ExifTool ---
set "ET_FOUND=0"
if exist "%~dp0exiftool.exe" (
    if exist "%~dp0exiftool_files\" (
        set "ET_FOUND=1"
    ) else (
        for %%F in ("%~dp0exiftool.exe") do if %%~zF GTR 1000000 set "ET_FOUND=1"
        if "!ET_FOUND!"=="0" echo [!] exiftool.exe est seul, sans dossier exiftool_files : inutilisable, ignore.
    )
)
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
set "RC=!errorlevel!"
>> "%LOG%" echo outil termine code=!RC!
if not "!RC!"=="0" (
    echo.
    echo [ERREUR] Le programme s'est ferme avec une erreur.
    pause
) else (
    echo.
    echo [i] L'outil s'est ferme normalement ^(code 0^). Cette fenetre se ferme dans 10 s.
    timeout /t 10
)
endlocal
