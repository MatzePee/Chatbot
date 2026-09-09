"""Wasserzeichen je Kanal.

Das Wasserzeichen wird **beim Veröffentlichen** aufgebracht, nie in die
gespeicherte Datei geschrieben. So bleibt das Original unangetastet, dasselbe
Bild kann auf zwei Kanälen mit unterschiedlichen Zeichen laufen, und eine
Änderung an der Platzierung wirkt rückwirkend auf alles, was noch aussteht.

Alle Maße sind **relativ zum Bild**, denn die Posts haben unterschiedliche
Formate: Größe in Prozent der Bildbreite, Abstände in Prozent der kürzeren
Seite. Dadurch sieht das Zeichen auf einem Hochformat genauso aus wie auf einem
Querformat – ein fester Pixelwert täte das nicht.
"""
from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from autoposter.services.media import media_root

logger = logging.getLogger(__name__)

#: Ankerpunkte im 3×3-Raster. Der Standard ist links unten mit kleinem Abstand.
ANCHORS = (
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
)
DEFAULT_ANCHOR = "bottom-left"
DEFAULT_SCALE_PCT = 18.0
DEFAULT_MARGIN_PCT = 3.0
DEFAULT_OPACITY = 1.0

MAX_BYTES = 4 * 1024 * 1024


class WatermarkError(ValueError):
    pass


@dataclass
class Placement:
    """Die relativen Vorgaben eines Kanals."""

    anchor: str = DEFAULT_ANCHOR
    #: Breite des Wasserzeichens in Prozent der Bildbreite
    scale_pct: float = DEFAULT_SCALE_PCT
    #: Abstände in Prozent der kürzeren Bildseite
    margin_x_pct: float = DEFAULT_MARGIN_PCT
    margin_y_pct: float = DEFAULT_MARGIN_PCT
    opacity: float = DEFAULT_OPACITY

    @classmethod
    def from_channel(cls, channel) -> "Placement":
        return cls(
            anchor=(channel.watermark_anchor or DEFAULT_ANCHOR),
            scale_pct=float(channel.watermark_scale_pct or DEFAULT_SCALE_PCT),
            margin_x_pct=float(
                channel.watermark_margin_x_pct
                if channel.watermark_margin_x_pct is not None
                else DEFAULT_MARGIN_PCT
            ),
            margin_y_pct=float(
                channel.watermark_margin_y_pct
                if channel.watermark_margin_y_pct is not None
                else DEFAULT_MARGIN_PCT
            ),
            opacity=float(
                channel.watermark_opacity if channel.watermark_opacity is not None else DEFAULT_OPACITY
            ),
        )

    def clamped(self) -> "Placement":
        return Placement(
            anchor=self.anchor if self.anchor in ANCHORS else DEFAULT_ANCHOR,
            scale_pct=min(100.0, max(1.0, self.scale_pct)),
            margin_x_pct=min(45.0, max(0.0, self.margin_x_pct)),
            margin_y_pct=min(45.0, max(0.0, self.margin_y_pct)),
            opacity=min(1.0, max(0.05, self.opacity)),
        )


# --------------------------------------------------------------------------- #
# Ablage
# --------------------------------------------------------------------------- #
def watermark_dir() -> Path:
    path = media_root() / "watermarks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def path_for(channel_id: uuid.UUID) -> Path:
    return watermark_dir() / f"{channel_id}.png"


def store(channel_id: uuid.UUID, data: bytes) -> Tuple[str, int, int]:
    """PNG prüfen und ablegen. Gibt den relativen Pfad und die Maße zurück."""
    if len(data) > MAX_BYTES:
        raise WatermarkError("Die Datei ist größer als 4 MB")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001
        raise WatermarkError(f"Das ist kein lesbares Bild: {exc}") from exc

    if image.format != "PNG":
        raise WatermarkError(
            f"Erwartet wird ein PNG mit freigestelltem Hintergrund, erhalten: {image.format}"
        )
    if image.mode not in ("RGBA", "LA", "PA"):
        raise WatermarkError(
            "Dem PNG fehlt der Transparenzkanal – der Hintergrund wäre ein weißer Kasten"
        )

    target = path_for(channel_id)
    # Normalisiert ablegen: RGBA, ohne Metadaten.
    rgba = image.convert("RGBA")
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG", optimize=True)
    target.write_bytes(buffer.getvalue())
    return f"watermarks/{channel_id}.png", rgba.width, rgba.height


def remove(channel_id: uuid.UUID) -> bool:
    target = path_for(channel_id)
    if target.exists():
        target.unlink()
        return True
    return False


def load(channel_id: uuid.UUID) -> Optional[Image.Image]:
    target = path_for(channel_id)
    if not target.exists():
        return None
    try:
        return Image.open(target).convert("RGBA")
    except Exception as exc:  # noqa: BLE001 – ein kaputtes Zeichen darf nicht posten verhindern
        logger.warning("Wasserzeichen %s nicht lesbar: %s", target, exc)
        return None


