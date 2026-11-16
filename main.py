"""Мой плеер — версия для Android (Kivy).

Запуск на компьютере для проверки:  python main.py
Сборка APK: см. buildozer.spec (buildozer android debug).
"""
import colorsys
import json
import math
import os
import re
import random
import shutil
import ssl
import threading
import time
import traceback
import uuid
import urllib.request
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode, quote, urlparse, unquote

from kivy.animation import Animation
from kivy.app import App
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock, mainthread
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics import (Color, Ellipse, Line, Mesh, PopMatrix, PushMatrix, Rectangle,
                           RoundedRectangle, Rotate)
from kivy.graphics.texture import Texture
from kivy.lang import Builder
from kivy.logger import Logger
from kivy.metrics import dp, sp
from kivy.properties import (BooleanProperty, ListProperty, NumericProperty,
                             ObjectProperty, StringProperty)
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import Screen, SlideTransition
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex, platform

try:
    import mutagen
except Exception:          # без mutagen просто не будет встроенных обложек
    mutagen = None

ON_ANDROID = platform == "android"
BASE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(BASE, "assets", "icons")   # на старте заменяется на папку приложения
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
SPEEDS = [0.75, 1.0, 1.25, 1.5, 2.0]
LOOP_ICONS = [("repeat", [0.74, 0.74, 0.74, 1]), ("repeat", [0.118, 0.843, 0.376, 1]),
              ("repeat1", [0.118, 0.843, 0.376, 1])]
TITLES = {"library": "Плейлисты", "player": "Сейчас играет",
          "online": "Музыка из интернета", "device": "Музыка на телефоне",
          "settings": "Настройки"}
GREEN = [0.118, 0.843, 0.376, 1]
LIGHT = [0.92, 0.92, 0.92, 1]
DIM = [0.68, 0.68, 0.7, 1]
RICK_URL = "https://www.youtube.com/watch?v=hvL1339luv0"
BG_MODES = [("blobs", "Светящиеся огни"), ("aurora", "Северное сияние"),
            ("stars", "Звёздное небо"), ("plain", "Спокойный градиент"),
            ("image", "Своя картинка")]
BG_DEFAULT = {"mode": "blobs", "dim": 25, "speed": 100, "image": ""}
SITES = [("Jamendo — получить бесплатный API-ключ", "https://devportal.jamendo.com/"),
         ("Wikimedia Commons — аудио", "https://commons.wikimedia.org/wiki/Category:Audio_files"),
         ("Jamendo (музыка с разрешения авторов)", "https://www.jamendo.com/start"),
         ("Musopen (классика, общественное достояние)", "https://musopen.org/"),
         ("Internet Archive — аудио", "https://archive.org/details/audio")]


def icon_path(name):
    return os.path.join(ICON_DIR, name + ".png")


def ensure_icons(dest):
    """Иконки лежат в icons_data.py (base64): записываем их файлами и возвращаем папку."""
    from base64 import b64decode
    from icons_data import ICONS_B64
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name, b64 in ICONS_B64.items():
        (dest / (name + ".png")).write_bytes(b64decode(b64))
    return str(dest)


def fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


# ====================== аудио ======================
class AndroidEngine:
    """Проигрывание через android.media.MediaPlayer (пауза, перемотка, скорость)."""

    def __init__(self):
        from jnius import autoclass
        self.autoclass = autoclass
        self.MediaPlayer = autoclass("android.media.MediaPlayer")
        self.PlaybackParams = autoclass("android.media.PlaybackParams")
        self.mp = None
        self.loaded = self.started = self.paused = False
        self.volume = 0.9
        self.speed = 1.0
        self._speed_touched = False
        self.on_focus = None          # app.on_focus_change(change)
        self.stalled = False
        self._duck = 1.0
        self._am = self._listener = None
        try:
            from jnius import PythonJavaClass, java_method

            class FocusListener(PythonJavaClass):
                __javainterfaces__ = ["android/media/AudioManager$OnAudioFocusChangeListener"]
                __javacontext__ = "app"

                def __init__(self, engine):
                    super().__init__()
                    self.engine = engine

                @java_method("(I)V")
                def onAudioFocusChange(self, change):
                    cb = self.engine.on_focus
                    if cb:
                        cb(change)

            act = autoclass("org.kivy.android.PythonActivity").mActivity
            self._am = act.getSystemService("audio")
            self._listener = FocusListener(self)
        except Exception:
            self._am = self._listener = None

    def request_focus(self):
        """Просим у Android звук: Телеграм/звонок отберёт его — плеер встанет на паузу."""
        try:
            if self._am is not None:
                self._am.requestAudioFocus(self._listener, 3, 1)   # STREAM_MUSIC, GAIN
        except Exception:
            pass

    def abandon_focus(self):
        try:
            if self._am is not None:
                self._am.abandonAudioFocus(self._listener)
        except Exception:
            pass

    def set_duck(self, on):
        self._duck = 0.3 if on else 1.0
        if self.mp:
            try:
                self.mp.setVolume(self.volume * self._duck, self.volume * self._duck)
            except Exception:
                pass

    def load(self, path):
        self.release()
        mp = self.MediaPlayer()
        try:
            mp.setDataSource(path)
            mp.prepare()
        except Exception:
            mp.release()
            raise
        mp.setVolume(self.volume * self._duck, self.volume * self._duck)
        try:   # чтобы музыка не засыпала при выключенном экране
            act = self.autoclass("org.kivy.android.PythonActivity").mActivity
            mp.setWakeMode(act, 1)
        except Exception:
            pass
        self.mp = mp
        self.loaded, self.started, self.paused = True, False, False

    def _apply_speed(self):
        if not self.mp or not self._speed_touched:
            return
        try:
            pp = self.PlaybackParams()
            pp.setSpeed(float(self.speed))
            self.mp.setPlaybackParams(pp)
            if self.paused:      # на некоторых версиях это запускает воспроизведение
                self.mp.pause()
        except Exception:
            pass

    def start(self):
        if self.mp:
            self.request_focus()
            self.mp.start()
            self.started, self.paused = True, False
            self._apply_speed()

    def pause(self):
        if self.mp:
            try:
                self.mp.pause()
            except Exception:
                pass
            self.paused = True

    def stop(self):
        self.started, self.paused = False, True
        if self.mp:
            try:
                self.mp.pause()
                self.mp.seekTo(0)
            except Exception:
                pass

    def seek(self, sec):
        if self.mp:
            self.mp.seekTo(int(sec * 1000))

    @property
    def position(self):
        try:
            return self.mp.getCurrentPosition() / 1000.0
        except Exception:
            return 0.0

    @property
    def length(self):
        try:
            return self.mp.getDuration() / 1000.0
        except Exception:
            return 0.0

    def ended(self):
        if not (self.mp and self.started and not self.paused):
            return False
        try:
            if not self.mp.isPlaying():
                dur, pos = self.length, self.position
                if dur > 0 and pos >= dur - 1.5:       # правда дошли до конца
                    self.started = False
                    return True
                # трек остановила система (Телеграм, звонок…), а не конец —
                # не листаем дальше, а помечаем как паузу
                self.paused = True
                self.stalled = True
        except Exception:
            pass
        return False

    def set_volume(self, v):
        self.volume = v
        if self.mp:
            self.mp.setVolume(v * self._duck, v * self._duck)

    def set_speed(self, v):
        self.speed = v
        self._speed_touched = True
        self._apply_speed()

    def release(self):
        if self.mp:
            try:
                self.mp.release()
            except Exception:
                pass
        self.mp = None
        self.loaded = self.started = self.paused = False


class DesktopEngine:
    """Запасной вариант для проверки на компьютере (через Kivy SoundLoader)."""

    def __init__(self):
        self.snd = None
        self.loaded = self.started = self.paused = False
        self.volume = 0.9
        self.speed = 1.0
        self._resume = 0.0

    def load(self, path):
        from kivy.core.audio import SoundLoader
        self.release()
        self.snd = SoundLoader.load(path)
        if self.snd is None:
            raise RuntimeError("не удалось открыть файл")
        self.snd.volume = self.volume
        self.loaded, self.started, self.paused = True, False, False
        self._resume = 0.0

    def start(self):
        if self.snd:
            self.snd.play()
            if self._resume:
                self.snd.seek(self._resume)
            self.started, self.paused = True, False

    def pause(self):
        if self.snd:
            self._resume = self.snd.get_pos()
            self.snd.stop()
            self.paused = True

    def stop(self):
        self._resume = 0.0
        self.started, self.paused = False, True
        if self.snd:
            self.snd.stop()

    def seek(self, sec):
        if self.paused or not self.started:
            self._resume = sec
        elif self.snd:
            self.snd.seek(sec)

    @property
    def position(self):
        if self.paused or not self.snd:
            return self._resume
        return self.snd.get_pos()

    @property
    def length(self):
        return self.snd.length if self.snd else 0.0

    def ended(self):
        if self.snd and self.started and not self.paused and self.snd.state == "stop":
            self.started = False
            return True
        return False

    def set_volume(self, v):
        self.volume = v
        if self.snd:
            self.snd.volume = v

    def set_speed(self, v):
        self.speed = v

    def release(self):
        if self.snd:
            self.snd.stop()
            self.snd.unload()
        self.snd = None
        self.loaded = self.started = self.paused = False


