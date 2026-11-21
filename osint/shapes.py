"""Three shapes Vega-Lite will not draw, worked out here and handed over as geometry.

Altair covers bars, lines, areas and arcs, which is most of what this dashboard needs. Three
things it has no mark for:

  * a **treemap**, because squarified layout is an algorithm rather than an encoding;
  * a **gauge**, which is an arc with a needle and a scale, drawn at a fixed size;
  * a **radar**, which is a line chart in polar coordinates that Vega-Lite cannot express.

Each is a pure function from numbers to shapes. `squarify` returns rectangles for Altair to
draw, so the treemap keeps Vega's tooltips and theming; the gauge and the radar return SVG,
because both are fixed-size diagrams with their own chrome and are easier to read as markup
than as a layered chart spec. Nothing here touches Streamlit, pandas or the database, which
is what makes all three testable on a list of numbers.
"""
from __future__ import annotations

import math
from html import escape


# ─── Treemap ──────────────────────────────────────────────────────────────────
# Bruls, Huizing and van Wijk's squarified treemap (2000). Laying the rectangles out in a
# simple strip gives slivers — a 400-item category becomes a line two pixels tall, which is
# unreadable and unclickable. Squarifying keeps each rectangle as close to a square as the
# numbers allow, which is the whole reason the layout is worth the arithmetic.
def _worst(areas: list[float], length: float) -> float:
    """The worst aspect ratio in a row of already-scaled areas laid along `length`.

    Bruls' own formula: the squarest row is the one where the fattest and the thinnest
    rectangle are both closest to square, so the row is judged by whichever is worse.
    """
    total = sum(areas)
    if not areas or length <= 0 or total <= 0:
        return math.inf
    widest, narrowest = max(areas), min(areas)
    if narrowest <= 0:
        return math.inf
    span = length * length
    return max(span * widest / (total * total), total * total / (span * narrowest))


def squarify(values: list[float], width: float, height: float) -> list[tuple[float, float, float, float]]:
    """Rectangles (x0, y0, x1, y1) for `values`, filling width × height.

    The order given is preserved — rectangle *i* belongs to value *i* — so the caller can sort
    however it likes and still match a rectangle back to its row. A value of zero or less gets
    an empty rectangle rather than being dropped, which keeps the two lists the same length.
    """
    out: list[tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 0.0)] * len(values)
    queue = [(i, float(v)) for i, v in enumerate(values) if v and v > 0]
    total = sum(v for _, v in queue)
    if not queue or total <= 0 or width <= 0 or height <= 0:
        return out

    scale = (width * height) / total
    x, y, w, h = 0.0, 0.0, float(width), float(height)

    while queue:
        length = min(w, h)
        row = [queue.pop(0)]
        # Keep adding to this row for as long as doing so makes its worst rectangle squarer.
        while queue:
            grown = [v * scale for _, v in row] + [queue[0][1] * scale]
            if _worst(grown, length) > _worst([v * scale for _, v in row], length):
                break
            row.append(queue.pop(0))

        thickness = (sum(v for _, v in row) * scale / length) if length else 0.0
        along = 0.0
        for index, value in row:
            side = (value * scale / thickness) if thickness else 0.0
            out[index] = ((x, y + along, x + thickness, y + along + side) if w >= h
                          else (x + along, y, x + along + side, y + thickness))
            along += side
        # The row is laid; the free space loses its thickness off the shorter side.
        if w >= h:
            x, w = x + thickness, w - thickness
        else:
            y, h = y + thickness, h - thickness
    return out


