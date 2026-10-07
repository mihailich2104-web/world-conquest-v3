# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onefile, windowed, имя WorldConquest.

По умолчанию GeoPandas в exe НЕ включается (игра читает GeoJSON напрямую через json —
exe получается в разы меньше и надёжнее). Чтобы включить: WC_BUNDLE_GEOPANDAS=1.
"""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
icon = ROOT / "assets" / "icons" / "game.ico"
bundle_geo = os.environ.get("WC_BUNDLE_GEOPANDAS") == "1"

hidden = ["pygame", "pygame_gui", "core", "map", "ui", "tutorial", "net", "core.session", "core.events",
          "core.clock", "core.commands", "net.host", "net.client", "net.discovery", "net.protocol"] \
    + collect_submodules("pygame_gui")
excludes = ["tkinter", "matplotlib", "scipy", "IPython"]
datas = [(str(ROOT / "src" / "data"), "src/data"), (str(ROOT / "assets"), "assets")] \
    + collect_data_files("pygame_gui")
if bundle_geo:
    hidden += ["geopandas", "shapely", "fiona", "pyogrio", "pyproj"] + collect_submodules("shapely")
    datas += collect_data_files("pyproj") + collect_data_files("pyogrio")
else:
    excludes += ["geopandas", "pandas", "shapely", "fiona", "pyogrio", "pyproj"]

a = Analysis(
    [str(ROOT / "src" / "main.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hidden,
    excludes=excludes,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="WorldConquest",
    console=False,          # windowed
    icon=str(icon) if icon.exists() else None,
)
