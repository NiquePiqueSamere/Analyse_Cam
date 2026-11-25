#!/bin/bash
# LAUNCHER_VERSION=2
# Lanceur macOS - Analyse Camera V5.4
# Double-cliquer sur ce fichier pour lancer l'application.

set -u

pause() { printf "\nAppuyez sur Entree pour fermer cette fenetre..."; read -r _; }
log_msg() { printf "%s\n" "$*" | tee -a "$LOG"; }

main() {
    SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    HERE="$(dirname "$SELF")"
    cd "$HERE" || return 1
    LOG="$HERE/lancement.log"
    : >> "$LOG"
    echo "==== $(date) ====" >> "$LOG"

    # Retire la quarantaine du dossier de l'application et rend les lanceurs exécutables.
    chmod +x "$HERE"/*.command "$HERE"/*xif*ool*/exiftool 2>/dev/null || true
    xattr -dr com.apple.quarantine "$HERE" "$HERE/.." 2>/dev/null || true

    echo "========================================================"
    echo "  ANALYSE CAMERA - VERIFICATION DE L'ENVIRONNEMENT"
    echo "========================================================"
    echo

    # Homebrew est facultatif : on l'ajoute au PATH s'il est déjà présent.
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
    done

    # 1. Python avec Tkinter (Python.org recommandé ; on ignore le Python Apple).
    PY="${ANALYSE_CAMERA_PYTHON:-}"
    if [ -n "$PY" ] && ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
        echo "[!] ANALYSE_CAMERA_PYTHON est défini mais Tkinter ne fonctionne pas avec ce Python."
        PY=""
    fi
    if [ -z "$PY" ]; then
        for c in python3.13 python3.12 python3.14 python3.11 python3.10 python3.9 \
                 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
                 /usr/local/bin/python3 python3; do
            p="$(command -v "$c" 2>/dev/null)" || continue
            [ "$p" = "/usr/bin/python3" ] && continue
            if "$p" -c "import tkinter" >/dev/null 2>&1; then PY="$p"; break; fi
        done
    fi

    if [ -z "$PY" ]; then
        echo "[!] Aucun Python compatible avec Tkinter n'a ete trouve."
        echo "[*] Telechargement de l'installateur officiel Python 3.13.17..."
        TMP_PY="$(mktemp -d "${TMPDIR:-/tmp}/analyse-camera-python.XXXXXX")" || return 1
        PKG="$TMP_PY/python-3.13.17-macos11.pkg"
        if ! curl -fL --connect-timeout 15 --max-time 600 \
            "https://www.python.org/ftp/python/3.13.17/python-3.13.17-macos11.pkg" -o "$PKG"; then
            echo "[ERREUR] Impossible de telecharger Python depuis python.org."
            echo "Installez Python 3.13 depuis https://www.python.org/downloads/macos/ puis relancez ce fichier."
            open "https://www.python.org/downloads/macos/" 2>/dev/null || true
            pause
            return 1
        fi
        echo
        echo "[OK] Installateur telecharge : $PKG"
        echo "[ACTION REQUISE] L'installateur macOS va s'ouvrir."
        echo "Suivez les etapes et autorisez l'installation (droits administrateur necessaires)."
        echo "Une fois l'installation terminee, relancez Lancer_Analyse_Camera_V5.command."
        open "$PKG"
        return 0
    fi

    log_msg "[OK] Python detecte : $PY"
    "$PY" --version 2>&1 | tee -a "$LOG"

    # 2. Environnement virtuel propre. On le reconstruit s'il est incomplet/corrompu.
    VENV="$HERE/.venv"
    VENV_OK=0
    if [ -x "$VENV/bin/python" ] &&
       "$VENV/bin/python" -c "import sys, pip; assert sys.version_info >= (3,9)" >/dev/null 2>&1; then
        VENV_OK=1
    fi
    if [ "$VENV_OK" = 0 ]; then
        echo "[*] Creation/reparation de l'environnement Python (.venv)..."
        rm -rf "$VENV"
        if ! "$PY" -m venv "$VENV"; then
            echo "[ERREUR] Impossible de creer l'environnement Python."
            echo "Verifiez l'installation de Python et ses composants."
            pause
            return 1
        fi
    fi
    PYV="$VENV/bin/python"

    # Mise a jour de pip dans le venv, sans toucher au Python systeme.
    if ! "$PYV" -m pip --version >/dev/null 2>&1; then
        echo "[*] Reparation de pip..."
        "$PYV" -m ensurepip --upgrade >> "$LOG" 2>&1 || true
    fi
    if ! "$PYV" -m pip --version >/dev/null 2>&1; then
        echo "[ERREUR] pip ne fonctionne pas dans .venv. Suppression du venv et relancez."
        rm -rf "$VENV"
        pause
        return 1
    fi

    # 3. Mise a jour de l'application (facultative si GitHub est inaccessible).
    if [ -f "$HERE/updater.py" ]; then
        "$PYV" "$HERE/updater.py" >> "$LOG" 2>&1
        echo "updater code=$?" >> "$LOG"
    fi

    # Applique un nouveau lanceur s'il a ete fourni par le mecanisme de mise a jour.
    if [ -f "$SELF.new" ]; then
        echo "[*] Nouveau lanceur recu : application et relance..."
        cp "$SELF" "$SELF.bak"
        if cat "$SELF.new" > "$SELF"; then
            rm -f "$SELF.new"
            chmod +x "$SELF"
            export LAUNCHER_JUST_UPDATED=1
            exec "$SELF" "$@"
        fi
        echo "[!] Impossible d'appliquer le nouveau lanceur ; poursuite avec l'ancien."
    fi

    # 4. Dependances Python. Elles sont facultatives pour certaines fonctions,
    # mais les echecs sont maintenant explicites et inscrits dans lancement.log.
    echo "[*] Verification des modules Python..."
    if ! "$PYV" -c "import tkinterdnd2" >/dev/null 2>&1; then
        echo "[*] Installation de tkinterdnd2 (glisser-deposer)..."
        if ! "$PYV" -m pip install --disable-pip-version-check tkinterdnd2 >> "$LOG" 2>&1; then
            echo "[AVERTISSEMENT] tkinterdnd2 n'a pas pu etre installe : le glisser-deposer sera desactive."
        fi
    fi
    if ! "$PYV" -c "import pymediainfo" >/dev/null 2>&1; then
        echo "[*] Installation de pymediainfo (informations MediaInfo)..."
        if ! "$PYV" -m pip install --disable-pip-version-check pymediainfo >> "$LOG" 2>&1; then
            echo "[AVERTISSEMENT] pymediainfo n'a pas pu etre installe : certaines donnees MediaInfo seront absentes."
        fi
    fi
    echo "[OK] Verification des modules terminee."

    # 5. ExifTool : recherche locale/système, puis téléchargement automatique.
    ET=""
    [ -x "$HERE/exiftool" ] && ET="$HERE/exiftool"
    if [ -z "$ET" ]; then
        for f in "$HERE"/*xif*ool*/exiftool; do
            if [ -x "$f" ]; then ET="$f"; break; fi
        done
    fi
    if [ -z "$ET" ]; then
        ET="$(command -v exiftool 2>/dev/null || true)"
    fi

    if [ -z "$ET" ]; then
        if ! command -v perl >/dev/null 2>&1; then
            echo "[ERREUR] Perl est absent. ExifTool ne peut pas fonctionner sans Perl."
            echo "Installez le paquet macOS officiel depuis https://exiftool.org/ (droits administrateur possibles), puis relancez."
            open "https://exiftool.org/" 2>/dev/null || true
            pause
            return 1
        fi

        echo "[*] ExifTool absent : telechargement de la distribution officielle..."
        EXIF_VERSION="$(curl -fsSL --connect-timeout 10 --max-time 30 https://exiftool.org/ver.txt 2>/dev/null | tr -d '[:space:]' || true)"
        if ! printf '%s' "$EXIF_VERSION" | grep -Eq '^[0-9]+(\.[0-9]+)+$'; then
            # Version de secours si le fichier ver.txt est temporairement inaccessible.
            EXIF_VERSION="13.59"
        fi
        ARCHIVE="Image-ExifTool-${EXIF_VERSION}.tar.gz"
        TMP_ET="$HERE/.exiftool_download"
        rm -rf "$TMP_ET"
        mkdir -p "$TMP_ET"
        if ! curl -fL --connect-timeout 15 --max-time 300 \
            "https://exiftool.org/${ARCHIVE}" -o "$TMP_ET/$ARCHIVE"; then
            echo "[ERREUR] Telechargement d'ExifTool impossible."
            echo "Verifiez votre connexion Internet ou installez ExifTool depuis https://exiftool.org/."
            open "https://exiftool.org/" 2>/dev/null || true
            rm -rf "$TMP_ET"
            pause
            return 1
        fi
        if ! tar -xzf "$TMP_ET/$ARCHIVE" -C "$TMP_ET"; then
            echo "[ERREUR] Archive ExifTool invalide ou incomplete."
            rm -rf "$TMP_ET"
            pause
            return 1
        fi
        EXTRACTED="$TMP_ET/Image-ExifTool-${EXIF_VERSION}"
        if [ ! -f "$EXTRACTED/exiftool" ] || [ ! -d "$EXTRACTED/lib" ]; then
            echo "[ERREUR] Le contenu de l'archive ExifTool est incomplet."
            rm -rf "$TMP_ET"
            pause
            return 1
        fi
        chmod +x "$EXTRACTED/exiftool"
        rm -rf "$HERE/Image-ExifTool-${EXIF_VERSION}"
        mv "$EXTRACTED" "$HERE/Image-ExifTool-${EXIF_VERSION}"
        rm -rf "$TMP_ET"
        ET="$HERE/Image-ExifTool-${EXIF_VERSION}/exiftool"
        echo "[OK] ExifTool ${EXIF_VERSION} installe localement dans le dossier de l'application."
    fi

    # Test réel avant de lancer l'interface.
    if [ ! -x "$ET" ] || ! "$ET" -ver >/dev/null 2>&1; then
        echo "[ERREUR] ExifTool est present mais ne peut pas etre execute."
        echo "Consultez lancement.log ; verifiez notamment la presence de Perl."
        pause
        return 1
    fi
    log_msg "[OK] ExifTool : $ET ($("$ET" -ver 2>/dev/null))"

    # 6. Lancement de l'application.
    if [ ! -f "$HERE/Analyse_Camera_V5.py" ]; then
        echo "[ERREUR] Analyse_Camera_V5.py introuvable dans : $HERE"
        pause
        return 1
    fi
    echo
    echo "[OK] Environnement pret. Lancement de Analyse Camera..."
    "$PYV" "$HERE/Analyse_Camera_V5.py" "$@"
    RC=$?
    echo "outil termine code=$RC" >> "$LOG"
    if [ "$RC" -ne 0 ]; then
        echo
        echo "[ERREUR] Le programme s'est ferme avec une erreur (code $RC)."
        echo "Consultez le fichier lancement.log dans le dossier bin."
        pause
    fi
    return "$RC"
}

main "$@"
exit $?