# ─── Gauge ────────────────────────────────────────────────────────────────────
def gauge(label: str, reading: float, ceiling: float, *, count: int, colour: str,
          track: str = "#1b1f25", ink: str = "#eceef1", dim: str = "#8a919c",
          note: str = "", foot: str | None = None, big: str | None = None) -> str:
    """A three-quarter dial: the arc is `reading` out of `ceiling`, the big number is `count`.

    Two numbers, because either alone misleads. A count says how loud a category is and
    nothing about how bad; an average severity says how bad and nothing about how much of it
    there is, so a single harsh item would peg the dial. The arc carries the rating, which is
    bounded and therefore has somewhere to point; the count sits in the middle of it.
    """
    share = 0.0 if ceiling <= 0 else max(0.0, min(1.0, reading / ceiling))
    # The line under the number says what the arc is a fraction of. For a severity dial that
    # is "3.4 / 5" and needs no help; for a dial whose arc is one window against another there
    # is no such unit, so the caller supplies the words instead.
    scale = foot if foot is not None else f"{reading:.1f} / {ceiling:.0f}"
    size, stroke = 108, 9
    centre, radius = size / 2, (size - stroke) / 2 - 6
    sweep = 270                                  # a full dial minus a quarter at the bottom
    start = 135                                  # ...so it opens downwards, like a meter

    def point(angle_deg: float) -> tuple[float, float]:
        rad = math.radians(angle_deg)
        return centre + radius * math.cos(rad), centre + radius * math.sin(rad)

    def arc(frac: float, stroke_colour: str, width: float) -> str:
        if frac <= 0:
            return ""
        end = start + sweep * min(frac, 0.9999)
        x0, y0 = point(start)
        x1, y1 = point(end)
        large = 1 if sweep * frac > 180 else 0
        return (f'<path d="M {x0:.2f} {y0:.2f} A {radius:.2f} {radius:.2f} 0 {large} 1 '
                f'{x1:.2f} {y1:.2f}" fill="none" stroke="{stroke_colour}" '
                f'stroke-width="{width}" stroke-linecap="round"/>')

    return (
        f'<div class="axn-gauge">'
        f'<svg viewBox="0 0 {size} {size}" role="img" '
        f'aria-label="{escape(label)}: {count:,} — {escape(scale)}">'
        f'{arc(1.0, track, stroke)}{arc(share, colour, stroke)}'
        f'<text x="{centre}" y="{centre - 2}" text-anchor="middle" fill="{ink}" '
        f'font-size="21" font-weight="600">{escape(big) if big is not None else f"{count:,}"}</text>'
        f'<text x="{centre}" y="{centre + 15}" text-anchor="middle" fill="{dim}" '
        f'font-size="10">{escape(scale)}</text>'
        f'</svg>'
        f'<span class="g-label">{escape(label)}</span>'
        + (f'<span class="g-note">{escape(note)}</span>' if note else "")
        + '</div>')



# ─── Ring ─────────────────────────────────────────────────────────────────────
def ring(count: int, share: float, *, caption: str = "", note: str = "", colour: str = "#5ee6ff",
         track: str = "#1b1f25", ink: str = "#eceef1", dim: str = "#8a919c",
         size: int = 136) -> str:
    """A closed circle with `share` of it drawn, and `count` in the middle.

    A full circle rather than the gauge's three-quarter dial, because what this carries has no
    ceiling to point at: it is one window measured against another, and a window can easily be
    twice its neighbour. The arc therefore stops at a full turn, and the words underneath say
    by how much it ran over — a dial that silently pegs at its maximum is worse than no dial.
    """
    stroke = 10
    radius = (size - stroke) / 2 - 2
    circumference = 2 * math.pi * radius
    drawn = max(0.0, min(1.0, share)) * circumference
    centre = size / 2
    return (
        f'<div class="axn-ring">'
        f'<svg viewBox="0 0 {size} {size}" role="img" '
        f'aria-label="{escape(caption)}: {count:,}">'
        f'<circle cx="{centre}" cy="{centre}" r="{radius:.2f}" fill="none" stroke="{track}" '
        f'stroke-width="{stroke}"/>'
        f'<circle cx="{centre}" cy="{centre}" r="{radius:.2f}" fill="none" stroke="{colour}" '
        f'stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-dasharray="{drawn:.2f} {circumference:.2f}" '
        f'transform="rotate(-90 {centre} {centre})"/>'
        f'<text x="{centre}" y="{centre + 2}" text-anchor="middle" fill="{ink}" '
        f'font-size="27" font-weight="600">{count:,}</text>'
        f'<text x="{centre}" y="{centre + 19}" text-anchor="middle" fill="{dim}" '
        f'font-size="9">{escape(caption)}</text>'
        f'</svg>'
        + (f'<span class="g-note">{escape(note)}</span>' if note else "")
        + '</div>')



