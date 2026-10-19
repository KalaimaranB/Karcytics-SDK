"""Image loading, format conversion, and preprocessing utilities (deprecated).

Deprecated: this is western-blot band-image logic, which moved into that
plugin. It will be removed in karcytics-sdk 4.0.

This module provides low-level image I/O and transformation functions
used throughout the Karcytics analysis pipeline. All functions operate on
NumPy arrays and are agnostic to the analysis type.

Design Notes:
    - Images are represented as ``numpy.ndarray`` with dtype ``float64``
      and values in the range [0.0, 1.0].
    - Grayscale conversion uses luminance-weighted averaging.
    - All functions are pure (no side effects) and stateless.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import (
    gaussian_filter,
    median_filter,
    uniform_filter1d,
)
from skimage import exposure
from skimage import io as skio
from skimage.color import rgb2gray
from skimage.filters import threshold_otsu
from skimage.measure import label, regionprops
from skimage.transform import rotate
from skimage.util import img_as_float64

warnings.warn(
    "karcytics_sdk.contrib.image_utils is deprecated and will be removed in karcytics-sdk 4.0: it is "
    "western-blot band-image logic, which now lives in that plugin. Copy what you need into your plugin.",
    DeprecationWarning,
    stacklevel=2,
)


def adjust_contrast(image: NDArray[np.float64], alpha: float = 1.0, beta: float = 0.0) -> NDArray[np.float64]:
    """Adjust contrast and brightness of a float64 image.

    Formula: output = alpha * image + beta

    Args:
        image: Float64 image in [0.0, 1.0].
        alpha: Contrast control (1.0 = original, >1.0 = more contrast, <1.0 = less contrast).
        beta: Brightness control (0.0 = original, >0.0 = brighter, <0.0 = darker).

    Returns:
        Adjusted float64 image clipped to [0.0, 1.0].
    """
    if abs(alpha - 1.0) < 0.001 and abs(beta) < 0.001:  # noqa: PLR2004
        return image.copy()

    adjusted = image * alpha + beta
    return np.clip(adjusted, 0.0, 1.0)


def load_and_convert(
    path: str | Path,
    as_grayscale: bool = True,
) -> NDArray[np.float64]:
    """Load an image from disk and convert to normalized float64.

    Supports all formats handled by scikit-image (TIFF, PNG, JPG, BMP, etc.),
    including 16-bit and 32-bit scientific image formats.

    Args:
        path: Path to the image file.
        as_grayscale: If True (default), convert to single-channel grayscale.
            If False, return as RGB float64 with shape (H, W, 3).

    Returns:
        Image as a float64 array with values in [0.0, 1.0].

    Raises:
        FileNotFoundError: If the image file does not exist.
        ValueError: If the file cannot be read as an image.

    Example::

        >>> img = load_and_convert("blot.tif")
        >>> img.dtype
        dtype('float64')
        >>> 0.0 <= img.min() <= img.max() <= 1.0
        True
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    try:
        image = skio.imread(str(path))
    except Exception as exc:
        raise ValueError(f"Could not read image file '{path}': {exc}") from exc

    # Convert to float64 in [0, 1]
    image = img_as_float64(image)

    # Handle RGBA by dropping alpha channel
    if image.ndim == 3 and image.shape[2] == 4:  # noqa: PLR2004
        image = image[:, :, :3]

    # Convert to grayscale if requested
    if as_grayscale and image.ndim == 3:  # noqa: PLR2004
        image = rgb2gray(image)

    return image


