"""Генерирует иконки (белые PNG на прозрачном фоне) в assets/icons.
Запуск:  python tools/gen_icons.py   (нужен Pillow)
Приложение красит их в нужный цвет само."""
import os
from PIL import Image, ImageDraw

K = 16                      # масштаб рисования (потом уменьшаем — получается гладко)
S = 24 * K
OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "icons")
W = (255, 255, 255, 255)
os.makedirs(OUT, exist_ok=True)


def P(x, y):
    return (x * K, y * K)


def new():
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def quad(p0, c, p1, n=14):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t ** 2 * p1[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t ** 2 * p1[1]
        pts.append((x, y))
    return pts


def line(d, pts, w):
    pts = [P(*p) for p in pts]
    d.line(pts, fill=W, width=int(w * K), joint="curve")
    r = w * K / 2
    for x, y in pts:
        d.ellipse([x - r, y - r, x + r, y + r], fill=W)


def poly(d, pts):
    d.polygon([P(*p) for p in pts], fill=W)


def rrect(d, x, y, w, h, r):
    d.rounded_rectangle([x * K, y * K, (x + w) * K, (y + h) * K], radius=r * K, fill=W)


def dot(d, cx, cy, rx, ry=None):
    ry = rx if ry is None else ry
    d.ellipse([(cx - rx) * K, (cy - ry) * K, (cx + rx) * K, (cy + ry) * K], fill=W)


def ring(d, cx, cy, rx, ry, w):
    d.ellipse([(cx - rx) * K, (cy - ry) * K, (cx + rx) * K, (cy + ry) * K],
              outline=W, width=int(w * K))


def speaker(d):
    poly(d, [(3.5, 9.5), (7.5, 9.5), (12.5, 5), (12.5, 19), (7.5, 14.5), (3.5, 14.5)])


ICONS = {}


def icon(name):
    def deco(fn):
        ICONS[name] = fn
        return fn
    return deco


@icon("play")
def _(d): poly(d, [(8, 5), (19, 12), (8, 19)])


@icon("pause")
def _(d):
    rrect(d, 6, 5, 4, 14, 1.2)
    rrect(d, 14, 5, 4, 14, 1.2)


@icon("prev")
def _(d):
    rrect(d, 5, 5, 2.4, 14, 1)
    poly(d, [(19, 5.5), (9, 12), (19, 18.5)])


@icon("next")
def _(d):
    rrect(d, 16.6, 5, 2.4, 14, 1)
    poly(d, [(5, 5.5), (15, 12), (5, 18.5)])


def repeat(d):
    line(d, [(3, 12), (3, 10)] + quad((3, 10), (3, 7), (6, 7)) + [(21, 7)], 1.9)
    line(d, [(17.5, 3.5), (21, 7), (17.5, 10.5)], 1.9)
    line(d, [(21, 12), (21, 14)] + quad((21, 14), (21, 17), (18, 17)) + [(3, 17)], 1.9)
    line(d, [(6.5, 13.5), (3, 17), (6.5, 20.5)], 1.9)


@icon("repeat")
def _(d): repeat(d)


@icon("repeat1")
def _(d):
    repeat(d)
    line(d, [(10.8, 10.2), (12.5, 8.8), (12.5, 15.2)], 1.5)


@icon("volume")
def _(d):
    speaker(d)
    line(d, quad((16, 9), (18.6, 12), (16, 15)), 1.8)
    line(d, quad((18.6, 6.4), (23.4, 12), (18.6, 17.6)), 1.8)


@icon("plus")
def _(d):
    line(d, [(12, 5), (12, 19)], 2.2)
    line(d, [(5, 12), (19, 12)], 2.2)


@icon("pencil")
def _(d):
    line(d, [(4, 20), (4.8, 15.2), (15.5, 4.5), (19.5, 8.5), (8.8, 19.2), (4, 20)], 1.8)
    line(d, [(13, 7), (17, 11)], 1.8)


@icon("trash")
def _(d):
    line(d, [(4, 7), (20, 7)], 1.8)
    line(d, [(9, 7), (9, 4), (15, 4), (15, 7)], 1.8)
    line(d, [(6, 7), (7, 20), (17, 20), (18, 7)], 1.8)
    line(d, [(10.2, 11), (10.2, 16)], 1.8)
    line(d, [(13.8, 11), (13.8, 16)], 1.8)


@icon("globe")
def _(d):
    ring(d, 12, 12, 9, 9, 1.7)
    ring(d, 12, 12, 4, 9, 1.7)
    line(d, [(3, 12), (21, 12)], 1.7)
    line(d, [(4.5, 8), (19.5, 8)], 1.7)
    line(d, [(4.5, 16), (19.5, 16)], 1.7)


@icon("search")
def _(d):
    ring(d, 10.5, 10.5, 6.3, 6.3, 2.1)
    line(d, [(15.2, 15.2), (20.5, 20.5)], 2.1)


@icon("download")
def _(d):
    line(d, [(12, 4), (12, 15)], 2.0)
    line(d, [(7.5, 10.8), (12, 15.3), (16.5, 10.8)], 2.0)
    line(d, [(5, 19.5), (19, 19.5)], 2.0)


@icon("check")
def _(d): line(d, [(5, 12.5), (10, 17.5), (19, 7)], 2.6)


@icon("note")
def _(d):
    dot(d, 7.5, 18, 3.2, 2.6)
    dot(d, 16.5, 16, 3.2, 2.6)
    line(d, [(10.4, 17.5), (10.4, 5.5)], 2.0)
    line(d, [(19.4, 15.5), (19.4, 3.5)], 2.0)
    poly(d, [(10.4, 5.5), (19.4, 3.5), (19.4, 7.5), (10.4, 9.5)])


for _i, _hs in enumerate([(0.9, 0.4, 0.65), (0.45, 0.95, 0.35), (0.7, 0.5, 1.0)]):
    def _mk(hs=_hs):
        def f(d):
            for i, hh in enumerate(hs):
                h = hh * 16
                rrect(d, 4.5 + i * 5.5, 20 - h, 3.6, h, 1.2)
        return f
    ICONS[f"bars{_i}"] = _mk()


@icon("list")
def _(d):
    for y in (7, 12, 17):
        line(d, [(9, y), (20, y)], 2.0)
        dot(d, 4.8, y, 1.3)


@icon("more")
def _(d):
    for y in (5.5, 12, 18.5):
        dot(d, 12, y, 1.9)


@icon("back")
def _(d): line(d, [(15, 5), (8, 12), (15, 19)], 2.4)



@icon("image")
def _(d):
    line(d, [(4.5, 5), (19.5, 5), (21, 6.5), (21, 17.5), (19.5, 19), (4.5, 19), (3, 17.5), (3, 6.5), (4.5, 5)], 1.8)
    line(d, [(3.5, 17.5), (9, 12), (13, 16), (16, 13), (20.5, 17.5)], 1.8)
    dot(d, 8.2, 9.8, 1.7)


@icon("settings")
def _(d):
    import math
    dot(d, 12, 12, 7.3)
    for k in range(8):
        a = k * math.pi / 4
        line(d, [(12 + 7.0 * math.cos(a), 12 + 7.0 * math.sin(a)),
                 (12 + 9.4 * math.cos(a), 12 + 9.4 * math.sin(a))], 3.6)
    d.ellipse([(12 - 3.2) * K, (12 - 3.2) * K, (12 + 3.2) * K, (12 + 3.2) * K],
              fill=(0, 0, 0, 0))     # отверстие


@icon("close")
def _(d):
    line(d, [(6, 6), (18, 18)], 2.2)
    line(d, [(18, 6), (6, 18)], 2.2)


for name, fn in ICONS.items():
    im, d = new()
    fn(d)
    im.resize((96, 96), Image.LANCZOS).save(os.path.join(OUT, name + ".png"))

# иконка приложения: зелёный квадрат с нотой
size = 512
big = Image.new("RGBA", (size, size), (0, 0, 0, 0))
g = ImageDraw.Draw(big)
for y in range(size):
    t = y / size
    c = (int(59 + (15 - 59) * t), int(228 + (157 - 228) * t), int(119 + (71 - 119) * t), 255)
    g.line([(0, y), (size, y)], fill=c)
mask = Image.new("L", (size, size), 0)
ImageDraw.Draw(mask).rounded_rectangle([16, 16, size - 16, size - 16], radius=110, fill=255)
big.putalpha(mask)
note, nd = new()
ICONS["note"](nd)
alpha = note.getchannel("A").resize((300, 300), Image.LANCZOS)
dark = Image.new("RGBA", (300, 300), (6, 33, 15, 255))
big.paste(dark, (106, 106), alpha)
ROOT = os.path.join(os.path.dirname(__file__), "..")
big.save(os.path.join(ROOT, "icon.png"))

import base64
with open(os.path.join(ROOT, "icons_data.py"), "w", encoding="utf-8") as f:
    f.write('"""Иконки приложения (PNG в base64). Создано tools/gen_icons.py."""\nICONS_B64 = {\n')
    for name in sorted(ICONS):
        v = base64.b64encode(open(os.path.join(OUT, name + ".png"), "rb").read()).decode()
        f.write(f'    "{name}": (\n')
        for i in range(0, len(v), 96):
            f.write(f'        "{v[i:i+96]}"\n')
        f.write("    ),\n")
    f.write("}\n")
print("готово:", len(ICONS), "иконок")