# ─── Spark bars ───────────────────────────────────────────────────────────────
def spark(values: list[float], *, width: int = 146, height: int = 42, colour: str = "#f87171",
          track: str = "#1b1f25") -> str:
    """A row of bars at a fixed size, with no axes, no ticks and no numbers.

    Built for a tooltip, where there is room for a shape and none for a chart. It answers one
    question — is this place dominated by a single kind of event, or spread across several —
    and a reader gets that from the silhouette without reading anything. Anything that needed
    a scale printed beside it would not fit in the box and would not be read there anyway.
    """
    values = [max(0.0, float(v)) for v in values]
    if not values:
        return ""
    peak = max(values) or 1.0
    gap = 3.0
    bar = max(2.0, (width - gap * (len(values) - 1)) / len(values))
    floor = height - 3
    parts = [f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" '
             f'height="{height}" role="img" aria-hidden="true">']
    for i, value in enumerate(values):
        x = i * (bar + gap)
        tall = max(2.0, (value / peak) * (height - 6))
        parts.append(f'<rect x="{x:.1f}" y="{floor - tall:.1f}" width="{bar:.1f}" '
                     f'height="{tall:.1f}" rx="1.5" fill="{colour}" '
                     f'fill-opacity="{1 if i == 0 else 0.45 + 0.4 * value / peak:.2f}"/>')
    parts.append(f'<rect x="0" y="{floor:.1f}" width="{width}" height="1" fill="{track}"/>')
    parts.append("</svg>")
    return "".join(parts)


# ─── Radar ────────────────────────────────────────────────────────────────────
def radar(axes: list[str], series: list[tuple[str, str, list[float]]], *,
          size: int = 340, grid: str = "#232a30", ink: str = "#eceef1",
          dim: str = "#8a919c", rings: int = 4) -> str:
    """One polygon per series over shared axes, scaled to the largest value anywhere on it.

    Everything is drawn against one scale rather than one per axis. Per-axis scaling makes
    every polygon fill the web and so compares nothing; one scale is what lets this week's
    shape be read against last week's.
    """
    if not axes or not series:
        return ""
    ceiling = max((v for _, _, values in series for v in values), default=0) or 1
    # The canvas is wider than it is tall because the labels that need room are the ones at
    # the sides: "Social issues & abuse" reaches much further out than "Crime & law" below it.
    width = size * 1.6
    centre, middle = width / 2, size / 2
    radius = size / 2 - 34
    step = 2 * math.pi / len(axes)

    def point(index: int, value: float) -> tuple[float, float]:
        angle = -math.pi / 2 + index * step     # first axis straight up
        reach = radius * max(0.0, min(1.0, value / ceiling))
        return centre + reach * math.cos(angle), middle + reach * math.sin(angle)

    parts = [f'<svg viewBox="0 0 {width:.0f} {size}" role="img" '
             f'aria-label="{escape(", ".join(name for name, _, _ in series))} by event type">']

    for ring in range(1, rings + 1):
        corners = " ".join(f"{x:.1f},{y:.1f}" for x, y in
                           (point(i, ceiling * ring / rings) for i in range(len(axes))))
        parts.append(f'<polygon points="{corners}" fill="none" stroke="{grid}" stroke-width="1"/>')
    for i in range(len(axes)):
        x, y = point(i, ceiling)
        parts.append(f'<line x1="{centre}" y1="{middle}" x2="{x:.1f}" y2="{y:.1f}" '
                     f'stroke="{grid}" stroke-width="1"/>')

    for name, colour, values in series:
        corners = " ".join(f"{x:.1f},{y:.1f}" for x, y in
                           (point(i, values[i] if i < len(values) else 0) for i in range(len(axes))))
        parts.append(f'<polygon points="{corners}" fill="{colour}" fill-opacity="0.16" '
                     f'stroke="{colour}" stroke-width="2" stroke-linejoin="round"><title>'
                     f'{escape(name)}</title></polygon>')
        for i in range(len(axes)):
            x, y = point(i, values[i] if i < len(values) else 0)
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{colour}"/>')

    for i, name in enumerate(axes):
        angle = -math.pi / 2 + i * step
        x = centre + (radius + 14) * math.cos(angle)
        y = middle + (radius + 14) * math.sin(angle)
        anchor = "middle" if abs(math.cos(angle)) < 0.3 else ("start" if math.cos(angle) > 0 else "end")
        parts.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="{anchor}" fill="{dim}" '
                     f'font-size="10.5">{escape(name)}</text>')

    # Bottom left, not top centre: the top of the web is where the first axis label sits, and
    # the two were printing over each other.
    parts.append(f'<text x="6" y="{size - 6}" text-anchor="start" '
                 f'fill="{ink}" font-size="10" opacity="0.7">outer ring = {ceiling:,.0f}</text>')
    parts.append("</svg>")
    return "".join(parts)
