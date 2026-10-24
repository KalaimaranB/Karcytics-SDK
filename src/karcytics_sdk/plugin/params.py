"""User-settable parameters, declared once and turned into controls by ``ParamsForm``.

An algorithm (a segmenter, a background method, a lane finder…) declares
what it can be told as a list of :class:`Param`. The form builds the
controls from that list, so adding a parameter never means editing UI code.
This module imports no Qt, so analysis code can declare parameters freely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The kinds a ``Param`` can be. ``options`` is a pick-list whose entries
#: arrive at runtime (channel names, image names…) through
#: ``ParamsForm.set_options(param.source, names)``.
KINDS = ("float", "int", "bool", "choice", "options")


@dataclass(frozen=True)
class Param:
    """One parameter: enough to build its control, label it and explain it.

    ``unit`` is shown after the value. A form can map a unit to another
    display (µm instead of px, say) with ``ParamsForm.set_unit_display``;
    the algorithm always receives the value in the unit it declared.
    ``advanced`` parameters sit behind the form's "Advanced settings" toggle.
    ``enabled_by`` names a ``bool`` parameter that switches this one on.
    """

    key: str
    label: str
    kind: str
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    decimals: int = 1
    unit: str = ""
    choices: tuple[tuple[str, Any], ...] = ()
    source: str = ""
    advanced: bool = False
    help: str = ""
    special_value_text: str = ""
    enabled_by: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"Param {self.key!r}: kind must be one of {KINDS}, not {self.kind!r}")
        if self.kind == "options" and not self.source:
            raise ValueError(f"Param {self.key!r}: an 'options' parameter needs a source")
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"Param {self.key!r}: a 'choice' parameter needs choices")


def defaults(params: list[Param]) -> dict[str, Any]:
    """Every parameter's default, keyed like ``ParamsForm.values()``."""
    return {p.key: p.default for p in params}