# ====================== обложки ======================
def accent_from_rgb(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return list(colorsys.hsv_to_rgb(h, min(s, 0.75), 0.39)) + [1]


def glow_from_accent(accent):
    h, _s, _v = colorsys.rgb_to_hsv(*accent[:3])
    return list(colorsys.hsv_to_rgb(h, 0.55, 1.0)) + [1]


_PLACEHOLDERS = {}


def placeholder_cover(name):
    """Градиентная заглушка (цвет зависит от названия) -> (текстура, акцент)."""
    if name in _PLACEHOLDERS:
        return _PLACEHOLDERS[name]
    hue = (sum(ord(c) for c in name) * 37 % 360) / 360.0
    c1 = colorsys.hsv_to_rgb(hue, 0.75, 0.92)
    c2 = colorsys.hsv_to_rgb((hue + 0.17) % 1, 0.86, 0.43)
    n = 32
    buf = bytearray()
    for y in range(n):
        for x in range(n):
            t = (x + y) / (2.0 * (n - 1))
            buf += bytes(int((c1[i] * (1 - t) + c2[i] * t) * 255) for i in range(3)) + b"\xff"
    tex = Texture.create(size=(n, n), colorfmt="rgba")
    tex.mag_filter = "linear"
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    mid = [(c1[i] + c2[i]) / 2 for i in range(3)]
    _PLACEHOLDERS[name] = (tex, accent_from_rgb(*mid))
    return _PLACEHOLDERS[name]


def embedded_cover_bytes(path):
    if mutagen is None:
        return None
    try:
        f = mutagen.File(path)
        if f is None:
            return None
        tags = getattr(f, "tags", None)
        if tags is not None and hasattr(tags, "getall"):        # mp3 (ID3)
            pics = tags.getall("APIC")
            if pics:
                return pics[0].data
        if hasattr(f, "pictures") and f.pictures:               # flac
            return f.pictures[0].data
        if tags is not None and "covr" in tags:                 # m4a
            return bytes(tags["covr"][0])
    except Exception:
        return None
    return None


def decode_cover(raw):
    """Байты картинки -> (квадратная текстура, акцентный цвет) или (None, None)."""
    ext = "png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
    try:
        img = CoreImage(BytesIO(raw), ext=ext, keep_data=True)
        w, h = img.size
        n, r, g, b = 6, 0.0, 0.0, 0.0
        for i in range(n):
            for j in range(n):
                px = img.read_pixel(int((i + 0.5) * w / n), int((j + 0.5) * h / n))
                r += px[0]
                g += px[1]
                b += px[2]
        tex = img.texture
        s = min(w, h)
        tex = tex.get_region((w - s) // 2, (h - s) // 2, s, s)
        k = n * n
        return tex, accent_from_rgb(r / k, g / k, b / k)
    except Exception:
        return None, None


# ====================== интернет ======================
UA = {"User-Agent": "MyMusicPlayer/1.0 (personal player)"}
MIME_EXT = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/ogg": ".ogg",
            "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac",
            "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".aac"}


def ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def http_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20, context=ssl_ctx()) as r:
        return json.loads(r.read().decode("utf-8"))


def first(v):
    if isinstance(v, list):
        return v[0] if v else ""
    return v or ""


def ia_search(text):
    """Поиск аудио на archive.org -> [(подпись, identifier)]."""
    q = re.sub(r"[^\w\s]", " ", text).strip()
    if not q:
        return []
    query = f"(title:({q}) OR creator:({q})) AND mediatype:audio"
    params = urlencode([("q", query), ("fl[]", "identifier"), ("fl[]", "title"),
                        ("fl[]", "creator"), ("sort[]", "downloads desc"),
                        ("rows", "40"), ("page", "1"), ("output", "json")])
    docs = http_json("https://archive.org/advancedsearch.php?" + params)["response"]["docs"]
    out = []
    for d in docs:
        title = str(first(d.get("title")) or d["identifier"])
        who = str(first(d.get("creator")))
        out.append((f"{title} — {who}" if who else title, d["identifier"]))
    return out


def ia_tracks(ident):
    """mp3 (или ogg) из записи -> [(подпись, {"url","name"})]."""
    files = http_json(f"https://archive.org/metadata/{quote(ident)}").get("files", [])

    def pick(ext):
        seen, res = set(), []
        for f in files:
            n = f.get("name", "")
            if n.lower().endswith(ext) and n not in seen:
                seen.add(n)
                try:
                    size = int(f.get("size") or 0)
                except ValueError:
                    size = 0
                label = str(f.get("title") or Path(n).stem)
                if size:
                    label += f"  ({size / 1048576:.1f} МБ)"
                url = f"https://archive.org/download/{quote(ident)}/{quote(n)}"
                res.append((label, {"url": url, "name": Path(n).name}))
        return res

    return pick(".mp3") or pick(".ogg")


def open_url(url):
    """Открывает ссылку в браузере телефона (или компьютера)."""
    try:
        if ON_ANDROID:
            from jnius import autoclass
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            act.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        else:
            import webbrowser
            webbrowser.open(url)
    except Exception:
        Logger.exception("Player: не удалось открыть ссылку")


def wc_search(text):
    """Аудиофайлы на Wikimedia Commons -> [(подпись, заголовок файла)]."""
    q = re.sub(r"[^\w\s]", " ", text).strip()
    if not q:
        return []
    params = urlencode([("action", "query"), ("list", "search"),
                        ("srsearch", q + " filetype:audio"), ("srnamespace", "6"),
                        ("srlimit", "40"), ("format", "json")])
    found = http_json("https://commons.wikimedia.org/w/api.php?" + params)
    out = []
    for r in found.get("query", {}).get("search", []):
        name = r["title"].split(":", 1)[-1].rsplit(".", 1)[0].replace("_", " ")
        out.append((name, r["title"]))
    return out


def wc_tracks(title):
    """Прямая ссылка на файл с Commons -> [(подпись, {"url","name"})]."""
    params = urlencode([("action", "query"), ("titles", title), ("prop", "imageinfo"),
                        ("iiprop", "url|size"), ("format", "json")])
    pages = http_json("https://commons.wikimedia.org/w/api.php?" + params)
    out = []
    for pg in pages.get("query", {}).get("pages", {}).values():
        for ii in pg.get("imageinfo", []):
            url = ii.get("url")
            if not url:
                continue
            name = unquote(Path(urlparse(url).path).name)
            if name.lower().endswith(".oga"):
                name = name[:-4] + ".ogg"
            label = name.rsplit(".", 1)[0].replace("_", " ")
            if ii.get("size"):
                label += f"  ({ii['size'] / 1048576:.1f} МБ)"
            out.append((label, {"url": url, "name": name}))
    return out


JAMENDO = {"id": ""}   # client_id; хранится в настройках плеера


def jamendo_get(path, **params):
    if not JAMENDO["id"]:
        raise RuntimeError("не задан ключ Jamendo (client_id)")
    q = urlencode([("client_id", JAMENDO["id"]), ("format", "json")] + list(params.items()))
    data = http_json(f"https://api.jamendo.com/v3.0/{path}/?{q}")
    head = data.get("headers", {})
    if head.get("status") != "success":
        raise RuntimeError(head.get("error_message") or "Jamendo отклонил запрос (проверь ключ client_id)")
    return data.get("results", [])


def jm_search(text):
    res = jamendo_get("tracks", search=text, limit=40, audioformat="mp32")
    return [(f"{t.get('name', '?')} — {t.get('artist_name', '')}", str(t["id"])) for t in res]


def jm_tracks(ident):
    out = []
    for t in jamendo_get("tracks", id=ident, audioformat="mp32"):
        url = t.get("audiodownload") if t.get("audiodownload_allowed") else None
        url = url or t.get("audio")
        if not url:
            continue
        title = t.get("name") or "track"
        dur = int(t.get("duration") or 0)
        name = f"{t.get('artist_name', '')} - {title}.mp3".strip(" -")
        out.append((f"{title}  ({dur // 60}:{dur % 60:02d})", {"url": url, "name": name}))
    return out


# (название, функция поиска, функция списка треков, подсказка)
SOURCES = [
    ("Internet Archive", ia_search, ia_tracks,
     "Поиск бесплатной музыки (Internet Archive). Скачивай только то, "
     "что автор разрешил: свободные лицензии, общественное достояние."),
    ("Wikimedia Commons", wc_search, wc_tracks,
     "Аудио на Wikimedia Commons: всё там под свободными лицензиями (CC, "
     "общественное достояние). Хорошо находятся классика, народная музыка и звуки."),
    ("Jamendo", jm_search, jm_tracks,
     "Jamendo: музыка независимых артистов по лицензиям Creative Commons. "
     "Нужен бесплатный ключ client_id (спросим при первом поиске)."),
]


def import_android_image(uri, dest, max_side=1400):
    """Копирует фото из галереи в файл dest (JPEG, не больше ~1400 px, с учётом поворота)."""
    from jnius import autoclass
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    resolver = act.getContentResolver()
    BitmapFactory = autoclass("android.graphics.BitmapFactory")
    Options = autoclass("android.graphics.BitmapFactory$Options")
    Bitmap = autoclass("android.graphics.Bitmap")
    Fmt = autoclass("android.graphics.Bitmap$CompressFormat")
    FileOutputStream = autoclass("java.io.FileOutputStream")

    opts = Options()
    opts.inJustDecodeBounds = True
    stream = resolver.openInputStream(uri)
    BitmapFactory.decodeStream(stream, None, opts)
    stream.close()
    w, h = opts.outWidth, opts.outHeight
    if w <= 0 or h <= 0:
        raise RuntimeError("это не картинка")
    sample = 1
    while max(w, h) / sample > max_side:
        sample *= 2
    opts2 = Options()
    opts2.inSampleSize = sample
    stream = resolver.openInputStream(uri)
    bmp = BitmapFactory.decodeStream(stream, None, opts2)
    stream.close()
    if bmp is None:
        raise RuntimeError("не удалось прочитать картинку")
    try:                                  # учитываем поворот из EXIF
        ExifInterface = autoclass("android.media.ExifInterface")
        Matrix = autoclass("android.graphics.Matrix")
        stream = resolver.openInputStream(uri)
        orient = ExifInterface(stream).getAttributeInt("Orientation", 1)
        stream.close()
        deg = {6: 90, 3: 180, 8: 270}.get(orient, 0)
        if deg:
            m = Matrix()
            m.postRotate(float(deg))
            bmp = Bitmap.createBitmap(bmp, 0, 0, bmp.getWidth(), bmp.getHeight(), m, True)
    except Exception:
        pass
    fos = FileOutputStream(dest)
    bmp.compress(Fmt.JPEG, 90, fos)
    fos.flush()
    fos.close()
    bmp.recycle()


def safe_name(n):
    return re.sub(r'[\\/:*?"<>|]+', "_", n).strip() or "track"


def unique_path(p):
    if not p.exists():
        return p
    for i in range(2, 1000):
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
    return p


def scan_audio():
    """Вся музыка на устройстве -> [(название, исполнитель, путь)]."""
    out = []
    if ON_ANDROID:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        media = autoclass("android.provider.MediaStore$Audio$Media")
        cur = act.getContentResolver().query(
            media.EXTERNAL_CONTENT_URI, ["_data", "title", "artist"],
            "is_music != 0", None, "title COLLATE NOCASE ASC")
        if cur is None:
            return out
        try:
            while cur.moveToNext():
                path = cur.getString(0)
                if not path:
                    continue
                artist = cur.getString(2) or ""
                if artist == "<unknown>":
                    artist = ""
                out.append((cur.getString(1) or Path(path).stem, artist, path))
        finally:
            cur.close()
    else:
        root = Path.home() / "Music"
        if root.exists():
            for f in sorted(root.rglob("*")):
                if f.suffix.lower() in AUDIO_EXT:
                    out.append((f.stem, "", str(f)))
    return out


# ====================== виджеты ======================
def _vertical_texture(n=64):
    tex = Texture.create(size=(1, n), colorfmt="rgba")
    tex.mag_filter = "linear"
    return tex


def make_glow_texture():
    """Мягкое белое пятно (центр непрозрачный, к краю растворяется)."""
    n = 64
    c = (n - 1) / 2
    buf = bytearray()
    for y in range(n):
        for x in range(n):
            a = max(0.0, 1.0 - math.hypot(x - c, y - c) / c)
            a = a * a * (3 - 2 * a)
            buf += b"\xff\xff\xff" + bytes((int(a * 255),))
    tex = Texture.create(size=(n, n), colorfmt="rgba")
    tex.mag_filter = "linear"
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    return tex


def make_band_texture():
    """Вертикальная полоса для северного сияния: прозрачная сверху и снизу."""
    n = 64
    buf = bytearray()
    for i in range(n):                      # строка 0 — низ полосы
        pos = 1 - i / (n - 1)               # 0 — верх, 1 — низ
        a = pos / 0.35 * 0.37 if pos < 0.35 else (1 - pos) / 0.65 * 0.37
        buf += b"\xff\xff\xff" + bytes((int(max(0.0, a) * 255),))
    tex = _vertical_texture(n)
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    return tex


class Background(Widget):
    """Фон: светящиеся огни, северное сияние, звёзды, градиент или своя картинка
    + затемнение. Цвет подстраивается под обложку."""
    accent = ListProperty([0.16, 0.16, 0.27, 1])

    def __init__(self, **kw):
        super().__init__(**kw)
        self.mode, self.dim, self.speed = "blobs", 0.25, 1.0
        self.t = 0.0
        self.shoot = None
        self.img_tex = None
        self._img_path = None
        self._img_region = None
        self.grad = _vertical_texture()
        self.grad_stars = _vertical_texture()
        self.glow = make_glow_texture()
        self.band = make_band_texture()
        self.stars = [[random.random(), random.random(), random.uniform(0.6, 2.0),
                       random.uniform(0.5, 1.6), random.uniform(0, 6.28)] for _ in range(110)]
        self._paint_gradients()
        self.bind(pos=self.redraw, size=self.redraw, accent=self._accent_changed)
        Clock.schedule_interval(self.tick, 1 / 30)
        self.redraw()

    # ----- настройки -----
    def apply(self, cfg):
        self.mode = cfg.get("mode", "blobs")
        self.dim = max(0, min(90, cfg.get("dim", 25))) / 100
        self.speed = max(0, cfg.get("speed", 100)) / 100
        img = cfg.get("image") or ""
        if img != self._img_path:
            self._img_path = img
            self.img_tex = None
            self._img_region = None
            if img and os.path.exists(img):
                try:
                    self.img_tex = CoreImage(img).texture
                except Exception:
                    Logger.exception("Player: не удалось загрузить обои")
        self.redraw()

    def _accent_changed(self, *a):
        self._paint_gradients()
        self.redraw()

    def _fill(self, tex, top, bottom, split):
        buf = bytearray()
        for i in range(64):                 # строка 0 — низ, 63 — верх
            m = max(0.0, (i / 63 - split) / (1 - split))
            buf += bytes(int((bottom[k] * (1 - m) + top[k] * m) * 255) for k in range(3))
            buf += b"\xff"
        tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")

    def _paint_gradients(self):
        r, g, b = self.accent[:3]
        self._fill(self.grad, (r, g, b), (16 / 255, 16 / 255, 20 / 255), 0.2)
        h, s, _v = colorsys.rgb_to_hsv(r, g, b)
        low = colorsys.hsv_to_rgb(h, min(s, 200 / 255), 80 / 255)
        self._fill(self.grad_stars, (5 / 255, 7 / 255, 20 / 255), low, 0.0)

    # ----- анимация -----
    def tick(self, dt):
        if self.speed <= 0.01 or self.mode not in ("blobs", "aurora", "stars") or self.width < 2:
            return
        self.t += 0.9 * dt * self.speed
        if self.mode == "stars":
            k = dt * 30 * self.speed
            if self.shoot:
                x, y, vx, vy, life = self.shoot
                self.shoot = [x + vx * k, y + vy * k, vx, vy, life - 0.02 * k]
                if self.shoot[4] <= 0:
                    self.shoot = None
            elif random.random() < 0.006 * dt * 30:
                u = dp(1)
                self.shoot = [random.uniform(0.3, 1.0) * self.width,
                              random.uniform(0.0, 0.4) * self.height,
                              -random.uniform(9, 14) * u, random.uniform(4, 7) * u, 1.0]
        self.redraw()

    # ----- рисование -----
    def _cover_texture(self, w, h):
        key = (int(w), int(h), self._img_path)
        if self._img_region is None or self._img_region[0] != key:
            iw, ih = self.img_tex.size
            k = max(w / iw, h / ih)
            sw, sh = w / k, h / k
            reg = self.img_tex.get_region(int((iw - sw) / 2), int((ih - sh) / 2),
                                          max(1, int(sw)), max(1, int(sh)))
            self._img_region = (key, reg)
        return self._img_region[1]

    def redraw(self, *a):
        c = self.canvas
        c.clear()
        w, h = self.width, self.height
        if w < 2 or h < 2:
            return
        with c:
            if self.mode == "image" and self.img_tex is not None:
                Color(1, 1, 1, 1)
                Rectangle(texture=self._cover_texture(w, h), pos=self.pos, size=self.size)
            else:
                self._procedural(w, h)
            if self.dim > 0:
                Color(0, 0, 0, self.dim)
                Rectangle(pos=self.pos, size=self.size)

    def _procedural(self, w, h):
        top = self.top
        hue = colorsys.rgb_to_hsv(*self.accent[:3])[0] * 360
        Color(1, 1, 1, 1)
        Rectangle(texture=self.grad_stars if self.mode == "stars" else self.grad,
                  pos=self.pos, size=self.size)
        t = self.t
        if self.mode == "blobs":
            for i, (fx, fy, ph) in enumerate([(0.5, 0.7, 0.0), (0.8, 0.45, 2.1), (0.35, 0.9, 4.2)]):
                cx = self.x + w * (0.5 + 0.42 * math.sin(t * fx + ph))
                cy = top - h * (0.5 + 0.38 * math.cos(t * fy + ph * 1.3))
                rad = min(w, h) * 0.4
                r, g, b = colorsys.hsv_to_rgb(((hue + i * 35) % 360) / 360, 170 / 255, 1.0)
                Color(r, g, b, 70 / 255)
                Rectangle(texture=self.glow, pos=(cx - rad, cy - rad), size=(2 * rad, 2 * rad))
        elif self.mode == "aurora":
            step = max(dp(14), w / 48)
            n = int(w // step) + 2
            u = dp(1)
            for i in range(4):
                base, amp, band = h * (0.18 + 0.13 * i), h * 0.06, h * 0.3
                r, g, b = colorsys.hsv_to_rgb(((hue + 90 + i * 45) % 360) / 360, 200 / 255, 1.0)
                Color(r, g, b, 1)
                verts = []
                for j in range(n):
                    x = j * step
                    xs = x / u
                    y = (base + amp * math.sin(xs * 0.008 + t * (0.7 + 0.2 * i) + i * 1.7)
                         + amp * 0.5 * math.sin(xs * 0.017 - t * 0.9 + i))
                    verts += [self.x + x, top - y, 0, 1, self.x + x, top - (y + band), 0, 0]
                Mesh(vertices=verts, indices=list(range(2 * n)), mode="triangle_strip",
                     texture=self.band)
        elif self.mode == "stars":
            u = dp(0.9)
            for sx, sy, sz, sp_, ph in self.stars:
                x = ((sx + t * 0.004 * sp_) % 1.0) * w
                a = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * 1.6 * sp_ + ph))
                r = sz * u
                Color(1, 1, 1, a)
                Ellipse(pos=(self.x + x - r, top - sy * h - r), size=(2 * r, 2 * r))
            if self.shoot:
                x, y, vx, vy, life = self.shoot
                segs = 6
                for k in range(segs):
                    f0, f1 = k / segs, (k + 1) / segs
                    Color(1, 1, 1, 0.9 * life * (1 - f0))
                    Line(points=[self.x + x - vx * 8 * f0, top - (y - vy * 8 * f0),
                                 self.x + x - vx * 8 * f1, top - (y - vy * 8 * f1)],
                         width=dp(1.5))


