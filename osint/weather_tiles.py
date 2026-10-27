"""The moving weather on the globe: cloud cover and a rain-radar loop.

Both are public and keyless:

  clouds  NASA GIBS, VIIRS true colour — a daily global mosaic (gibs.earthdata.nasa.gov)
  rain    RainViewer radar, thirteen frames at ten-minute steps (rainviewer.com)

Our globe library has no tiled-imagery layer, and its bundle keeps three.js to itself, so there
is no way to add a second sphere from the page. What there is, is one texture slot. So the
frames are composed here instead: the cloud mosaic with a radar frame laid over it, one image
per step, written next to the component for the browser to flip through.

Radar only exists where somebody built a radar — North America, Europe, Japan, Australia and
patches elsewhere. Empty ocean in this layer means no coverage, not no rain.
"""
from __future__ import annotations

import datetime
import io
import math
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFilter

from osint.globe import DIR as COMPONENT_DIR

FRAMES_DIR = COMPONENT_DIR / "frames"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; axn-pulse-news-monitor/1.0)"}
WIDTH, HEIGHT = 1536, 768           # the texture the globe is wrapped in; six of these
                                    # cross the wire, so it is kept modest
ZOOM = 2                            # 4x4 radar tiles covers the world


def _mercator_to_equirect(img: Image.Image) -> Image.Image:
    """Web Mercator squeezes the poles; the globe wants even steps of latitude."""
    source = np.array(img.resize((WIDTH, img.size[1]), Image.BILINEAR))
    rows = source.shape[0]
    out = np.empty((HEIGHT, WIDTH, source.shape[2]), dtype=source.dtype)
    for j in range(HEIGHT):
        lat = max(-85.05113, min(85.05113, 90 - (j + 0.5) * 180 / HEIGHT))
        y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2
        out[j] = source[min(rows - 1, int(y * rows))]
    return Image.fromarray(out)


def _tile_mosaic(url_for, zoom: int, session: requests.Session | None = None) -> Image.Image:
    """Stitch a whole world of Web Mercator tiles, then square it up into equirectangular."""
    session = session or requests.Session()
    size = 2 ** zoom
    canvas = Image.new("RGBA", (256 * size, 256 * size), (0, 0, 0, 0))

    def tile(xy):
        x, y = xy
        try:
            r = session.get(url_for(x, y, zoom), headers=HEADERS, timeout=20)
            return x, y, (r.content if r.status_code == 200 else None)
        except requests.RequestException:
            return x, y, None

    with ThreadPoolExecutor(max_workers=16) as pool:
        for x, y, data in pool.map(tile, [(x, y) for x in range(size) for y in range(size)]):
            if data:
                try:
                    canvas.paste(Image.open(io.BytesIO(data)).convert("RGBA"), (256 * x, 256 * y))
                except Exception:
                    pass                      # one unreadable tile is a hole, not a failure
    return _mercator_to_equirect(canvas)


def _radar_frame(host: str, path: str, session: requests.Session) -> Image.Image:
    return _tile_mosaic(lambda x, y, z: f"{host}{path}/256/{z}/{x}/{y}/2/1_1.png", ZOOM, session)


def _shade_night(picture: Image.Image, sun: dict) -> Image.Image:
    """Dim the half of the world the sun is not on, with a real twilight between.

    A flat dark disc laid over the globe gives a hard rim no dusk ever has. Working out the
    sun's elevation for every pixel instead costs one array pass and gives the band of
    half-light that actually exists — civil twilight to night across about 18 degrees.
    """
    lat = np.radians(np.linspace(90, -90, HEIGHT))[:, None]
    lng = np.radians(np.linspace(-180, 180, WIDTH))[None, :]
    declination, meridian = math.radians(sun["lat"]), math.radians(sun["lng"])
    cos_zenith = (np.sin(lat) * math.sin(declination)
                  + np.cos(lat) * math.cos(declination) * np.cos(lng - meridian))
    elevation = np.degrees(np.arcsin(np.clip(cos_zenith, -1, 1)))
    daylight = np.clip((elevation + 12) / 18, 0, 1)        # -12° is night, +6° is full day
    factor = (0.22 + 0.78 * daylight)[:, :, None]
    return Image.fromarray((np.asarray(picture).astype(np.float32) * factor).astype(np.uint8))


@st.cache_data(ttl=86400, show_spinner=False)
def _satellite_base() -> bytes | None:
    """Esri's imagery, one world at a time — the same picture the globe's tile engine shows,
    flattened so the weather can be laid over it."""
    try:
        mosaic = _tile_mosaic(
            lambda x, y, z: ("https://server.arcgisonline.com/ArcGIS/rest/services/"
                             f"World_Imagery/MapServer/tile/{z}/{y}/{x}"), 3)
        buffer = io.BytesIO()
        mosaic.convert("RGB").save(buffer, "JPEG", quality=76)
        return buffer.getvalue()
    except Exception:
        return None


