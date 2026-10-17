"""Read microscopy images without losing bit depth or physical scale.

`read_image(path)` returns an `ImageData`: the pixels in their native dtype
(a 16-bit camera image stays uint16), arranged in one canonical axis order,
plus whatever physical calibration the file carries. Unlike
`image_utils.load_and_convert`, which normalises to float64 in [0, 1] for
display-style processing, this is for quantitative work where intensities
and micrometres matter.

Canonical axes are ``TCZYX`` (time, channel, z, y, x); every axis is present
(size 1 if the file has none). An RGB(A) image's colour samples become
channels.

Pixel size sources, most to least trusted:

* OME-XML ``PhysicalSizeX/Y/Z`` (OME-TIFF), converted to µm.
* ImageJ hyperstack metadata (``unit`` plus the resolution tags, ``spacing``
  for z, ``finterval`` for time).
* Plain TIFF ``XResolution``/``YResolution`` in centimetre units.
* ND2 voxel size (needs the optional ``nd2`` package: ``karcytics-sdk[imaging]``).

TIFF resolution in *inch* units is deliberately ignored: it is almost
always a screen DPI (72, 96, 300) written by a non-scientific tool, which
would claim several hundred µm per pixel.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

CANONICAL_AXES = "TCZYX"

_RESOLUTION_UNIT_CENTIMETER = 3
_MICRONS_PER_CM = 10_000.0
_RGB_SAMPLES = (3, 4)

# OME-XML length units → micrometres.
_OME_UNITS_TO_UM = {
    "m": 1e6,
    "cm": 1e4,
    "mm": 1e3,
    "µm": 1.0,
    "um": 1.0,
    "nm": 1e-3,
    "Å": 1e-4,
}
# ImageJ's free-text `unit` → micrometres.
_IMAGEJ_UNITS_TO_UM = {
    "micron": 1.0,
    "microns": 1.0,
    "um": 1.0,
    "µm": 1.0,
    "\\u00B5m": 1.0,
    "nm": 1e-3,
    "nanometer": 1e-3,
    "mm": 1e3,
    "millimeter": 1e3,
    "cm": 1e4,
}


class UnsupportedImageError(ValueError):
    """The file isn't an image this reader understands."""


@dataclass(frozen=True)
class PixelSize:
    """Physical size of one pixel, in micrometres, and where it came from."""

    x_um: float
    y_um: float
    source: str


@dataclass
class ImageData:
    """Pixels in canonical ``TCZYX`` order plus physical metadata."""

    data: np.ndarray
    path: Path
    channel_names: list[str] = field(default_factory=list)
    pixel_size: PixelSize | None = None
    z_step_um: float | None = None
    time_interval_s: float | None = None

    @property
    def sizes(self) -> dict[str, int]:
        return dict(zip(CANONICAL_AXES, self.data.shape, strict=True))

    @property
    def dtype(self) -> np.dtype:
        return self.data.dtype

    def plane(self, c: int = 0, z: int = 0, t: int = 0) -> np.ndarray:
        """One 2-D (Y, X) plane in the native dtype."""
        return self.data[t, c, z]