class Glyph(Widget):
    """Иконка из PNG (белая, красится в tint) с необязательной подложкой."""
    source = StringProperty("")
    tint = ListProperty(list(LIGHT))
    bg = ListProperty([0, 0, 0, 0])
    icon_dp = NumericProperty(24)
    scale = NumericProperty(1.0)
    ox = NumericProperty(0.0)
    circle = BooleanProperty(False)
    corner = NumericProperty(10)

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
            self._bgc = Color(0, 0, 0, 0)
            self._bgr = RoundedRectangle()
            self._ic = Color(1, 1, 1, 1)
            self._img = Rectangle()
        self._src = None
        self.bind(pos=self._redraw, size=self._redraw, source=self._redraw,
                  tint=self._redraw, bg=self._redraw, icon_dp=self._redraw,
                  scale=self._redraw, ox=self._redraw, circle=self._redraw, corner=self._redraw)
        self._redraw()

    def _redraw(self, *a):
        self._bgc.rgba = self.bg
        r = min(self.width, self.height) / 2 if self.circle else dp(self.corner)
        self._bgr.pos, self._bgr.size, self._bgr.radius = self.pos, self.size, [r]
        if self.source != self._src:
            self._src = self.source
            if self.source:
                self._img.source = icon_path(self.source)
            else:
                self._img.texture = None
        if not self.source:            # иконки нет — ничего не рисуем
            self._ic.rgba = (0, 0, 0, 0)
            self._img.size = (0, 0)
            return
        self._ic.rgba = self.tint
        s = dp(self.icon_dp) * self.scale
        self._img.size = (s, s)
        self._img.pos = (self.center_x - s / 2 + self.ox, self.center_y - s / 2)


class IconButton(ButtonBehavior, Glyph):
    """Кнопка-иконка: при нажатии сжимается, при отпускании пружинит."""

    def on_state(self, inst, value):
        Animation.cancel_all(self, "scale")
        if value == "down":
            Animation(scale=0.78, d=0.08).start(self)
        else:
            Animation(scale=1.0, d=0.4, t="out_back").start(self)

    def pop(self):
        Animation.cancel_all(self, "scale")
        self.scale = 0.6
        Animation(scale=1.0, d=0.45, t="out_back").start(self)

    def kick(self, direction):
        """Иконку «выталкивает» в сторону и пружинит назад (след./пред. трек)."""
        Animation.cancel_all(self, "ox")
        self.ox = dp(14) * direction
        Animation(ox=0, d=0.45, t="out_back").start(self)


class RoundButton(ButtonBehavior, BoxLayout):
    """Скруглённая кнопка с необязательной иконкой слева."""
    text = StringProperty("")
    icon = StringProperty("")
    fg = ListProperty(list(LIGHT))
    bg = ListProperty([1, 1, 1, 0.12])
    bg_now = ListProperty([1, 1, 1, 0.12])
    radius = NumericProperty(12)
    font_size = NumericProperty(sp(15))
    label_w = NumericProperty(0)

    def on_bg(self, *a):
        self.bg_now = list(self.bg)

    def on_state(self, inst, value):
        Animation.cancel_all(self, "bg_now")
        b = self.bg
        if value == "down":
            Animation(bg_now=[min(1, b[0] + .22), min(1, b[1] + .22), min(1, b[2] + .22),
                              min(1, b[3] + .25)], d=0.06).start(self)
        else:
            Animation(bg_now=list(b), d=0.4).start(self)


class Chip(RoundButton):
    active = BooleanProperty(False)


class Input(TextInput):
    pass


class Toast(Label):
    pass


class MiniBar(ButtonBehavior, BoxLayout):
    want = BooleanProperty(False)
    show = NumericProperty(0.0)

    def on_want(self, inst, value):
        Animation.cancel_all(self, "show")
        Animation(show=1.0 if value else 0.0, d=0.35, t="out_cubic").start(self)


class TrackRow(ButtonBehavior, BoxLayout):
    press = NumericProperty(0.0)

    def on_state(self, inst, value):
        Animation.cancel_all(self, "press")
        Animation(press=1.0 if value == "down" else 0.0,
                  d=0.1 if value == "down" else 0.35, t="out_cubic").start(self)

    title = StringProperty("")
    idx = NumericProperty(0)
    playing = BooleanProperty(False)


class PickRow(ButtonBehavior, BoxLayout):
    press = NumericProperty(0.0)

    def on_state(self, inst, value):
        Animation.cancel_all(self, "press")
        Animation(press=1.0 if value == "down" else 0.0,
                  d=0.1 if value == "down" else 0.35, t="out_cubic").start(self)

    title = StringProperty("")
    subtitle = StringProperty("")
    idx = NumericProperty(0)
    selected = BooleanProperty(False)
    show_check = BooleanProperty(False)
    owner = StringProperty("")


class CoverView(Widget):
    """Обложка: сдвиг при смене, «дыхание», прыжок на новом треке, волны при игре."""
    fade = NumericProperty(1.0)
    scale = NumericProperty(0.0)
    ring = NumericProperty(0.0)
    phase = NumericProperty(0.0)
    tex = ObjectProperty(None)
    old = ObjectProperty(None, allownone=True)
    placeholder = BooleanProperty(True)
    ring_color = ListProperty(list(GREEN))
    playing = BooleanProperty(False)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.tex = placeholder_cover("music")[0]
        self._ring_ev = None
        self.parts = []            # искры, летящие от краёв обложки
        self.bind(pos=self.redraw, size=self.redraw, fade=self.redraw, scale=self.redraw,
                  ring=self.redraw, phase=self.redraw, tex=self.redraw)
        self.redraw()

    def redraw(self, *a):
        c = self.canvas
        c.clear()
        side = min(self.width, self.height) * 0.9 * self.scale
        if side <= 1:
            return
        x, y = self.center_x - side / 2, self.center_y - side / 2
        rad = dp(20)
        with c:
            for i in range(5):                              # мягкая тень
                Color(0, 0, 0, 0.07)
                RoundedRectangle(pos=(x - i * dp(2), y - i * dp(2) - dp(6)),
                                 size=(side + i * dp(4), side + i * dp(4)),
                                 radius=[rad + i * dp(2)])
            dx = dp(18)
            if self.old is not None and self.fade < 1:     # старая уезжает влево
                Color(1, 1, 1, 1)
                RoundedRectangle(texture=self.old, pos=(x - self.fade * dx, y),
                                 size=(side, side), radius=[rad])
            Color(1, 1, 1, self.fade)                       # новая приезжает справа
            RoundedRectangle(texture=self.tex, pos=(x + (1 - self.fade) * dx, y),
                             size=(side, side), radius=[rad])
            if self.placeholder:
                s = side * 0.5
                Color(1, 1, 1, 0.85 * self.fade)
                Rectangle(source=icon_path("note"), pos=(self.center_x - s / 2, self.center_y - s / 2),
                          size=(s, s))
            if self.ring > 0.01:                            # расходящиеся волны
                for k in (0.0, 0.5):
                    ph = (self.phase + k) % 1
                    grow = ph * dp(30)
                    Color(*self.ring_color[:3], 0.6 * (1 - ph) * self.ring)
                    Line(rounded_rectangle=(x - grow, y - grow, side + 2 * grow,
                                            side + 2 * grow, rad + grow), width=dp(2))
            for px, py, _vx, _vy, life, sz in self.parts:   # светящиеся искры
                Color(*self.ring_color[:3], 0.82 * life)
                r = sz * life + dp(0.8)
                Ellipse(pos=(px - r, py - r), size=(2 * r, 2 * r))

    def set_cover(self, tex, placeholder):
        self.old = self.tex
        self.tex = tex
        self.placeholder = placeholder
        self.fade = 0.0
        Animation.cancel_all(self, "fade")
        Animation(fade=1.0, d=0.65, t="out_cubic").start(self)

    def intro(self):
        Animation(scale=0.92, d=1.0, t="out_back").start(self)

    def bump(self):
        Animation.cancel_all(self, "scale")
        self.scale = 0.86
        a = Animation(scale=0.98 if self.playing else 0.92, d=0.55, t="out_back")
        a.bind(on_complete=self._breathe)
        a.start(self)

    def set_playing(self, on):
        self.playing = on
        Animation.cancel_all(self, "scale")
        a = Animation(scale=0.98 if on else 0.92, d=0.3, t="out_cubic")
        a.bind(on_complete=self._breathe)
        a.start(self)
        Animation.cancel_all(self, "ring")
        Animation(ring=1.0 if on else 0.0, d=0.5).start(self)
        if on and self._ring_ev is None:
            self._ring_ev = Clock.schedule_interval(self._ring_tick, 1 / 30)

    def _breathe(self, *a):
        if not self.playing:
            return
        seq = (Animation(scale=1.03, d=1.2, t="in_out_sine") +
               Animation(scale=0.99, d=1.2, t="in_out_sine"))
        seq.repeat = True
        seq.start(self)

    def _ring_tick(self, dt):
        self.phase = (self.phase + dt / 1.8) % 1
        self._tick_particles()
        if not self.playing and self.ring < 0.01 and self._ring_ev is not None:
            self._ring_ev.cancel()
            self._ring_ev = None
            self.parts.clear()

    def _tick_particles(self):
        """Пока играет музыка, от краёв обложки поднимаются искры."""
        k = dp(2)
        if self.playing and random.random() < 0.4:
            half = min(self.width, self.height) * 0.9 * self.scale / 2
            cx, cy = self.center_x, self.center_y
            side = random.choice((0, 1, 2))
            if side == 0:
                x, y = cx + random.uniform(-half, half), cy - half
            elif side == 1:
                x, y = cx - half, cy + random.uniform(-half, half)
            else:
                x, y = cx + half, cy + random.uniform(-half, half)
            self.parts.append([x, y, random.uniform(-0.5, 0.5) * k, random.uniform(0.6, 1.8) * k,
                               1.0, random.uniform(2.0, 4.5) * dp(1)])
        for q in self.parts:
            q[0] += q[2]
            q[1] += q[3]
            q[4] -= 0.024
        self.parts = [q for q in self.parts if q[4] > 0]

    def on_touch_down(self, touch):
        """Тап по обложке — она подпрыгивает."""
        if self.scale > 0.1 and self.collide_point(*touch.pos):
            self.bump()
            app = App.get_running_app()
            if app is not None:
                app.cover_tapped()
            return True
        return super().on_touch_down(touch)