def _cloud_mask(raw: bytes) -> Image.Image:
    """White cloud over transparent everything else.

    The mosaic is a photograph of the whole earth, so used as-is it reads as terrain, not
    weather. Cloud is the one thing in it that is both bright and colourless — that is what is
    kept. Snow and bright desert come through faintly too; there is no free global cloud mask
    that updates by the hour, and this is the honest approximation.
    """
    photo = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB").resize((WIDTH, HEIGHT),
                                                                        Image.BILINEAR)).astype(np.float32) / 255
    brightest = photo.max(axis=2)
    spread = brightest - photo.min(axis=2)
    saturation = np.divide(spread, np.maximum(brightest, 1e-3))
    alpha = np.clip((brightest - 0.42) / 0.38, 0, 1) * np.clip(1 - saturation * 2.3, 0, 1)
    layer = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
    layer[..., :3] = 255
    layer[..., 3] = (alpha * 235).astype(np.uint8)
    return Image.fromarray(layer).filter(ImageFilter.GaussianBlur(0.6))


@st.cache_data(ttl=10800, show_spinner=False)
def _wind_grid(step_deg: int = 10) -> tuple | None:
    """A coarse global wind field from Open-Meteo, as two arrays of east and north components."""
    lats = np.arange(80, -81, -step_deg, dtype=float)
    lngs = np.arange(-180, 180, step_deg, dtype=float)
    points = [(la, ln) for la in lats for ln in lngs]
    speed = np.zeros((len(lats), len(lngs)))
    bearing = np.zeros_like(speed)
    try:
        for start in range(0, len(points), 250):          # 300 answer fine in one call; 250 is safe
            chunk = points[start:start + 250]
            r = requests.get("https://api.open-meteo.com/v1/forecast",
                             params={"latitude": ",".join(f"{la:.1f}" for la, _ in chunk),
                                     "longitude": ",".join(f"{ln:.1f}" for _, ln in chunk),
                                     "current": "wind_speed_10m,wind_direction_10m"},
                             headers=HEADERS, timeout=60)
            r.raise_for_status()
            payload = r.json()
            payload = payload if isinstance(payload, list) else [payload]
            for offset, place in enumerate(payload):
                now = place.get("current") or {}
                index = start + offset
                row, col = divmod(index, len(lngs))
                speed[row, col] = now.get("wind_speed_10m") or 0
                bearing[row, col] = now.get("wind_direction_10m") or 0
    except Exception:
        return None
    # meteorological bearing is where the wind comes FROM
    radians = np.radians(bearing)
    u = -speed * np.sin(radians)
    v = -speed * np.cos(radians)
    return lats, lngs, u, v


def _wind_streaks(steps: int, seeds: int = 2200, tail: int = 10, trace: int = 52) -> list[Image.Image] | None:
    """One overlay per step, each showing the same streamlines a little further along.

    Flipping through them is what makes the wind appear to move: every streak is a short comet
    sliding down a path the field itself drew.
    """
    grid = _wind_grid()
    if grid is None:
        return None
    lats, lngs, u, v = grid

    rng = np.random.default_rng(7)                      # a fixed field, so the loop never jumps
    lat = rng.uniform(-72, 72, seeds)
    lng = rng.uniform(-180, 180, seeds)
    paths = np.empty((trace, seeds, 2), dtype=np.float32)

    def sample(field, la, ln):
        """Bilinear, because nearest-cell on a 10° grid turns every streak into a zigzag."""
        fy = np.clip((lats[0] - la) / (lats[0] - lats[-1]) * (len(lats) - 1), 0, len(lats) - 1.001)
        fx = ((ln - lngs[0]) / 360) * len(lngs) % len(lngs)
        y0, x0 = fy.astype(int), fx.astype(int)
        y1, x1 = np.minimum(y0 + 1, len(lats) - 1), (x0 + 1) % len(lngs)
        ty, tx = fy - y0, fx - x0
        top = field[y0, x0] * (1 - tx) + field[y0, x1] * tx
        bottom = field[y1, x0] * (1 - tx) + field[y1, x1] * tx
        return top * (1 - ty) + bottom * ty

    for t in range(trace):
        paths[t, :, 0], paths[t, :, 1] = lng, lat
        east, north = sample(u, lat, lng), sample(v, lat, lng)
        stretch = 0.035 / np.maximum(np.cos(np.radians(lat)), 0.3)
        lng = ((lng + east * stretch + 180) % 360) - 180
        lat = np.clip(lat + north * 0.035, -84, 84)

    overlays = []
    for frame in range(steps):
        layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        head = int(frame * (trace - tail) / max(1, steps))
        for i in range(tail):
            a, b = paths[head + i], paths[head + i + 1]
            fade = int(18 + 120 * (i / tail))           # the tail dims behind the head
            for s in range(seeds):
                x0 = (a[s, 0] + 180) / 360 * WIDTH
                x1 = (b[s, 0] + 180) / 360 * WIDTH
                if abs(x1 - x0) > WIDTH / 2:            # a streak that wrapped the dateline
                    continue
                y0 = (90 - a[s, 1]) / 180 * HEIGHT
                y1 = (90 - b[s, 1]) / 180 * HEIGHT
                pen.line([(x0, y0), (x1, y1)], fill=(175, 235, 255, fade), width=1)
        overlays.append(layer)
    return overlays


