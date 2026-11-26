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

    for p in candidates:
        if p.is_file() and _exiftool_usable(p):
            return str(p)

    return shutil.which("exiftool") or shutil.which("exiftool.exe")


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
    row["Focus_