class Equalizer(Widget):
    """Столбики эквалайзера с падающими «колпачками»."""
    N = 28
    color = ListProperty(list(GREEN))
    active = BooleanProperty(False)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.h = [0.04] * self.N
        self.t = [0.04] * self.N
        self.pk = [0.04] * self.N
        self._ev = None
        self.bind(pos=lambda *a: self.redraw(), size=lambda *a: self.redraw())
        self.redraw()

    def on_active(self, inst, value):
        if value and self._ev is None:
            self._ev = Clock.schedule_interval(self.tick, 1 / 30)

    def tick(self, dt):
        import random
        for i in range(self.N):
            if self.active:
                self.t[i] = max(0.06, min(1.0, self.t[i] * 0.55 + random.random() * 0.9 * 0.45))
            else:
                self.t[i] = 0.04
            self.h[i] += (self.t[i] - self.h[i]) * 0.35
            self.pk[i] = max(self.h[i], self.pk[i] - 0.014)
        self.redraw()
        if not self.active and max(self.pk) < 0.06 and self._ev is not None:
            self._ev.cancel()
            self._ev = None

    def redraw(self):
        c = self.canvas
        c.clear()
        if self.width < 2:
            return
        w = self.width / self.N
        r, g, b = self.color[:3]
        with c:
            for i, v in enumerate(self.h):
                Color(r, g, b, 0.45 + 0.55 * v)
                bh = max(dp(3), v * self.height)
                RoundedRectangle(pos=(self.x + i * w + dp(1), self.y), size=(w - dp(2), bh),
                                 radius=[dp(2)])
                if self.pk[i] > v + 0.03:
                    Color(r, g, b, 0.85)
                    cy = min(self.height - dp(3), self.pk[i] * self.height + dp(3))
                    RoundedRectangle(pos=(self.x + i * w + dp(1), self.y + cy),
                                     size=(w - dp(2), dp(3)), radius=[dp(1.5)])


class ModeChip(RoundButton):
    active = BooleanProperty(False)


