"""Генератор картинок бусин в виде SVG.

Нужен, чтобы магазин работал «из коробки»: стартовый каталог выглядит красиво
без единого загруженного файла, а картинки не пропадают при редеплое на Render
(их не нужно хранить: они рисуются по ссылке вида /beadart/gem.svg?c=9a6bd1).

Свои бусины владелец загружает в админке как прозрачные PNG.
"""
import math
import random
import re
from functools import lru_cache
from xml.sax.saxutils import escape

HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")

KINDS = {
    # kind: (цвет по умолчанию, допустимые варианты)
    "gem":    ("9a6bd1", {"plain", "banded", "stripes", "flecks", "veins", "sheen"}),
    "pearl":  ("f4ece1", {"round", "baroque"}),
    "metal":  ("c9ced6", {"shiny", "matte"}),
    "ring":   ("c9ced6", {"shiny"}),
    "letter": ("fff4f7", {"plain"}),
    "charm":  ("c9ced6", {"heart", "star", "moon", "flower"}),
}


DEFAULT_VARIANT = {
    "gem": "plain", "pearl": "round", "metal": "shiny",
    "ring": "shiny", "letter": "plain", "charm": "heart",
}


# --- Цвет ---------------------------------------------------------------

def _rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def _mix(a, b, t):
    ra, rb = _rgb(a), _rgb(b)
    return _hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


def _light(h, t):
    return _mix(h, "ffffff", t)


def _dark(h, t):
    return _mix(h, "000000", t)


# --- Общие детали -------------------------------------------------------

def _svg(defs, body):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f"<defs>{defs}</defs>{body}</svg>"
    )


def _gloss(k=1.0):
    return (
        f'<ellipse cx="35" cy="27" rx="16" ry="9" fill="#fff" opacity="{0.5 * k:.2f}" '
        'transform="rotate(-32 35 27)"/>'
        f'<circle cx="29" cy="23" r="3.4" fill="#fff" opacity="{0.9 * k:.2f}"/>'
    )


# --- Камни --------------------------------------------------------------

def _gem(c, v):
    defs = (
        f'<radialGradient id="g" cx="36%" cy="30%" r="78%">'
        f'<stop offset="0" stop-color="{_light(c, .55)}"/>'
        f'<stop offset=".48" stop-color="#{c}"/>'
        f'<stop offset="1" stop-color="{_dark(c, .42)}"/></radialGradient>'
        f'<radialGradient id="r" cx="68%" cy="76%" r="42%">'
        f'<stop offset="0" stop-color="{_light(c, .6)}" stop-opacity=".45"/>'
        f'<stop offset="1" stop-color="{_light(c, .6)}" stop-opacity="0"/></radialGradient>'
        '<clipPath id="k"><circle cx="50" cy="50" r="47"/></clipPath>'
    )
    rng = random.Random(int(c, 16))
    extra = ""
    if v == "banded":        # агат, малахит: полосы
        for i in range(1, 6):
            col = _dark(c, .35) if i % 2 else _light(c, .45)
            extra += (
                f'<ellipse cx="{55 - i}" cy="{57 - i}" rx="{46 - i * 8}" ry="{42 - i * 7}" '
                f'fill="none" stroke="{col}" stroke-width="2.4" opacity=".5"/>'
            )
    elif v == "stripes":     # тигровый глаз: шелковистые полосы
        for n, y in enumerate(range(6, 100, 13)):
            col = _dark(c, .45) if n % 2 else _light(c, .5)
            extra += (
                f'<path d="M-10 {y} Q50 {y + 14} 110 {y - 8}" fill="none" '
                f'stroke="{col}" stroke-width="5" opacity=".42"/>'
            )
    elif v == "flecks":      # лазурит: золотые и белые вкрапления
        for n in range(26):
            a, d = rng.random() * math.tau, math.sqrt(rng.random()) * 42
            col = "#f1d58a" if n % 3 else "#ffffff"
            extra += (
                f'<circle cx="{50 + d * math.cos(a):.1f}" cy="{50 + d * math.sin(a):.1f}" '
                f'r="{rng.uniform(.8, 2.3):.1f}" fill="{col}" opacity=".8"/>'
            )
    elif v == "veins":       # бирюза: тёмные прожилки
        for _ in range(4):
            x, y = rng.uniform(5, 40), rng.uniform(10, 90)
            extra += (
                f'<path d="M{x:.0f} {y:.0f} q{rng.uniform(10, 25):.0f} {rng.uniform(-18, 18):.0f} '
                f'{rng.uniform(30, 55):.0f} {rng.uniform(-14, 14):.0f} t{rng.uniform(15, 30):.0f} '
                f'{rng.uniform(-16, 16):.0f}" fill="none" stroke="#5b3b2a" '
                'stroke-width="1.5" opacity=".5" stroke-linecap="round"/>'
            )
    elif v == "sheen":       # лунный камень, кварц: мягкое свечение
        extra = (
            '<ellipse cx="62" cy="64" rx="28" ry="20" fill="#9fc4ff" opacity=".24"/>'
            '<ellipse cx="42" cy="42" rx="22" ry="15" fill="#ffffff" opacity=".2"/>'
        )
    body = (
        '<circle cx="50" cy="50" r="47" fill="url(#g)"/>'
        f'<g clip-path="url(#k)">{extra}</g>'
        '<circle cx="50" cy="50" r="47" fill="url(#r)"/>'
        f"{_gloss()}"
        '<circle cx="50" cy="50" r="46.5" fill="none" stroke="#000" stroke-opacity=".14"/>'
    )
    return _svg(defs, body)


