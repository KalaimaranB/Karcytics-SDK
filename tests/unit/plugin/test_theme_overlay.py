"""The "Changing theme…" cover an isolated plugin window shows while it restyles."""

from PySide6.QtWidgets import QWidget

from karcytics_sdk.plugin.theme_overlay import TEXT, ThemeChangeOverlay, covered_while


class _ShownWindow(QWidget):
    """Reports itself visible without being shown (SDK tests never show widgets)."""

    def isVisible(self) -> bool:  # noqa: N802 - Qt override
        return True


def _overlays(window: QWidget) -> list[ThemeChangeOverlay]:
    return [o for o in window.findChildren(ThemeChangeOverlay) if not o.isHidden()]


def test_the_window_is_covered_while_it_restyles():
    window = _ShownWindow()
    window.resize(800, 600)
    during: list[list[ThemeChangeOverlay]] = []

    covered_while(window, lambda: during.append(_overlays(window)))

    assert len(during[0]) == 1
    assert during[0][0].text == TEXT
    assert during[0][0].geometry() == window.rect()
    assert not _overlays(window)  # gone once the restyle is done


def test_the_cover_goes_even_if_the_restyle_fails():
    window = _ShownWindow()

    def fail() -> None:
        raise RuntimeError("boom")

    try:
        covered_while(window, fail)
    except RuntimeError:
        pass
    assert not _overlays(window)


def test_a_restyle_inside_a_restyle_isnt_covered_twice():
    window = _ShownWindow()
    counts: list[int] = []
    covered_while(window, lambda: covered_while(window, lambda: counts.append(len(_overlays(window)))))
    assert counts == [1]


def test_a_hidden_window_just_restyles():
    window = QWidget()
    ran: list[bool] = []
    covered_while(window, lambda: ran.append(not window.findChildren(ThemeChangeOverlay)))
    assert ran == [True]
