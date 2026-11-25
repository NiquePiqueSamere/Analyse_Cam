#!/bin/bash
# Lanceur public macOS - Analyse Camera
# Double-cliquer uniquement sur ce fichier, a la racine du dossier.

set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
INTERNAL="$ROOT/bin/Lancer_Analyse_Camera_V5.command"

if [ ! -f "$INTERNAL" ]; then
    echo "[ERREUR] Le lanceur interne est introuvable :"
    echo "$INTERNAL"
    echo
    echo "Verifiez que le dossier bin est present et que l'archive a ete extraite completement."
    printf "\nAppuyez sur Entree pour fermer..."
    read -r _
    exit 1
fi

chmod +x "$INTERNAL" 2>/dev/null || true
exec "$INTERNAL" "$@"