def _pearl(c, v):
    blob = (
        "M50 5 C73 3 95 21 94 47 C96 72 77 96 51 95 C25 98 4 76 6 51 C5 26 26 7 50 5Z"
    )
    if v == "baroque":
        shape = lambda a: f'<path d="{blob}" {a} transform="rotate(-14 50 50)"/>'
        clip = f'<clipPath id="k"><path d="{blob}" transform="rotate(-14 50 50)"/></clipPath>'
    else:
        shape = lambda a: f'<circle cx="50" cy="50" r="46" {a}/>'
        clip = '<clipPath id="k"><circle cx="50" cy="50" r="46"/></clipPath>'
    defs = (
        '<radialGradient id="g" cx="36%" cy="30%" r="80%">'
        '<stop offset="0" stop-color="#ffffff"/>'
        f'<stop offset=".38" stop-color="{_light(c, .5)}"/>'
        f'<stop offset=".78" stop-color="#{c}"/>'
        f'<stop offset="1" stop-color="{_mix(c, "b79aa6", .55)}"/></radialGradient>'
        '<linearGradient id="i" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#ffd1e8" stop-opacity=".5"/>'
        '<stop offset=".5" stop-color="#d1ecff" stop-opacity=".28"/>'
        '<stop offset="1" stop-color="#e6d1ff" stop-opacity=".45"/></linearGradient>'
        + clip
    )
    ridges = ""
    if v == "baroque":       # лёгкая неровность поверхности
        ridges = (
            '<g clip-path="url(#k)" fill="none" stroke="#fff" stroke-opacity=".35" stroke-width="1.6">'
            '<path d="M18 60 Q40 48 70 64"/><path d="M26 76 Q52 64 84 74"/></g>'
        )
    body = (
        shape('fill="url(#g)"') + shape('fill="url(#i)"') + ridges + _gloss(.95)
        + shape('fill="none" stroke="#7a5565" stroke-opacity=".18"')
    )
    return _svg(defs, body)


# --- Металл -------------------------------------------------------------

def _metal_gradient(c, shiny=True):
    if shiny:
        stops = (
            f'<stop offset="0" stop-color="#ffffff"/><stop offset=".16" stop-color="{_light(c, .6)}"/>'
            f'<stop offset=".4" stop-color="#{c}"/><stop offset=".62" stop-color="{_dark(c, .38)}"/>'
            f'<stop offset=".8" stop-color="{_light(c, .32)}"/><stop offset="1" stop-color="{_dark(c, .5)}"/>'
        )
    else:
        stops = (
            f'<stop offset="0" stop-color="{_light(c, .5)}"/><stop offset=".55" stop-color="#{c}"/>'
            f'<stop offset="1" stop-color="{_dark(c, .35)}"/>'
        )
    return f'<radialGradient id="g" cx="36%" cy="30%" r="80%">{stops}</radialGradient>'


def _metal(c, v):
    shiny = v != "matte"
    defs = _metal_gradient(c, shiny)
    streak = (
        '<path d="M20 62 Q30 84 56 90 Q36 82 28 60Z" fill="#fff" opacity=".5"/>' if shiny else ""
    )
    body = (
        '<circle cx="50" cy="50" r="47" fill="url(#g)"/>' + streak + _gloss(1 if shiny else .35)
        + '<circle cx="50" cy="50" r="46.5" fill="none" stroke="#000" stroke-opacity=".18"/>'
    )
    return _svg(defs, body)


def _ring(c, v):
    defs = _metal_gradient(c, True)
    body = (
        '<path d="M50 3a47 47 0 1 0 0 94a47 47 0 1 0 0-94Z M50 30a20 20 0 1 1 0 40a20 20 0 1 1 0-40Z" '
        'fill="url(#g)" fill-rule="evenodd"/>'
        f'<circle cx="50" cy="50" r="20" fill="none" stroke="{_dark(c, .45)}" stroke-width="2.4" opacity=".55"/>'
        '<ellipse cx="27" cy="30" rx="11" ry="5" fill="#fff" opacity=".6" transform="rotate(-42 27 30)"/>'
        '<circle cx="50" cy="50" r="46.5" fill="none" stroke="#000" stroke-opacity=".16"/>'
    )
    return _svg(defs, body)


