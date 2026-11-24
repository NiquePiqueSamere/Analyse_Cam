# -*- coding: utf-8 -*-
"""
Analyse Camera V5.3
Assistant d'identification camera (DJI, Sony, Canon, Fuji, etc.), profils couleur
et preparation CST pour DaVinci Resolve.
Combine ExifTool + pymediainfo + lecture automatique des fichiers Sidecar XML (Sony/Canon).
Inclut un mode sombre/clair, une coloration syntaxique et un export CSV compatible Excel.
"""
import csv
import json
import os
import re
import subprocess
import threading
import queue
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from pymediainfo import MediaInfo
    HAS_PYMEDIAINFO = True
except ImportError:
    HAS_PYMEDIAINFO = False

APP_VERSION = "5.3"
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
        "cst": "#d73a49",
        "xml_tag": "#6f42c1",
        "mi_tag": "#005cc5",
        "exif_tag": "#22863a",
    }
    
}
MONO_FONT = "Menlo" if sys.platform == "darwin" else "Consolas"


class RoundedButton(tk.Canvas):
    """Bouton moderne avec coins arrondis et effet de survol."""
    def __init__(self, parent, text, command=None, radius=16, padx=14, pady=6,
                 bg="#2c313c", hover_bg="#3e4451", fg="#ffffff", canvas_bg="#181a1f", font=None):
        self.font = font or (MONO_FONT, 9, "bold")
        tmp = tk.Label(parent, text=text, font=self.font)
        w = tmp.winfo_reqwidth() + padx * 2
        h = tmp.winfo_reqheight() + pady * 2
        tmp.destroy()

        super().__init__(parent, width=w, height=h, bg=canvas_bg, highlightthickness=0, cursor="hand2")
        self.command = command
        self.radius = radius
        self.bg_normal = bg
        self.bg_hover = hover_bg
        self.fg_color = fg
        self.text_str = text

        self._draw(self.bg_normal)
        self.bind("<Enter>", lambda e: self._draw(self.bg_hover))
        self.bind("<Leave>", lambda e: self._draw(self.bg_normal))
        self.bind("<Button-1>", self._on_click)

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
        self._round_rect(1, 1, w - 1, h - 1, self.radius, fill=fill_color, outline="")
        self.create_text(w // 2, h // 2, text=self.text_str, fill=self.fg_color, font=self.font)

    def _on_click(self, event):
        if self.command:
            self.command()

    def set_text(self, new_text):
        self.text_str = new_text
        self._draw(self.bg_normal)

    def update_colors(self, bg, hover_bg, fg, canvas_bg):
        self.bg_normal = bg
        self.bg_hover = hover_bg
        self.fg_color = fg
        self.configure(bg=canvas_bg)
        self._draw(self.bg_normal)

def norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def metadata_value(data, *names):
    for name in names:
        n = norm(name)
        for key, value in data.items():
            if norm(key.split(":")[-1]) == n and value not in (None, "", []):
                return ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return ""


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


def parse_sony_xml(xml_path):
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
    except Exception as e:
        extracted["XML:Error"] = str(e)
    return extracted


def find_exiftool():
    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / "exiftool.exe",
        Path.cwd() / "exiftool.exe",
        Path(sys.executable).parent / "exiftool.exe",
    ]
    # Cherche aussi dans d'eventuels sous-dossiers exiftool-* locaux ou dans Telechargements
    for folder in (base_dir, Path.home() / "Downloads"):
        if folder.is_dir():
            candidates.extend(folder.glob("exiftool*/exiftool.exe"))

    for p in candidates:
        if p.is_file():
            return str(p)
    return shutil.which("exiftool") or shutil.which("exiftool.exe")



