from types import SimpleNamespace

import pytest
from PIL import Image

from desktop import DesktopController


@pytest.fixture
def desktop_view(tmp_path):
    """Real capture/observation logic over a harmless physical desktop boundary."""
    frames = []
    window = SimpleNamespace(
        title="Chrome profiles", left=0, top=0, width=800, height=600
    )
    backend = SimpleNamespace(
        size=lambda: (800, 600),
        screenshot=lambda: Image.new("RGB", (800, 600), "#333333"),
        getActiveWindow=lambda: window,
    )
    context = SimpleNamespace(
        session=SimpleNamespace(
            _activity=SimpleNamespace(
                _started=True, _rt_session=object(), push_video=frames.append
            )
        )
    )
    return (
        DesktopController(mouse=backend, capture_dir=tmp_path),
        backend,
        context,
        frames,
    )
