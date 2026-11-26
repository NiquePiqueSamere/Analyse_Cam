# -*- coding: utf-8 -*-
"""
Analyse Camera V5.8.0
Assistant d'identification caméra et profils couleur pour DaVinci Resolve.

Fonctions :
- ExifTool + MediaInfo + Sidecar XML Sony / Canon
- Moteur de profils multi-caméras extensible (Sony, Canon, ARRI, Apple, DJI,
  GoPro, Panasonic, Fujifilm, Blackmagic, RED, Android)
- Détection des incohérences colorimétriques multi-sources (dont HLG / BT.709)
- Décodage natif des métadonnées dynamiques ARRI MXF + navigation image / timecode
- Interface Tkinter, thèmes clair/sombre, code couleur des statuts
- Retour visuel sur le bouton Copier rapport ("Copié !")
- Export CSV récapitulatif et JSON des métadonnées
"""

import csv
import json
import os
import re
import struct
import subprocess
import threading
import queue
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from pymediainfo import MediaInfo
    HAS_PYMEDIAINFO = True
except ImportError:
    HAS_PYMEDIAINFO = False


APP_VERSION = "5.8.0"
DEEP_SCAN = False

VIDEO_EXTS = {
    ".mov", ".mp4", ".mxf", ".mkv", ".avi", ".mts",
    ".m2ts", ".braw", ".r3d", ".crm", ".ari", ".mpg", ".m4v"
}

DJI_MODELS = {
    "FC8282": "DJI Air 3",
    "FC8482": "DJI Mini 4 Pro",
    "FC3582": "DJI Mini 3 Pro",
    "FC3682": "DJI Mini 3",
    "FC7303": "DJI Mini 2 / Mini SE",
    "L2D-20c": "DJI Mavic 3 / Mavic 3 Classic",
    "FC4382": "DJI Mavic 3 Pro",
    "FC3411": "DJI Air 2S",
    "FC3170": "DJI Mavic Air 2",
    "FC220": "DJI Mavic Pro",
    "FC6310": "DJI Phantom 4 Pro",
    "FC6540": "DJI Inspire 2 (Zenmuse X7)",
    "FC8671": "DJI Avata 2",
    "FC8171": "DJI Avata",
    "AC003": "DJI Osmo Action 4",
    "AC004": "DJI Osmo Action 5 Pro",
    "OP003": "DJI Osmo Pocket 3",
}

THEMES = {
    "dark": {
        "bg": "#181a1f",
        "panel": "#21252b",
        "text_bg": "#14161a",
        "fg": "#abb2bf",
        "fg_bright": "#ffffff",
        "drop_bg": "#282c34",
        "drop_fg": "#98c379",
        "select_bg": "#3e4451",
        "select_fg": "#ffffff",
        "border": "#333842",
        "label": "#7f848e",
        "camera": "#61afef",
        "log": "#c678dd",
        "rec709": "#56b6c2",
        "ok": "#98c379",
        "warn": "#e5c07b",
        "err": "#e06c75",
        "unknown": "#8b929e",
        "cst": "#d19a66",
        "xml_tag": "#c678dd",
        "mi_tag": "#61afef",
        "exif_tag": "#98c379",
    },
    "light": {
        "bg": "#FAFAFA",
        "panel": "#ffffff",
        "text_bg": "#fafafa",
        "fg": "#2c313a",
        "fg_bright": "#000000",
        "drop_bg": "#e2e6ea",
        "drop_fg": "#1e6f3d",
        "select_bg": "#cce5ff",
        "select_fg": "#003366",
        "border": "#cccccc",
        "label": "#5c6370",
        "camera": "#005cc5",
        "log": "#6f42c1",
        "rec709": "#0366d6",
        "ok": "#22863a",
        "warn": "#b08800",
        "err": "#cb2431",
        "unknown": "#6a737d",
        "cst": "#d73a49",
        "xml_tag": "#6f42c1",
        "mi_tag": "#005cc5",
        "exif_tag": "#22863a",
    }
}

MONO_FONT = "Menlo" if sys.platform == "darwin" else "Consolas"


# ==================================================
# CATALOGUE DE PROFILS MULTI-CAMÉRAS (EXTENSIBLE)
# ==================================================

CAMERA_PROFILES = [
    {
        "id": "braw_camera_raw",
        "priority": 10,
        "make_override": "Blackmagic Design",
        "model_hint": "Caméra Blackmagic (BRAW)",
        "match_regex": r"\.braw\b|blackmagic\s*design|braw",
        "confirmed": "Blackmagic RAW",
        "probable": "",
        "gamut": "Blackmagic Wide Gamut Gen 5",
        "confidence": "Confirmé",
        "method": "Flux Blackmagic RAW (.BRAW) détecté.",
        "action": (
            "BRAW : régler le décodage dans l'onglet Camera RAW de DaVinci Resolve "
            "(Blackmagic Design Film Gen 5 / BRAW) ; ne pas empiler un CST redondant."
        ),
    },
    {
        "id": "red_r3d_camera_raw",
        "priority": 11,
        "make_override": "RED",
        "model_hint": "Caméra RED (R3D)",
        "match_regex": r"\.r3d\b|redwidegamutrgb|log3g10|\bred\s*digital\s*cinema",
        "confirmed": "RED Log3G10",
        "probable": "",
        "gamut": "REDWideGamutRGB",
        "confidence": "Confirmé",
        "method": "Flux RED (.R3D / IPP2) détecté.",
        "action": (
            "R3D : régler le pipeline IPP2 / Camera RAW dans DaVinci Resolve "
            "(REDWideGamutRGB / Log3G10) avant toute conversion de sortie."
        ),
    },
    {
        "id": "canon_crm_raw",
        "priority": 12,
        "make_override": "Canon",
        "model_hint": "",
        "match_regex": r"\.crm\b|cinema\s*raw\s*light",
        "confirmed": "Canon Cinema RAW Light",
        "probable": "",
        "gamut": "Canon Cinema Gamut / Log2 ou Log3",
        "confidence": "Confirmé",
        "method": "Flux Canon Cinema RAW Light (.CRM) détecté.",
        "action": (
            "Canon RAW : vérifier les réglages Camera RAW dans DaVinci Resolve "
            "(Canon Log2 / Log3) selon le réglage de prise de vue."
        ),
    },
    {
        "id": "sony_slog3",
        "priority": 13,
        "make_override": "Sony",
        "model_hint": "",
        "match_regex": r"\bs-logs?\s*3\b|slog3|sony.*log.*3",
        "confirmed": "Sony S-Log3",
        "probable": "",
        "gamut": "S-Gamut3.Cine / S-Gamut3",
        "confidence": "Confirmé",
        "method": "Tag Sony S-Log3 détecté.",
        "action": (
            "CST DaVinci Resolve : Input Color Space = S-Gamut3.Cine, "
            "Input Gamma = Sony S-Log3 -> DaVinci Wide Gamut / Intermediate."
        ),
    },
    {
        "id": "sony_slog2",
        "priority": 14,
        "make_override": "Sony",
        "model_hint": "",
        "match_regex": r"\bs-logs?\s*2\b|slog2|sony.*log.*2",
        "confirmed": "Sony S-Log2",
        "probable": "",
        "gamut": "S-Gamut",
        "confidence": "Confirmé",
        "method": "Tag Sony S-Log2 détecté.",
        "action": (
            "CST DaVinci Resolve : Input Color Space = S-Gamut, "
            "Input Gamma = Sony S-Log2 -> DaVinci Wide Gamut / Intermediate."
        ),
    },
    {
        "id": "apple_log",
        "priority": 15,
        "make_override": "Apple",
        "model_hint": "",
        "match_regex": r"\bapple.*log\b|applelog",
        "confirmed": "Apple Log",
        "probable": "",
        "gamut": "Apple Wide Gamut",
        "confidence": "Confirmé",
        "method": "Tag Apple Log détecté.",
        "action": (
            "CST DaVinci Resolve : Input Color Space = Apple Wide Gamut, "
            "Input Gamma = Apple Log -> DaVinci Wide Gamut / Intermediate."
        ),
    },
    {
        "id": "fujifilm_flog2",
        "priority": 30,
        "make_override": "Fujifilm",
        "model_hint": "",
        "match_regex": r"com\.fujifilm\.bt2020\.f-log2|f-log2|flog2",
        "confirmed": "Fujifilm F-Log2",
        "probable": "",
        "gamut": "F-Gamut / BT.2020",
        "confidence": "Confirmé",
        "method": "Tag explicite Fujifilm F-Log2 détecté.",
        "action": "CST : Input Color Space = Rec.2020 (ou F-Gamut), Input Gamma = Fujifilm F-Log2.",
    },
    {
        "id": "fujifilm_flog",
        "priority": 31,
        "make_override": "Fujifilm",
        "model_hint": "",
        "match_regex": r"\bf-log\b|\bflog\b",
        "confirmed": "",
        "probable": "Fujifilm F-Log",
        "gamut": "Fujifilm F-Gamut",
        "confidence": "Probable",
        "method": "Tag Fujifilm F-Log détecté.",
        "action": "Proposition avec avertissement : vérifier F-Log / F-Gamut avant CST.",
    },
    {
        "id": "panasonic_vlog",
        "priority": 32,
        "make_override": "Panasonic",
        "model_hint": "",
        "match_regex": r"\bv-log\b|\bvlog\b|v-gamut",
        "confirmed": "",
        "probable": "Panasonic V-Log",
        "gamut": "V-Gamut",
        "confidence": "Probable",
        "method": "Tag Panasonic V-Log détecté (vérifier Video Range Full/Limited).",
        "action": "Proposition avec avertissement : vérifier V-Log / V-Gamut et la plage vidéo.",
    },
    {
        "id": "gopro_gplog",
        "priority": 33,
        "make_override": "GoPro",
        "model_hint": "",
        "match_regex": r"\bgp-log\b|\bgplog\b",
        "confirmed": "GoPro GP-Log",
        "probable": "",
        "gamut": "GP-Log / BT.709 ou Wide",
        "confidence": "Confirmé",
        "method": "Tag GoPro GP-Log détecté.",
        "action": "CST / LUT : appliquer le profil ou la LUT officielle GoPro GP-Log -> Rec.709 / DWG.",
    },
    {
        "id": "gopro_flat_sdr",
        "priority": 34,
        "make_override": "GoPro",
        "model_hint": "",
        "match_regex": r"\bgopro\b",
        "confirmed": "",
        "probable": "GoPro Flat / SDR (BT.709)",
        "gamut": "BT.709",
        "confidence": "Probable",
        "method": "Flux GoPro détecté sans tag GP-Log explicite.",
        "action": "Proposition avec avertissement : vérifier Flat vs GoPro Color et plage Full/Limited.",
    },
    {
        "id": "canon_log3",
        "priority": 35,
        "make_override": "Canon",
        "model_hint": "",
        "match_regex": r"\bcanon.*log.*3\b|clog3|canonlog3",
        "confirmed": "Canon Log 3",
        "probable": "",
        "gamut": "Canon Cinema Gamut",
        "confidence": "Confirmé",
        "method": "Tag Canon Log 3 détecté.",
        "action": (
            "CST DaVinci Resolve : Input Color Space = Canon Cinema Gamut, "
            "Input Gamma = Canon Log 3 -> DaVinci Wide Gamut / Intermediate."
        ),
    },
]


# ==================================================
# BOUTON PERSONNALISÉ
# ==================================================