class Confetti(Widget):
    """Салют из конфетти поверх всего экрана (когда скачивание закончилось)."""
    COLORS = ["#1ed760", "#ff5c8a", "#ffd23f", "#4cc9f0", "#b388ff", "#ff9f1c"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.parts = []
        self._ev = None

    def burst(self, n=90):
        k = dp(1)
        w, h = self.size
        for _ in range(n):
            ang = random.uniform(math.pi * 0.08, math.pi * 0.92)
            sp_ = random.uniform(7, 17) * k
            self.parts.append([w / 2 + random.uniform(-60, 60) * k, h * 0.2,
                               math.cos(ang) * sp_, math.sin(ang) * sp_,
                               random.uniform(0, 6.28), random.uniform(-0.35, 0.35),
                               get_color_from_hex(random.choice(self.COLORS)),
                               random.uniform(6, 11) * k, 1.0])
        if self._ev is None:
            self._ev = Clock.schedule_interval(self.tick, 1 / 30)

    def tick(self, dt):
        s = dt * 60                    # шаги «как на компьютере» за один кадр
        g = 0.33 * dp(1)
        for q in self.parts:
            q[0] += q[2] * s
            q[1] += q[3] * s
            q[3] -= g * s
            q[2] *= 0.985 ** s
            q[4] += q[5] * s
            q[8] -= 0.007 * s
        self.parts = [q for q in self.parts if q[8] > 0 and q[1] > -dp(40)]
        self.canvas.clear()
        with self.canvas:
            for x, y, _vx, _vy, rot, _vr, col, sz, life in self.parts:
                PushMatrix()
                Rotate(angle=math.degrees(rot), origin=(x, y))
                Color(col[0], col[1], col[2], min(1.0, life * 2))
                Rectangle(pos=(x - sz / 2, y - sz / 4), size=(sz, sz / 2))
                PopMatrix()
        if not self.parts and self._ev is not None:
            self._ev.cancel()
            self._ev = None


class SafeHandler(ExceptionHandler):
    """Ошибка в обработчике больше не закрывает приложение: пишем её в журнал и идём дальше."""

    def handle_exception(self, inst):
        if isinstance(inst, (KeyboardInterrupt, SystemExit)):
            return ExceptionManager.RAISE
        try:
            tb = "".join(traceback.format_exception(type(inst), inst, inst.__traceback__))
            Logger.error("Player: необработанная ошибка:\n" + tb)
            app = App.get_running_app()
            if app is not None:
                app.report_error(tb)
        except Exception:
            pass
        return ExceptionManager.PASS


class LibraryScreen(Screen):
    pass


class PlayerScreen(Screen):
    shown = StringProperty("Ничего не играет")
    album = StringProperty("")
    pos_text = StringProperty("0:00")
    dur_text = StringProperty("0:00")
    seek_max = NumericProperty(1)
    seek_val = NumericProperty(0)
    title_full = StringProperty("")
    typed = NumericProperty(0)

    def type_text(self, text):
        """Название «печатается» буква за буквой."""
        Animation.cancel_all(self, "typed")
        self.title_full = text
        self.typed = 0
        Animation(typed=len(text), d=min(0.9, 0.035 * max(1, len(text)))).start(self)

    def on_typed(self, *a):
        self.shown = self.title_full[:int(self.typed)]


class OnlineScreen(Screen):
    pass


class DeviceScreen(Screen):
    pass


class SettingsScreen(Screen):
    pass


def shake(w):
    """Покачивание виджета — когда ввод неверный."""
    x = w.x
    a = (Animation(x=x - dp(9), d=0.06) + Animation(x=x + dp(9), d=0.08) +
         Animation(x=x - dp(6), d=0.07) + Animation(x=x, d=0.06))
    Animation.cancel_all(w, "x")
    a.start(w)


# ====================== разметка ======================
KV = """
#:set GREEN (0.118, 0.843, 0.376, 1)
#:set LIGHT (0.92, 0.92, 0.92, 1)
#:set DIM (0.68, 0.68, 0.7, 1)

<RoundButton>:
    padding: dp(14), dp(6)
    spacing: dp(8)
    label_w: lbl.texture_size[0]
    opacity: 0.45 if self.disabled else 1
    canvas.before:
        Color:
            rgba: self.bg_now
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(self.radius)]
    Widget:
    Glyph:
        size_hint: None, None
        size: (dp(22), dp(22)) if root.icon else (0, 0)
        pos_hint: {'center_y': .5}
        source: root.icon
        tint: root.fg
        icon_dp: 20
    Label:
        id: lbl
        size_hint_x: None
        width: self.texture_size[0]
        text: root.text
        color: root.fg
        font_size: root.font_size
        bold: True
    Widget:

<Chip>:
    size_hint: None, 1
    width: self.label_w + dp(36)
    bg: GREEN if self.active else (1, 1, 1, 0.12)
    fg: (0, 0, 0, 1) if self.active else LIGHT

<ModeChip>:
    bg: GREEN if self.active else (1, 1, 1, 0.12)
    fg: (0, 0, 0, 1) if self.active else LIGHT

<Input>:
    multiline: False
    write_tab: False
    background_normal: ''
    background_active: ''
    background_color: 1, 1, 1, 0.12
    foreground_color: LIGHT
    hint_text_color: DIM
    cursor_color: GREEN
    selection_color: 0.118, 0.843, 0.376, 0.4
    font_size: sp(16)
    padding: dp(14), dp(13), dp(14), dp(10)
    size_hint_y: None
    height: dp(48)

<Toast>:
    size_hint: None, None
    size: self.texture_size[0] + dp(44), dp(44)
    color: 0, 0, 0, 1
    bold: True
    opacity: 0
    center_x: (self.parent.width / 2) if self.parent else 0
    canvas.before:
        Color:
            rgba: GREEN
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(22)]

<MiniBar>:
    size_hint_y: None
    want: bool(app.has_track and app.screen != 'player')
    height: dp(60) * self.show
    opacity: self.show
    disabled: self.show < 0.5
    padding: dp(20), dp(6), dp(10), dp(6)
    spacing: dp(10)
    canvas.before:
        Color:
            rgba: 1, 1, 1, 0.09
        RoundedRectangle:
            pos: self.x + dp(8), self.y + dp(2)
            size: self.width - dp(16), self.height - dp(4)
            radius: [dp(14)]
    on_release: app.go('player')
    Glyph:
        size_hint: None, None
        size: dp(26), dp(26)
        pos_hint: {'center_y': .5}
        source: 'note'
        tint: GREEN
        icon_dp: 22
    Label:
        text: app.mini_title
        color: LIGHT
        font_size: sp(15)
        text_size: self.size
        halign: 'left'
        valign: 'middle'
        shorten: True
        shorten_from: 'right'
    IconButton:
        id: mplay
        size_hint: None, None
        size: dp(46), dp(46)
        pos_hint: {'center_y': .5}
        source: 'pause' if app.playing else 'play'
        icon_dp: 26
        on_release: app.toggle_play()
    IconButton:
        size_hint: None, None
        size: dp(46), dp(46)
        pos_hint: {'center_y': .5}
        source: 'next'
        icon_dp: 24
        on_release: app.next_track()

<TrackRow>:
    size_hint_y: None
    height: dp(58)
    padding: dp(12), dp(4), dp(4), dp(4)
    spacing: dp(12)
    canvas.before:
        Color:
            rgba: (0.118, 0.843, 0.376, 0.20) if root.playing else (0, 0, 0, 0)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]
        Color:
            rgba: 1, 1, 1, 0.10 * root.press
        RoundedRectangle:
            pos: self.x + dp(10) * (1 - root.press), self.y
            size: self.width - dp(20) * (1 - root.press), self.height
            radius: [dp(12)]
    on_release: app.play_from_list(root.idx)
    Glyph:
        size_hint: None, None
        size: dp(26), dp(26)
        pos_hint: {'center_y': .5}
        icon_dp: 22
        source: (('bars%d' % (app.ind % 3)) if app.playing else 'pause') if root.playing else 'note'
        tint: (0.49, 1, 0.68, 1) if root.playing else DIM
    Label:
        text: root.title
        color: LIGHT
        font_size: sp(16)
        text_size: self.size
        halign: 'left'
        valign: 'middle'
        shorten: True
        shorten_from: 'right'
    IconButton:
        size_hint: None, None
        size: dp(44), dp(44)
        pos_hint: {'center_y': .5}
        source: 'more'
        tint: DIM
        on_release: app.track_menu(root.idx)

<PickRow>:
    size_hint_y: None
    height: dp(60)
    padding: dp(12), dp(6)
    spacing: dp(12)
    canvas.before:
        Color:
            rgba: (0.118, 0.843, 0.376, 0.18) if root.selected else (0, 0, 0, 0)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]
        Color:
            rgba: 1, 1, 1, 0.10 * root.press
        RoundedRectangle:
            pos: self.x + dp(10) * (1 - root.press), self.y
            size: self.width - dp(20) * (1 - root.press), self.height
            radius: [dp(12)]
    on_release: app.row_tap(root.owner, root.idx)
    Glyph:
        size_hint: None, None
        size: dp(28), dp(28)
        pos_hint: {'center_y': .5}
        icon_dp: 22
        source: ('check' if root.selected else 'plus') if root.show_check else 'note'
        tint: GREEN if root.selected else DIM
    BoxLayout:
        orientation: 'vertical'
        Label:
            text: root.title
            color: LIGHT
            font_size: sp(15)
            text_size: self.size
            halign: 'left'
            valign: 'middle'
            shorten: True
            shorten_from: 'right'
        Label:
            text: root.subtitle
            color: DIM
            font_size: sp(12)
            size_hint_y: None
            height: dp(18) if root.subtitle else 0
            text_size: self.size
            halign: 'left'
            valign: 'middle'
            shorten: True
            shorten_from: 'right'

<LibraryScreen>:
    name: 'library'
    BoxLayout:
        orientation: 'vertical'
        padding: dp(12), dp(4), dp(12), dp(8)
        spacing: dp(8)
        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(2)
            ScrollView:
                do_scroll_y: False
                bar_width: 0
                BoxLayout:
                    id: chips
                    size_hint_x: None
                    width: self.minimum_width
                    spacing: dp(8)
                    padding: 0, dp(3)
            IconButton:
                size_hint: None, 1
                width: dp(42)
                source: 'image'
                on_release: app.pick_album_cover(app.cur_pl)
            IconButton:
                size_hint: None, 1
                width: dp(42)
                source: 'plus'
                on_release: app.new_playlist()
            IconButton:
                size_hint: None, 1
                width: dp(42)
                source: 'pencil'
                on_release: app.rename_playlist()
            IconButton:
                size_hint: None, 1
                width: dp(42)
                source: 'trash'
                on_release: app.delete_playlist()
        Label:
            id: tracks_label
            size_hint_y: None
            height: dp(22)
            color: DIM
            bold: True
            font_size: sp(12)
            text_size: self.size
            halign: 'left'
            valign: 'middle'
        FloatLayout:
            RecycleView:
                id: rv
                viewclass: 'TrackRow'
                RecycleBoxLayout:
                    default_size: None, dp(58)
                    default_size_hint: 1, None
                    size_hint_y: None
                    height: self.minimum_height
                    orientation: 'vertical'
                    spacing: dp(4)
            Label:
                id: empty
                text: 'Здесь пока пусто.\\nНажми «Добавить музыку с телефона».'
                halign: 'center'
                color: DIM
                opacity: 0
                size_hint: None, None
                size: self.texture_size
                pos_hint: {'center_x': .5, 'center_y': .5}
        RoundButton:
            size_hint_y: None
            height: dp(50)
            text: 'Добавить музыку с телефона'
            icon: 'plus'
            bg: GREEN
            fg: 0, 0, 0, 1
            on_release: app.go('device')

<PlayerScreen>:
    name: 'player'
    BoxLayout:
        orientation: 'vertical'
        padding: dp(22), dp(4), dp(22), dp(8)
        spacing: dp(6)
        CoverView:
            id: cover
            size_hint_y: 0.5
        Label:
            text: root.shown
            font_size: sp(21)
            bold: True
            color: LIGHT
            size_hint_y: None
            height: dp(60)
            text_size: self.size
            halign: 'center'
            valign: 'middle'
        Label:
            text: root.album
            color: DIM
            font_size: sp(14)
            size_hint_y: None
            height: dp(22)
        Equalizer:
            id: eq
            size_hint_y: None
            height: dp(52)
        BoxLayout:
            size_hint_y: None
            height: dp(40)
            Label:
                text: root.pos_text
                size_hint_x: None
                width: dp(46)
                font_size: sp(13)
                color: DIM
            Slider:
                min: 0
                max: max(root.seek_max, 1)
                value: root.seek_val
                value_track: True
                value_track_color: GREEN
                value_track_width: dp(4)
                cursor_size: dp(22), dp(22)
                padding: dp(14)
                on_touch_down: if self.collide_point(*args[1].pos): app.seeking = True
                on_touch_up: if app.seeking: app.finish_seek(self.value)
            Label:
                text: root.dur_text
                size_hint_x: None
                width: dp(46)
                font_size: sp(13)
                color: DIM
        BoxLayout:
            size_hint_y: None
            height: dp(84)
            spacing: dp(4)
            IconButton:
                source: app.loop_icon
                tint: app.loop_tint
                icon_dp: 24
                on_release: app.cycle_loop()
            IconButton:
                source: 'prev'
                icon_dp: 30
                on_release: self.kick(-1); app.prev_track()
            IconButton:
                id: play_btn
                size_hint: None, None
                size: dp(74), dp(74)
                pos_hint: {'center_y': .5}
                circle: True
                bg: GREEN
                tint: 0, 0, 0, 1
                icon_dp: 34
                source: 'pause' if app.playing else 'play'
                on_release: app.toggle_play()
            IconButton:
                source: 'next'
                icon_dp: 30
                on_release: self.kick(1); app.next_track()
            RoundButton:
                size_hint: None, None
                size: dp(64), dp(40)
                pos_hint: {'center_y': .5}
                text: app.speed_text
                on_release: app.cycle_speed()

<OnlineScreen>:
    name: 'online'
    BoxLayout:
        orientation: 'vertical'
        padding: dp(12), dp(4), dp(12), dp(8)
        spacing: dp(8)
        BoxLayout:
            size_hint_y: None
            height: dp(48)
            spacing: dp(8)
            Input:
                id: q
                hint_text: 'Название или исполнитель…'
                on_text_validate: app.online_search(self.text)
            IconButton:
                size_hint: None, None
                size: dp(48), dp(48)
                source: 'search'
                bg: GREEN
                tint: 0, 0, 0, 1
                corner: 12
                on_release: app.online_search(q.text)
        ScrollView:
            size_hint_y: None
            height: dp(40)
            do_scroll_y: False
            bar_width: 0
            BoxLayout:
                id: srcs
                size_hint_x: None
                width: self.minimum_width
                spacing: dp(8)
                padding: 0, dp(2)
        Label:
            id: hint
            text: app.src_hint
            color: DIM
            font_size: sp(12)
            size_hint_y: None
            text_size: self.width, None
            height: self.texture_size[1]
            halign: 'left'
        BoxLayout:
            size_hint_y: None
            height: dp(34)
            IconButton:
                size_hint: None, 1
                width: dp(40) if app.online_stage == 'tracks' else 0
                opacity: 1 if app.online_stage == 'tracks' else 0
                disabled: app.online_stage != 'tracks'
                source: 'back'
                on_release: app.online_back()
            Label:
                text: app.online_status
                color: DIM
                font_size: sp(13)
                text_size: self.size
                halign: 'left'
                valign: 'middle'
            IconButton:
                size_hint: None, 1
                width: dp(40)
                source: 'globe'
                tint: DIM
                on_release: app.sites_menu()
        RecycleView:
            id: rv
            viewclass: 'PickRow'
            RecycleBoxLayout:
                default_size: None, dp(60)
                default_size_hint: 1, None
                size_hint_y: None
                height: self.minimum_height
                orientation: 'vertical'
                spacing: dp(4)
        BoxLayout:
            size_hint_y: None
            height: dp(48)
            spacing: dp(8)
            Input:
                id: url
                hint_text: 'Или прямая ссылка на mp3…'
                on_text_validate: app.download_url(self.text)
            IconButton:
                size_hint: None, None
                size: dp(48), dp(48)
                source: 'download'
                bg: 1, 1, 1, 0.12
                corner: 12
                on_release: app.download_url(url.text)
        ProgressBar:
            id: bar
            max: 100
            size_hint_y: None
            height: dp(8)
        RoundButton:
            size_hint_y: None
            height: dp(50)
            text: 'Скачать выбранные'
            icon: 'download'
            bg: GREEN
            fg: 0, 0, 0, 1
            disabled: app.dl_busy
            on_release: app.download_selected()

<DeviceScreen>:
    name: 'device'
    BoxLayout:
        orientation: 'vertical'
        padding: dp(12), dp(4), dp(12), dp(8)
        spacing: dp(8)
        Input:
            id: flt
            hint_text: 'Поиск по списку'
            on_text: app.device_filter(self.text)
        Label:
            text: app.dev_status
            color: DIM
            font_size: sp(13)
            size_hint_y: None
            height: dp(22)
            text_size: self.size
            halign: 'left'
            valign: 'middle'
        RecycleView:
            id: rv
            viewclass: 'PickRow'
            RecycleBoxLayout:
                default_size: None, dp(60)
                default_size_hint: 1, None
                size_hint_y: None
                height: self.minimum_height
                orientation: 'vertical'
                spacing: dp(4)
        RoundButton:
            size_hint_y: None
            height: dp(50)
            text: 'Добавить выбранные'
            icon: 'check'
            bg: GREEN
            fg: 0, 0, 0, 1
            on_release: app.device_add()

<SettingsScreen>:
    name: 'settings'
    ScrollView:
        do_scroll_x: False
        bar_width: dp(3)
        BoxLayout:
            orientation: 'vertical'
            size_hint_y: None
            height: self.minimum_height
            padding: dp(16), dp(8), dp(16), dp(24)
            spacing: dp(12)
            Label:
                text: 'ФОН ПЛЕЕРА'
                color: DIM
                bold: True
                font_size: sp(12)
                size_hint_y: None
                height: dp(22)
                text_size: self.size
                halign: 'left'
                valign: 'middle'
            GridLayout:
                id: modes
                cols: 2
                size_hint_y: None
                height: self.minimum_height
                spacing: dp(8)
            RoundButton:
                size_hint_y: None
                height: dp(48)
                text: 'Выбрать картинку для фона'
                icon: 'image'
                on_release: app.pick_bg_image()
            Label:
                text: app.bg_file_text
                color: DIM
                font_size: sp(13)
                size_hint_y: None
                text_size: self.width, None
                height: self.texture_size[1] if self.text else 0
                halign: 'left'
            BoxLayout:
                size_hint_y: None
                height: dp(44)
                Label:
                    text: 'Затемнение'
                    size_hint_x: None
                    width: dp(110)
                    color: LIGHT
                    text_size: self.size
                    halign: 'left'
                    valign: 'middle'
                Slider:
                    id: dim_sl
                    min: 0
                    max: 90
                    step: 1
                    value_track: True
                    value_track_color: GREEN
                    cursor_size: dp(22), dp(22)
                    on_value: app.on_bg_slider('dim', self.value)
                Label:
                    text: app.bg_dim_text
                    size_hint_x: None
                    width: dp(52)
                    color: DIM
            BoxLayout:
                size_hint_y: None
                height: dp(44)
                Label:
                    text: 'Скорость'
                    size_hint_x: None
                    width: dp(110)
                    color: LIGHT
                    text_size: self.size
                    halign: 'left'
                    valign: 'middle'
                Slider:
                    id: speed_sl
                    min: 0
                    max: 200
                    step: 1
                    value_track: True
                    value_track_color: GREEN
                    cursor_size: dp(22), dp(22)
                    on_value: app.on_bg_slider('speed', self.value)
                Label:
                    text: app.bg_speed_text
                    size_hint_x: None
                    width: dp(52)
                    color: DIM
            RoundButton:
                size_hint_y: None
                height: dp(46)
                text: 'Сбросить фон'
                icon: 'close'
                on_release: app.reset_bg()
            Label:
                text: 'ОБЛОЖКИ'
                color: DIM
                bold: True
                font_size: sp(12)
                size_hint_y: None
                height: dp(30)
                text_size: self.size
                halign: 'left'
                valign: 'bottom'
            RoundButton:
                size_hint_y: None
                height: dp(54)
                text: 'Сменить обложку альбома'
                icon: 'image'
                on_release: app.choose_album_for_cover()
            Label:
                text: 'Выбери альбом, потом фото из галереи.\\nОбложку отдельного трека можно сменить в его меню (три точки). Тап по обложке на экране плеера — она подпрыгивает.'
                color: DIM
                font_size: sp(13)
                size_hint_y: None
                text_size: self.width, None
                height: self.texture_size[1]
                halign: 'left'
            Label:
                text: 'ЗАГРУЗКА МУЗЫКИ'
                color: DIM
                bold: True
                font_size: sp(12)
                size_hint_y: None
                height: dp(30)
                text_size: self.size
                halign: 'left'
                valign: 'bottom'
            RoundButton:
                size_hint_y: None
                height: dp(50)
                text: 'Ключ Jamendo'
                icon: 'settings'
                on_release: app.ask_jamendo_key(None)
            Label:
                text: app.jamendo_text
                color: DIM
                font_size: sp(13)
                size_hint_y: None
                text_size: self.width, None
                height: self.texture_size[1]
                halign: 'left'
            Label:
                text: 'ПРОЧЕЕ'
                color: DIM
                bold: True
                font_size: sp(12)
                size_hint_y: None
                height: dp(30)
                text_size: self.size
                halign: 'left'
                valign: 'bottom'
            RoundButton:
                size_hint_y: None
                height: dp(46)
                text: 'Журнал ошибок'
                icon: 'list'
                on_release: app.show_error_log()

FloatLayout:
    Background:
        id: bg
    BoxLayout:
        orientation: 'vertical'
        BoxLayout:
            size_hint_y: None
            height: dp(52)
            padding: dp(18), 0
            Label:
                text: app.screen_title
                font_size: sp(22)
                bold: True
                color: LIGHT
                text_size: self.size
                halign: 'left'
                valign: 'middle'
        ScreenManager:
            id: sm
            LibraryScreen:
                id: lib
            PlayerScreen:
                id: ply
            OnlineScreen:
                id: onl
            DeviceScreen:
                id: dev
            SettingsScreen:
                id: sett
        MiniBar:
            id: mini
        BoxLayout:
            size_hint_y: None
            height: dp(58)
            canvas.before:
                Color:
                    rgba: 0, 0, 0, 0.35
                Rectangle:
                    pos: self.pos
                    size: self.size
            IconButton:
                id: nav_library
                source: 'list'
                icon_dp: 26
                tint: GREEN if app.screen in ('library', 'device') else DIM
                on_release: app.go('library')
            IconButton:
                id: nav_player
                source: 'note'
                icon_dp: 26
                tint: GREEN if app.screen == 'player' else DIM
                on_release: app.go('player')
            IconButton:
                id: nav_online
                source: 'globe'
                icon_dp: 26
                tint: GREEN if app.screen == 'online' else DIM
                on_release: app.go('online')
            IconButton:
                id: nav_settings
                source: 'settings'
                icon_dp: 26
                tint: GREEN if app.screen == 'settings' else DIM
                on_release: app.go('settings')
    Toast:
        id: toast
    Confetti:
        id: confetti
"""


# ====================== приложение ======================
class PlayerApp(App):
    playing = BooleanProperty(False)
    has_track = BooleanProperty(False)
    seeking = BooleanProperty(False)
    dl_busy = BooleanProperty(False)
    screen = StringProperty("library")
    screen_title = StringProperty(TITLES["library"])
    mini_title = StringProperty("")
    loop_icon = StringProperty("repeat")
    loop_tint = ListProperty(LOOP_ICONS[0][1])
    speed_text = StringProperty("1.0x")
    online_status = StringProperty("Введи запрос и нажми поиск.")
    online_stage = StringProperty("results")
    dev_status = StringProperty("")
    ind = NumericProperty(0)
    src_hint = StringProperty("")
    bg_dim_text = StringProperty("")
    bg_speed_text = StringProperty("")
    bg_file_text = StringProperty("")
    jamendo_text = StringProperty("")

    # ---------- запуск ----------
    def build(self):
        self.title = "Мой плеер"
        self.last_error = ""
        self._bg_loading = True          # пока строится интерфейс, ползунки ничего не сохраняют
        ExceptionManager.add_handler(SafeHandler())   # ошибки больше не закрывают приложение
        Window.clearcolor = (0.06, 0.06, 0.08, 1)
        Window.softinput_mode = "below_target"
        if not ON_ANDROID:
            Window.size = (420, 820)
        self.data = {"playlists": {"Моя музыка": []}, "titles": {}, "covers": {}, "track_covers": {},
                     "bg": dict(BG_DEFAULT), "jamendo_id": ""}
        self.cur_src = 0
        self._dots_ev, self._dots_base, self._dots_i = None, "", 0
        self._rick_resume, self._cover_taps = False, []
        self._save_ev = None
        self._mode_chips, self._src_chips = {}, []
        self.cur_pl = "Моя музыка"
        self.queue, self.qi, self.queue_pl = [], -1, None
        self.loop_mode, self.speed_i = 0, SPEEDS.index(1.0)
        self.dev_all, self.dev_view, self.dev_sel = [], [], set()
        self.res_items, self.trk_items, self.trk_sel = [], [], set()
        self.cur_ident = None
        self.dl_cancel = False
        self._popup = None
        self.engine = AndroidEngine() if ON_ANDROID else DesktopEngine()
        self._focus_paused = False
        if ON_ANDROID:
            self.engine.on_focus = self.on_focus_change
        self.load()
        global ICON_DIR
        ICON_DIR = ensure_icons(Path(self.user_data_dir) / "icons")
        root = Builder.load_string(KV)
        self.lib, self.ply = root.ids.lib, root.ids.ply
        self.onl, self.dev = root.ids.onl, root.ids.dev
        root.ids.sm.transition = SlideTransition(duration=0.3)
        return root

    def on_start(self):
        self.ask_permissions()
        Window.bind(on_keyboard=self.on_key)
        self.refresh_playlists()
        self.show_playlist_cover()
        self.ply.ids.cover.intro()
        sm = self.root.ids.sm
        sm.opacity = 0
        Animation(opacity=1, d=0.6, t="out_cubic").start(sm)
        Clock.schedule_interval(self.tick, 0.25)
        Clock.schedule_interval(self.tick_ind, 0.26)
        self.setup_settings_ui()
        self.setup_sources_ui()
        self.apply_bg()
        self._bg_loading = False

    def on_pause(self):
        return True            # музыка продолжает играть в фоне

    def on_stop(self):
        self.dl_cancel = True
        if ON_ANDROID:
            self.engine.on_focus = None
            self.engine.abandon_focus()
        self.engine.release()

    def on_resume(self):
        try:
            if self.engine.loaded:
                self.tick(0)
        except Exception:
            pass
        if self._rick_resume:                # вернулись из браузера — продолжаем музыку
            self._rick_resume = False
            try:
                if self.engine.loaded and not self.playing:
                    self.engine.start()
                    self.set_playing(True)
            except Exception:
                pass

    # ---------- аудиофокус (Телеграм, звонки, другие плееры) ----------
    def on_focus_change(self, change):
        # вызывается из потока Java — переносим в основной поток Kivy
        Clock.schedule_once(lambda dt: self._focus(change), 0)

    def _focus(self, change):
        try:
            if change in (-1, -2):                  # потеряли звук (насовсем / ненадолго)
                if self.playing:
                    self._focus_paused = change == -2
                    self.engine.pause()
                    self.set_playing(False)
            elif change == -3:                      # можно тише (уведомление)
                self.engine.set_duck(True)
            elif change == 1:                       # звук вернули
                self.engine.set_duck(False)
                if self._focus_paused and not self.playing and self.engine.loaded:
                    self._focus_paused = False
                    self.engine.start()
                    self.set_playing(True)
        except Exception:
            pass

    def ask_permissions(self):
        if not ON_ANDROID:
            return
        try:
            from android.permissions import request_permissions
            request_permissions(["android.permission.READ_MEDIA_AUDIO",
                                 "android.permission.READ_EXTERNAL_STORAGE"], self._perm_cb)
        except Exception:
            pass

    def _perm_cb(self, perms, grants):
        Clock.schedule_once(lambda dt: self.scan_device() if self.screen == "device" else None, 0.3)

    def on_key(self, window, key, *args):
        if key != 27:                       # кнопка «Назад»
            return False
        if self._popup is not None and self._popup._window is not None:
            self._popup.dismiss()
            return True
        if self.screen != "library":
            self.go("library")
            return True
        if ON_ANDROID:                      # сворачиваем, а не закрываем — музыка играет
            from jnius import autoclass
            autoclass("org.kivy.android.PythonActivity").mActivity.moveTaskToBack(True)
            return True
        return False

    # ---------- данные ----------
    def load(self):
        p = Path(self.user_data_dir) / "player.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("playlists"):
                self.data["playlists"] = d["playlists"]
                self.data["titles"] = d.get("titles", {})
                self.data["covers"] = d.get("covers", {})
                self.data["track_covers"] = d.get("track_covers", {})
                self.data["bg"] = {**BG_DEFAULT, **d.get("bg", {})}
                self.data["jamendo_id"] = str(d.get("jamendo_id", ""))
                if d.get("current") in self.data["playlists"]:
                    self.cur_pl = d["current"]
        except Exception:
            pass
        JAMENDO["id"] = self.data.get("jamendo_id", "")
        if self.cur_pl not in self.data["playlists"]:
            self.cur_pl = next(iter(self.data["playlists"]))

    def save(self):
        try:
            Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
            out = {"playlists": self.data["playlists"], "titles": self.data["titles"],
                   "covers": self.data["covers"], "track_covers": self.data["track_covers"],
                   "bg": self.data["bg"], "jamendo_id": self.data.get("jamendo_id", ""),
                   "current": self.cur_pl}
            (Path(self.user_data_dir) / "player.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    def title_of(self, path):
        return self.data["titles"].get(path) or Path(path).stem

    def is_current(self, name, path):
        return (self.has_track and self.queue_pl == name and 0 <= self.qi < len(self.queue)
                and self.queue[self.qi] == path)

    # ---------- навигация и мелочи ----------
    def go(self, name):
        if name == self.screen:
            return
        order = ["library", "player", "online", "device", "settings"]
        sm = self.root.ids.sm
        sm.transition.direction = "left" if order.index(name) > order.index(self.screen) else "right"
        sm.current = name
        self.screen = name
        self.screen_title = TITLES[name]
        nav = self.root.ids.get("nav_" + ("library" if name == "device" else name))
        if nav is not None:
            nav.pop()
        self.stagger_in(sm.get_screen(name))
        if name == "device":
            self.dev_open()

    def stagger_in(self, screen):
        """Элементы экрана по очереди плавно проявляются."""
        if screen.name not in ("player", "settings"):
            return
        box = screen.children[0] if screen.children else None
        if isinstance(box, ScrollView) and box.children:
            box = box.children[0]
        if box is None:
            return
        for i, w in enumerate(reversed(box.children)):
            Animation.cancel_all(w, "opacity")
            w.opacity = 0
            Animation(opacity=1, d=0.35, t="out_cubic").start(w) if i == 0 else \
                (Animation(d=0.04 * i) + Animation(opacity=1, d=0.35, t="out_cubic")).start(w)

    def toast(self, text):
        t = self.root.ids.toast
        Animation.cancel_all(t)
        t.text = text
        t.opacity = 0
        t.y = dp(150) - dp(40)
        (Animation(opacity=1, y=dp(150), d=0.35, t="out_back") + Animation(d=2.0) +
         Animation(opacity=0, d=0.4)).start(t)

    def ask_text(self, title, initial, cb, hint=""):
        ti = Input(text=initial)
        box = BoxLayout(orientation="vertical", spacing=dp(12), padding=dp(12))
        if hint:
            lbl = Label(text=hint, size_hint_y=None, font_size=sp(13), halign="left",
                        valign="top", color=DIM)
            lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
                     texture_size=lambda w, s: setattr(w, "height", s[1]))
            box.add_widget(lbl)
        box.add_widget(ti)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        cancel = RoundButton(text="Отмена")
        ok = RoundButton(text="Готово", bg=GREEN, fg=[0, 0, 0, 1])
        row.add_widget(cancel)
        row.add_widget(ok)
        box.add_widget(row)
        pop = Popup(title=title, content=box, size_hint=(0.9, None),
                    height=dp(200) + (dp(110) if hint else 0), separator_color=GREEN)

        def done(*a):
            v = ti.text.strip()
            pop.dismiss()
            if v:
                cb(v)
        ok.bind(on_release=done)
        ti.bind(on_text_validate=done)
        cancel.bind(on_release=lambda *a: pop.dismiss())
        self._popup = pop
        pop.open()
        Clock.schedule_once(lambda dt: setattr(ti, "focus", True), 0.3)

    def confirm(self, text, cb):
        box = BoxLayout(orientation="vertical", spacing=dp(12), padding=dp(12))
        box.add_widget(Label(text=text, halign="center"))
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        cancel = RoundButton(text="Отмена")
        ok = RoundButton(text="Да", bg=GREEN, fg=[0, 0, 0, 1])
        row.add_widget(cancel)
        row.add_widget(ok)
        box.add_widget(row)
        pop = Popup(title="Подтверди", content=box, size_hint=(0.9, None), height=dp(200),
                    separator_color=GREEN)
        ok.bind(on_release=lambda *a: (pop.dismiss(), cb()))
        cancel.bind(on_release=lambda *a: pop.dismiss())
        self._popup = pop
        pop.open()

    # ---------- плейлисты ----------
    def refresh_playlists(self):
        box = self.lib.ids.chips
        box.clear_widgets()
        for name in self.data["playlists"]:
            c = Chip(text=name, active=(name == self.cur_pl))
            c.bind(on_release=lambda w, n=name: self.select_pl(n))
            box.add_widget(c)
        self.refresh_tracks(animate=True)

    def select_pl(self, name):
        if name == self.cur_pl:
            return
        self.cur_pl = name
        for c in self.lib.ids.chips.children:
            c.active = (c.text == name)
        self.save()
        self.refresh_tracks(animate=True)
        if not self.has_track:
            self.show_playlist_cover()

    def new_playlist(self):
        def cb(name):
            if name in self.data["playlists"]:
                self.toast("Такой плейлист уже есть")
                return
            self.data["playlists"][name] = []
            self.cur_pl = name
            self.save()
            self.refresh_playlists()
            self.toast(f"Создан плейлист: {name}")
        self.ask_text("Новый плейлист", "", cb)

    def rename_playlist(self):
        old = self.cur_pl

        def cb(new):
            if new == old:
                return
            if new in self.data["playlists"]:
                self.toast("Такой плейлист уже есть")
                return
            self.data["playlists"] = {(new if k == old else k): v
                                      for k, v in self.data["playlists"].items()}
            if self.queue_pl == old:
                self.queue_pl = new
            if old in self.data["covers"]:
                self.data["covers"][new] = self.data["covers"].pop(old)
            self.cur_pl = new
            self.save()
            self.refresh_playlists()
            self.toast("Плейлист переименован")
        self.ask_text("Переименовать плейлист", old, cb)

    def delete_playlist(self):
        if len(self.data["playlists"]) < 2:
            self.toast("Нельзя удалить последний плейлист")
            return
        name = self.cur_pl

        def go_on():
            del self.data["playlists"][name]
            self.remove_cover_file(self.data["covers"].pop(name, None))
            if self.queue_pl == name:
                self.queue_pl = None
            self.cur_pl = next(iter(self.data["playlists"]))
            self.save()
            self.refresh_playlists()
            self.toast("Плейлист удалён")
        self.confirm(f"Удалить плейлист «{name}»?\n(Сами файлы останутся на телефоне.)", go_on)

    # ---------- список треков ----------
    def refresh_tracks(self, animate=False):
        name = self.cur_pl
        lib = self.lib
        lib.ids.tracks_label.text = f"ТРЕКИ — {name.upper()}"
        paths = self.data["playlists"][name]
        rv = lib.ids.rv
        rv.data = [{"title": self.title_of(p), "idx": i, "playing": self.is_current(name, p)}
                   for i, p in enumerate(paths)]
        Animation.cancel_all(lib.ids.empty, "opacity")
        Animation(opacity=0 if paths else 1, d=0.3).start(lib.ids.empty)
        if animate:
            rv.opacity = 0
            Animation(opacity=1, d=0.4, t="out_cubic").start(rv)

    def mark_playing(self):
        rv = self.lib.ids.rv
        paths = self.data["playlists"][self.cur_pl]
        for i, d in enumerate(rv.data):
            if i < len(paths):
                d["playing"] = self.is_current(self.cur_pl, paths[i])
        rv.refresh_from_data()

    def add_paths(self, paths):
        lst = self.data["playlists"][self.cur_pl]
        n = 0
        for p in paths:
            if Path(p).suffix.lower() in AUDIO_EXT and p not in lst:
                lst.append(p)
                n += 1
        if n:
            self.save()
            self.refresh_tracks()
        return n

    def track_menu(self, idx):
        idx = int(idx)
        path = self.data["playlists"][self.cur_pl][idx]
        has_cover = path in self.data["track_covers"]
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        n = 4 + (1 if has_cover else 0)
        pop = Popup(title=self.title_of(path), content=box, size_hint=(0.9, None),
                    height=dp(70) + dp(58) * n, separator_color=GREEN)

        def add(icon, text, fn):
            b = RoundButton(text=text, icon=icon, size_hint_y=None, height=dp(48))
            b.bind(on_release=lambda *a: (pop.dismiss(), fn()))
            box.add_widget(b)
        add("play", "Играть", lambda: self.play_index(self.cur_pl, idx))
        add("pencil", "Переименовать", lambda: self.rename_track(idx))
        add("image", "Обложка трека", lambda: self.pick_track_cover(path))
        if has_cover:
            add("close", "Убрать свою обложку", lambda: self.reset_track_cover(path))
        add("trash", "Убрать из плейлиста", lambda: self.remove_track(idx))
        self._popup = pop
        pop.open()

    def rename_track(self, idx):
        path = self.data["playlists"][self.cur_pl][idx]

        def cb(new):
            if new == Path(path).stem:
                self.data["titles"].pop(path, None)       # вернули исходное имя
            else:
                self.data["titles"][path] = new
            self.save()
            self.refresh_tracks()
            if self.is_current(self.cur_pl, path):
                self.mini_title = new
                self.ply.type_text(new)
            self.toast(f"Переименовано: {new}")
        self.ask_text("Название трека", self.title_of(path), cb)

    def remove_track(self, idx):
        lst = self.data["playlists"][self.cur_pl]
        if 0 <= idx < len(lst):
            del lst[idx]
            self.save()
            self.refresh_tracks()
            self.toast("Трек убран из плейлиста")

    def play_from_list(self, idx):
        self.play_index(self.cur_pl, int(idx))

    # ---------- обложки ----------
    def cover_from_file(self, f):
        try:
            return decode_cover(Path(f).read_bytes())
        except Exception:
            return None, None

    def cover_for(self, path, pl):
        """Порядок: своя обложка трека, обложка из тегов, обложка альбома, заглушка."""
        if path:
            f = self.data["track_covers"].get(path)
            if f:
                tex, accent = self.cover_from_file(f)
                if tex is not None:
                    return tex, False, accent
            raw = embedded_cover_bytes(path)
            if raw:
                tex, accent = decode_cover(raw)
                if tex is not None:
                    return tex, False, accent
        f = self.data["covers"].get(pl)
        if f:
            tex, accent = self.cover_from_file(f)
            if tex is not None:
                return tex, False, accent
        tex, accent = placeholder_cover(pl)
        return tex, True, accent

    def apply_cover(self, tex, placeholder, accent):
        cv = self.ply.ids.cover
        cv.set_cover(tex, placeholder)
        glow = glow_from_accent(accent)
        cv.ring_color = glow
        self.ply.ids.eq.color = glow
        bg = self.root.ids.bg
        Animation.cancel_all(bg, "accent")
        Animation(accent=accent, d=0.9, t="out_cubic").start(bg)

    def show_playlist_cover(self):
        self.refresh_cover()

    def refresh_cover(self):
        """Перерисовать обложку на экране плеера (после смены картинки)."""
        if self.has_track and 0 <= self.qi < len(self.queue):
            tex, ph, accent = self.cover_for(self.queue[self.qi], self.queue_pl)
        else:
            tex, ph, accent = self.cover_for(None, self.cur_pl)
        self.apply_cover(tex, ph, accent)

    # ---------- выбор картинки для обложки ----------
    def covers_dir(self):
        d = Path(self.user_data_dir) / "covers"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def remove_cover_file(self, f):
        try:
            if f and Path(f).parent == self.covers_dir():
                Path(f).unlink(missing_ok=True)
        except Exception:
            pass

    def pick_image(self, callback, max_side=1400):
        """Открывает выбор фото. callback(путь_к_копии) вызывается в основном потоке."""
        if not ON_ANDROID:
            path = None
            try:
                import tkinter
                from tkinter import filedialog
                r = tkinter.Tk()
                r.withdraw()
                r.attributes("-topmost", True)
                src = filedialog.askopenfilename(
                    title="Выбери картинку",
                    filetypes=[("Картинки", "*.png *.jpg *.jpeg *.bmp"), ("Все файлы", "*.*")])
                r.destroy()
                if src:
                    path = str(self.covers_dir() / (uuid.uuid4().hex + Path(src).suffix.lower()))
                    shutil.copyfile(src, path)
            except Exception as e:
                self.toast(f"Не удалось выбрать: {e}")
            if path:
                callback(path)
            return
        from android import activity
        from jnius import autoclass
        self._img_cb = callback
        self._img_max = max_side
        Intent = autoclass("android.content.Intent")
        intent = Intent(Intent.ACTION_GET_CONTENT)
        intent.setType("image/*")
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        activity.bind(on_activity_result=self._on_activity_result)
        autoclass("org.kivy.android.PythonActivity").mActivity.startActivityForResult(intent, 4321)

    def _on_activity_result(self, request_code, result_code, intent):
        if request_code != 4321:
            return
        from android import activity
        activity.unbind(on_activity_result=self._on_activity_result)
        cb, self._img_cb = getattr(self, "_img_cb", None), None
        if result_code != -1 or intent is None or cb is None:      # -1 = RESULT_OK
            return
        uri = intent.getData()
        if uri is None:
            return
        dest = str(self.covers_dir() / (uuid.uuid4().hex + ".jpg"))

        def work():
            try:
                import_android_image(uri, dest, getattr(self, "_img_max", 1400))
            except Exception as e:
                self._image_done(None, cb, str(e))
                return
            self._image_done(dest, cb, "")
        threading.Thread(target=work, daemon=True).start()

    @mainthread
    def _image_done(self, path, cb, err):
        if path:
            cb(path)
        else:
            self.toast(f"Не удалось загрузить фото: {err}")

    def pick_album_cover(self, name):
        self.pick_image(lambda p: self.set_album_cover(name, p))

    def set_album_cover(self, name, path):
        self.remove_cover_file(self.data["covers"].get(name))
        self.data["covers"][name] = path
        self.save()
        if not self.has_track or self.queue_pl == name or name == self.cur_pl:
            self.refresh_cover()
        self.toast(f"Обложка альбома «{name}» изменена")

    def reset_album_cover(self, name):
        self.remove_cover_file(self.data["covers"].pop(name, None))
        self.save()
        self.refresh_cover()
        self.toast("Обложка альбома сброшена")

    def pick_track_cover(self, path):
        self.pick_image(lambda p: self.set_track_cover(path, p))

    def set_track_cover(self, path, img):
        self.remove_cover_file(self.data["track_covers"].get(path))
        self.data["track_covers"][path] = img
        self.save()
        if self.is_current(self.queue_pl, path):
            self.refresh_cover()
        self.toast("Обложка трека изменена")

    def reset_track_cover(self, path):
        self.remove_cover_file(self.data["track_covers"].pop(path, None))
        self.save()
        if self.is_current(self.queue_pl, path):
            self.refresh_cover()
        self.toast("Своя обложка трека убрана")

    def choose_album_for_cover(self):
        """Настройки: выбираешь альбом -> выбираешь фото."""
        names = list(self.data["playlists"])
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        pop = Popup(title="Для какого альбома обложка?", content=box, size_hint=(0.92, None),
                    height=min(dp(130) + dp(56) * len(names), Window.height * 0.8),
                    separator_color=GREEN)
        sv = ScrollView()
        inner = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        inner.bind(minimum_height=inner.setter("height"))
        for name in names:
            row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
            b = RoundButton(text=name, icon="image")
            b.bind(on_release=lambda w, n=name: (pop.dismiss(), self.pick_album_cover(n)))
            row.add_widget(b)
            if name in self.data["covers"]:
                t = IconButton(source="trash", size_hint=(None, 1), width=dp(46))
                t.bind(on_release=lambda w, n=name: (pop.dismiss(), self.reset_album_cover(n)))
                row.add_widget(t)
            inner.add_widget(row)
        sv.add_widget(inner)
        box.add_widget(sv)
        cancel = RoundButton(text="Отмена", size_hint_y=None, height=dp(46))
        cancel.bind(on_release=lambda *a: pop.dismiss())
        box.add_widget(cancel)
        self._popup = pop
        pop.open()

    # ---------- воспроизведение ----------
    def play_index(self, pl, idx):
        tracks = list(self.data["playlists"].get(pl, []))
        if not 0 <= idx < len(tracks):
            return
        path = tracks[idx]
        if not os.path.exists(path):
            self.toast("Файл не найден — убери его из списка")
            return
        try:
            self.engine.load(path)
        except Exception:
            self.toast("Не удалось открыть этот файл")
            return
        self.queue, self.qi, self.queue_pl = tracks, idx, pl
        self.engine.set_speed(SPEEDS[self.speed_i])
        self.engine.start()
        self.has_track = True
        self.set_playing(True)
        title = self.title_of(path)
        self.mini_title = title
        self.ply.type_text(title)
        self.ply.album = pl
        self.ply.seek_max = max(self.engine.length, 1)
        self.ply.seek_val = 0
        self.ply.dur_text = fmt_time(self.engine.length)
        tex, placeholder, accent = self.cover_for(path, pl)
        self.apply_cover(tex, placeholder, accent)
        self.ply.ids.cover.bump()
        self.mark_playing()

    def set_playing(self, on):
        self.playing = on
        self.ply.ids.cover.set_playing(on)
        self.ply.ids.eq.active = on
        self.ply.ids.play_btn.pop()
        self.root.ids.mini.ids.mplay.pop()

    def toggle_play(self):
        self._focus_paused = False
        if self.playing:
            self.engine.pause()
            self.set_playing(False)
        elif self.engine.loaded and self.queue:
            self.engine.start()
            self.set_playing(True)
        else:
            lst = self.data["playlists"][self.cur_pl]
            if lst:
                self.play_index(self.cur_pl, 0)
            else:
                self.toast("Сначала добавь музыку")

    def next_track(self):
        if not self.queue:
            return
        if self.qi + 1 < len(self.queue):
            self.play_index(self.queue_pl, self.qi + 1)
        elif self.loop_mode == 1:
            self.play_index(self.queue_pl, 0)
        else:
            self.toast("Это последний трек")

    def prev_track(self):
        if not self.queue:
            return
        if self.engine.position > 3 or self.qi == 0:
            self.engine.seek(0)
        else:
            self.play_index(self.queue_pl, self.qi - 1)

    def on_end(self):
        if self.loop_mode == 2:
            self.engine.seek(0)
            self.engine.start()
        elif self.qi + 1 < len(self.queue):
            self.play_index(self.queue_pl, self.qi + 1)
        elif self.loop_mode == 1 and self.queue:
            self.play_index(self.queue_pl, 0)
        else:
            self.engine.stop()
            self.set_playing(False)
            self.ply.seek_val = 0
            self.ply.pos_text = "0:00"

    def finish_seek(self, value):
        self.seeking = False
        if self.engine.loaded:
            self.engine.seek(value)
            self.ply.pos_text = fmt_time(value)

    def cycle_loop(self):
        self.loop_mode = (self.loop_mode + 1) % 3
        self.loop_icon, self.loop_tint = LOOP_ICONS[self.loop_mode][0], LOOP_ICONS[self.loop_mode][1]
        self.toast(["Повтор выключен", "Повтор плейлиста", "Повтор трека"][self.loop_mode])

    def cycle_speed(self):
        self.speed_i = (self.speed_i + 1) % len(SPEEDS)
        s = SPEEDS[self.speed_i]
        self.engine.set_speed(s)
        self.speed_text = f"{s}x"
        if ON_ANDROID is False:
            self.toast("Скорость работает только на Android")

    def tick(self, dt):
        e = self.engine
        if not e.loaded:
            return
        if e.ended():
            self.on_end()
            return
        if getattr(e, "stalled", False):
            e.stalled = False
            if self.playing:
                self._focus_paused = True
                self.set_playing(False)
            return
        if self.seeking:
            return
        pos, dur = e.position, e.length
        p = self.ply
        if dur > 0 and abs(p.seek_max - dur) > 0.5:
            p.seek_max = dur
            p.dur_text = fmt_time(dur)
        p.seek_val = pos
        p.pos_text = fmt_time(pos)

    def tick_ind(self, dt):
        if self.playing:
            self.ind += 1

    # ---------- музыка на телефоне ----------
    def dev_open(self):
        if not self.dev_all:
            self.scan_device()

    def scan_device(self):
        self.dev_status = "Ищу музыку на телефоне…"

        def work():
            try:
                items = scan_audio()
            except Exception as e:
                self._device_failed(str(e))
                return
            self._device_ready(items)
        threading.Thread(target=work, daemon=True).start()

    @mainthread
    def _device_failed(self, msg):
        self.dev_status = f"Не получилось: {msg}"

    @mainthread
    def _device_ready(self, items):
        self.dev_all = items
        if not items:
            self.dev_status = "Музыки не найдено. Проверь, что разрешён доступ к аудио."
        self.device_filter(self.dev.ids.flt.text)

    def device_filter(self, text):
        q = text.strip().lower()
        self.dev_view = [it for it in self.dev_all if not q or q in (it[0] + " " + it[1]).lower()]
        self._set_rows(self.dev.ids.rv, [
            {"title": t, "subtitle": a, "idx": i, "selected": p in self.dev_sel,
             "show_check": True, "owner": "device"}
            for i, (t, a, p) in enumerate(self.dev_view)], animate=False)
        if self.dev_all:
            self.dev_status = f"Найдено: {len(self.dev_view)}.  Выбрано: {len(self.dev_sel)}"

    def device_add(self):
        paths = [p for _t, _a, p in self.dev_all if p in self.dev_sel]
        if not paths:
            shake(self.dev.ids.rv)
            self.toast("Сначала выбери треки")
            return
        n = self.add_paths(paths)
        self.dev_sel.clear()
        self.device_filter(self.dev.ids.flt.text)
        self.go("library")
        self.toast(f"Добавлено треков: {n}")

    def _set_rows(self, rv, rows, animate=True):
        rv.data = rows
        if animate:
            rv.opacity = 0
            rv.scroll_y = 1
            Animation(opacity=1, d=0.35, t="out_cubic").start(rv)

    def _toggle_row(self, rv, sel, key, idx):
        if key in sel:
            sel.discard(key)
        else:
            sel.add(key)
        rv.data[idx]["selected"] = key in sel
        rv.refresh_from_data()

    def row_tap(self, owner, idx):
        idx = int(idx)
        if owner == "device":
            if 0 <= idx < len(self.dev_view):
                self._toggle_row(self.dev.ids.rv, self.dev_sel, self.dev_view[idx][2], idx)
                self.dev_status = f"Найдено: {len(self.dev_view)}.  Выбрано: {len(self.dev_sel)}"
        elif owner == "online":
            if self.online_stage == "results":
                self.open_result(idx)
            elif 0 <= idx < len(self.trk_items):
                self._toggle_row(self.onl.ids.rv, self.trk_sel, idx, idx)

    # ---------- интернет ----------
    def online_search(self, text):
        text = text.strip()
        if not text:
            self.online_status = "Сначала введи, что искать."
            shake(self.onl.ids.q)
            return
        src = SOURCES[self.cur_src]
        if src[0] == "Jamendo" and not JAMENDO["id"]:
            self.ask_jamendo_key(lambda: self.online_search(text))
            return
        self.start_dots("Ищу")

        def work():
            try:
                items = src[1](text)
            except Exception as e:
                Logger.exception("Player: ошибка поиска")
                self._online_error(str(e) or repr(e))
                return
            self._search_done(items)
        threading.Thread(target=work, daemon=True).start()

    @mainthread
    def _online_error(self, msg):
        self.stop_dots()
        if SOURCES[self.cur_src][0] == "Jamendo" and "client" in msg.lower():
            self.set_jamendo_key("")          # ключ не подошёл — спросим заново
        self.online_status = f"Не получилось (нет интернета или сайт не отвечает): {msg}"

    @mainthread
    def _search_done(self, items):
        self.stop_dots()
        self.res_items = items
        self.show_results()

    def show_results(self):
        self.stop_dots()
        self.online_stage = "results"
        self._set_rows(self.onl.ids.rv, [
            {"title": t, "subtitle": "", "idx": i, "selected": False,
             "show_check": False, "owner": "online"} for i, (t, _id) in enumerate(self.res_items)])
        self.online_status = (f"Найдено: {len(self.res_items)}. Выбери запись."
                              if self.res_items else "Ничего не найдено. Попробуй другой запрос.")

    def online_back(self):
        self.show_results()

    def open_result(self, idx):
        if not 0 <= idx < len(self.res_items):
            return
        ident = self.res_items[idx][1]
        self.cur_ident = ident
        get_tracks = SOURCES[self.cur_src][2]
        self.start_dots("Загружаю список треков")

        def work():
            try:
                tracks = get_tracks(ident)
            except Exception as e:
                Logger.exception("Player: ошибка списка треков")
                self._online_error(str(e) or repr(e))
                return
            self._tracks_done(ident, tracks)
        threading.Thread(target=work, daemon=True).start()

    @mainthread
    def _tracks_done(self, ident, tracks):
        if ident != self.cur_ident:
            return
        self.stop_dots()
        if not tracks:
            self.online_status = "В этой записи нет mp3/ogg — выбери другую."
            return
        self.trk_items, self.trk_sel = tracks, set()
        self.online_stage = "tracks"
        self._set_rows(self.onl.ids.rv, [
            {"title": label, "subtitle": "", "idx": i, "selected": False,
             "show_check": True, "owner": "online"} for i, (label, _j) in enumerate(tracks)])
        self.online_status = f"Треков: {len(tracks)}. Отметь нужные и нажми «Скачать»."

    def download_selected(self):
        if self.online_stage != "tracks":
            self.online_status = "Сначала выбери запись, потом отметь треки."
            shake(self.onl.ids.rv)
            return
        jobs = [self.trk_items[i][1] for i in sorted(self.trk_sel)]
        if not jobs:
            self.online_status = "Отметь треки в списке."
            shake(self.onl.ids.rv)
            return
        self.start_download(jobs)

    def download_url(self, text):
        url = text.strip()
        if not url.lower().startswith(("http://", "https://")):
            self.online_status = "Ссылка должна начинаться с http:// или https://"
            shake(self.onl.ids.url)
            return
        name = unquote(Path(urlparse(url).path).name) or "track"
        self.start_download([{"url": url, "name": name}])

    def dl_dir(self):
        if ON_ANDROID:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            d = act.getExternalFilesDir(None)
            base = d.getAbsolutePath() if d is not None else self.user_data_dir
            return Path(base) / "music"
        return Path.home() / "Music" / "MyMusicPlayer"

    def start_download(self, jobs):
        if self.dl_busy:
            return
        self.dl_busy = True
        self.dl_cancel = False
        bar = self.onl.ids.bar
        Animation.cancel_all(bar)
        bar.value = 0
        try:
            dest = self.dl_dir()
        except Exception:
            Logger.exception("Player: нет внешней папки, пишем во внутреннюю")
            dest = Path(self.user_data_dir) / "music"
        threading.Thread(target=self._dl_work, args=(jobs, dest), daemon=True).start()

    def _dl_work(self, jobs, dest):
        last = -1
        try:
            dest.mkdir(parents=True, exist_ok=True)
            n = len(jobs)
            for i, job in enumerate(jobs):
                req = urllib.request.Request(job["url"], headers=UA)
                with urllib.request.urlopen(req, timeout=20, context=ssl_ctx()) as r:
                    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    if ctype.startswith("text/"):
                        raise RuntimeError("Ссылка ведёт на веб-страницу, а не на аудиофайл.")
                    name = safe_name(job["name"])
                    if Path(name).suffix.lower() not in AUDIO_EXT:
                        name += MIME_EXT.get(ctype, ".mp3")
                    path = unique_path(dest / name)
                    part = path.with_name(path.name + ".part")
                    total = int(r.headers.get("Content-Length") or 0)
                    got = 0
                    try:
                        with open(part, "wb") as f:
                            while True:
                                if self.dl_cancel:
                                    raise RuntimeError("Скачивание отменено.")
                                chunk = r.read(65536)
                                if not chunk:
                                    break
                                f.write(chunk)
                                got += len(chunk)
                                pct = int((i + (got / total if total else 0.5)) / n * 100)
                                if pct != last:
                                    last = pct
                                    self._dl_progress(pct, path.stem)
                    except Exception:
                        part.unlink(missing_ok=True)
                        raise
                part.replace(path)
                self._dl_got(str(path))
            self._dl_done()
        except BaseException as e:
            Logger.exception("Player: ошибка скачивания")
            self._dl_failed(str(e) or repr(e))

    @mainthread
    def _dl_progress(self, pct, name):
        self.online_status = f"Скачиваю: {name}"
        bar = self.onl.ids.bar
        Animation.cancel_all(bar, "value")
        Animation(value=pct, d=0.25).start(bar)

    @mainthread
    def _dl_got(self, path):
        if self.add_paths([path]):
            self.toast(f"Добавлено: {Path(path).stem}")

    @mainthread
    def _dl_done(self):
        self.dl_busy = False
        Animation(value=100, d=0.25).start(self.onl.ids.bar)
        self.online_status = "Готово! Треки добавлены в текущий плейлист."
        try:
            self.root.ids.confetti.burst()          # салют
        except Exception:
            Logger.exception("Player: конфетти")

    @mainthread
    def _dl_failed(self, msg):
        self.dl_busy = False
        self.online_status = f"Ошибка скачивания: {msg}"

    # ---------- фон (настройки) ----------
    def setup_settings_ui(self):
        st = self.root.ids.sett
        cfg = self.data["bg"]
        grid = st.ids.modes
        grid.clear_widgets()
        self._mode_chips = {}
        for key, title in BG_MODES:
            c = ModeChip(text=title, active=(cfg["mode"] == key), size_hint_y=None, height=dp(44))
            c.bind(on_release=lambda w, k=key: self.set_bg_mode(k))
            grid.add_widget(c)
            self._mode_chips[key] = c
        st.ids.dim_sl.value = cfg["dim"]
        st.ids.speed_sl.value = cfg["speed"]
        self.update_bg_texts()

    def update_bg_texts(self):
        cfg = self.data["bg"]
        self.bg_dim_text = f"{cfg['dim']}%"
        self.bg_speed_text = f"{cfg['speed']}%"
        if cfg["mode"] == "image":
            self.bg_file_text = ("Обои выбраны." if cfg.get("image")
                                 else "Картинка не выбрана — нажми кнопку ниже.")
        else:
            self.bg_file_text = ""
        self.jamendo_text = "Ключ Jamendo задан." if JAMENDO["id"] else "Ключ Jamendo не задан."

    def apply_bg(self):
        self.root.ids.bg.apply(self.data["bg"])

    def set_bg_mode(self, key):
        cfg = self.data["bg"]
        cfg["mode"] = key
        for k, c in self._mode_chips.items():
            c.active = (k == key)
        self.update_bg_texts()
        self.apply_bg()
        self.save()
        if key == "image" and not cfg.get("image"):
            self.toast("Выбери картинку для фона")

    def on_bg_slider(self, key, val):
        if self._bg_loading:
            return
        self.data["bg"][key] = int(val)
        self.update_bg_texts()
        self.apply_bg()
        if self._save_ev is not None:
            self._save_ev.cancel()
        self._save_ev = Clock.schedule_once(lambda dt: self.save(), 0.6)

    def pick_bg_image(self):
        self.pick_image(self._bg_image_chosen, max_side=2600)

    def _bg_image_chosen(self, path):
        cfg = self.data["bg"]
        self.remove_cover_file(cfg.get("image"))
        cfg["image"], cfg["mode"] = path, "image"
        for k, c in self._mode_chips.items():
            c.active = (k == "image")
        self.update_bg_texts()
        self.apply_bg()
        self.save()
        self.toast("Обои установлены")

    def reset_bg(self):
        cfg = self.data["bg"]
        self.remove_cover_file(cfg.get("image"))
        cfg.clear()
        cfg.update(BG_DEFAULT)
        self._bg_loading = True
        self.setup_settings_ui()
        self._bg_loading = False
        self.apply_bg()
        self.save()
        self.toast("Фон сброшен")

    # ---------- источники музыки ----------
    def setup_sources_ui(self):
        box = self.onl.ids.srcs
        box.clear_widgets()
        self._src_chips = []
        for i, src in enumerate(SOURCES):
            c = Chip(text=src[0], active=(i == self.cur_src))
            c.bind(on_release=lambda w, i=i: self.set_source(i))
            box.add_widget(c)
            self._src_chips.append(c)
        self.src_hint = SOURCES[self.cur_src][3]

    def set_source(self, i):
        if i == self.cur_src:
            return
        self.cur_src, self.cur_ident = i, None
        for k, c in enumerate(self._src_chips):
            c.active = (k == i)
        self.stop_dots()
        self.res_items, self.trk_items, self.trk_sel = [], [], set()
        self.online_stage = "results"
        self.onl.ids.rv.data = []
        self.src_hint = SOURCES[i][3]
        hint = self.onl.ids.hint                    # подсказка плавно проявляется
        Animation.cancel_all(hint, "opacity")
        hint.opacity = 0
        Animation(opacity=1, d=0.5).start(hint)
        self.online_status = f"Источник: {SOURCES[i][0]}. Введи запрос и нажми поиск."

    def start_dots(self, base):
        """Бегущие точки: «Ищу», «Ищу.», «Ищу..»…"""
        self._dots_base, self._dots_i = base, 0
        self.online_status = base
        if self._dots_ev is None:
            self._dots_ev = Clock.schedule_interval(self._dots_tick, 0.35)

    def stop_dots(self):
        if self._dots_ev is not None:
            self._dots_ev.cancel()
            self._dots_ev = None

    def _dots_tick(self, dt):
        self._dots_i += 1
        self.online_status = self._dots_base + "." * (self._dots_i % 4)

    def ask_jamendo_key(self, then=None):
        def cb(key):
            self.set_jamendo_key(key)
            if then:
                then()
        self.ask_text("Ключ Jamendo", JAMENDO["id"], cb,
                      hint="Нужен бесплатный ключ (client_id): зарегистрируйся на "
                           "devportal.jamendo.com, создай приложение и вставь сюда его client_id.")

    def set_jamendo_key(self, key):
        JAMENDO["id"] = key
        self.data["jamendo_id"] = key
        self.save()
        self.update_bg_texts()

    def sites_menu(self):
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        pop = Popup(title="Открыть сайт в браузере", content=box, size_hint=(0.92, None),
                    height=dp(90) + dp(58) * len(SITES), separator_color=GREEN)
        for title, link in SITES:
            b = RoundButton(text=title, icon="globe", size_hint_y=None, height=dp(48),
                            font_size=sp(13))
            b.bind(on_release=lambda w, u=link: (pop.dismiss(), open_url(u)))
            box.add_widget(b)
        self._popup = pop
        pop.open()

    # ---------- тап по обложке и пасхалка ----------
    def cover_tapped(self):
        """Обложка подпрыгивает (это делает сама). Семь быстрых тапов — сюрприз."""
        now = time.monotonic()
        self._cover_taps = [t for t in self._cover_taps if now - t < 3.0] + [now]
        if len(self._cover_taps) >= 7:
            self._cover_taps = []
            self.rickroll()

    def rickroll(self):
        self._rick_resume = self.playing
        if self.playing:
            self.engine.pause()
            self.set_playing(False)
        open_url(RICK_URL)

    # ---------- ошибки ----------
    def report_error(self, tb):
        self.last_error = tb
        try:
            (Path(self.user_data_dir) / "last_error.txt").write_text(tb, encoding="utf-8")
        except Exception:
            pass
        last = tb.strip().splitlines()[-1] if tb.strip() else "ошибка"
        try:
            self.toast("Ошибка: " + last[:70])
        except Exception:
            pass

    def show_error_log(self):
        text = self.last_error or "Ошибок пока не было."
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
        sv = ScrollView()
        lbl = Label(text=text, size_hint_y=None, font_size=sp(11), halign="left", valign="top",
                    color=LIGHT)
        lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
                 texture_size=lambda w, s: setattr(w, "height", s[1]))
        sv.add_widget(lbl)
        box.add_widget(sv)
        close = RoundButton(text="Закрыть", size_hint_y=None, height=dp(46))
        box.add_widget(close)
        pop = Popup(title="Журнал ошибок", content=box, size_hint=(0.94, 0.8), separator_color=GREEN)
        close.bind(on_release=lambda *a: pop.dismiss())
        self._popup = pop
        pop.open()


if __name__ == "__main__":
    PlayerApp().run()