# --------------------------------------------------------------------------- #
# Anwenden
# --------------------------------------------------------------------------- #
def position_for(
    anchor: str, image_size: Tuple[int, int], mark_size: Tuple[int, int],
    margin_x: int, margin_y: int,
) -> Tuple[int, int]:
    """Linke obere Ecke des Wasserzeichens für einen Ankerpunkt."""
    width, height = image_size
    mark_w, mark_h = mark_size
    vertical, _, horizontal = (anchor if anchor in ANCHORS else DEFAULT_ANCHOR).partition("-")

    if horizontal == "left":
        x = margin_x
    elif horizontal == "right":
        x = width - mark_w - margin_x
    else:
        x = (width - mark_w) // 2

    if vertical == "top":
        y = margin_y
    elif vertical == "bottom":
        y = height - mark_h - margin_y
    else:
        y = (height - mark_h) // 2

    # Nicht aus dem Bild laufen lassen, auch bei extremen Werten.
    x = max(0, min(x, max(0, width - mark_w)))
    y = max(0, min(y, max(0, height - mark_h)))
    return x, y


def apply_to_image(base: Image.Image, mark: Image.Image, placement: Placement) -> Image.Image:
    """Wasserzeichen relativ zur Bildgröße einrechnen."""
    placement = placement.clamped()
    base = base.convert("RGBA")
    width, height = base.size
    short_side = min(width, height)

    target_w = max(1, int(round(width * placement.scale_pct / 100.0)))
    ratio = mark.height / mark.width if mark.width else 1.0
    target_h = max(1, int(round(target_w * ratio)))
    # Passt das Zeichen sonst nicht ins Bild, verkleinern statt abschneiden.
    if target_h > height:
        target_h = height
        target_w = max(1, int(round(target_h / ratio))) if ratio else target_w
    resized = mark.resize((target_w, target_h), Image.LANCZOS)

    if placement.opacity < 1.0:
        alpha = resized.getchannel("A").point(lambda v: int(v * placement.opacity))
        resized.putalpha(alpha)

    # Abstände an der KÜRZEREN Seite messen: so ist der optische Randabstand
    # auf Hoch- und Querformat gleich.
    margin_x = int(round(short_side * placement.margin_x_pct / 100.0))
    margin_y = int(round(short_side * placement.margin_y_pct / 100.0))
    x, y = position_for(placement.anchor, (width, height), (target_w, target_h), margin_x, margin_y)

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    # Bewusst OHNE Maske einsetzen. Mit `paste(bild, box, bild)` würde Pillow den
    # Alphakanal gegen die durchsichtige Ebene verrechnen und ihn damit ein
    # zweites Mal anwenden – das Zeichen käme sichtbar zu blass heraus.
    layer.paste(resized, (x, y))
    return Image.alpha_composite(base, layer)


def apply_bytes(data: bytes, channel, *, mime: str = "image/jpeg") -> bytes:
    """Wasserzeichen auf Bilddaten anwenden.

    Schlägt irgendetwas fehl, kommen die Originaldaten zurück: Ein Post ohne
    Wasserzeichen ist besser als ein Post, der gar nicht erst rausgeht.
    """
    if not getattr(channel, "watermark_enabled", False):
        return data
    mark = load(channel.id)
    if mark is None:
        return data

    try:
        base = Image.open(io.BytesIO(data))
        base.load()
        result = apply_to_image(base, mark, Placement.from_channel(channel))

        buffer = io.BytesIO()
        if (mime or "").lower() in ("image/png", "image/webp") or base.format == "PNG":
            result.save(buffer, format="PNG", optimize=True)
        else:
            # JPEG kennt keine Transparenz – auf Weiß setzen, wie es die
            # Plattformen ohnehin täten.
            flat = Image.new("RGB", result.size, (255, 255, 255))
            flat.paste(result, mask=result.getchannel("A"))
            flat.save(buffer, format="JPEG", quality=92, optimize=True, progressive=True)
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wasserzeichen konnte nicht aufgebracht werden: %s", exc)
        return data


def preview_bytes(data: bytes, channel, placement: Optional[Placement] = None) -> bytes:
    """Vorschau für die Oberfläche – immer PNG, ohne Zwischenspeicher."""
    mark = load(channel.id)
    base = Image.open(io.BytesIO(data))
    base.load()
    if mark is not None:
        base = apply_to_image(base, mark, placement or Placement.from_channel(channel))
    else:
        base = base.convert("RGBA")
    base.thumbnail((900, 900), Image.LANCZOS)
    buffer = io.BytesIO()
    base.save(buffer, format="PNG")
    return buffer.getvalue()