@st.cache_data(ttl=21600, show_spinner=False)
def _clouds() -> bytes | None:
    """Yesterday's global mosaic — today's is still being flown and comes back half empty."""
    day = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    try:
        r = requests.get("https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi",
                         params={"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0",
                                 "LAYERS": "VIIRS_SNPP_CorrectedReflectance_TrueColor",
                                 "CRS": "EPSG:4326", "BBOX": "-90,-180,90,180",
                                 "WIDTH": WIDTH, "HEIGHT": HEIGHT, "FORMAT": "image/jpeg",
                                 "TIME": day},
                         headers=HEADERS, timeout=90)
        r.raise_for_status()
        return r.content if len(r.content) > 60_000 else None      # a stub means no mosaic yet
    except requests.RequestException:
        return None


@st.cache_data(ttl=600, show_spinner=False)
def frames(count: int = 6, with_clouds: bool = True, with_rain: bool = True,
           with_wind: bool = False, satellite: bool = False,
           night: dict | None = None) -> tuple[list[dict], str]:
    """Build the loop and return [{url, label}], newest last, plus a note if a source failed.

    Each step is one composed texture: a dark earth, the cloud mask over it, the wind streaks
    a little further along than the step before, and that minute's radar on top. The urls are
    relative to the component, which is what the browser asks for.
    """
    if not any((with_clouds, with_rain, with_wind, night)):
        return [], ""

    notes = []
    base = Image.new("RGB", (WIDTH, HEIGHT), (7, 12, 20))

    if satellite:
        # Keep the satellite look while the weather plays over it: the globe has one surface,
        # so the imagery has to be *in* the frames rather than under them.
        raw = _satellite_base()
        if raw:
            base = Image.open(io.BytesIO(raw)).convert("RGB").resize((WIDTH, HEIGHT), Image.BILINEAR)
            base = Image.eval(base, lambda v: int(v * 0.85))
        else:
            notes.append("Satellite imagery unavailable (Esri)")
    elif with_clouds:
        raw = _clouds()
        if raw:
            # The mosaic is a photograph of the earth — cloud, land and sea together. Deriving a
            # cloud-only mask from it was tried and dropped: at this scale the ocean is as bright
            # and as colourless as the cloud, so the mask whited out the whole globe. The free
            # cloud-fraction products render a ramp over every pixel, which is no better. So the
            # photograph goes on as it is, dimmed to sit under the map and the dots.
            clouds = Image.open(io.BytesIO(raw)).convert("RGB").resize((WIDTH, HEIGHT), Image.BILINEAR)
            base = Image.eval(clouds, lambda v: int(v * 0.5))
        else:
            notes.append("Cloud mosaic unavailable (NASA GIBS)")
    clouds = None

    rain: list[tuple[str, Image.Image]] = []
    if with_rain:
        try:
            maps = requests.get("https://api.rainviewer.com/public/weather-maps.json",
                                headers=HEADERS, timeout=25).json()
            host, past = maps["host"], maps["radar"]["past"][-count:]
            session = requests.Session()
            for step in past:
                stamp = datetime.datetime.fromtimestamp(step["time"], datetime.timezone.utc)
                rain.append((stamp.strftime("%H:%M UTC"), _radar_frame(host, step["path"], session)))
        except Exception as e:
            notes.append(f"RainViewer radar unavailable ({type(e).__name__})")

    steps = len(rain) or count
    wind = _wind_streaks(steps) if with_wind else None
    if with_wind and wind is None:
        notes.append("Wind field unavailable (Open-Meteo)")

    FRAMES_DIR.mkdir(exist_ok=True)
    for old in FRAMES_DIR.glob("*.jpg"):
        if time.time() - old.stat().st_mtime > 3600:
            old.unlink(missing_ok=True)

    built, stamp = [], int(time.time())
    for i in range(steps):
        picture = base.copy()
        if clouds is not None:
            picture.paste(clouds, (0, 0), clouds)
        if wind is not None:
            picture.paste(wind[i], (0, 0), wind[i])
        if night:
            picture = _shade_night(picture, night)
        label = ""
        if rain:
            label, echo = rain[i]
            picture.paste(echo, (0, 0), echo)
        elif with_wind:
            label = "wind · live"
        elif with_clouds:
            label = "clouds · " + (datetime.date.today() - datetime.timedelta(days=1)).strftime("%d %b")
        elif night:
            label = "day & night · now"
        name = f"f{stamp}_{i}.jpg"
        picture.save(FRAMES_DIR / name, "JPEG", quality=68, optimize=True)
        built.append({"url": f"frames/{name}", "label": label})
    return built, " · ".join(notes)


def clear_frames() -> None:
    """Throw the loop away — used when the layer is switched off."""
    shutil.rmtree(FRAMES_DIR, ignore_errors=True)
