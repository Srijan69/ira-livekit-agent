"""Unit tests for the image pipeline and asset optimization tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from image_pipeline import (
    download_image,
    download_images,
    extract_image_urls_from_html,
    get_desktop_optimized_assets_dir,
    launch_image_editor,
    resize_image,
    run_image_pipeline,
    scrape_image_urls,
)


def _create_synthetic_image(
    path: Path, size: tuple[int, int] = (1920, 1080), mode: str = "RGB"
) -> Path:
    """Helper to create a synthetic image file for offline testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    color = (255, 0, 128, 200) if mode == "RGBA" else (40, 80, 200)
    img = Image.new(mode, size, color)
    img.save(path, "PNG" if mode == "RGBA" else "JPEG")
    return path


def test_resize_image_to_exact_dimensions(tmp_path: Path):
    """Verify an arbitrary resolution image is resized to exactly 1080x1080."""
    src = _create_synthetic_image(tmp_path / "src_large.jpg", (2560, 1440))
    dest = tmp_path / "resized_1080.jpg"

    out = resize_image(src, dest, size=(1080, 1080))
    assert out.exists()

    with Image.open(out) as img:
        assert img.size == (1080, 1080)
        assert img.mode == "RGB"
        assert img.format == "JPEG"


def test_resize_handles_rgba_with_transparency(tmp_path: Path):
    """Verify RGBA images with transparency are composited and saved as clean RGB JPEGs."""
    src = _create_synthetic_image(tmp_path / "transparent.png", (800, 600), mode="RGBA")
    dest = tmp_path / "converted.jpg"

    out = resize_image(src, dest, size=(1080, 1080))
    assert out.exists()

    with Image.open(out) as img:
        assert img.size == (1080, 1080)
        assert img.mode == "RGB"


def test_resize_nonexistent_file_raises_error(tmp_path: Path):
    """Verify FileNotFoundError is raised when source image does not exist."""
    dest = tmp_path / "out.jpg"
    with pytest.raises(FileNotFoundError):
        resize_image(tmp_path / "missing.jpg", dest)


def test_download_image_from_local_path(tmp_path: Path):
    """Verify downloading from a local path copies file correctly."""
    src = _create_synthetic_image(tmp_path / "local_source.jpg", (500, 500))
    dest = tmp_path / "dest" / "copied.jpg"

    out = download_image(str(src), dest)
    assert out.exists()
    assert out.stat().st_size == src.stat().st_size


def test_download_image_from_file_uri(tmp_path: Path):
    """Verify downloading from a file:// URI works correctly."""
    src = _create_synthetic_image(tmp_path / "source.jpg", (400, 400))
    dest = tmp_path / "from_uri.jpg"

    uri = src.as_uri()
    out = download_image(uri, dest)
    assert out.exists()
    assert out.stat().st_size == src.stat().st_size


def test_download_images_multiple_local_files(tmp_path: Path):
    """Verify downloading multiple images from local files."""
    f1 = _create_synthetic_image(tmp_path / "img1.jpg", (300, 300))
    f2 = _create_synthetic_image(tmp_path / "img2.jpg", (400, 400))
    f3 = _create_synthetic_image(tmp_path / "img3.jpg", (500, 500))

    dest_dir = tmp_path / "downloaded"
    results = download_images([str(f1), str(f2), str(f3)], dest_dir)

    assert len(results) == 3
    for p in results:
        assert p.exists()


def test_download_images_empty_list_raises(tmp_path: Path):
    """Verify ValueError is raised if URL list is empty."""
    with pytest.raises(ValueError, match="empty URL list"):
        download_images([], tmp_path / "dest")


def test_extract_image_urls_from_html():
    """Verify HTML parser extracts image URLs from img tags and data attributes."""
    html = """
    <div>
      <img src="https://example.com/thumb1.jpg" data-highres="https://example.com/full1.jpg" />
      <img src="https://example.com/regular2.jpg" />
      <img src="https://example.com/pic3.png" />
      <img src="https://example.com/extra4.jpg" />
    </div>
    """
    urls = extract_image_urls_from_html(html, count=3)
    assert len(urls) == 3
    assert urls[0] == "https://example.com/full1.jpg"
    assert urls[1] == "https://example.com/regular2.jpg"
    assert urls[2] == "https://example.com/pic3.png"


def test_scrape_image_urls_with_html_content():
    """Verify scrape_image_urls extracts URLs directly from provided HTML."""
    html = """
    <figure data-test="photo-grid-image">
      <img src="https://images.unsplash.com/city-1?w=1920" />
      <img src="https://images.unsplash.com/city-2?w=1920" />
      <img src="https://images.unsplash.com/city-3?w=1920" />
    </figure>
    """
    urls = scrape_image_urls(html_content=html, count=3)
    assert len(urls) == 3
    assert "city-1" in urls[0]


def test_run_image_pipeline_end_to_end(tmp_path: Path):
    """Test 2 End-to-End: Create 3 source images, run pipeline, verify 1080x1080 and city_01.jpg naming."""
    src1 = _create_synthetic_image(tmp_path / "raw1.jpg", (1920, 1080))
    src2 = _create_synthetic_image(tmp_path / "raw2.png", (2560, 1440), mode="RGBA")
    src3 = _create_synthetic_image(tmp_path / "raw3.jpg", (3840, 2160))

    out_dir = tmp_path / "Optimized_Assets"

    results = run_image_pipeline(
        query="cyberpunk city",
        source_urls=[str(src1), str(src2), str(src3)],
        output_dir=out_dir,
        count=3,
        target_size=(1080, 1080),
    )

    assert len(results) == 3
    expected_names = ["city_01.jpg", "city_02.jpg", "city_03.jpg"]

    for _idx, (path, expected_name) in enumerate(zip(results, expected_names)):
        assert path.name == expected_name
        assert path.exists()
        with Image.open(path) as img:
            assert img.size == (1080, 1080)
            assert img.mode == "RGB"
            assert img.format == "JPEG"


def test_get_desktop_optimized_assets_dir_respects_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify get_desktop_optimized_assets_dir uses IRA_OPTIMIZED_ASSETS_DIR environment variable."""
    custom_dir = tmp_path / "Custom_Assets"
    monkeypatch.setenv("IRA_OPTIMIZED_ASSETS_DIR", str(custom_dir))

    resolved = get_desktop_optimized_assets_dir()
    assert resolved == custom_dir.resolve()
    assert resolved.exists()


def test_launch_image_editor_callable():
    """Verify launch_image_editor calls custom open_app_fn."""
    mock_launcher = MagicMock()
    fake_img = Path("test.jpg")

    with patch.object(Path, "exists", return_value=True):
        success = launch_image_editor(
            fake_img, app_name="MS Paint", open_app_fn=mock_launcher
        )
        assert success is True
        mock_launcher.assert_called_once_with("MS Paint")


def test_error_logging_on_corrupted_image(tmp_path: Path):
    """Verify error_logger records log when resizing invalid/corrupted file."""
    corrupted = tmp_path / "bad.jpg"
    corrupted.write_text("NOT AN IMAGE CONTENT", encoding="utf-8")
    dest = tmp_path / "out.jpg"

    with patch("image_pipeline.log_tool_error") as mock_log:
        with pytest.raises((Image.UnidentifiedImageError, OSError)):
            resize_image(corrupted, dest)
        mock_log.assert_called_once()
        assert mock_log.call_args[1]["tool_name"] == "resize_image"