# --- Буквы --------------------------------------------------------------

def _letter(c, v, t):
    ch = escape((t or "?")[:1])
    defs = (
        '<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#ffffff"/>'
        f'<stop offset=".55" stop-color="#{c}"/>'
        f'<stop offset="1" stop-color="{_mix(c, "D9608A", .28)}"/></linearGradient>'
    )
    body = (
        '<rect x="5" y="5" width="90" height="90" rx="22" fill="url(#g)"/>'
        '<rect x="12" y="10" width="76" height="26" rx="14" fill="#fff" opacity=".5"/>'
        '<rect x="5.5" y="5.5" width="89" height="89" rx="21.5" fill="none" '
        'stroke="#D9608A" stroke-opacity=".32"/>'
        '<text x="50" y="70" text-anchor="middle" font-size="58" font-weight="700" '
        "font-family=\"Georgia, 'Times New Roman', serif\" fill=\"#7a2f4d\">" + ch + "</text>"
    )
    return _svg(defs, body)


# --- Подвески -----------------------------------------------------------

def _star_points():
    pts = []
    for i in range(10):
        r = 47 if i % 2 == 0 else 20
        a = -math.pi / 2 + i * math.pi / 5
        pts.append(f"{50 + r * math.cos(a):.1f},{54 + r * math.sin(a):.1f}")
    return " ".join(pts)


def _charm(c, v):
    defs = _metal_gradient(c, True).replace(
        '<radialGradient id="g" cx="36%" cy="30%" r="80%">',
        '<radialGradient id="g" gradientUnits="userSpaceOnUse" cx="36" cy="28" r="85">',
    )
    edge = 'stroke="#000" stroke-opacity=".2" stroke-width="1.2" stroke-linejoin="round"'
    if v == "heart":
        shape = (
            '<path d="M50 90 C20 67 7 49 7 33 C7 18 19 8 31 8 C40 8 47 13 50 21 C53 13 60 8 69 8 '
            f'C81 8 93 18 93 33 C93 49 80 67 50 90Z" fill="url(#g)" {edge}/>'
        )
        gloss = '<ellipse cx="30" cy="27" rx="12" ry="6" fill="#fff" opacity=".6" transform="rotate(-35 30 27)"/>'
    elif v == "star":
        shape = f'<polygon points="{_star_points()}" fill="url(#g)" {edge}/>'
        gloss = '<ellipse cx="42" cy="38" rx="10" ry="4.5" fill="#fff" opacity=".55" transform="rotate(-48 42 38)"/>'
    elif v == "moon":
        defs += '<mask id="m"><rect width="100" height="100" fill="#fff"/><circle cx="66" cy="40" r="34" fill="#000"/></mask>'
        shape = f'<circle cx="44" cy="52" r="42" fill="url(#g)" mask="url(#m)"/>'
        gloss = '<ellipse cx="26" cy="38" rx="9" ry="4.5" fill="#fff" opacity=".55" transform="rotate(-62 26 38)"/>'
    else:  # flower
        petals = "".join(
            f'<circle cx="{50 + 27 * math.cos(-math.pi / 2 + i * math.tau / 5):.1f}" '
            f'cy="{50 + 27 * math.sin(-math.pi / 2 + i * math.tau / 5):.1f}" r="20" fill="url(#g)" {edge}/>'
            for i in range(5)
        )
        shape = petals + f'<circle cx="50" cy="50" r="12" fill="#e9c46a" {edge}/>'
        gloss = '<circle cx="46" cy="46" r="3.5" fill="#fff" opacity=".8"/>'
    return _svg(defs, shape + gloss)


# --- Публичный интерфейс ------------------------------------------------

@lru_cache(maxsize=512)
def render(kind: str, c: str = "", v: str = "", t: str = "") -> str:
    """Возвращает SVG-строку. Входные данные проверяются, лишнее заменяется значениями по умолчанию."""
    if kind not in KINDS:
        raise ValueError("unknown kind")
    default_c, variants = KINDS[kind]
    c = c.lstrip("#").lower() if HEX_RE.match(c.lstrip("#")) else default_c
    v = v if v in variants else DEFAULT_VARIANT[kind]
    if kind == "gem":
        return _gem(c, v)
    if kind == "pearl":
        return _pearl(c, v)
    if kind == "metal":
        return _metal(c, v)
    if kind == "ring":
        return _ring(c, v)
    if kind == "letter":
        return _letter(c, v, t)
    return _charm(c, v)


def art_url(kind: str, c: str = "", v: str = "", t: str = "") -> str:
    """Ссылка на картинку для записи в Bead.image_url."""
    from urllib.parse import urlencode
    params = {k: val for k, val in (("c", c), ("v", v), ("t", t)) if val}
    return f"/beadart/{kind}.svg" + (f"?{urlencode(params)}" if params else "")