class RoundedButton(tk.Canvas):
    def __init__(
        self, parent, text, command=None, radius=16,
        padx=14, pady=6, bg="#2c313c",
        hover_bg="#3e4451", fg="#ffffff",
        canvas_bg="#181a1f", font=None
    ):
        self.font = font or (MONO_FONT, 9, "bold")
        self.padx = padx
        self.pady = pady

        tmp = tk.Label(parent, text=text, font=self.font)
        w = tmp.winfo_reqwidth() + padx * 2
        h = tmp.winfo_reqheight() + pady * 2
        tmp.destroy()

        super().__init__(
            parent, width=w, height=h,
            bg=canvas_bg, highlightthickness=0,
            cursor="hand2"
        )

        self.command = command
        self.radius = radius
        self.bg_normal = bg
        self.bg_hover = hover_bg
        self.fg_color = fg
        self.text_str = text
        self._hovered = False

        self._draw(self.bg_normal)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _on_enter(self, event=None):
        self._hovered = True
        self._draw(self.bg_hover)

    def _on_leave(self, event=None):
        self._hovered = False
        self._draw(self.bg_normal)

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        pts = [
            x1+r, y1, x2-r, y1, x2, y1, x2, y1+r,
            x2, y2-r, x2, y2, x2-r, y2, x1+r, y2,
            x1, y2, x1, y2-r, x1, y1+r, x1, y1
        ]
        return self.create_polygon(pts, smooth=True, **kwargs)

    def _draw(self, fill_color):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        self._round_rect(
            1, 1, w - 1, h - 1,
            self.radius, fill=fill_color, outline=""
        )
        self.create_text(
            w // 2, h // 2,
            text=self.text_str,
            fill=self.fg_color,
            font=self.font
        )

    def _on_click(self, event):
        if self.command:
            self.command()

    def set_text(self, new_text):
        self.text_str = new_text
        self._draw(self.bg_hover if self._hovered else self.bg_normal)

    def update_colors(self, bg, hover_bg, fg, canvas_bg):
        self.bg_normal = bg
        self.bg_hover = hover_bg
        self.fg_color = fg
        self.configure(bg=canvas_bg)
        self._draw(self.bg_hover if self._hovered else self.bg_normal)


# ==================================================
# OUTILS MÉTADONNÉES GÉNÉRALES
# ==================================================

def norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def metadata_value(data, *names):
    for name in names:
        n = norm(name)
        for key, value in data.items():
            full_key = norm(key)
            short_key = norm(key.split(":")[-1])
            if n in (full_key, short_key):
                if value not in (None, "", []):
                    return (
                        ", ".join(map(str, value))
                        if isinstance(value, list)
                        else str(value)
                    )
    return ""


def collect_source_values(data, source_prefix, *names):
    """Récupère les valeurs non vides d'une source donnée (MI:, XML:, ou ExifTool)."""
    found = []
    for name in names:
        n = norm(name)
        for key, value in data.items():
            is_mi = key.startswith("MI:")
            is_xml = key.startswith("XML:")
            if source_prefix == "MI" and not is_mi:
                continue
            if source_prefix == "XML" and not is_xml:
                continue
            if source_prefix == "EXIF" and (is_mi or is_xml or key.startswith("_Analyse:")):
                continue

            short_key = norm(key.split(":")[-1])
            full_key = norm(key)
            if n in (short_key, full_key):
                if value not in (None, "", []):
                    txt = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
                    if txt not in found:
                        found.append(txt)
    return found


def all_values(data, patterns):
    out = []
    for key, value in data.items():
        if any(re.search(p, key, re.I) for p in patterns):
            if isinstance(value, list):
                value = ", ".join(map(str, value))
            if value not in (None, ""):
                out.append((key, str(value)))
    return out


DJI_NAME = re.compile(r"^DJI_\d{14}_\d{4}", re.I)


def looks_dji(path, data):
    if path and DJI_NAME.match(Path(path).name):
        return True

    for key, value in data.items():
        if key == "SourceFile" or key.split(":")[0] in ("File", "System"):
            continue
        if "dji" in key.lower() or "dji" in str(value).lower():
            return True

    return False


# ==================================================
# SIDECAR XML (SONY / CANON)
# ==================================================

def find_sidecar_xml(video_path):
    p = Path(video_path)

    candidates = [
        p.with_suffix(".XML"),
        p.with_suffix(".xml"),
        p.parent / f"{p.stem}M01.XML",
        p.parent / f"{p.stem}M01.xml",
    ]

    for c in candidates:
        if c.is_file():
            return c

    return None


def parse_sidecar_xml(xml_path):
    extracted = {}

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        for elem in root.iter():
            tag = elem.tag.split("}")[-1]

            if tag == "Device":
                manuf = elem.attrib.get("manufacturer", "")
                model = elem.attrib.get("modelName", "")
                if manuf:
                    extracted["XML:Make"] = manuf
                if model:
                    extracted["XML:Model"] = model

            elif tag == "Item":
                name = elem.attrib.get("name", "")
                val = elem.attrib.get("value", "")
                if name and val:
                    extracted[f"XML:{name}"] = val

            elif tag == "VideoFrame":
                for attr in ("videoCodec", "captureFps", "formatFps"):
                    if attr in elem.attrib:
                        extracted[f"XML:{attr}"] = elem.attrib[attr]

            elif elem.text and elem.text.strip() and len(list(elem)) == 0:
                t_low = tag.lower()
                if any(k in t_low for k in ("gamma", "color", "transfer", "model", "make", "log")):
                    key_name = f"XML:{tag}"
                    if key_name not in extracted:
                        extracted[key_name] = elem.text.strip()

    except Exception as e:
        extracted["XML:Error"] = str(e)

    return extracted


# ==================================================
# EXIFTOOL
# ==================================================

def _exiftool_usable(p):
    try:
        if p.suffix.lower() == ".exe":
            return (
                (p.parent / "exiftool_files").is_dir()
                or p.stat().st_size > 1_000_000
            )
        return os.access(str(p), os.X_OK)
    except OSError:
        return False


def find_exiftool():
    base_dir = Path(__file__).resolve().parent
    names = ("exiftool.exe",) if os.name == "nt" else ("exiftool",)
    candidates = []

    for n in names:
        candidates += [
            base_dir / n,
            Path.cwd() / n,
            Path(sys.executable).parent / n
        ]

    for folder in (base_dir, Path.home() / "Downloads"):
        if folder.is_dir():
            for n in names:
                candidates.extend(folder.glob(f"*xif*ool*/{n}"))

    if os.name != "nt":
        candidates += [
            Path("/opt/homebrew/bin/exiftool"),
            Path("/usr/local/bin/exiftool")
        ]

    # Precompile regex patterns for better performance
    exiftool_pattern = re.compile(r"exiftool", re.IGNORECASE)
    
    for p in candidates:
        if p.is_file() and _exiftool_usable(p):
            return str(p)

    # Check system PATH with precompiled pattern
    system_path_result = shutil.which("exiftool") or shutil.which("exiftool.exe")
    if system_path_result:
        return system_path_result

    return None


def mxf_camera_identity(data):
    groups = {}
    wanted = {
        "applicationsuppliername",
        "applicationname",
        "applicationversionstring",
    }

    for key, value in data.items():
        if value in (None, "", []):
            continue
        parts = str(key).split(":")
        tag = norm(parts[-1])
        if tag not in wanted:
            continue
        group = ":".join(parts[:-1])
        if isinstance(value, list):
            value = ", ".join(map(str, value))
        groups.setdefault(group, {})[tag] = str(value)

    candidates = list(groups.values())
    candidates.sort(key=lambda item: (
        "arri" not in item.get("applicationsuppliername", "").lower(),
        not bool(item.get("applicationname")),
    ))

    for item in candidates:
        supplier = item.get("applicationsuppliername", "")
        application = item.get("applicationname", "")
        version = item.get("applicationversionstring", "")
        if supplier or application:
            return supplier, application, version

    return "", "", ""


# ==================================================
# DÉCODEUR NATIF ARRI MXF
# ==================================================

ARRI_SET_KEYS = {
    "position": bytes.fromhex(
        "06 0E 2B 34 02 01 01 01 0E 17 01 02 0C 00 00 00"
    ),
    "lens": bytes.fromhex(
        "06 0E 2B 34 02 01 01 01 0E 17 01 02 0D 00 00 00"
    ),
    "capture": bytes.fromhex(
        "06 0E 2B 34 02 01 01 01 0E 17 01 02 0E 00 00 00"
    ),
}

ARRI_FIELD_KEYS = {
    "tilt": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0C 03 00 00",
    "roll": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0C 04 00 00",
    "focus_metric": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0D 01 00 00",
    "focus_imperial": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0D 02 00 00",
    "iris": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0D 03 00 00",
    "entrance_pupil": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0D 04 00 00",
    "focal_nominal": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0D 05 00 00",
    "focal_effective": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0D 06 00 00",
    "exposure_time": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0E 01 00 00",
    "averaged_frames": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0E 02 00 00",
    "ei": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0E 03 00 00",
    "nd_index": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0E 04 00 00",
    "white_balance": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0E 05 00 00",
    "tint": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0E 06 00 00",
    "sensor_rate": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0E 07 00 00",
    "nd_density": "06 0E 2B 34 01 01 01 0E 0E 17 01 02 0E 08 00 00",
}

ARRI_FIELD_KEYS = {
    name: bytes.fromhex(value)
    for name, value in ARRI_FIELD_KEYS.items()
}

ARRI_CHUNK_SIZE = 16 * 1024 * 1024
ARRI_OVERLAP = 32


def arri_read_ber(data, position):
    if position >= len(data):
        return None
    first = data[position]
    if first < 0x80:
        return first, 1
    count = first & 0x7F
    if count == 0 or count > 8:
        return None
    end = position + 1 + count
    if end > len(data):
        return None
    length = int.from_bytes(data[position + 1:end], "big")
    return length, count + 1


def arri_parse_klv(data):
    result = {}
    position = 0
    while position + 17 <= len(data):
        key = data[position:position + 16]
        ber = arri_read_ber(data, position + 16)
        if ber is None:
            break
        length, ber_size = ber
        value_start = position + 16 + ber_size
        value_end = value_start + length
        if value_end > len(data):
            break
        result[key] = data[value_start:value_end]
        position = value_end
    return result


def arri_find_packets(path, progress_callback=None):
    hits = []
    position = 0
    overlap = b""
    file_size = path.stat().st_size
    search_key = ARRI_SET_KEYS["position"]

    with path.open("rb") as f:
        while True:
            chunk = f.read(ARRI_CHUNK_SIZE)
            if not chunk:
                break
            data = overlap + chunk
            base = position - len(overlap)
            start = 0
            while True:
                index = data.find(search_key, start)
                if index < 0:
                    break
                absolute = base + index
                packet_offset = absolute - 20
                if packet_offset >= 0:
                    hits.append(packet_offset)
                start = index + 1
            overlap = data[-ARRI_OVERLAP:]
            position += len(chunk)
            if progress_callback and file_size:
                progress_callback(min(position / file_size * 100, 100))

    return sorted(set(hits))


def arri_uint(data):
    return int.from_bytes(data, "big", signed=False)


def arri_sint(data):
    return int.from_bytes(data, "big", signed=True)


def arri_float32(data):
    return struct.unpack(">f", data)[0]


def arri_rational(data):
    numerator = arri_uint(data[:4])
    denominator = arri_uint(data[4:8])
    if denominator == 0:
        return None
    return numerator / denominator


