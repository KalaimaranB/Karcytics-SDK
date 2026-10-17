"""contrib.imaging: native bit depth, canonical axes and physical calibration."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest
import tifffile
from PIL import Image

from karcytics_sdk.contrib.imaging import (
    CANONICAL_AXES,
    UnsupportedImageError,
    read_image,
    to_canonical,
)


def _ramp(shape, dtype=np.uint16):
    return (np.arange(np.prod(shape)) % 60000).astype(dtype).reshape(shape)


class TestToCanonical:
    def test_plain_2d_gets_singleton_t_c_z(self):
        out = to_canonical(np.zeros((4, 5)), "YX")
        assert out.shape == (1, 1, 1, 4, 5)

    def test_reorders_axes(self):
        arr = _ramp((3, 2, 4, 5))  # Z C Y X
        out = to_canonical(arr, "ZCYX")
        assert out.shape == (1, 2, 3, 4, 5)
        np.testing.assert_array_equal(out[0, 1, 2], arr[2, 1])

    def test_rgb_samples_become_channels(self):
        arr = _ramp((4, 5, 3), np.uint8)
        out = to_canonical(arr, "YXS")
        assert out.shape == (1, 3, 1, 4, 5)
        np.testing.assert_array_equal(out[0, 2, 0], arr[..., 2])

    def test_samples_fold_into_existing_channels(self):
        arr = _ramp((2, 4, 5, 3), np.uint8)  # C Y X S
        out = to_canonical(arr, "CYXS")
        assert out.shape == (1, 6, 1, 4, 5)
        np.testing.assert_array_equal(out[0, 4, 0], arr[1, ..., 1])  # channel-major: c=1, s=1

    def test_unknown_axis_becomes_time(self):
        out = to_canonical(np.zeros((7, 4, 5)), "QYX")
        assert out.shape == (7, 1, 1, 4, 5)

    def test_mismatched_axes_are_rejected(self):
        with pytest.raises(UnsupportedImageError):
            to_canonical(np.zeros((4, 5)), "ZYX")


class TestTiff:
    def test_sixteen_bit_survives_untouched(self, tmp_path):
        arr = _ramp((16, 16))
        path = tmp_path / "deep.tif"
        tifffile.imwrite(path, arr)

        image = read_image(path)

        assert image.dtype == np.uint16
        np.testing.assert_array_equal(image.plane(), arr)
        assert image.channel_names == ["deep"]
        assert image.pixel_size is None

    def test_centimetre_resolution_gives_microns_per_pixel(self, tmp_path):
        path = tmp_path / "cm.tif"
        tifffile.imwrite(path, _ramp((8, 8)), resolution=(20000, 10000), resolutionunit="CENTIMETER")

        image = read_image(path)

        assert image.pixel_size is not None
        assert image.pixel_size.x_um == pytest.approx(0.5)
        assert image.pixel_size.y_um == pytest.approx(1.0)
        assert image.pixel_size.source == "tiff-resolution"

    def test_inch_resolution_is_ignored(self, tmp_path):
        path = tmp_path / "dpi.tif"
        tifffile.imwrite(path, _ramp((8, 8)), resolution=(72, 72), resolutionunit="INCH")

        assert read_image(path).pixel_size is None

    def test_imagej_hyperstack_axes_scale_spacing_and_interval(self, tmp_path):
        arr = _ramp((2, 3, 2, 8, 9))  # T Z C Y X
        path = tmp_path / "hyper.tif"
        tifffile.imwrite(
            path,
            arr,
            imagej=True,
            resolution=(1 / 0.25, 1 / 0.25),
            metadata={"axes": "TZCYX", "unit": "um", "spacing": 1.5, "finterval": 30.0},
        )

        image = read_image(path)

        assert image.sizes == {"T": 2, "C": 2, "Z": 3, "Y": 8, "X": 9}
        np.testing.assert_array_equal(image.plane(c=1, z=2, t=1), arr[1, 2, 1])
        assert image.pixel_size is not None
        assert image.pixel_size.x_um == pytest.approx(0.25)
        assert image.pixel_size.source == "imagej"
        assert image.z_step_um == pytest.approx(1.5)
        assert image.time_interval_s == pytest.approx(30.0)
        assert image.channel_names == ["hyper (Ch 1)", "hyper (Ch 2)"]

    def test_ome_physical_size_and_channel_names(self, tmp_path):
        arr = _ramp((2, 8, 9))
        path = tmp_path / "cells.ome.tif"
        tifffile.imwrite(
            path,
            arr,
            ome=True,
            metadata={
                "axes": "CYX",
                "PhysicalSizeX": 325.0,
                "PhysicalSizeXUnit": "nm",
                "PhysicalSizeY": 0.325,
                "PhysicalSizeYUnit": "µm",
                "Channel": {"Name": ["DAPI", "GFP"]},
            },
        )

        image = read_image(path)

        assert image.channel_names == ["DAPI", "GFP"]
        assert image.pixel_size is not None
        assert image.pixel_size.x_um == pytest.approx(0.325)
        assert image.pixel_size.y_um == pytest.approx(0.325)
        assert image.pixel_size.source == "ome"
        np.testing.assert_array_equal(image.plane(c=1), arr[1])


class TestPlainFormats:
    def test_rgb_png_splits_into_named_channels(self, tmp_path):
        arr = _ramp((6, 7, 3), np.uint8)
        path = tmp_path / "rgb.png"
        Image.fromarray(arr).save(path)

        image = read_image(path)

        assert image.channel_names == ["Red", "Green", "Blue"]
        np.testing.assert_array_equal(image.plane(c=0), arr[..., 0])

    def test_grayscale_png(self, tmp_path):
        path = tmp_path / "g.png"
        Image.fromarray(_ramp((6, 7), np.uint8)).save(path)

        image = read_image(path)

        assert image.sizes["C"] == 1
        assert image.channel_names == ["g"]

    def test_not_an_image(self, tmp_path):
        path = tmp_path / "notes.png"
        path.write_text("hello")

        with pytest.raises(UnsupportedImageError):
            read_image(path)

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_image(tmp_path / "nope.tif")


class TestNd2:
    def test_without_the_optional_package_says_how_to_install_it(self, tmp_path, monkeypatch):
        path = tmp_path / "x.nd2"
        path.write_bytes(b"")
        monkeypatch.setitem(sys.modules, "nd2", None)

        with pytest.raises(UnsupportedImageError, match=r"karcytics-sdk\[imaging\]"):
            read_image(path)

    def test_reads_axes_voxel_size_and_channel_names(self, tmp_path, monkeypatch):
        arr = _ramp((3, 2, 8, 9))  # Z C Y X

        class FakeND2File:
            sizes = {"Z": 3, "C": 2, "Y": 8, "X": 9}
            metadata = types.SimpleNamespace(
                channels=[
                    types.SimpleNamespace(channel=types.SimpleNamespace(name="DAPI")),
                    types.SimpleNamespace(channel=types.SimpleNamespace(name="mCherry")),
                ]
            )

            def __init__(self, _path):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def asarray(self):
                return arr

            def voxel_size(self):
                return types.SimpleNamespace(x=0.1, y=0.1, z=0.5)

        monkeypatch.setitem(sys.modules, "nd2", types.SimpleNamespace(ND2File=FakeND2File))
        path = tmp_path / "stack.nd2"
        path.write_bytes(b"")

        image = read_image(path)

        assert "".join(image.sizes) == CANONICAL_AXES
        assert image.sizes["Z"] == 3
        assert image.channel_names == ["DAPI", "mCherry"]
        assert image.pixel_size is not None and image.pixel_size.x_um == pytest.approx(0.1)
        assert image.z_step_um == pytest.approx(0.5)
        np.testing.assert_array_equal(image.plane(c=1, z=2), arr[2, 1])