def identify(data, path=None):
    make = metadata_value(data, "XML:Make", "Make", "Manufacturer", "CameraManufacturer")
    model = metadata_value(data, "XML:Model", "Model", "CameraModelName", "CameraModel", "AppleProappsModelname")
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
                if k.startswith("Protobuf:") and isinstance(v, str) and v.startswith("DJI ") and not v.startswith("DJI FC"):
                    model = v
                    break

    primaries = metadata_value(data, "MI:colour_primaries", "ColorPrimaries", "Primaries")
    transfer = metadata_value(data, "MI:transfer_characteristics", "TransferCharacteristics", "TransferFunction")
    matrix = metadata_value(data, "MI:matrix_coefficients", "MatrixCoefficients")
    codec = metadata_value(data, "MI:format", "CompressorID", "CodecID", "VideoCodec")
    format_profile = metadata_value(data, "MI:format_profile")
    bitdepth = metadata_value(data, "MI:bit_depth", "BitDepth", "BitsPerSample")
    chroma = metadata_value(data, "MI:chroma_subsampling", "ChromaSubsampling")
    width = metadata_value(data, "MI:width", "ImageWidth", "SourceImageWidth")
    height = metadata_value(data, "MI:height", "ImageHeight", "SourceImageHeight")
    fps = metadata_value(data, "MI:frame_rate", "VideoFrameRate", "FrameRate")

    tags = []
    for key, value in data.items():
        if key == "SourceFile" or key.split(":")[0] in ("File", "System", "ExifTool"):
            continue
        if isinstance(value, (str, int, float)):
            tags.append(f"{key}={value}")
    tagtext = " ".join(tags).lower()

    profile = ""
    gamut = ""
    confidence = "Indetermine"
    method = "Aucun tag de profil Log suffisamment explicite detecte."
    action = "Ne pas appliquer de CST camera au hasard. Verifier le menu image de la camera ou tester visuellement la courbe."

    sony_gamma = metadata_value(data, "XML:CaptureGammaEquation")
    sony_prim = metadata_value(data, "XML:CaptureColorPrimaries")
    if sony_gamma or "sony" in make.lower() or "xavc" in tagtext:
        if not make:
            make = "Sony"
        sg = (sony_gamma or tagtext).lower()
        sp = (sony_prim or tagtext).lower()

        if "s-log3" in sg or "slog3" in sg:
            profile = "Sony S-Log3"
            if "cine" in sp:
                gamut = "S-Gamut3.Cine"
            elif "s-gamut3" in sp or "sgamut3" in sp:
                gamut = "S-Gamut3"
            else:
                gamut = "S-Gamut3.Cine (probable - a verifier)"
            confidence = "Confirme" if sony_gamma else "Probable"
            method = f"Metadonnees Sony ({'Fichier Sidecar XML' if sony_gamma else 'Tags MP4'}) : {sony_gamma} / {sony_prim}".strip(" /")
            clean_gamut = gamut.split(" (")[0]
            action = f"CST DaVinci Resolve : Input Color Space = {clean_gamut}, Input Gamma = Sony S-Log3 -> Output = DaVinci Wide Gamut / DaVinci Intermediate."
        elif "s-log2" in sg or "slog2" in sg:
            profile = "Sony S-Log2"
            gamut = "S-Gamut"
            confidence = "Confirme" if sony_gamma else "Probable"
            method = "Indication Sony S-Log2 detectee."
            action = "CST DaVinci Resolve : Input Color Space = S-Gamut, Input Gamma = Sony S-Log2 -> DaVinci Wide Gamut / Intermediate."
        elif "rec709" in sg or "itu709" in sg or "s-cinetone" in sg:
            profile = "Sony S-Cinetone / Rec.709" if "cinetone" in sg else "Sony Rec.709 (Standard / PP Off)"
            gamut = "Rec.709"
            confidence = "Confirme" if sony_gamma else "Probable"
            method = f"XML Sony CaptureGammaEquation = {sony_gamma}" if sony_gamma else "Profil standard Sony detecte."
            action = "Signal deja en Rec.709 : pas de CST Log necessaire (ou CST Rec.709 -> DaVinci Wide Gamut si workflow DWG)."

    if dji and not profile:
        is_10bit = str(bitdepth) == "10" or "10" in str(format_profile)
        if "d-log m" in tagtext or "d-logm" in tagtext or "dlogm" in tagtext:
            profile = "DJI D-Log M"
            gamut = "Rec.709 (Encodage D-Log M)"
            confidence = "Confirme"
            method = "Tag explicite DJI D-Log M."
            action = "Utiliser la LUT officielle DJI D-Log M -> Rec.709 specifique au modele (" + (model or "DJI") + "). Ne pas utiliser de CST D-Log/D-Gamut classique."
        elif "d-log" in tagtext or "dlog" in tagtext:
            profile = "DJI D-Log"
            gamut = "DJI D-Gamut"
            confidence = "Confirme"
            method = "Tag explicite DJI D-Log."
            action = "CST DaVinci Resolve : Input Color Space = DJI D-Gamut, Input Gamma = DJI D-Log -> DaVinci Wide Gamut / Intermediate."
        elif "hlg" in tagtext or "arib-std-b67" in tagtext:
            profile = "DJI HLG (HDR)"
            gamut = "BT.2020"
            confidence = "Confirme"
            method = "Courbe de transfert HLG detectee."
            action = "CST DaVinci Resolve : Input Color Space = Rec.2020, Input Gamma = Rec.2100 HLG."
        elif is_10bit:
            profile = "DJI D-Log M (Tres probable - Video 10-bit)"
            gamut = "Rec.709 (Conteneur)"
            confidence = "Probable"
            method = f"Drone DJI encode en {bitdepth}-bit ({format_profile or codec}) sans tag HLG : sur DJI recent, le 10-bit correspond quasi systematiquement au mode D-Log M."
            action = f"Si l'image est plate/desaturee : appliquer la LUT officielle DJI [{model or 'Modele DJI'}] D-Log M to Rec.709. (Si l'image est deja contrastee, traiter en Rec.709)."
        else:
            profile = "DJI Normal (Rec.709)"
            gamut = "Rec.709"
            confidence = "Confirme"
            method = f"Drone DJI encode en {bitdepth or '8'}-bit avec flags BT.709 : le D-Log / D-Log M n'est pas disponible en 8-bit sur ces modeles."
            action = "Signal Rec.709 standard : aucun CST Log requis (ou CST Rec.709 -> DaVinci Wide Gamut / Intermediate)."

    if not profile:
        if "canonlogversion" in tagtext or re.search(r"canon.?log.?3|clog.?3", tagtext):
            profile = "Canon Log 3"
            gamut = "Cinema Gamut / BT.709 (verifier ColorSpace2)"
            confidence = "Confirme"
            method = "Tag Canon Log 3 detecte."
            action = "CST : Input Gamma = Canon Log 3, Input Color Space = Canon Cinema Gamut (ou Rec.709 selon reglage boitier)."
        elif "com.fujifilm.bt2020.f-log2" in tagtext or "f-log2" in tagtext:
            profile = "Fujifilm F-Log2"
            gamut = "F-Gamut / BT.2020"
            confidence = "Confirme"
            method = "Tag Fujifilm F-Log2 detecte."
            action = "CST : Input Color Space = Rec.2020 (ou Fujifilm F-Gamut), Input Gamma = Fujifilm F-Log2."
        elif "f-log" in tagtext:
            profile = "Fujifilm F-Log"
            gamut = "Fujifilm F-Gamut"
            confidence = "Probable"
            method = "Tag Fujifilm F-Log detecte."
            action = "CST : Input Color Space = Fujifilm F-Gamut, Input Gamma = Fujifilm F-Log."
        elif "v-log" in tagtext or "vlog" in tagtext:
            profile = "Panasonic V-Log"
            gamut = "V-Gamut"
            confidence = "Probable"
            method = "Tag Panasonic V-Log detecte."
            action = "CST : Input Color Space = Panasonic V-Gamut, Input Gamma = Panasonic V-Log."
        elif "apple log" in tagtext or "applelog" in tagtext:
            profile = "Apple Log"
            gamut = "Rec.2020"
            confidence = "Confirme"
            method = "Tag Apple Log detecte."
            action = "CST : Input Color Space = Rec.2020, Input Gamma = Apple Log."
        elif "hlg" in tagtext or "arib-std-b67" in tagtext:
            profile = "HLG (HDR)"
            gamut = "Rec.2020"
            confidence = "Probable"
            method = "Transfert HLG detecte."
            action = "CST : Input Color Space = Rec.2020, Input Gamma = Rec.2100 HLG."

    if not model:
        model = "Modele non identifie"
    if not make:
        make = "Fabricant non identifie"

    tech_bit = f"{bitdepth} bits" if bitdepth and "bit" not in str(bitdepth).lower() else (bitdepth or "Non renseigne")
    if chroma:
        tech_bit += f" ({chroma})"

    return {
        "make": make,
        "model": model,
        "profile": profile or "Non identifie",
        "gamut": gamut or "Non identifie",
        "confidence": confidence,
        "method": method,
        "action": action,
        "primaries": primaries or "Non renseigne",
        "transfer": transfer or "Non renseigne",
        "matrix": matrix or "Non renseigne",
        "codec": f"{codec} ({format_profile})" if codec and format_profile else (codec or "Non renseigne"),
        "bitdepth": tech_bit,
        "resolution": (f"{width} x {height}" if width and height else "Non renseignee"),
        "fps": fps or "Non renseignee",
        "xml_found": "Oui (" + Path(data["_Analyse:XMLPath"]).name + ")" if data.get("_Analyse:XMLPath") else "Non"
    }