def arri_decode_fields(sets):
    row = {}
    position = arri_parse_klv(sets.get(ARRI_SET_KEYS["position"], b""))
    lens = arri_parse_klv(sets.get(ARRI_SET_KEYS["lens"], b""))
    capture = arri_parse_klv(sets.get(ARRI_SET_KEYS["capture"], b""))

    def get(mapping, name):
        return mapping.get(ARRI_FIELD_KEYS[name])

    value = get(position, "tilt")
    row["Tilt_deg"] = arri_float32(value) if value and len(value) == 4 else None

    value = get(position, "roll")
    row["Roll_deg"] = arri_float32(value) if value and len(value) == 4 else None

    value = get(lens, "focus_metric")
    row["Focus_mm"] = arri_sint(value) if value and len(value) == 4 else None

    value = get(lens, "focus_imperial")
    row["Focus_thousandths_inch"] = arri_uint(value) if value and len(value) == 4 else None

    value = get(lens, "iris")
    row["Iris_T"] = arri_sint(value) / 1000 if value and len(value) == 4 else None

    value = get(lens, "entrance_pupil")
    row["Entrance_pupil_offset_um"] = arri_sint(value) if value and len(value) == 4 else None

    value = get(lens, "focal_nominal")
    row["Focal_length_mm"] = arri_uint(value) / 1000 if value and len(value) == 4 else None

    value = get(lens, "focal_effective")
    row["Effective_focal_length_mm"] = arri_uint(value) / 1000 if value and len(value) == 4 else None

    value = get(capture, "exposure_time")
    row["Exposure_time_s"] = arri_rational(value) if value and len(value) == 8 else None

    value = get(capture, "averaged_frames")
    row["Averaged_frames"] = arri_uint(value) if value and len(value) == 4 else None

    value = get(capture, "ei")
    row["EI"] = arri_uint(value) if value and len(value) == 4 else None

    value = get(capture, "nd_index")
    row["ND_index"] = arri_uint(value) if value and len(value) == 2 else None

    value = get(capture, "white_balance")
    row["White_balance_K"] = arri_uint(value) if value and len(value) == 2 else None

    value = get(capture, "tint")
    row["Tint"] = arri_float32(value) if value and len(value) == 4 else None

    value = get(capture, "sensor_rate")
    row["Sensor_rate_fps"] = arri_rational(value) if value and len(value) == 8 else None

    value = get(capture, "nd_density")
    row["ND_density"] = arri_float32(value) if value and len(value) == 4 else None

    return row


def arri_decode_packet(file_handle, offset):
    file_handle.seek(offset + 16)
    header = file_handle.read(9)
    ber = arri_read_ber(header, 0)
    if not ber:
        return None
    length, ber_size = ber
    file_handle.seek(offset + 16 + ber_size)
    payload = file_handle.read(length)
    if len(payload) != length:
        return None
    sets = arri_parse_klv(payload)
    if not all(key in sets for key in ARRI_SET_KEYS.values()):
        return None
    return arri_decode_fields(sets)