def read_image(path: str | Path) -> ImageData:
    """Read `path` (TIFF/OME-TIFF/ImageJ TIFF, ND2, PNG, JPEG, BMP)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        return _read_tiff(path)
    if suffix == ".nd2":
        return _read_nd2(path)
    return _read_plain(path)


# ── Axis handling ────────────────────────────────────────────────────────────


def _merge_axis(array: np.ndarray, axes: str, src: str, dst: str) -> tuple[np.ndarray, str]:
    """Fold axis `src` into `dst` (dst-major), so `dst` grows by a factor of len(src)."""
    rest = [i for i, a in enumerate(axes) if a not in (src, dst)]
    array = np.transpose(array, [*rest, axes.index(dst), axes.index(src)])
    array = array.reshape(*array.shape[:-2], -1)
    return array, "".join(axes[i] for i in rest) + dst


def to_canonical(array: np.ndarray, axes: str) -> np.ndarray:
    """Reorder/expand `array` (described by `axes`) to ``TCZYX``.

    ``S`` (colour samples) becomes, or is folded into, the channel axis.
    Axes outside ``TCZYXS`` (e.g. tifffile's ``Q`` or ``I``) become, or are
    folded into, time.
    """
    axes = axes.upper()
    if len(axes) != array.ndim:
        raise UnsupportedImageError(f"axes {axes!r} don't match an array with {array.ndim} dimensions")
    folds = [("S", "C")] + [(a, "T") for a in axes if a not in CANONICAL_AXES + "S"]
    for src, dst in folds:
        if src not in axes:
            continue
        if dst in axes:
            array, axes = _merge_axis(array, axes, src, dst)
        else:
            axes = axes.replace(src, dst)
    for a in CANONICAL_AXES:
        if a not in axes:
            array = array[np.newaxis]
            axes = a + axes
    return np.transpose(array, [axes.index(a) for a in CANONICAL_AXES])


# ── TIFF ─────────────────────────────────────────────────────────────────────


def _read_tiff(path: Path) -> ImageData:
    import tifffile

    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        data = to_canonical(series.asarray(), series.axes)
        image = ImageData(data=data, path=path)
        if tif.is_ome and tif.ome_metadata:
            _apply_ome(image, tif.ome_metadata)
        if image.pixel_size is None and tif.is_imagej and tif.imagej_metadata:
            _apply_imagej(image, tif.imagej_metadata, tif.pages[0])
        if image.pixel_size is None:
            image.pixel_size = _tiff_resolution_cm(tif.pages[0])
    if not image.channel_names:
        image.channel_names = _default_channel_names(image)
    return image


def _ome_length_um(value: str | None, unit: str | None) -> float | None:
    if value is None:
        return None
    factor = _OME_UNITS_TO_UM.get(unit or "µm")
    if factor is None:
        return None
    um = float(value) * factor
    return um if um > 0 and math.isfinite(um) else None


def _apply_ome(image: ImageData, ome_xml: str) -> None:
    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError:
        return
    pixels = next((el for el in root.iter() if el.tag.endswith("}Pixels") or el.tag == "Pixels"), None)
    if pixels is None:
        return
    x = _ome_length_um(pixels.get("PhysicalSizeX"), pixels.get("PhysicalSizeXUnit"))
    y = _ome_length_um(pixels.get("PhysicalSizeY"), pixels.get("PhysicalSizeYUnit"))
    if x is not None:
        image.pixel_size = PixelSize(x, y if y is not None else x, "ome")
    image.z_step_um = _ome_length_um(pixels.get("PhysicalSizeZ"), pixels.get("PhysicalSizeZUnit"))
    if pixels.get("TimeIncrement") is not None and pixels.get("TimeIncrementUnit", "s") == "s":
        image.time_interval_s = float(pixels.get("TimeIncrement", "0")) or None
    names = [ch.get("Name", "") for ch in pixels if ch.tag.endswith("Channel")]
    if names and all(names) and len(names) == image.sizes["C"]:
        image.channel_names = names


def _resolution_value(page: Any, tag: str) -> float | None:
    """Pixels per unit from an X/YResolution rational tag, or None."""
    entry = page.tags.get(tag)
    if entry is None:
        return None
    num, den = entry.value
    if not den or not num:
        return None
    return float(num) / float(den)


def _apply_imagej(image: ImageData, meta: dict, page: Any) -> None:
    factor = _IMAGEJ_UNITS_TO_UM.get(str(meta.get("unit", "")).strip().lower())
    if factor is None:
        return
    px_per_unit_x = _resolution_value(page, "XResolution")
    px_per_unit_y = _resolution_value(page, "YResolution") or px_per_unit_x
    if px_per_unit_x:
        image.pixel_size = PixelSize(factor / px_per_unit_x, factor / (px_per_unit_y or px_per_unit_x), "imagej")
    if meta.get("spacing"):
        image.z_step_um = float(meta["spacing"]) * factor
    if meta.get("finterval"):
        image.time_interval_s = float(meta["finterval"])


def _tiff_resolution_cm(page: Any) -> PixelSize | None:
    unit = page.tags.get("ResolutionUnit")
    if unit is None or int(unit.value) != _RESOLUTION_UNIT_CENTIMETER:
        return None
    px_per_cm_x = _resolution_value(page, "XResolution")
    if not px_per_cm_x:
        return None
    px_per_cm_y = _resolution_value(page, "YResolution") or px_per_cm_x
    return PixelSize(_MICRONS_PER_CM / px_per_cm_x, _MICRONS_PER_CM / px_per_cm_y, "tiff-resolution")


# ── ND2 (optional dependency) ────────────────────────────────────────────────


def _read_nd2(path: Path) -> ImageData:
    try:
        import nd2
    except ImportError as exc:
        raise UnsupportedImageError(
            "Reading .nd2 files needs the optional 'nd2' package: pip install 'karcytics-sdk[imaging]'"
        ) from exc

    with nd2.ND2File(path) as f:
        axes = "".join(f.sizes)  # nd2 orders sizes like the array, e.g. {"T":..,"Z":..,"C":..,"Y":..,"X":..}
        data = to_canonical(np.asarray(f.asarray()), axes)
        image = ImageData(data=data, path=path)
        voxel = f.voxel_size()
        if voxel.x > 0 and voxel.y > 0:
            image.pixel_size = PixelSize(float(voxel.x), float(voxel.y), "nd2")
        if image.sizes["Z"] > 1 and voxel.z > 0:
            image.z_step_um = float(voxel.z)
        channels = getattr(f.metadata, "channels", None) or []
        names = [ch.channel.name for ch in channels]
        if len(names) == image.sizes["C"] and all(names):
            image.channel_names = names
    if not image.channel_names:
        image.channel_names = _default_channel_names(image)
    return image


# ── PNG / JPEG / BMP ─────────────────────────────────────────────────────────


def _read_plain(path: Path) -> ImageData:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as img:
            array = np.asarray(img)
    except UnidentifiedImageError as exc:
        raise UnsupportedImageError(f"Not a readable image: {path.name}") from exc
    if array.ndim == 3 and array.shape[-1] in _RGB_SAMPLES:  # noqa: PLR2004
        data = to_canonical(array, "YXS")
        image = ImageData(data=data, path=path, channel_names=["Red", "Green", "Blue", "Alpha"][: array.shape[-1]])
    else:
        image = ImageData(data=to_canonical(array, "YX"), path=path)
        image.channel_names = _default_channel_names(image)
    return image


def _default_channel_names(image: ImageData) -> list[str]:
    n = image.sizes["C"]
    return [image.path.stem] if n == 1 else [f"{image.path.stem} (Ch {i + 1})" for i in range(n)]
