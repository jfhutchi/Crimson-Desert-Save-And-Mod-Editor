"""Regenerate splash.png with the current APP_VERSION.

Run before PyInstaller build so the splash reflects the actual version:
    python tools/regen_splash.py

Reads version from updater.APP_VERSION — single source of truth.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw, ImageFont
from crimson.game_mods.updater import APP_VERSION


def build_splash() -> None:
    W, H = 520, 200
    img = Image.new('RGB', (W, H), (26, 26, 32))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, H - 1], outline=(255, 100, 40), width=2)
    try:
        title_f = ImageFont.truetype('arialbd.ttf', 32)
        sub_f = ImageFont.truetype('arial.ttf', 14)
        ver_f = ImageFont.truetype('arialbd.ttf', 18)
    except Exception:
        title_f = sub_f = ver_f = ImageFont.load_default()
    d.text((20, 20), 'Crimson Game Mods', font=title_f, fill=(255, 255, 255))
    d.text((20, 62), 'PAZ/pabgb modding toolkit', font=sub_f, fill=(180, 180, 180))
    d.text((20, 90), f'v{APP_VERSION}', font=ver_f, fill=(255, 100, 40))
    out = os.path.join(ROOT, 'splash.png')
    img.save(out)
    print(f'splash.png regenerated with v{APP_VERSION} -> {out}')


if __name__ == '__main__':
    build_splash()