class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Analyse Camera - V{APP_VERSION} (ExifTool + MediaInfo + XML + Export CSV)")
        self.root.geometry("1220x800")
        self.root.minsize(920, 640)
        self.files = []
        self.results = []
        self.index = 0
        self.q = queue.Queue()
        self.busy = False
        self.cache = {}
        self.theme_name = "dark"

        self.exiftool = find_exiftool()

        self._build()
        self.apply_theme("dark")
        self.root.after(100, self._poll)
        try:
            from tkinterdnd2 import DND_FILES
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._drop)
            self.drop_enabled = True
        except Exception:
            self.drop_enabled = False
            self.drop_label.config(text="Glisser-depose des videos ou dossiers ici (lance via le .BAT pour activer le glisser-deposer)")
        self._show_status()

    def _build(self):
        self.style = ttk.Style()
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")

        self.buttons = []
        self.top = ttk.Frame(self.root, padding=10)
        self.top.pack(fill="x")

        btn_specs = [
            ("Importer videos...", self.pick),
            ("Importer dossier...", self.pick_folder),
            ("Reanalyser", lambda: self.analyze(force=True)),
            ("Copier rapport", self.copy_report),
            ("Exporter CSV...", self.export_csv),
            ("JSON...", self.export_dump),
        ]
        for label, cmd in btn_specs:
            b = RoundedButton(self.top, text=label, command=cmd)
            b.pack(side="left", padx=(0, 6))
            self.buttons.append(b)

        self.theme_btn = RoundedButton(self.top, text="Mode Clair", command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=(12, 0))
        self.buttons.append(self.theme_btn)

        self.status = ttk.Label(self.top, text="Pret", font=(MONO_FONT, 9))
        self.status.pack(side="right")

        self.drop_label = tk.Label(
            self.root,
            text="[ Glisse-depose tes videos ou dossiers de rushs (DJI, Sony, Canon...) ici ]",
            anchor="center", pady=10, font=(MONO_FONT, 9, "bold")
        )
        self.drop_label.pack(fill="x", padx=10, pady=(0, 8))

        self.body = ttk.Panedwindow(self.root, orient="horizontal")
        self.body.pack(fill="both", expand=True, padx=10, pady=5)

        left = ttk.Frame(self.body, padding=5)
        self.body.add(left, weight=1)
        ttk.Label(left, text="FICHIERS ANALYSES", font=(MONO_FONT, 10, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(left, width=36, activestyle="dotbox", borderwidth=1, highlightthickness=0, font=(MONO_FONT, 9))
        self.listbox.pack(fill="both", expand=True, pady=5)
        self.listbox.bind("<<ListboxSelect>>", self._select)

        right = ttk.Frame(self.body, padding=8)
        self.body.add(right, weight=4)
        self.heading = ttk.Label(right, text="Aucun fichier charge", font=(MONO_FONT, 13, "bold"), wraplength=780)
        self.heading.pack(anchor="w", pady=(0, 8))

        self.summary = tk.Text(right, height=13, wrap="word", font=(MONO_FONT, 10), relief="flat", padx=12, pady=10, borderwidth=1)
        self.summary.pack(fill="x", pady=4)

        ttk.Label(right, text="METADONNEES TECHNIQUES & TAGS DETECTES", font=(MONO_FONT, 10, "bold")).pack(anchor="w", pady=(8, 2))
        self.details = tk.Text(right, wrap="none", font=(MONO_FONT, 9), relief="flat", padx=12, pady=10, borderwidth=1)
        self.details.pack(fill="both", expand=True)

        nav = ttk.Frame(right)
        nav.pack(fill="x", pady=8)
        b_prev = RoundedButton(nav, text="< Precedent", command=lambda: self.navigate(-1), radius=12, padx=12, pady=4)
        b_prev.pack(side="left")
        b_next = RoundedButton(nav, text="Suivant >", command=lambda: self.navigate(1), radius=12, padx=12, pady=4)
        b_next.pack(side="left", padx=6)
        self.buttons.extend([b_prev, b_next])

        self.counter = ttk.Label(nav, text="0 / 0", font=(MONO_FONT, 9, "bold"))
        self.counter.pack(side="right")

    def toggle_theme(self):
        new_theme = "light" if self.theme_name == "dark" else "dark"
        self.apply_theme(new_theme)
        if self.results and self.results[self.index] is not None:
            self.show(self.index)

    def apply_theme(self, name):
        self.theme_name = name
        t = THEMES[name]
        self.theme_btn.set_text("Mode Clair" if name == "dark" else "Mode Sombre")

        self.root.configure(bg=t["bg"])
        self.style.configure("TFrame", background=t["bg"])
        self.style.configure("TPanedwindow", background=t["bg"])
        self.style.configure("TLabel", background=t["bg"], foreground=t["fg"], font=(MONO_FONT, 9))

        btn_bg = "#2c313c" if name == "dark" else "#e1e4e8"
        btn_hover = "#3e4451" if name == "dark" else "#d1d5da"
        for b in getattr(self, "buttons", []):
            b.update_colors(bg=btn_bg, hover_bg=btn_hover, fg=t["fg_bright"], canvas_bg=t["bg"])

        self.drop_label.config(bg=t["drop_bg"], fg=t["drop_fg"])
        self.listbox.config(bg=t["panel"], fg=t["fg"], selectbackground=t["select_bg"], selectforeground=t["select_fg"])
        self.summary.config(bg=t["text_bg"], fg=t["fg"], insertbackground=t["fg_bright"], highlightbackground=t["border"])
        self.details.config(bg=t["text_bg"], fg=t["fg"], insertbackground=t["fg_bright"], highlightbackground=t["border"])

        for widget in (self.summary, self.details):
            sz = 10 if widget == self.summary else 9
            widget.tag_configure("label", foreground=t["label"], font=(MONO_FONT, sz, "bold") if widget == self.summary else (MONO_FONT, sz))
            widget.tag_configure("camera", foreground=t["camera"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("log", foreground=t["log"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("rec709", foreground=t["rec709"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("ok", foreground=t["ok"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("warn", foreground=t["warn"], font=(MONO_FONT, sz, "bold"))
            widget.tag_configure("err", foreground=t["err"], font=(MONO_FONT, sz, "bold"))
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
        paths = filedialog.askopenfilenames(title="Choisir des fichiers video", filetypes=[
            ("Videos", "*.mov *.mp4 *.mxf *.mkv *.avi *.mts *.m2ts *.braw *.r3d *.crm *.ari *.mpg *.m4v"),
            ("Tous les fichiers", "*.*")])
        if paths:
            self.add_files(paths)

    def pick_folder(self):
        folder = filedialog.askdirectory(title="Choisir un dossier de rushs (scan recursif)")
        if folder:
            self.add_files([folder])

    def _drop(self, event):
        try:
            paths = self.root.tk.splitlist(event.data)
            self.add_files(paths)
        except Exception as e:
            messagebox.showerror("Glisser-deposer", str(e))

    def add_files(self, paths):
        added = []
        for raw_p in paths:
            p = Path(raw_p)
            if p.is_file():
                added.append(str(p))
            elif p.is_dir():
                for child in sorted(p.rglob("*")):
                    if child.is_file() and child.suffix.lower() in VIDEO_EXTS:
                        added.append(str(child))
        if not added:
            messagebox.showinfo("Aucune video", "Aucun fichier video compatible trouve dans la selection.")
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
            messagebox.showerror("ExifTool requis", "ExifTool est introuvable. Lancez Lancer_Analyse_Camera_V5.bat pour l'installer automatiquement.")
            return
        self.busy = True
        self.status.config(text="Analyse en cours...")
        self.results = [None] * len(self.files)
        threading.Thread(target=self._worker, daemon=True).start()

    def _run_exiftool(self, path, deep=False):
        import tempfile
        args = ["-json", "-G0:1:2:3", "-a", "-u"]
        if deep:
            args.append("-ee")
        args.extend(["-api", "RequestAll=3", "-charset", "filename=UTF8", str(path)])

        # Ecrit les arguments dans un fichier UTF-8 pour eviter la corruption des accents (ex: Côte Brune) sous Windows
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as tf:
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
            raise RuntimeError(proc.stderr.strip() or f"ExifTool n'a renvoye aucune donnee (code {proc.returncode}).")
        parsed = json.loads(out)
        data = parsed[0] if parsed else {}
        return {k: v for k, v in data.items() if not re.search(r":Doc(?:[2-9]|\d{2,}):", k)}


    def _run_mediainfo(self, path):
        if not HAS_PYMEDIAINFO:
            return {}
        try:
            mi = MediaInfo.parse(path)
            mi_data = {}
            for track in mi.tracks:
                if track.track_type == "Video":
                    for k, v in track.to_data().items():
                        if v not in (None, "", []) and isinstance(v, (str, int, float)):
                            mi_data[f"MI:{k}"] = v
                    break
            return mi_data
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
                    data.update(parse_sony_xml(xml_file))

                self.cache[path] = data
                self.q.put(("result", i, path, data, None))
            except Exception as e:
                self.q.put(("result", i, path, {}, str(e)))
        self.q.put(("done",))
    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "result":
                    _, i, path, data, error = item
                    self.results[i] = {"path": path, "data": data, "error": error, "info": identify(data, path) if (data and not error) else None}
                    self.listbox.delete(i)
                    self.listbox.insert(i, Path(path).name + ("  [ERR]" if error else ""))
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

    def _insert_kv(self, widget, label, value, val_tag="val_normal", suffix="\n"):
        widget.insert("end", label, "label")
        widget.insert("end", value + suffix, val_tag)

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
        self.details.delete("1.0", "end")

        x = r["info"]
        if r["error"] or not x:
            self.summary.insert("end", "ERREUR D'ANALYSE\n", "err")
            self.summary.insert("end", r["error"] or "Aucune donnee exploitable.", "val_normal")
            return

        cam_tag = "err" if "non identifie" in (x["make"] + x["model"]).lower() else "camera"
        self._insert_kv(self.summary, "Fabricant : ", x["make"], cam_tag, "   |   ")
        self._insert_kv(self.summary, "Modele : ", x["model"], cam_tag, "\n")

        prof_low = x["profile"].lower()
        if "log" in prof_low or "hlg" in prof_low:
            prof_tag = "log"
        elif "709" in prof_low or "normal" in prof_low or "cinetone" in prof_low:
            prof_tag = "rec709"
        else:
            prof_tag = "err"
        self._insert_kv(self.summary, "Profil detecte : ", x["profile"], prof_tag, "\n")

        conf_low = x["confidence"].lower()
        conf_tag = "ok" if "confirme" in conf_low else ("warn" if "probable" in conf_low else "err")
        self._insert_kv(self.summary, "Gamut : ", x["gamut"], prof_tag, "   |   ")
        self._insert_kv(self.summary, "Confiance : ", x["confidence"], conf_tag, "\n")

        xml_tag = "ok" if x["xml_found"].startswith("Oui") else "label"
        self._insert_kv(self.summary, "Fichier Sidecar XML : ", x["xml_found"], xml_tag, "\n")
        self._insert_kv(self.summary, "Detection : ", x["method"], "val_normal", "\n\n")

        self.summary.insert("end", "REGLAGE / CONSEIL POUR DAVINCI RESOLVE :\n", "cst_title")
        self.summary.insert("end", x["action"], "cst_body")

        bit_tag = "ok" if "10 bit" in x["bitdepth"].lower() or "12 bit" in x["bitdepth"].lower() else ("warn" if "8 bit" in x["bitdepth"].lower() else "val_normal")
        info_lines = [
            ("Resolution", x["resolution"], "val_normal"),
            ("Cadence", x["fps"], "val_normal"),
            ("Codec / Profil", x["codec"], "camera"),
            ("Profondeur / Chroma", x["bitdepth"], bit_tag),
            ("ColorPrimaries", x["primaries"], "rec709" if "709" in x["primaries"] else "log"),
            ("TransferCharacteristics", x["transfer"], "rec709" if "709" in x["transfer"] else "log"),
            ("MatrixCoefficients", x["matrix"], "val_normal"),
        ]
        for k, v, vtag in info_lines:
            self.details.insert("end", f"{k:<25} ", "label")
            self.details.insert("end", f"{v}\n", vtag)

        self.details.insert("end", "\n--- Tags detailles (XML / MediaInfo / ExifTool) ---\n", "cst_title")
        selected = all_values(r["data"], [r"^XML:", r"^MI:", r"model", r"make", r"log", r"gamut", r"color", r"primar", r"transfer", r"gamma", r"codec", r"lens", r"camera", r"dji", r"encoder", r"iso", r"white"])
        for k, v in selected[:250]:
            if k.startswith("XML:"):
                k_tag = "xml_tag"
            elif k.startswith("MI:"):
                k_tag = "mi_tag"
            else:
                k_tag = "exif_tag"

            v_low = v.lower()
            if any(w in v_low for w in ("s-log", "slog", "d-log", "dlog", "f-log", "v-log", "clog", "logc", "s-gamut")):
                v_tag = "log"
            elif any(w in v_low for w in ("709", "cinetone")):
                v_tag = "rec709"
            elif any(w in v_low for w in ("dji", "sony", "canon", "fuji", "panasonic", "arri", "red")):
                v_tag = "camera"
            else:
                v_tag = "val_normal"

            self.details.insert("end", f"{k}", k_tag)
            self.details.insert("end", " = ", "label")
            self.details.insert("end", f"{v}\n", v_tag)

    def export_csv(self):
        valid = [r for r in self.results if r is not None]
        if not valid:
            messagebox.showwarning("Export CSV", "Aucune video analysee a exporter.")
            return
        if self.busy:
            messagebox.showinfo("Analyse en cours", "Attendez la fin de l'analyse avant d'exporter.")
            return

        default_name = "Rapport_Cameras_Tournage.csv"
        if len(valid) == 1:
            default_name = Path(valid[0]["path"]).stem + "_rapport.csv"

        out = filedialog.asksaveasfilename(
            title="Enregistrer le tableau recapitulatif CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("Fichier CSV (Excel)", "*.csv"), ("Tous les fichiers", "*.*")]
        )
        if not out:
            return

        headers = [
            "Nom du fichier", "Fabricant", "Modele", "Profil couleur",
            "Gamut", "Confiance", "Resolution", "Cadence (FPS)",
            "Codec / Profil", "Profondeur / Chroma", "Fichier XML",
            "Reglage CST DaVinci Resolve", "Chemin complet"
        ]

        try:
            with open(out, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(headers)
                for r in valid:
                    p = Path(r["path"])
                    if r["error"] or not r["info"]:
                        writer.writerow([p.name, "ERREUR", "", "", "", "", "", "", "", "", "", r["error"] or "Erreur", str(p)])
                    else:
                        x = r["info"]
                        writer.writerow([
                            p.name, x["make"], x["model"], x["profile"],
                            x["gamut"], x["confidence"], x["resolution"], x["fps"],
                            x["codec"], x["bitdepth"], x["xml_found"],
                            x["action"], str(p)
                        ])
            self.status.config(text=f"CSV exporte ({len(valid)} fichiers)")
            messagebox.showinfo("Export CSV reussi", f"Le tableau recapitulatif de {len(valid)} video(s) a ete enregistre :\n{out}")
        except Exception as e:
            messagebox.showerror("Erreur d'export CSV", str(e))

    def export_dump(self):
        if not self.results or self.results[self.index] is None:
            return
        r = self.results[self.index]
        out = filedialog.asksaveasfilename(defaultextension=".json", initialfile=Path(r["path"]).stem + "_exiftool_v5.json",
                                           filetypes=[("JSON", "*.json"), ("Tous les fichiers", "*.*")])
        if not out:
            return
        with open(out, "w", encoding="utf-8") as f:
            json.dump(r["data"], f, ensure_ascii=False, indent=1)
        self.status.config(text="Tags JSON exportes")

    def copy_report(self):
        if not self.results or self.results[self.index] is None:
            return
        r = self.results[self.index]
        if r["error"]:
            text = f"{r['path']}\nErreur: {r['error']}"
        else:
            text = f"Fichier: {r['path']}\n" + self.summary.get("1.0", "end") + "\n\n" + self.details.get("1.0", "end")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.config(text="Rapport copie dans le presse-papiers")


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