def auto_detect_inversion(image: NDArray[np.float64]) -> bool:
    """Determine whether the image LUT should be inverted.

    Western blot images may have either:

    - **Light background with dark bands** (standard case) — this should
      generally *not* be inverted; the analysis pipeline can work directly
      with dark valleys against a bright background.
    - **Dark background with light bands** — in this case inversion is
      helpful so that bands become bright peaks on a light background.

    This heuristic is deliberately conservative and will only request
    inversion when the image looks like ``light bands on dark background``.

    Args:
        image: Grayscale float64 image in [0.0, 1.0].

    Returns:
        True if the image should be inverted (dark background, light bands),
        False otherwise.
    """
    h, w = image.shape[:2]

    # Ignore exact 0.0 pixels which are likely rotation padding
    valid_pixels = image[image > 0.0]
    if len(valid_pixels) == 0:
        return False

    # Primary heuristic: check overall image median
    overall_median = float(np.median(valid_pixels))

    # Predominantly light background (standard blot: white bg, dark bands)
    # → do NOT invert.
    if overall_median > 0.6:  # noqa: PLR2004
        return False

    # Predominantly dark background → candidate for inversion if the center
    # region contains brighter structures than the corners.
    if overall_median < 0.4:  # noqa: PLR2004
        corner_size_h = max(1, h // 10)
        corner_size_w = max(1, w // 10)

        corners = np.concatenate(
            [
                image[:corner_size_h, :corner_size_w].ravel(),  # top-left
                image[:corner_size_h, -corner_size_w:].ravel(),  # top-right
                image[-corner_size_h:, :corner_size_w].ravel(),  # bottom-left
                image[-corner_size_h:, -corner_size_w:].ravel(),  # bottom-right
            ]
        )
        corners = corners[corners > 0.0]

        ch, cw = h // 4, w // 4
        center = image[ch : h - ch, cw : w - cw].ravel()
        center = center[center > 0.0]

        if len(center) == 0 or len(corners) == 0:
            return True

        corner_median = float(np.median(corners))
        center_median = float(np.median(center))

        return center_median > corner_median + 0.05

    # For ambiguous mid-range cases (overall ~mid-gray), compare corners to center.
    corner_size_h = max(1, h // 10)
    corner_size_w = max(1, w // 10)

    corners = np.concatenate(
        [
            image[:corner_size_h, :corner_size_w].ravel(),
            image[:corner_size_h, -corner_size_w:].ravel(),
            image[-corner_size_h:, :corner_size_w].ravel(),
            image[-corner_size_h:, -corner_size_w:].ravel(),
        ]
    )
    corners = corners[corners > 0.0]

    ch, cw = h // 4, w // 4
    center = image[ch : h - ch, cw : w - cw].ravel()
    center = center[center > 0.0]

    if len(center) == 0 or len(corners) == 0:
        return False

    corner_median = float(np.median(corners))
    center_median = float(np.median(center))

    return center_median > corner_median + 0.05


def invert_image(image: NDArray[np.float64]) -> NDArray[np.float64]:
    """Invert a normalized float64 image (1.0 - image).

    Args:
        image: Float64 image in [0.0, 1.0].

    Returns:
        Inverted image.
    """
    return 1.0 - image


def enhance_for_band_detection(  # noqa: PLR0913
    image: NDArray[np.float64],
    *,
    apply_clahe: bool = True,
    clahe_clip_limit: float = 2.0,
    clahe_tile_grid_size: int = 8,
    denoise_median_ksize: int = 0,
    background_kernel_size: int = 0,
) -> NDArray[np.float64]:
    """Enhance contrast and flatten background for band detection.

    This function is intended to be used *after* polarity normalization so that
    bands are peaks (higher intensity) and background is lower intensity.

    Steps (all optional):
    - CLAHE (local contrast enhancement)
    - Median denoise (speckle/dust suppression)
    - Large-kernel background estimation + subtraction (gradient flattening)

    Args:
        image: Grayscale float64 in [0, 1].
        apply_clahe: Apply CLAHE to boost local contrast.
        clahe_clip_limit: CLAHE clip limit (higher = more contrast).
        clahe_tile_grid_size: CLAHE tile grid size in pixels (OpenCV uses (n,n)).
        denoise_median_ksize: Median blur kernel size (odd int). 0 disables.
        background_kernel_size: Gaussian blur kernel size (odd int) used to
            estimate background. 0 disables.

    Returns:
        Enhanced float64 image in [0, 1].
    """
    out = np.clip(image, 0.0, 1.0)

    if apply_clahe:
        clip = float(np.clip(clahe_clip_limit / 10.0, 0.001, 0.2))
        k = int(max(2, clahe_tile_grid_size))
        out = exposure.equalize_adapthist(out, clip_limit=clip, kernel_size=k)

    if denoise_median_ksize and denoise_median_ksize >= 3:  # noqa: PLR2004
        k = int(denoise_median_ksize)
        if k % 2 == 0:
            k += 1
        out = median_filter(out, size=(k, k))

    if background_kernel_size and background_kernel_size >= 5:  # noqa: PLR2004
        k = int(background_kernel_size)
        if k % 2 == 0:
            k += 1
        sigma = max(1.0, float(k) / 6.0)
        bg = gaussian_filter(out, sigma=sigma)
        out = out - bg
        lo, hi = np.percentile(out, [1.0, 99.5])
        if hi > lo:
            out = (out - lo) / (hi - lo)
        out = np.clip(out, 0.0, 1.0)

    return out


def rotate_image(
    image: NDArray[np.float64],
    angle: float,
    fill_value: float = 1.0,
) -> NDArray[np.float64]:
    """Rotate an image by the given angle in degrees.

    Positive angles rotate counter-clockwise. The image is resized to
    contain the full rotated content (no cropping).

    Args:
        image: Float64 image.
        angle: Rotation angle in degrees. Positive = counter-clockwise.
        fill_value: Value to fill border pixels created by rotation.
            Default 1.0 (white) suits dark-on-light blot images.

    Returns:
        Rotated image as float64.
    """
    if abs(angle) < 0.01:  # noqa: PLR2004
        return image.copy()

    return rotate(
        image,
        angle=angle,
        resize=True,
        mode="constant",
        cval=fill_value,
        preserve_range=True,
    )


def auto_contrast_stretch(
    image: NDArray[np.float64],
    low_pct: float = 1.0,
    high_pct: float = 99.0,
) -> tuple[float, float]:
    """Compute optimal contrast alpha/beta by percentile stretching.

    Finds the alpha (contrast) and beta (brightness offset) values that
    stretch the meaningful pixel range to fill [0, 1], ignoring the
    darkest ``low_pct``% and brightest ``high_pct``% as outliers.

    Args:
        image: Grayscale float64 image in [0.0, 1.0].
        low_pct: Lower percentile to clip (outlier dark pixels).
        high_pct: Upper percentile to clip (outlier bright pixels).

    Returns:
        Tuple of (alpha, beta) ready to pass to ``adjust_contrast``.
    """
    valid = image[image > 0.0] if np.any(image > 0.0) else image.ravel()
    p_lo = float(np.percentile(valid, low_pct))
    p_hi = float(np.percentile(valid, high_pct))

    if p_hi <= p_lo:
        return 1.0, 0.0

    alpha = round(1.0 / (p_hi - p_lo), 3)
    beta = round(-p_lo * alpha, 3)
    alpha = float(np.clip(alpha, 0.1, 5.0))
    beta = float(np.clip(beta, -1.0, 1.0))
    return alpha, beta


def auto_detect_rotation(
    image: NDArray[np.float64],
    angle_range: float = 15.0,
    angle_step: float = 0.25,
) -> float:
    """Detect the tilt angle of horizontal bands in a western blot image.

    Strategy:
        1. Contrast-stretch so bands are visible.
        2. Compute horizontal Sobel edges — band edges are strong
           horizontal features.
        3. Sweep candidate angles; pick the one that maximises the
           variance of the vertical projection of the edge image.
           When bands are perfectly horizontal each band collapses to
           a sharp spike in the projection → variance is maximised.

    Args:
        image: Grayscale float64 image in [0.0, 1.0].
        angle_range: Search ±angle_range degrees from zero.
        angle_step: Angular resolution in degrees.

    Returns:
        Estimated correction angle in degrees (positive = counter-clockwise).
        Returns 0.0 if no clear tilt is found.
    """
    from scipy.ndimage import sobel
    from skimage.transform import rotate as sk_rotate

    h, w = image.shape[:2]
    scale = min(1.0, 400.0 / max(h, w))
    if scale < 1.0:
        from skimage.transform import resize as sk_resize

        small = sk_resize(image, (int(h * scale), int(w * scale)), anti_aliasing=True, preserve_range=True)
    else:
        small = image.copy()

    alpha, beta = auto_contrast_stretch(small)
    small = np.clip(small * alpha + beta, 0.0, 1.0)
    small = 1.0 - small

    edges = np.abs(sobel(small, axis=0))

    angles = np.arange(-angle_range, angle_range + angle_step, angle_step)
    best_angle = 0.0
    best_var = -1.0

    for angle in angles:
        if abs(angle) < 1e-3:  # noqa: PLR2004
            rotated = edges
        else:
            rotated = sk_rotate(edges, angle, resize=False, mode="constant", cval=0.0, preserve_range=True)
        proj = rotated.sum(axis=1)
        var = float(np.var(proj))
        if var > best_var:
            best_var = var
            best_angle = float(angle)

    if abs(best_angle) < angle_step:
        return 0.0
    return round(best_angle, 2)


def calculate_band_crop_region(  # noqa: C901, PLR0912, PLR0913, PLR0915, PLR0917
    image: NDArray[np.float64],
    dark_threshold: float | None = None,
    min_band_width_frac: float = 0.025,
    min_band_height_frac: float = 0.01,
    vertical_padding_frac: float = 0.15,
    horizontal_padding_frac: float = 0.10,
    smoothing_window: int = 7,
) -> tuple[int, int, int, int] | None:
    """Compute the 2D bounding box crop region containing the bands."""
    h, w = image.shape[:2]
    if h < 2 or w < 2:  # noqa: PLR2004
        return None

    # 1. Ignore rotation padding (pure white pixels)
    valid_mask = image < 0.99  # noqa: PLR2004
    valid_pixels = image[valid_mask]

    if len(valid_pixels) < 100:  # noqa: PLR2004
        return None

    # 2. Dynamic Threshold (Otsu) for Vertical Rows
    try:
        thresh_v = threshold_otsu(valid_pixels) * 0.95
    except Exception:
        thresh_v = float(np.percentile(valid_pixels, 25))

    dark_pixels = (image < thresh_v) & valid_mask

    # --- 3. Vertical (Row) Calculation ---
    dark_frac_rows = np.mean(dark_pixels, axis=1)

    if smoothing_window > 1:
        dark_frac_rows = uniform_filter1d(dark_frac_rows, size=smoothing_window, mode="nearest")

    row_mask = dark_frac_rows >= min_band_width_frac
    if not np.any(row_mask):
        return None

    in_region = False
    start = 0
    best_r_region = (0, 0)
    best_size = 0

    for i, val in enumerate(row_mask):
        if val and not in_region:
            in_region = True
            start = i
        elif not val and in_region:
            in_region = False
            size = i - start
            if size > best_size:
                best_size = size
                best_r_region = (start, i)

    if in_region:
        size = h - start
        if size > best_size:
            best_size = size
            best_r_region = (start, h)

    if best_size < max(2, int(h * min_band_height_frac)):
        return None

    r_min, r_max = best_r_region

    # --- 4. Horizontal (Column) Calculation: Adaptive Geometry ---

    band_strip = image[r_min:r_max, :]
    h_strip, w_strip = band_strip.shape

    if h_strip < 2 or w_strip < 2:  # noqa: PLR2004
        return None

    valid_strip_mask = band_strip < 0.99  # noqa: PLR2004
    valid_strip_pixels = band_strip[valid_strip_mask]

    if len(valid_strip_pixels) < 100:  # noqa: PLR2004
        c_min, c_max = 0, w_strip
    else:
        try:
            # The * 0.95 strictly isolates the dense cores so they don't merge with shadows
            thresh_h = threshold_otsu(valid_strip_pixels) * 0.95
        except Exception:
            thresh_h = float(np.percentile(valid_strip_pixels, 20))

        binary_strip = (band_strip < thresh_h) & valid_strip_mask

        labeled_strip = label(binary_strip)
        props = regionprops(labeled_strip)

        accepted_bboxes = []
        core_widths = []

        min_band_w = max(5, int(w_strip * 0.015))

        # A. Collect all legitimate band cores
        for p in props:
            min_row, min_col, max_row, max_col = p.bbox
            bw = max_col - min_col
            bh = max_row - min_row

            if bh == 0:
                continue
            aspect_ratio = bw / float(bh)

            # Rule: Must be wider than it is tall, and ignore tiny dust/giant background blocks
            if aspect_ratio > 1.25 and bw >= min_band_w and bw < (w_strip * 0.9):  # noqa: PLR2004
                accepted_bboxes.append(p.bbox)
                core_widths.append(bw)

        # B. Calculate Adaptive Lane Edges based on Core Dimensions
        if not accepted_bboxes:
            c_min, c_max = 0, w_strip
        else:
            # Find the absolute left/right extremes of the dense cores
            core_c_min = min([bbox[1] for bbox in accepted_bboxes])
            core_c_max = max([bbox[3] for bbox in accepted_bboxes])

            # Calculate the median physical width of the bands in THIS specific image
            median_core_width = float(np.median(core_widths))

            # The physical lane fade out is usually ~50% of the core width.
            # This scales perfectly whether the bands are 20px wide or 200px wide!
            lane_expansion = int(median_core_width * 0.50)

            # Expand the bounds outward to the true fade-out edge
            c_min = max(0, core_c_min - lane_expansion)
            c_max = min(w_strip, core_c_max + lane_expansion)

    # --- 5. Apply Proportional Buffering ---
    band_height = max(r_max - r_min, 1)
    band_width = max(c_max - c_min, 1)

    # We now purely rely on the fractional padding (default 10%) for breathing room,
    # because the physical edges have already been adaptively calculated.
    v_pad = int(band_height * vertical_padding_frac)
    h_pad = int(band_width * horizontal_padding_frac)

    # Safe minimums just so the box never touches the absolute pixel edge on tight crops
    v_pad = max(v_pad, 20)
    h_pad = max(h_pad, 10)

    final_r_min = max(0, r_min - v_pad)
    final_r_max = min(h, r_max + v_pad)
    final_c_min = max(0, c_min - h_pad)
    final_c_max = min(w, c_max + h_pad)

    return (int(final_r_min), int(final_r_max), int(final_c_min), int(final_c_max))


def auto_crop_to_bands(  # noqa: PLR0913, PLR0917
    image: NDArray[np.float64],
    dark_threshold: float | None = None,
    min_band_width_frac: float = 0.025,
    min_band_height_frac: float = 0.01,
    vertical_padding_frac: float = 0.15,
    horizontal_padding_frac: float = 0.10,
    smoothing_window: int = 7,
) -> NDArray[np.float64]:
    """Crop the image in 2D to the region containing bands with ample padding."""
    region = calculate_band_crop_region(
        image,
        min_band_width_frac=min_band_width_frac,
        min_band_height_frac=min_band_height_frac,
        vertical_padding_frac=vertical_padding_frac,
        horizontal_padding_frac=horizontal_padding_frac,
        smoothing_window=smoothing_window,
    )

    if region is None:
        return image

    r_min, r_max, c_min, c_max = region

    # Perform the 2D crop!
    return image[r_min:r_max, c_min:c_max]


def calculate_autocrop_region(
    image: NDArray[np.float64],
    padding: int = 10,
    min_content_fraction: float = 0.005,
) -> tuple[int, int, int, int] | None:
    """Calculate a robust autocrop region for bands without applying it.

    Uses dynamic thresholding (Otsu's method) to separate dark bands from
    the blot background, completely ignoring white rotation padding.

    Args:
        image: Grayscale float64 image in [0.0, 1.0].
        padding: Number of pixels to keep around the detected content.
        min_content_fraction: Minimum fraction of dark pixels in a row/col
            to consider it actual band content (filters out noise).

    Returns:
        Tuple of (x, y, width, height) defining the crop region,
        or None if no content is detected.
    """
    # 1. Isolate the actual blot from the rotation padding.
    # Rotation padding is usually exactly 1.0. We look at pixels < 0.99.
    valid_mask = image < 0.99  # noqa: PLR2004
    valid_pixels = image[valid_mask]

    if len(valid_pixels) < 100:  # Failsafe for entirely blank images  # noqa: PLR2004
        return None

    # 2. Determine a dynamic threshold to separate bands from the blot background.
    try:
        # Otsu finds the optimal threshold splitting the bimodal distribution
        thresh = threshold_otsu(valid_pixels)
        # Make it slightly stricter (5% darker) to avoid capturing background gradients
        thresh = thresh * 0.95
    except Exception:
        # Fallback if the image is incredibly flat and Otsu fails
        thresh = np.median(valid_pixels) * 0.8

    # 3. Create a mask of just the bands
    content_mask = image < thresh

    # 4. Clean up noise by requiring a minimum density of dark pixels per row/column
    min_row_pixels = max(1, int(image.shape[1] * min_content_fraction))
    min_col_pixels = max(1, int(image.shape[0] * min_content_fraction))

    rows_with_content = np.sum(content_mask, axis=1) >= min_row_pixels
    cols_with_content = np.sum(content_mask, axis=0) >= min_col_pixels

    if not np.any(rows_with_content) or not np.any(cols_with_content):
        return None  # No valid bands detected

    row_indices = np.where(rows_with_content)[0]
    col_indices = np.where(cols_with_content)[0]

    # 5. Calculate bounds with padding
    r_min = max(0, row_indices[0] - padding)
    r_max = min(image.shape[0], row_indices[-1] + padding + 1)
    c_min = max(0, col_indices[0] - padding)
    c_max = min(image.shape[1], col_indices[-1] + padding + 1)

    # Guard against degenerate cases where padding pushes bounds too far
    if r_max <= r_min or c_max <= c_min:
        return None

    return (
        int(c_min),  # x
        int(r_min),  # y
        int(c_max - c_min),  # width
        int(r_max - r_min),  # height
    )


def crop_to_content(
    image: NDArray[np.float64],
    padding: int = 10,
    min_content_fraction: float = 0.005,
) -> NDArray[np.float64]:
    """Auto-crop whitespace/blackspace borders from an image.

    Dynamically isolates bands from the background and removes empty rows/cols.

    Args:
        image: Grayscale float64 image in [0.0, 1.0].
        padding: Number of pixels to keep around the detected content.
        min_content_fraction: Minimum fraction of dark pixels in a row/column
            to consider it part of the content region.

    Returns:
        Cropped image.
    """
    if image.ndim != 2:  # noqa: PLR2004
        raise ValueError("crop_to_content expects a 2D grayscale image")

    region = calculate_autocrop_region(image, padding=padding, min_content_fraction=min_content_fraction)

    if region is None:
        return image  # No content detected, return original safely

    x, y, w, h = region
    return image[y : y + h, x : x + w]


def apply_false_color(*args, **kwargs):
    pass


def extract_thumbnail(*args, **kwargs):
    pass