def arri_extract_timecodes(exiftool_path, mxf_path, frame_count):
    command = [
        exiftool_path,
        "-json",
        "-n",
        "-StartTimecode",
        "-EditRate",
        "-DropFrame",
        str(mxf_path)
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )

    metadata_list = json.loads(result.stdout)
    if not metadata_list:
        raise ValueError("ExifTool n'a renvoyé aucune métadonnée de timecode.")

    metadata = metadata_list[0]
    if "StartTimecode" not in metadata or "EditRate" not in metadata:
        raise ValueError("StartTimecode ou EditRate absent du MXF.")

    start_seconds = Decimal(str(metadata["StartTimecode"]))
    frame_rate = Decimal(str(metadata["EditRate"]))
    drop_frame = metadata.get("DropFrame", False)

    if isinstance(drop_frame, str):
        drop_frame = drop_frame.strip().lower() in ("true", "1", "yes")

    if frame_rate <= 0:
        raise ValueError("Cadence MXF invalide.")
    if frame_rate != frame_rate.to_integral_value():
        raise ValueError("Cadence fractionnaire non prise en charge par cette version.")
    if drop_frame:
        raise ValueError("Timecode Drop Frame non pris en charge par cette version.")

    fps = int(frame_rate)
    start_frame = int(
        (start_seconds * frame_rate).to_integral_value(rounding=ROUND_HALF_UP)
    )

    timecodes = []
    for i in range(frame_count):
        total_frames = start_frame + i
        hours = total_frames // (fps * 3600)
        minutes = (total_frames // (fps * 60)) % 60
        seconds = (total_frames // fps) % 60
        frames = total_frames % fps
        timecodes.append(f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}")

    return timecodes


def analyze_arri_mxf(path, exiftool_path, progress_callback=None):
    offsets = arri_find_packets(Path(path), progress_callback=progress_callback)
    rows = []

    with Path(path).open("rb") as f:
        for offset in offsets:
            decoded = arri_decode_packet(f, offset)
            if decoded is None:
                continue
            row = {
                "Frame": len(rows),
                "MXF_offset": hex(offset),
            }
            row.update(decoded)
            rows.append(row)

    if not rows:
        raise ValueError("Aucun paquet de métadonnées ARRI complet n'a été décodé.")

    timecodes = arri_extract_timecodes(exiftool_path, Path(path), len(rows))
    for row, timecode in zip(rows, timecodes):
        row["Timecode"] = timecode

    return rows


# ==================================================
# DÉTECTION D'INCOHÉRENCES COLORIMÉTRIQUES & ANDROID
# ==================================================

# Precompile regex patterns for better performance
import re

# Compile frequently used patterns once
CANONICAL_COLOR_PATTERN_709 = re.compile(r".*709.*", re.IGNORECASE)
CANONICAL_COLOR_PATTERN_2020 = re.compile(r".*2020.*", re.IGNORECASE)
CANONICAL_COLOR_PATTERN_HLG = re.compile(r".*hlg.*", re.IGNORECASE)
CANONICAL_COLOR_PATTERN_PQ = re.compile(r".*(pq|2084).*", re.IGNORECASE)

def _canonical_color_token(val):
    s = str(val).strip().lower()
    if not s or s in ("unspecified", "unknown", "non renseigne", "non renseigné"):
        return ""
    if "470" in s and ("b" in s or "g" in s or "bg" in s or "pal" in s):
        return "bt470_bg_bt601"
    if "601" in s and ("pal" in s or "bg" in s):
        return "bt470_bg_bt601"
    if "601" in s:
        return "bt601"
    if CANONICAL_COLOR_PATTERN_709.search(s):
        return "bt709"
    if CANONICAL_COLOR_PATTERN_2020.search(s):
        return "bt2020"
    if CANONICAL_COLOR_PATTERN_HLG.search(s):
        return "bt2100_hlg"
    if CANONICAL_COLOR_PATTERN_PQ.search(s):
        return "bt2100_pq"
    if "hlg" in s or "b67" in s:
        return "hlg"
    if "pq" in s or "2084" in s:
        return "pq"
    return norm(s)


def detect_color_inconsistencies(data, primaries, transfer, matrix, video_range):
    inconsistencies = []
    sources_seen = []

    if any(k.startswith("MI:") for k in data):
        sources_seen.append("MediaInfo")
    if any(k.startswith("XML:") for k in data):
        sources_seen.append("XML Sidecar")
    if any(not k.startswith(("MI:", "XML:", "_Analyse:", "File:", "System:")) for k in data):
        sources_seen.append("ExifTool")

    prim_by_source = {}
    for src in ("MI", "EXIF", "XML"):
        vals = collect_source_values(
            data, src,
            "colour_primaries", "ColorPrimaries", "Primaries", "CaptureColorPrimaries"
        )
        if vals:
            prim_by_source[src] = vals

    prim_tokens = set()
    prim_labels = []
    for _, vals in prim_by_source.items():
        for v in vals:
            if v not in prim_labels:
                prim_labels.append(v)
            tok = _canonical_color_token(v)
            if tok:
                prim_tokens.add(tok)

    if len(prim_tokens) > 1:
        inconsistencies.append(
            f"Primaires contradictoires entre sources : {' / '.join(prim_labels)}"
        )

    trc_by_source = {}
    for src in ("MI", "EXIF", "XML"):
        vals = collect_source_values(
            data, src,
            "transfer_characteristics", "TransferCharacteristics",
            "TransferFunction", "CaptureGammaEquation"
        )
        if vals:
            trc_by_source[src] = vals

    trc_tokens = set()
    trc_labels = []
    for _, vals in trc_by_source.items():
        for v in vals:
            if v not in trc_labels:
                trc_labels.append(v)
            tok = _canonical_color_token(v)
            if tok:
                trc_tokens.add(tok)

    if len(trc_tokens) > 1:
        inconsistencies.append(
            f"Courbes de transfert contradictoires : {' / '.join(trc_labels)}"
        )

    p_low = (primaries or "").lower()
    t_low = (transfer or "").lower()
    if "709" in t_low and ("601" in p_low or "470" in p_low or "bt470_bg_bt601" in prim_tokens):
        inconsistencies.append(
            f"Transfert déclaré ({transfer}) discordant avec les primaires ({primaries})"
        )

    mat_vals = []
    mat_tokens = set()
    for src in ("MI", "EXIF", "XML"):
        for v in collect_source_values(data, src, "matrix_coefficients", "MatrixCoefficients"):
            if v not in mat_vals:
                mat_vals.append(v)
            tok = _canonical_color_token(v)
            if tok:
                mat_tokens.add(tok)

    if len(mat_tokens) > 1:
        inconsistencies.append(
            f"Matrices contradictoires : {' / '.join(mat_vals)}"
        )

    rng_vals = []
    rng_tokens = set()
    for src in ("MI", "EXIF", "XML"):
        for v in collect_source_values(data, src, "colour_range", "ColorRange", "VideoRange"):
            if v not in rng_vals:
                rng_vals.append(v)
            low = v.lower()
            if "full" in low or "pc" in low or "0-255" in low:
                rng_tokens.add("full")
            elif "limited" in low or "video" in low or "tv" in low or "16-235" in low:
                rng_tokens.add("video")
            else:
                rng_tokens.add(norm(v))

    if len(rng_tokens) > 1:
        inconsistencies.append(
            f"Plages vidéo contradictoires : {' / '.join(rng_vals)}"
        )

    source_summary = " + ".join(sources_seen) if sources_seen else "Aucune"
    return inconsistencies, source_summary


def detect_android_identity(data, path=None):
    android_keys = [
        "Make", "Model", "Camera", "Manufacturer",
        "DeviceManufacturer", "DeviceModel", "Software",
        "Encoder", "HandlerDescription", "WritingApplication",
        "Android", "QuickTime:Keys", "QuickTime:UserData", "QuickTime:ItemList"
    ]

    make = metadata_value(data, "Manufacturer", "DeviceManufacturer", "Make")
    model = metadata_value(data, "DeviceModel", "Model", "Camera")

    if any(b in make.lower() for b in ("canon", "sony", "arri", "dji", "apple", "panasonic", "fujifilm", "red", "gopro")):
        return "", "", ""

    snippets = []
    for k, v in data.items():
        if k == "SourceFile" or k.split(":")[0] in ("File", "System", "ExifTool"):
            continue
        k_low = k.lower()
        if any(norm(ak) in norm(k) or ak.lower() in k_low for ak in android_keys):
            txt = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
            if txt:
                snippets.append(f"{k}={txt}")

    joined = " ".join(snippets).lower()

    is_android = (
        "android" in joined
        or any(
            brand in joined
            for brand in (
                "samsung", "galaxy", "pixel", "google", "oneplus",
                "xiaomi", "redmi", "poco", "oppo", "realme", "vivo",
                "nothing", "motorola", "huawei", "honor", "sony xperia"
            )
        )
    )

    if not is_android:
        return "", "", ""

    if not make:
        for brand, label in (
            ("samsung", "Samsung"),
            ("pixel", "Google"),
            ("google", "Google"),
            ("oneplus", "OnePlus"),
            ("xiaomi", "Xiaomi"),
            ("redmi", "Redmi"),
            ("poco", "POCO"),
            ("oppo", "OPPO"),
            ("realme", "Realme"),
            ("vivo", "Vivo"),
            ("nothing", "Nothing"),
            ("motorola", "Motorola"),
            ("huawei", "Huawei"),
            ("honor", "Honor"),
        ):
            if brand in joined:
                make = label
                break

    if not model:
        m = re.search(r"(sm-[a-z0-9]+|pixel\s+\d+\s*(?:pro|xl|a)?)", joined, re.I)
        if m:
            model = m.group(1).upper() if m.group(1).lower().startswith("sm-") else m.group(1).title()

    method = "Métadonnées étendues MP4/MOV/Android détectées (nom de fichier ignoré)."
    return make, model, method


def _hlg_gamut_and_action(primaries, transfer):
    p_low = (primaries or "").lower()
    if "709" in p_low and "2020" not in p_low:
        return (
            "BT.709 / HLG",
            "Proposition avec avertissement : HLG avec primaires BT.709 "
            "(CST Input Color Space = Rec.709, Input Gamma = Rec.2100 HLG)."
        )
    return (
        "Rec.2020 / HLG",
        "Proposition avec avertissement : vérifier HLG / Rec.2020."
    )


# ==================================================
# IDENTIFICATION CAMÉRA ET PROFILS COULEUR
# ==================================================

def identify(data, path=None):
    make = metadata_value(
        data, "XML:Make", "Make", "Manufacturer", "DeviceManufacturer", "CameraManufacturer"
    )

    model = metadata_value(
        data, "XML:Model", "DeviceModel", "Model", "CameraModelName",
        "CameraModel", "AppleProappsModelname"
    )

    mxf_supplier, mxf_application, _ = mxf_camera_identity(data)

    if mxf_supplier and "arri" in mxf_supplier.lower():
        make = "ARRI"

    if mxf_application and re.search(r"alexa|amira", mxf_application, re.I):
        model = mxf_application.strip()
        if not model.lower().startswith("arri"):
            model = "ARRI " + model

    dji = looks_dji(path, data)

    if dji and not make:
        make = "DJI"

    if dji and (not model or model.startswith("FC")):
        enc = metadata_value(data, "Encoder")
        if enc and "dji" in enc.lower():
            model = enc
        if not model:
            cat = metadata_value(data, "Category")
            m_code = re.search(r"model_name:([^;]+)", cat)
            if m_code:
                code = m_code.group(1).strip()
                model = DJI_MODELS.get(code, f"DJI ({code})")
        if not model:
            for k, v in data.items():
                if (
                    k.startswith("Protobuf:")
                    and isinstance(v, str)
                    and v.startswith("DJI ")
                    and not v.startswith("DJI FC")
                ):
                    model = v
                    break

    primaries = metadata_value(
        data, "MI:colour_primaries", "ColorPrimaries", "Primaries"
    )
    transfer = metadata_value(
        data, "MI:transfer_characteristics",
        "TransferCharacteristics", "TransferFunction"
    )
    matrix = metadata_value(
        data, "MI:matrix_coefficients", "MatrixCoefficients"
    )
    video_range = metadata_value(
        data, "MI:colour_range", "ColorRange", "VideoRange", "VideoFullRangeFlag"
    )
    codec = metadata_value(
        data, "MI:format", "CompressorID", "CodecID", "VideoCodec"
    )
    format_profile = metadata_value(data, "MI:format_profile")
    bitdepth = metadata_value(
        data, "MI:bit_depth", "BitDepth", "BitsPerSample"
    )
    chroma = metadata_value(
        data, "MI:chroma_subsampling", "ChromaSubsampling"
    )
    width = metadata_value(
        data, "MI:width", "ImageWidth", "SourceImageWidth"
    )
    height = metadata_value(
        data, "MI:height", "ImageHeight", "SourceImageHeight"
    )
    fps = metadata_value(
        data, "MI:frame_rate", "VideoFrameRate", "FrameRate"
    )

    tags = []
    for key, value in data.items():
        if key == "SourceFile" or key.split(":")[0] in ("File", "System", "ExifTool"):
            continue
        if isinstance(value, (str, int, float)):
            tags.append(f"{key}={value}")

    tagtext = " ".join(tags).lower()
    filename_text = Path(path).name.lower() if path else ""
    ext_text = Path(path).suffix.lower() if path else ""

    android_make, android_model, android_method = detect_android_identity(data, path)
    is_android_device = bool(android_make or android_model)
    if is_android_device:
        if not make and android_make:
            make = android_make
        if not model and android_model:
            model = android_model

    arri_identity_text = " ".join((make, model, mxf_supplier, mxf_application, tagtext)).lower()
    arri_detected = (
        "arri" in arri_identity_text
        or bool(re.search(r"\balexa\b|\bamira\b", arri_identity_text))
        or bool(re.search(r"arri|alexa|amira", filename_text))
    )

    if arri_detected and (not model or model == "Modele non identifie"):
        model_source = arri_identity_text + " " + filename_text
        arri_models = (
            (r"alexa\s*35", "ARRI ALEXA 35"),
            (r"alexa\s*mini\s*lf", "ARRI ALEXA Mini LF"),
            (r"alexa\s*lf", "ARRI ALEXA LF"),
            (r"alexa\s*65", "ARRI ALEXA 65"),
            (r"alexa\s*mini", "ARRI ALEXA Mini"),
            (r"\bamira\b", "ARRI AMIRA"),
            (r"alexa", "ARRI ALEXA (modele a preciser)"),
        )
        for pattern, label in arri_models:
            if re.search(pattern, model_source, re.I):
                model = label
                break

    inconsistencies, source_summary = detect_color_inconsistencies(
        data, primaries, transfer, matrix, video_range
    )

    confirmed_profile = ""
    probable_profile = ""
    gamut = ""
    confidence = "Indéterminé"
    method = "Aucun tag de profil couleur suffisamment explicite détecté."
    action = (
        "Aucun CST automatique : métadonnées insuffisantes pour garantir "
        "la courbe de transfert et l'espace couleur d'entrée."
    )

    is_apple = (
        "apple" in (make + " " + model + " " + tagtext).lower()
        or "iphone" in (model + " " + tagtext).lower()
        or "com.apple." in tagtext
    )

    # 1. Détection ARRI
    if arri_detected:
        logc4_explicit = bool(re.search(r"log\s*c\s*4|logc4", tagtext, re.I))
        logc3_explicit = bool(re.search(r"log\s*c\s*3|logc3", tagtext, re.I))
        awg4_explicit = bool(re.search(r"arri\s*wide\s*gamut\s*4|awg4", tagtext, re.I))
        awg3_explicit = bool(re.search(r"arri\s*wide\s*gamut\s*3|awg3", tagtext, re.I))
        is_arriraw = bool(
            re.search(r"arriraw|arri\s*raw", tagtext + " " + filename_text, re.I)
            or "0d01030102220100" in tagtext
        )

        if not make or "non identifie" in make.lower():
            make = "ARRI"

        if logc4_explicit:
            confirmed_profile = "ARRI LogC4"
            gamut = "ARRI Wide Gamut 4" if awg4_explicit or not awg3_explicit else "ARRI Wide Gamut 3"
            confidence = "Confirmé"
            method = "Tag explicite ARRI LogC4 détecté."
            action = (
                "CST DaVinci Resolve : Input Color Space = ARRI Wide Gamut 4, "
                "Input Gamma = ARRI LogC4 -> DaVinci Wide Gamut / Intermediate."
            )
        elif logc3_explicit:
            confirmed_profile = "ARRI LogC3"
            gamut = "ARRI Wide Gamut 3"
            confidence = "Confirmé"
            method = "Tag explicite ARRI LogC3 détecté."
            action = (
                "CST DaVinci Resolve : Input Color Space = ARRI Wide Gamut 3, "
                "Input Gamma = ARRI LogC3 -> DaVinci Wide Gamut / Intermediate."
            )
        elif re.search(r"alexa\s*35|alexa\s*mini\s*lf|alexa\s*lf|alexa\s*65", model, re.I):
            probable_profile = "ARRI LogC4 (déduit du modèle ARRI)"
            gamut = "ARRI Wide Gamut 4 (probable)"
            confidence = "Probable"
            method = "Modèle ARRI détecté sans tag explicite LogC4."
            action = (
                "Proposition avec avertissement : vérifier Camera RAW / métadonnées "
                "avant d'appliquer ARRI Wide Gamut 4 / LogC4."
            )
        elif "alexa" in model.lower() or "amira" in model.lower():
            probable_profile = "ARRI LogC (génération à vérifier)"
            gamut = "Gamut ARRI à vérifier"
            confidence = "Probable"
            method = "Caméra ARRI détectée sans mention explicite de courbe Log."
            action = (
                "Proposition avec avertissement : vérifier les réglages Camera RAW "
                "et les métadonnées avant d'appliquer un CST."
            )
        else:
            confidence = "Indéterminé"
            method = "Caméra ARRI détectée sans profil couleur explicite."
            action = "Aucun CST automatique : vérifier le réglage caméra / Camera RAW."

        if is_arriraw:
            method = f"Flux ARRIRAW détecté. {method}"
            action = (
                "ARRIRAW : régler d'abord Camera RAW / Decode Using dans DaVinci Resolve "
                "(ex. ARRI Wide Gamut 4 / LogC4) ; ne pas empiler un CST redondant."
            )

    # 2. Détection Sony
    sony_gamma = metadata_value(data, "XML:CaptureGammaEquation")
    sony_prim = metadata_value(data, "XML:CaptureColorPrimaries")

    if not confirmed_profile and not probable_profile and (
        sony_gamma or "sony" in make.lower() or "xavc" in tagtext
    ):
        if not make:
            make = "Sony"
        sg = (sony_gamma or tagtext).lower()
        sp = (sony_prim or tagtext).lower()

        if "s-log3" in sg or "slog3" in sg:
            if "cine" in sp:
                gamut = "S-Gamut3.Cine"
            elif "s-gamut3" in sp or "sgamut3" in sp:
                gamut = "S-Gamut3"
            else:
                gamut = "S-Gamut3.Cine (probable)"

            if sony_gamma:
                confirmed_profile = "Sony S-Log3"
                confidence = "Confirmé"
                method = f"XML Sony : {sony_gamma} / {sony_prim}".strip(" /")
                action = (
                    f"CST DaVinci Resolve : Input Color Space = {gamut.split(' (')[0]}, "
                    "Input Gamma = Sony S-Log3 -> DaVinci Wide Gamut / Intermediate."
                )
            else:
                probable_profile = "Sony S-Log3"
                confidence = "Probable"
                method = "Indice S-Log3 détecté hors XML explicite."
                action = (
                    "Proposition avec avertissement : confirmer S-Log3 et le gamut "
                    "S-Gamut3 / S-Gamut3.Cine avant CST."
                )

        elif "s-log2" in sg or "slog2" in sg:
            gamut = "S-Gamut"
            if sony_gamma:
                confirmed_profile = "Sony S-Log2"
                confidence = "Confirmé"
                method = f"XML Sony : {sony_gamma}"
                action = (
                    "CST DaVinci Resolve : Input Color Space = S-Gamut, "
                    "Input Gamma = Sony S-Log2 -> DaVinci Wide Gamut / Intermediate."
                )
            else:
                probable_profile = "Sony S-Log2"
                confidence = "Probable"
                method = "Indice S-Log2 détecté."
                action = "Proposition avec avertissement : vérifier S-Log2 / S-Gamut."

        elif "hlg" in sg or "hlg" in transfer.lower() or "arib-std-b67" in sg:
            gamut, action = _hlg_gamut_and_action(primaries, transfer)
            probable_profile = "HDR HLG"
            confidence = "Probable"
            method = f"Courbe HLG détectée ({sony_gamma or transfer})."

        elif "rec709" in sg or "itu709" in sg or "s-cinetone" in sg:
            label_709 = "Sony S-Cinetone / Rec.709" if "cinetone" in sg else "Sony Rec.709"
            gamut = "Rec.709"
            if sony_gamma:
                confirmed_profile = label_709
                confidence = "Confirmé"
                method = f"XML Sony : {sony_gamma}"
                action = "BT.709 confirmé : Rec.709 / Gamma 2.4 selon le signal (pas de CST Log)."
            else:
                probable_profile = label_709
                confidence = "Probable"
                method = "Profil standard Sony détecté."
                action = "Proposition avec avertissement : signal probable Rec.709."

    # 3. Détection DJI
    if dji and not confirmed_profile and not probable_profile:
        is_10bit = str(bitdepth) == "10" or "10" in str(format_profile)

        if "d-log m" in tagtext or "d-logm" in tagtext or "dlogm" in tagtext:
            confirmed_profile = "DJI D-Log M"
            gamut = "Rec.709 (Encodage D-Log M)"
            confidence = "Confirmé"
            method = "Tag explicite DJI D-Log M."
            action = f"Utiliser la LUT officielle DJI D-Log M -> Rec.709 ({model or 'DJI'})."
        elif "d-log" in tagtext or "dlog" in tagtext:
            confirmed_profile = "DJI D-Log"
            gamut = "DJI D-Gamut"
            confidence = "Confirmé"
            method = "Tag explicite DJI D-Log."
            action = "CST : Input Color Space = DJI D-Gamut, Input Gamma = DJI D-Log."
        elif "hlg" in tagtext or "arib-std-b67" in tagtext:
            confirmed_profile = "HDR HLG"
            gamut, _ = _hlg_gamut_and_action(primaries, transfer)
            confidence = "Confirmé"
            method = "Transfert HLG détecté."
            action = f"CST : Input Color Space = {'Rec.709' if '709' in gamut else 'Rec.2020'}, Input Gamma = Rec.2100 HLG."
        elif is_10bit:
            probable_profile = "DJI D-Log M (vidéo 10-bit sans tag Log explicite)"
            gamut = "Rec.709 (probable)"
            confidence = "Probable"
            method = f"DJI {bitdepth}-bit sans tag Log/HLG explicite."
            action = "Proposition avec avertissement : vérifier visuellement avant LUT/CST."
        else:
            confirmed_profile = "SDR (BT.709)"
            gamut = "Rec.709"
            confidence = "Confirmé"
            method = "DJI 8-bit SDR."
            action = "BT.709 confirmé : Rec.709 / Gamma 2.4 selon le signal."

    # 4. Détection Apple
    if is_apple and not confirmed_profile and not probable_profile:
        if not make:
            make = "Apple"

        has_apple_log = bool(re.search(r"apple\s*log|applelog", tagtext, re.I))
        has_dolby_vision = bool(
            re.search(r"dolby\s*vision|\bdovi\b|\bdvh1\b|\bdvhe\b", tagtext, re.I)
        )
        has_hlg = bool(
            re.search(r"arib.std.b67|\bhlg\b|hlg\s*bt2020|bt2100_hlg", tagtext, re.I)
            or "hlg" in transfer.lower()
        )
        has_709 = (
            "709" in transfer.lower()
            or "709" in primaries.lower()
            or "bt709" in tagtext
        )

        if has_apple_log:
            confirmed_profile = "Apple Log"
            gamut = "Apple Log / Apple Wide Gamut"
            confidence = "Confirmé"
            method = "Tag explicite Apple Log détecté dans les métadonnées."
            action = "Apple Log confirmé : CST Input = Apple Wide Gamut / Apple Log."
        elif has_dolby_vision:
            confirmed_profile = "Dolby Vision"
            gamut = "Dolby Vision"
            confidence = "Confirmé"
            method = "Métadonnées Dolby Vision explicitement détectées."
            action = "Dolby Vision confirmé : gérer selon le pipeline HDR Dolby Vision (pas de CST Log générique)."
        elif has_hlg:
            confirmed_profile = "HDR HLG"
            gamut, _ = _hlg_gamut_and_action(primaries, transfer)
            confidence = "Confirmé"
            method = "Courbe de transfert HLG confirmée."
            action = f"HDR HLG confirmé : CST Input = {'Rec.709' if '709' in gamut else 'Rec.2020'}, Input Gamma = Rec.2100 HLG."
        elif has_709 and not has_hlg and not has_dolby_vision:
            confirmed_profile = "SDR"
            gamut = "BT.709"
            confidence = "Confirmé"
            method = "Profil SDR / BT.709 détecté."
            action = "BT.709 confirmé : Rec.709 / Gamma 2.4 selon le signal."
        else:
            probable_profile = "SDR (BT.709 - à vérifier)"
            gamut = "BT.709 (probable)"
            confidence = "Probable"
            method = "Appareil Apple détecté sans tag Apple Log / HLG / Dolby Vision explicite."
            action = "Aucun CST automatique : vérifier le profil réel dans les métadonnées ou à l'image."

    # 5. Canon Log
    if not confirmed_profile and not probable_profile:
        canon_log = metadata_value(data, "CanonLogVersion", "XML:CanonLogVersion")
        canon_colorspace = metadata_value(data, "ColorSpace2", "XML:ColorSpace2")
        canon_log_norm = norm(canon_log)
        canon_gamut_norm = norm(canon_colorspace)

        canon_log3 = canon_log_norm in ("clogv3", "canonlog3", "clog3") or bool(
            re.search(r"canon.?log.?3|clog.?3", tagtext, re.I)
        )
        canon_log2 = canon_log_norm in ("clogv2", "canonlog2", "clog2") or bool(
            re.search(r"canon.?log.?2|clog.?2", tagtext, re.I)
        )
        canon_log1 = canon_log_norm in ("clogv1", "canonlog1", "canonlog", "clog") or bool(
            re.search(r"canon.?log(?!.?[23])|\bclog\b", tagtext, re.I)
        )

        if canon_log3 or canon_log2 or canon_log1:
            if not make or make == "Fabricant non identifié":
                make = "Canon"
            canon_curve = "Canon Log 3" if canon_log3 else ("Canon Log 2" if canon_log2 else "Canon Log")
            if canon_gamut_norm in ("bt709", "rec709", "itur709", "709"):
                confirmed_profile = canon_curve
                gamut = "Rec.709"
                confidence = "Confirmé"
                method = f"Tag constructeur Canon : {canon_log or canon_curve}, ColorSpace = {canon_colorspace}."
                action = f"CST : Input Gamma = {canon_curve}, Input Color Space = Rec.709."
            elif canon_gamut_norm in ("bt2020", "rec2020", "itur2020", "2020"):
                confirmed_profile = canon_curve
                gamut = "Rec.2020"
                confidence = "Confirmé"
                method = f"Tag constructeur Canon : {canon_log or canon_curve}, ColorSpace = {canon_colorspace}."
                action = f"CST : Input Gamma = {canon_curve}, Input Color Space = Rec.2020."
            elif "cinemagamut" in canon_gamut_norm:
                confirmed_profile = canon_curve
                gamut = "Canon Cinema Gamut"
                confidence = "Confirmé"
                method = f"Tag constructeur Canon : {canon_log or canon_curve}, ColorSpace = {canon_colorspace}."
                action = f"CST : Input Gamma = {canon_curve}, Input Color Space = Canon Cinema Gamut."
            else:
                probable_profile = canon_curve
                gamut = "Cinema Gamut / BT.709 (à vérifier)"
                confidence = "Probable"
                method = f"Tag Canon ({canon_curve}) détecté sans gamut explicite."
                action = "Proposition avec avertissement : vérifier l'espace couleur avant CST."

    # 6. Catalogue multi-caméras (Blackmagic, RED, Canon CRM, GoPro, Panasonic, Fujifilm)
    if not confirmed_profile and not probable_profile:
        haystack = f"{ext_text} {tagtext} {filename_text}"
        for entry in sorted(CAMERA_PROFILES, key=lambda x: x["priority"]):
            if re.search(entry["match_regex"], haystack, re.I):
                if entry["make_override"] and (not make or make == "Fabricant non identifié"):
                    make = entry["make_override"]
                if entry["model_hint"] and (not model or model == "Modèle non identifié"):
                    model = entry["model_hint"]
                confirmed_profile = entry["confirmed"]
                probable_profile = entry["probable"]
                gamut = entry["gamut"]
                confidence = entry["confidence"]
                method = entry["method"]
                action = entry["action"]
                break

    # 7. Fallbacks génériques HLG / BT.709
    if not confirmed_profile and not probable_profile:
        if "hlg" in tagtext or "arib-std-b67" in tagtext or "hlg" in transfer.lower():
            probable_profile = "HDR HLG"
            gamut, action = _hlg_gamut_and_action(primaries, transfer)
            confidence = "Probable"
            method = "Courbe de transfert HLG détectée."
        elif "709" in transfer.lower() or "709" in primaries.lower():
            probable_profile = "SDR (BT.709)"
            gamut = "BT.709"
            confidence = "Probable"
            method = "Signal BT.709 détecté."
            action = "Proposition avec avertissement : Rec.709 / Gamma 2.4 selon le signal."

    if inconsistencies:
        confidence = "Incohérent"
        action = (
            "Aucun CST automatique : métadonnées colorimétriques contradictoires. "
            "Vérifier manuellement la source et le signal avant toute conversion."
        )

    if is_android_device:
        method = f"{method} ({android_method})"

    if not model:
        model = "Modèle non identifié"
    if not make:
        make = "Fabricant non identifié"

    bit_clean = (
        f"{bitdepth} bits"
        if bitdepth and "bit" not in str(bitdepth).lower()
        else (bitdepth or "Non renseigné")
    )
    chroma_clean = chroma or "Non renseigné"

    display_profile = (
        confirmed_profile
        or probable_profile
        or ("Profil non identifié" if not inconsistencies else "Incohérence détectée")
    )

    return {
        "make": make,
        "model": model,
        "camera_identified": f"{make} — {model}",
        "confirmed_profile": confirmed_profile or "Aucun",
        "probable_profile": probable_profile or "Aucun",
        "inconsistencies": inconsistencies,
        "profile": display_profile,
        "gamut": gamut or "Non identifié",
        "confidence": confidence,
        "method": method,
        "action": action,
        "primaries": primaries or "Non renseigné",
        "transfer": transfer or "Non renseigné",
        "matrix": matrix or "Non renseigné",
        "video_range": video_range or "Non renseignée",
        "bitdepth_only": bit_clean,
        "chroma_only": chroma_clean,
        "source_summary": source_summary,
        "codec": (
            f"{codec} ({format_profile})"
            if codec and format_profile
            else (codec or "Non renseigné")
        ),
        "bitdepth": f"{bit_clean} ({chroma_clean})" if chroma else bit_clean,
        "resolution": (
            f"{width} x {height}"
            if width and height
            else "Non renseignée"
        ),
        "fps": fps or "Non renseignée",
        "xml_found": (
            "Oui (" + Path(data["_Analyse:XMLPath"]).name + ")"
            if data.get("_Analyse:XMLPath")
            else "Non"
        )
    }


# ==================================================
# APPLICATION
# ==================================================

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(
            f"Analyse Camera - V{APP_VERSION} "
            "(ExifTool + MediaInfo + ARRI + XML)"
        )
        self.root.geometry("1280x900")
        self.root.minsize(960, 700)

        self.files = []
        self.results = []
        self.index = 0

        self.q = queue.Queue()
        self.busy = False
        self.cache = {}
        self.theme_name = "dark"
        self._copy_reset_after_id = None

        self.exiftool = find_exiftool()

        self._build()
        self.apply_theme("dark")
        self.root.after(100, self._poll)
        
        # Initialisation de l'état de la barre de status
        self.status = ttk.Label(
            self.top,
            text="Prêt",
            font=(MONO_FONT, 9)
        )
        self.status.pack(side="right")
        self._show_status()

        try:
            from tkinterdnd2 import DND_FILES
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._drop)
            self.drop_enabled = True
        except Exception:
            self.drop_enabled = False
            self.drop_label.config(
                text=(
                    "Glisser-déposer des vidéos ou dossiers ici "
                    "(lance via le fichier .command ou .BAT "
                    "pour activer le glisser-déposer)"
                )
            )

        self._show_status()

    def _build(self):
        self.style = ttk.Style()
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")

        self.buttons = []

        self.top = ttk.Frame(self.root, padding=10)
        self.top.pack(fill="x")

        btn_specs = [
            ("Importer vidéos...", self.pick),
            ("Importer dossier...", self.pick_folder),
            ("Réanalyser", lambda: self.analyze(force=True)),
        ]

        for label, cmd in btn_specs:
            b = RoundedButton(self.top, text=label, command=cmd)
            b.pack(side="left", padx=(0, 6))
            self.buttons.append(b)

        self.copy_btn = RoundedButton(
            self.top,
            text="Copier rapport",
            command=self.copy_report
        )
        self.copy_btn.pack(side="left", padx=(0, 6))
        self.buttons.append(self.copy_btn)

        # Ajout du bouton de changement de thème
        self.theme_btn = RoundedButton(
            self.top,
            text="Mode Clair",
            command=self.toggle_theme
        )
        self.theme_btn.pack(side="right", padx=(12, 0))
        self.buttons.append(self.theme_btn)

        for label, cmd in [
            ("Exporter CSV...", self.export_csv),
            ("JSON...", self.export_dump),
        ]:
            b = RoundedButton(self.top, text=label, command=cmd)
            b.pack(side="left", padx=(0, 6))
            self.buttons.append(b)

        self.drop_label = tk.Label(
            self.root,
            text=(
                "[ Glisse-dépose tes vidéos ou dossiers de rushs "
                "(DJI, Sony, Canon, ARRI, Apple, Android...) ici ]"
            ),
            anchor="center",
            pady=10,
            font=(MONO_FONT, 9, "bold")
        )
        self.drop_label.pack(fill="x", padx=10, pady=(0, 8))

        self.body = ttk.Panedwindow(self.root, orient="horizontal")
        self.body.pack(fill="both", expand=True, padx=10, pady=5)

        left = ttk.Frame(self.body, padding=5)
        self.body.add(left, weight=1)

        ttk.Label(
            left,
            text="FICHIERS ANALYSÉS",
            font=(MONO_FONT, 10, "bold")
        ).pack(anchor="w")

        self.listbox = tk.Listbox(
            left,
            width=36,
            activestyle="dotbox",
            borderwidth=1,
            highlightthickness=0,
            font=(MONO_FONT, 9)
        )
        self.listbox.pack(fill="both", expand=True, pady=5)
        self.listbox.bind("<<ListboxSelect>>", self._select)

        right = ttk.Frame(self.body, padding=8)
        self.body.add(right, weight=4)

        self.heading = ttk.Label(
            right,
            text="Aucun fichier chargé",
            font=(MONO_FONT, 13, "bold"),
            wraplength=820
        )
        self.heading.pack(anchor="w", pady=(0, 6))

        self.summary_frame = tk.Frame(right, bg="#2c313c")
        self.summary_frame.pack(fill="x", pady=(0, 4))
        
        self.summary = tk.Text(
            right,
            height=11,
            wrap="word",
            font=(MONO_FONT, 10),
            relief="flat",
            padx=12,
            pady=8,
            borderwidth=1,
            bg="#2c313c",  # Fond sombre
            fg="#ffffff"   # Texte blanc
        )
        self.summary.pack(fill="both", expand=True)
        self.summary.pack(fill="x", pady=3)

        ttk.Label(
            right,
            text="IDENTIFICATION COULEUR",
            font=(MONO_FONT, 10, "bold")
        ).pack(anchor="w", pady=(6, 2))

        self.color_panel = tk.Text(
            right,
            height=7,
            wrap="word",
            font=(MONO_FONT, 9),
            relief="flat",
            padx=12,
            pady=8,
            borderwidth=1
        )
        self.color_panel.pack(fill="x", pady=(0, 4))

        self.arri_frame = ttk.LabelFrame(
            right,
            text=" MÉTADONNÉES IMAGE PAR IMAGE ARRI (MXF) ",
            padding=6
        )

        arri_nav = ttk.Frame(self.arri_frame)
        arri_nav.pack(fill="x", pady=(0, 4))

        ttk.Label(arri_nav, text="N° Image :").pack(side="left")
        self.arri_frame_entry = ttk.Entry(arri_nav, width=8, font=(MONO_FONT, 9))
        self.arri_frame_entry.pack(side="left", padx=(4, 4))
        self.arri_frame_entry.bind("<Return>", lambda e: self.jump_arri_frame())

        b_go_frame = RoundedButton(
            arri_nav, text="Aller", command=self.jump_arri_frame,
            radius=10, padx=10, pady=2
        )
        b_go_frame.pack(side="left", padx=(0, 12))
        self.buttons.append(b_go_frame)

        ttk.Label(arri_nav, text="Timecode :").pack(side="left")
        self.arri_tc_entry = ttk.Entry(arri_nav, width=14, font=(MONO_FONT, 9))
        self.arri_tc_entry.pack(side="left", padx=(4, 4))
        self.arri_tc_entry.bind("<Return>", lambda e: self.jump_arri_timecode())

        b_go_tc = RoundedButton(
            arri_nav, text="Chercher TC", command=self.jump_arri_timecode,
            radius=10, padx=10, pady=2
        )
        b_go_tc.pack(side="left")
        self.buttons.append(b_go_tc)

        self.arri_frame_info = tk.Text(
            self.arri_frame,
            height=4,
            wrap="word",
            font=(MONO_FONT, 9),
            relief="flat",
            padx=10,
            pady=6,
            borderwidth=1
        )
        self.arri_frame_info.pack(fill="x")

        self.details_label = ttk.Label(
            right,
            text="MÉTADONNÉES TECHNIQUES & TAGS DÉTECTÉS",
            font=(MONO_FONT, 10, "bold")
        )
        self.details_label.pack(anchor="w", pady=(6, 2))

        self.details = tk.Text(
            right,
            wrap="none",
            font=(MONO_FONT, 9),
            relief="flat",
            padx=12,
            pady=8,
            borderwidth=1,
            undo=True
        )
        self.details.pack(fill="both", expand=True)

        nav = ttk.Frame(right)
        nav.pack(fill="x", pady=8)

        b_prev = RoundedButton(
            nav,
            text="< Précédent",
            command=lambda: self.navigate(-1),
            radius=12,
            padx=12,
            pady=4
        )
        b_prev.pack(side="left")

        b_next = RoundedButton(
            nav,
            text="Suivant >",
            command=lambda: self.navigate(1),
            radius=12,
            padx=12,
            pady=4
        )
        b_next.pack(side="left", padx=6)

        self.buttons.extend([b_prev, b_next])

        self.counter = ttk.Label(
            nav,
            text="0 / 0",
            font=(MONO_FONT, 9, "bold")
        )
        self.counter.pack(side="right")

    def toggle_theme(self):
        new_theme = "light" if self.theme_name == "dark" else "dark"
        self.apply_theme(new_theme)
        if self.results and self.results[self.index] is not None:
            self.show(self.index)

    def apply_theme(self, name):
        self.theme_name = name
        t = THEMES[name]

        self.theme_btn.set_text(
            "Mode Clair" if name == "dark" else "Mode Sombre"
        )
        self.root.configure(bg=t["bg"])

        self.style.configure("TFrame", background=t["bg"])
        self.style.configure("TLabelframe", background=t["bg"], foreground=t["fg"])
        self.style.configure(
            "TLabelframe.Label",
            background=t["bg"],
            foreground=t["cst"],
            font=(MONO_FONT, 9, "bold")
        )
        self.style.configure("TPanedwindow", background=t["bg"])
        self.style.configure("TLabel", background=t["bg"], foreground=t["fg"], font=(MONO_FONT, 9))

        btn_bg = "#2c313c" if name == "dark" else "#e1e4e8"
        btn_hover = "#3e4451" if name == "dark" else "#d1d5da"

        for b in getattr(self, "buttons", []):
            b.update_colors(
                bg=btn_bg,
                hover_bg=btn_hover,
                fg=t["fg_bright"],
                canvas_bg=t["bg"]
            )

        self.drop_label.config(bg=t["drop_bg"], fg=t["drop_fg"])

        self.listbox.config(
            bg=t["panel"],
            fg=t["fg"],
            selectbackground=t["select_bg"],
            selectforeground=t["select_fg"]
        )

        for txt_widget in (self.summary, self.color_panel, self.arri_frame_info, self.details):
            txt_widget.config(
                bg=t["text_bg"],
                fg=t["fg"],
                insertbackground=t["fg_bright"],
                highlightbackground=t["border"]
            )

        for widget in (self.summary, self.color_panel, self.arri_frame_info, self.details):
            sz = 10 if widget == self.summary else 9
            widget.tag_configure("label", foreground=t["label"], font=(MONO_FONT, sz))
            widget.tag_configure("camera", foreground=t["camera"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("log", foreground=t["log"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("rec709", foreground=t["rec709"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("ok", foreground=t["ok"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("warn", foreground=t["warn"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("err", foreground=t["err"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("unknown", foreground=t["unknown"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("cst_title", foreground=t["cst"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("cst_body", foreground=t["fg_bright"], font=(MONO_FONT, sz))
            widget.tag_configure("xml_tag", foreground=t["xml_tag"], font=(MONO_FONT, sz))
            widget.tag_configure("mi_tag", foreground=t["mi_tag"], font=(MONO_FONT, sz))
            widget.tag_configure("exif_tag", foreground=t["exif_tag"], font=(MONO_FONT, sz))
            widget.tag_configure("val_normal", foreground=t["fg_bright"], font=(MONO_FONT, sz))

    def _show_status(self):
        et_txt = "ExifTool OK" if self.exiftool else "ExifTool MANQUANT"
        mi_txt = "MediaInfo OK" if HAS_PYMEDIAINFO else "pymediainfo manquant"
        self.status.config(text=f"{et_txt}  |  {mi_txt}")

    def pick(self):
        paths = filedialog.askopenfilenames(
            title="Choisir des fichiers vidéo",
            filetypes=[
                (
                    "Vidéos",
                    "*.mov *.mp4 *.mxf *.mkv *.avi *.mts *.m2ts "
                    "*.braw *.r3d *.crm *.ari *.mpg *.m4v"
                ),
                ("Tous les fichiers", "*.*")
            ]
        )
        if paths:
            self.add_files(paths)

    def pick_folder(self):
        folder = filedialog.askdirectory(
            title="Choisir un dossier de rushs (scan récursif)"
        )
        if folder:
            self.add_files([folder])

    def _drop(self, event):
        try:
            paths = self.root.tk.splitlist(event.data)
            self.add_files(paths)
        except Exception as e:
            messagebox.showerror("Glisser-déposer", str(e))

    def add_files(self, paths):
        added = []
        for raw_p in paths:
            p = Path(raw_p)
            if p.is_file():
                if p.suffix.lower() in VIDEO_EXTS:
                    added.append(str(p))
            elif p.is_dir():
                for child in sorted(p.rglob("*")):
                    if child.is_file() and child.suffix.lower() in VIDEO_EXTS:
                        added.append(str(child))

        if not added:
            messagebox.showinfo(
                "Aucune vidéo",
                "Aucun fichier vidéo compatible trouvé dans la sélection."
            )
            return

        seen = set(self.files)
        self.files.extend(p for p in added if p not in seen)

        self.listbox.delete(0, "end")
        for p in self.files:
            self.listbox.insert("end", Path(p).name)

        self.analyze()

    def analyze(self, force=False):
        if self.busy:
            return
        if force:
            self.cache.clear()
        if not self.files:
            self.pick()
            return
        if not self.exiftool:
            messagebox.showerror(
                "ExifTool requis",
                "ExifTool est introuvable. Fermez l’application puis "
                "relancez le lanceur Analyse Camera (.command sur Mac, "
                ".bat sur Windows) pour terminer son installation."
            )
            return

        self.busy = True
        self.status.config(text="Analyse en cours...")
        self.results = [None] * len(self.files)
        threading.Thread(target=self._worker, daemon=True).start()

    def _run_exiftool(self, path, deep=False):
        args = ["-json", "-G0:1:2:3", "-a", "-u"]
        if deep:
            args.append("-ee")
        args.extend([
            "-api", "RequestAll=3",
            "-charset", "filename=UTF8",
            str(path)
        ])

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False
        ) as tf:
            tf.write("\n".join(args))
            argfile = tf.name

        try:
            cmd = [self.exiftool, "-@", argfile]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300 if deep else 180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        finally:
            try:
                os.remove(argfile)
            except OSError:
                pass

        out = (proc.stdout or "").strip()
        if not out:
            raise RuntimeError(
                proc.stderr.strip()
                or f"ExifTool n'a renvoyé aucune donnée (code {proc.returncode})."
            )

        parsed = json.loads(out)
        data = parsed[0] if parsed else {}

        return {
            k: v for k, v in data.items()
            if not re.search(r":Doc(?:[2-9]|\d{2,}):", k)
        }

    def _run_mediainfo(self, path):
        if not HAS_PYMEDIAINFO:
            return {}

        try:
            mi = MediaInfo.parse(path)
            candidates = []

            for track in mi.tracks:
                if track.track_type != "Video":
                    continue
                data = track.to_data()
                width = data.get("width") or 0
                height = data.get("height") or 0
                try:
                    width = int(width)
                    height = int(height)
                except (ValueError, TypeError):
                    width = height = 0

                if width <= 0 or height <= 0:
                    continue

                attached = str(data.get("attached_pic", "")).lower() in ("yes", "true", "1")
                candidates.append((attached, width * height, data))

            if not candidates:
                return {}

            candidates.sort(key=lambda item: (not item[0], item[1]), reverse=True)
            selected = candidates[0][2]

            return {
                f"MI:{k}": v
                for k, v in selected.items()
                if v not in (None, "", []) and isinstance(v, (str, int, float))
            }
        except Exception:
            return {}

    def _worker(self):
        for i, path in enumerate(self.files):
            try:
                if path in self.cache:
                    self.q.put(("result", i, path, self.cache[path], None))
                    continue

                data = self._run_exiftool(path, deep=DEEP_SCAN)

                if not DEEP_SCAN and looks_dji(path, data):
                    try:
                        d2 = self._run_exiftool(path, deep=True)
                        d2["_Analyse:DeepPass"] = "ee"
                        data = d2
                    except Exception:
                        pass

                mi_data = self._run_mediainfo(path)
                data.update(mi_data)

                xml_file = find_sidecar_xml(path)
                if xml_file:
                    data["_Analyse:XMLPath"] = str(xml_file)
                    data.update(parse_sidecar_xml(xml_file))

                info = identify(data, path)

                arri_rows = None
                arri_error = None

                is_arri_mxf = (
                    Path(path).suffix.lower() == ".mxf"
                    and ("arri" in info["make"].lower() or "arri" in info["model"].lower())
                )

                if is_arri_mxf:
                    try:
                        arri_rows = analyze_arri_mxf(Path(path), self.exiftool)
                        data["_Analyse:ARRI"] = "Décodage ARRI réussi"
                        data["_Analyse:ARRIFrames"] = len(arri_rows)
                    except Exception as e:
                        arri_error = str(e)
                        data["_Analyse:ARRIError"] = arri_error

                result_data = {
                    "data": data,
                    "info": info,
                    "arri_rows": arri_rows,
                    "arri_error": arri_error,
                }

                self.cache[path] = result_data
                self.q.put(("result", i, path, result_data, None))

            except Exception as e:
                self.q.put(("result", i, path, {}, str(e)))

        self.q.put(("done",))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()

                if item[0] == "result":
                    _, i, path, result_data, error = item

                    if error:
                        self.results[i] = {
                            "path": path, "data": {}, "info": None,
                            "arri_rows": None, "arri_error": None, "error": error,
                        }
                    else:
                        self.results[i] = {
                            "path": path,
                            "data": result_data["data"],
                            "info": result_data["info"],
                            "arri_rows": result_data["arri_rows"],
                            "arri_error": result_data["arri_error"],
                            "error": None,
                        }

                    suffix = ""
                    if error:
                        suffix = "  [ERR]"
                    elif self.results[i]["arri_rows"] is not None:
                        suffix = "  [ARRI]"

                    self.listbox.delete(i)
                    self.listbox.insert(i, Path(path).name + suffix)
                    self.status.config(text=f"Analyse {i+1}/{len(self.files)}")

                elif item[0] == "done":
                    self.busy = False
                    self._show_status()
                    if self.results:
                        self.show(self.index if self.index < len(self.results) else 0)

        except queue.Empty:
            pass

        self.root.after(100, self._poll)

    def _select(self, event=None):
        sel = self.listbox.curselection()
        if sel:
            self.show(sel[0])

    def navigate(self, step):
        if not self.results:
            return
        self.show(max(0, min(len(self.results) - 1, self.index + step)))

    def _render_arri_frame(self, row):
        self.arri_frame_info.delete("1.0", "end")
        if not row:
            self.arri_frame_info.insert("end", "Aucune donnée image sélectionnée.\n", "label")
            return

        def fmt(val, unit=""):
            if val is None:
                return "N/A"
            if isinstance(val, float):
                val_s = f"{val:.2f}".rstrip("0").rstrip(".")
            else:
                val_s = str(val)
            return f"{val_s} {unit}".strip()

        self._insert_kv(self.arri_frame_info, "Image : ", row.get("Frame", "N/A"), "camera", "   |   ")
        self._insert_kv(self.arri_frame_info, "Timecode : ", row.get("Timecode", "N/A"), "ok", "\n")
        self._insert_kv(self.arri_frame_info, "EI : ", fmt(row.get("EI"), "ISO"), "val_normal", "   |   ")
        self._insert_kv(self.arri_frame_info, "WB : ", fmt(row.get("White_balance_K"), "K"), "val_normal", "   |   ")
        self._insert_kv(self.arri_frame_info, "Tint : ", fmt(row.get("Tint")), "val_normal", "\n")
        self._insert_kv(self.arri_frame_info, "Iris : ", fmt(row.get("Iris_T"), "T"), "val_normal", "   |   ")
        self._insert_kv(self.arri_frame_info, "Focale : ", fmt(row.get("Focal_length_mm"), "mm"), "val_normal", "   |   ")
        self._insert_kv(self.arri_frame_info, "Focus : ", fmt(row.get("Focus_mm"), "mm"), "val_normal", "   |   ")
        self._insert_kv(self.arri_frame_info, "ND : ", fmt(row.get("ND_density")), "val_normal", "\n")

    def jump_arri_frame(self):
        if not self.results or self.results[self.index] is None:
            return
        rows = self.results[self.index].get("arri_rows") or []
        if not rows:
            return
        try:
            target = int(self.arri_frame_entry.get().strip())
        except ValueError:
            messagebox.showwarning("N° Image", "Veuillez saisir un numéro d'image entier.")
            return

        match = next((r for r in rows if r.get("Frame") == target), None)
        if match is None:
            messagebox.showinfo("Image introuvable", f"Image {target} hors plage (0 à {len(rows)-1}).")
            return
        self._render_arri_frame(match)

    def jump_arri_timecode(self):
        if not self.results or self.results[self.index] is None:
            return
        rows = self.results[self.index].get("arri_rows") or []
        if not rows:
            return
        tc = self.arri_tc_entry.get().strip()
        if not tc:
            return
        match = next((r for r in rows if r.get("Timecode") == tc), None)
        if match is None:
            messagebox.showinfo("Timecode introuvable", f"Timecode {tc} introuvable dans ce clip.")
            return
        self.arri_frame_entry.delete(0, "end")
        self.arri_frame_entry.insert(0, str(match.get("Frame", "")))
        self._render_arri_frame(match)

    def _insert_kv(self, widget, label, value, val_tag="val_normal", suffix="\n"):
        widget.insert("end", label, "label")
        widget.insert("end", str(value) + suffix, val_tag)

    def _confidence_tag(self, confidence):
        c = (confidence or "").lower()
        if "confirm" in c:
            return "ok"
        if "prob" in c:
            return "warn"
        if "incohé" in c or "incoher" in c or "err" in c:
            return "err"
        return "unknown"

    def arri_consistent_value(self, rows, key, unit=""):
        values = []
        for row in rows:
            value = row.get(key)
            if value is not None and value not in values:
                values.append(value)

        if not values:
            return "Non renseigné"
        if len(values) > 1:
            return "Variable (voir image par image)"

        value = values[0]
        if isinstance(value, float):
            value = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{value} {unit}".strip()

    def show(self, i):
        if i >= len(self.results) or self.results[i] is None:
            return

        self.index = i
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        self.listbox.activate(i)

        r = self.results[i]
        p = r["path"]

        self.heading.config(text=Path(p).name)
        self.counter.config(text=f"{i+1} / {len(self.results)}")

        self.summary.delete("1.0", "end")
        self.color_panel.delete("1.0", "end")
        self.details.delete("1.0", "end")

        if r["error"] or not r["info"]:
            self.arri_frame.pack_forget()
            self.summary.insert("end", "ERREUR D'ANALYSE\n", "err")
            self.summary.insert("end", r["error"] or "Aucune donnée exploitable.", "val_normal")
            return

        x = r["info"]
        conf_tag = self._confidence_tag(x["confidence"])

        cam_tag = (
            "err"
            if "non identifié" in (x["make"] + x["model"]).lower()
            else "camera"
        )

        self._insert_kv(self.summary, "Caméra identifiée : ", x["camera_identified"], cam_tag, "\n")
        self._insert_kv(self.summary, "Profil identifié (confirmé) : ", x["confirmed_profile"], "ok" if x["confirmed_profile"] != "Aucun" else "unknown", "   |   ")
        self._insert_kv(self.summary, "Profil probable : ", x["probable_profile"], "warn" if x["probable_profile"] != "Aucun" else "unknown", "\n")
        self._insert_kv(self.summary, "Niveau de confiance : ", x["confidence"], conf_tag, "   |   ")
        self._insert_kv(self.summary, "Sidecar XML : ", x["xml_found"], "ok" if x["xml_found"].startswith("Oui") else "label", "\n")

        if x["inconsistencies"]:
            self.summary.insert("end", "Incohérences : ", "err")
            self.summary.insert("end", " / ".join(x["inconsistencies"]) + "\n", "err")
        else:
            self._insert_kv(self.summary, "Incohérences : ", "Aucune détectée", "ok", "\n")

        self._insert_kv(self.summary, "Détection : ", x["method"], "val_normal", "\n")

        self.summary.insert("end", "RECOMMANDATION CST / DAVINCI RESOLVE : ", "cst_title")
        self.summary.insert("end", x["action"], "cst_body")

        self._insert_kv(self.color_panel, "Espace colorimétrique : ", x["gamut"], conf_tag, "   |   ")
        self._insert_kv(self.color_panel, "Courbe gamma / transfert : ", x["transfer"], conf_tag, "\n")
        self._insert_kv(self.color_panel, "Matrice : ", x["matrix"], "val_normal", "   |   ")
        self._insert_kv(self.color_panel, "Plage vidéo : ", x["video_range"], "val_normal", "\n")
        self._insert_kv(self.color_panel, "Profondeur de couleur : ", x["bitdepth_only"], "val_normal", "   |   ")
        self._insert_kv(self.color_panel, "Sous-échantillonnage : ", x["chroma_only"], "val_normal", "\n")
        self._insert_kv(self.color_panel, "Source des informations : ", x["source_summary"], "camera", "   |   ")
        self._insert_kv(self.color_panel, "Niveau de confiance : ", x["confidence"], conf_tag, "\n")

        if x["inconsistencies"]:
            for inc in x["inconsistencies"]:
                self.color_panel.insert("end", f"! {inc}\n", "err")

        arri_rows = r.get("arri_rows")
        if arri_rows:
            self.arri_frame.pack(fill="x", pady=(0, 4), before=self.details_label)
            self._render_arri_frame(arri_rows[0])
        else:
            self.arri_frame.pack_forget()


        info_lines = [
            ("Résolution", x["resolution"], "val_normal"),
            ("Cadence", x["fps"], "val_normal"),
            ("Codec / Profil", x["codec"], "camera"),
            ("Profondeur / Chroma", x["bitdepth"], "val_normal"),
            ("ColorPrimaries", x["primaries"], "rec709" if "709" in x["primaries"] else "log"),
            ("TransferCharacteristics", x["transfer"], "rec709" if "709" in x["transfer"] else "log"),
            ("MatrixCoefficients", x["matrix"], "val_normal"),
        ]

        for k, v, vtag in info_lines:
            self.details.insert("end", f"{k:<25} ", "label")
            self.details.insert("end", f"{v}\n", vtag)

        if arri_rows:
            self.details.insert("end", "\n--- Synthèse tournage ARRI MXF ---\n", "cst_title")
            arri_fields = [
                ("Sensibilité EI", "EI", "ISO"),
                ("Balance des blancs", "White_balance_K", "K"),
                ("Teinte", "Tint", ""),
                ("Diaphragme", "Iris_T", "T"),
                ("Focale", "Focal_length_mm", "mm"),
                ("Mise au point", "Focus_mm", "mm"),
                ("Filtre ND", "ND_density", ""),
            ]
            for label, key, unit in arri_fields:
                val_str = self.arri_consistent_value(arri_rows, key, unit)
                self.details.insert("end", f"{label:<25} ", "label")
                self.details.insert("end", f"{val_str}\n", "val_normal")

        elif r.get("arri_error"):
            self.details.insert("end", "\n--- DÉCODEUR ARRI ---\n", "cst_title")
            self.details.insert("end", "Erreur de décodage : " + r["arri_error"] + "\n", "warn")

        self.details.insert(
            "end",
            "\n--- Tags détaillés (XML / MediaInfo / ExifTool) ---\n",
            "cst_title"
        )

        
        try:
            selected = all_values(
                r["data"],
                [
                    r"^XML:", r"^MI:", r"model", r"make", r"manufacturer",
                    r"device", r"software", r"handler", r"writing", r"android",
                    r"quicktime", r"application", r"supplier", r"log", r"gamut",
                    r"color", r"primar", r"transfer", r"gamma", r"codec",
                    r"lens", r"camera", r"dji", r"encoder", r"iso", r"white",
                    r"manufacturer", r"model", r"serial", r"device", r"camera",
                    r"recording", r"acquisition", r"capture", r"video", r"audio",
                    r"Sony", r"XAVC", r"HLG", r"Rec\.2100", r"BT\.709"
                ]
            )
            
            if not selected:
                self.details.insert("end", "Aucun tag technique détecté.\n", "warn")
                self.details.insert("end", "Note : Les métadonnées techniques peuvent être présentes dans les fichiers XML sidecar pour les caméras Sony/Canon.\n", "label")
            else:
                self.details.insert("end", f"Nombre de tags trouvés : {len(selected)}\n", "label")
                for k, v in selected[:250]:
                    if k.startswith("XML:"):
                        k_tag = "xml_tag"
                    elif k.startswith("MI:"):
                        k_tag = "mi_tag"
                    else:
                        k_tag = "exif_tag"

                    self.details.insert("end", f"{k}", k_tag)
                    self.details.insert("end", " = ", "label")
                    self.details.insert("end", f"{v}\n", "val_normal")
                    
        except Exception as e:
            self.details.insert("end", "\n=== ERREUR D'ANALYSE ===\n", "err")
            self.details.insert("end", f"Erreur lors de l'analyse : {str(e)}\n", "err")
            self.details.insert("end", "Le fichier est analysé mais des problèmes techniques empêchent l'affichage.\n", "label")
        
        self.details.insert("end", "\n--- FIN DU TRAITEMENT ---\n", "cst_title") 


    def _filter_files(self, *args):
        """Filtre la liste des fichiers selon la recherche et le filtre sélectionné."""
        search_term = self.search_var.get().lower()
        filter_type = self.filter_var.get()
        
        # Réinitialiser la liste
        self.listbox.delete(0, tk.END)
        
        # Filtrer les fichiers
        filtered_files = []
        for i, file_path in enumerate(self.files):
            file_name = Path(file_path).name
            
            # Vérifier le filtre de type
            should_show = True
            if filter_type != "Tous" and self.results:
                result = self.results[i]
                if result is not None:
                    confidence = result.get("confidence", "")
                    if filter_type == "Confirme" and "Confirmé" not in confidence:
                        should_show = False
                    elif filter_type == "Probable" and "Probable" not in confidence:
                        should_show = False
                    elif filter_type == "Erreur" and "Erreur" not in str(result):
                        should_show = False
            
            # Vérifier le terme de recherche
            if should_show and search_term in file_name.lower():
                filtered_files.append(file_path)
        
        # Afficher les fichiers filtrés
        for file_path in filtered_files:
            self.listbox.insert(tk.END, Path(file_path).name)

    def _reset_filters(self):
        """Réinitialise les filtres de recherche et de type."""
        self.search_var.set("")
        self.filter_var.set("Tous")
        self.listbox.delete(0, tk.END)
        for file_path in self.files:
            self.listbox.insert(tk.END, Path(file_path).name)

    def export_csv(self):
        valid = [r for r in self.results if r is not None]
        if not valid:
            messagebox.showwarning("Export CSV", "Aucune vidéo analysée à exporter.")
            return
        if self.busy:
            messagebox.showinfo("Analyse en cours", "Attendez la fin de l'analyse avant d'exporter.")
            return

        default_name = "Rapport_Cameras_Tournage.csv"
        if len(valid) == 1:
            default_name = Path(valid[0]["path"]).stem + "_rapport.csv"

        out = filedialog.asksaveasfilename(
            title="Enregistrer le tableau récapitulatif CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("Fichier CSV (Excel)", "*.csv"), ("Tous les fichiers", "*.*")]
        )
        if not out:
            return

        headers = [
            "Nom du fichier",
            "Fabricant",
            "Modèle",
            "Profil confirmé",
            "Profil probable",
            "Gamut",
            "Confiance",
            "Incohérences",
            "Résolution",
            "Cadence (FPS)",
            "Codec / Profil",
            "Profondeur / Chroma",
            "Fichier XML",
            "Recommandation CST DaVinci Resolve",
            "Chemin complet"
        ]

        try:
            with open(out, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(headers)

                for r in valid:
                    p = Path(r["path"])
                    if r["error"] or not r["info"]:
                        writer.writerow([
                            p.name, "ERREUR", "", "", "", "", "", "", "", "", "", "", "",
                            r["error"] or "Erreur", str(p)
                        ])
                    else:
                        x = r["info"]
                        writer.writerow([
                            p.name,
                            x["make"],
                            x["model"],
                            x["confirmed_profile"],
                            x["probable_profile"],
                            x["gamut"],
                            x["confidence"],
                            " | ".join(x["inconsistencies"]) if x["inconsistencies"] else "Aucune",
                            x["resolution"],
                            x["fps"],
                            x["codec"],
                            x["bitdepth"],
                            x["xml_found"],
                            x["action"],
                            str(p)
                        ])

            self.status.config(text=f"CSV exporté ({len(valid)} fichiers)")
            messagebox.showinfo(
                "Export CSV réussi",
                f"Le tableau récapitulatif de {len(valid)} vidéo(s) a été enregistré :\n{out}"
            )
        except Exception as e:
            messagebox.showerror("Erreur d'export CSV", str(e))

    def export_dump(self):
        if not self.results or self.results[self.index] is None:
            return

        r = self.results[self.index]
        out = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=Path(r["path"]).stem + "_exiftool_v5_8_0.json",
            filetypes=[("JSON", "*.json"), ("Tous les fichiers", "*.*")]
        )
        if not out:
            return

        dump = dict(r["data"])
        if r.get("info"):
            dump["_Analyse:Identification"] = r["info"]
        if r.get("arri_rows"):
            dump["_Analyse:ARRIFrames"] = r["arri_rows"]

        with open(out, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=1)

        self.status.config(text="Tags JSON exportés")

    def _reset_copy_button(self):
        t = THEMES[self.theme_name]
        btn_bg = "#2c313c" if self.theme_name == "dark" else "#e1e4e8"
        btn_hover = "#3e4451" if self.theme_name == "dark" else "#d1d5da"
        self.copy_btn.set_text("Copier rapport")
        self.copy_btn.update_colors(
            bg=btn_bg,
            hover_bg=btn_hover,
            fg=t["fg_bright"],
            canvas_bg=t["bg"]
        )
        self._copy_reset_after_id = None

    def copy_report(self):
        if not self.results or self.results[self.index] is None:
            return

        r = self.results[self.index]
        if r["error"]:
            text = f"{r['path']}\nErreur: {r['error']}"
        else:
            text = (
                f"Fichier: {r['path']}\n"
                + self.summary.get("1.0", "end")
                + "\n"
                + self.color_panel.get("1.0", "end")
                + "\n"
                + self.details.get("1.0", "end")
            )

        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.config(text="Rapport copié dans le presse-papiers")

        t = THEMES[self.theme_name]
        self.copy_btn.set_text("Copié !")
        self.copy_btn.update_colors(
            bg=t["ok"],
            hover_bg=t["ok"],
            fg="#14161a" if self.theme_name == "dark" else "#ffffff",
            canvas_bg=t["bg"]
        )

        if self._copy_reset_after_id is not None:
            self.root.after_cancel(self._copy_reset_after_id)
        self._copy_reset_after_id = self.root.after(2000, self._reset_copy_button)


def main():
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()

    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
