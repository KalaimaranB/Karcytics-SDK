"""Credit the published work a plugin's methods are built on.

Plugins register `Citation`s once (usually at import), name them from the
methods they implement, and use them in two places:

* In the UI: `CitationLabel` ("Powered by Cellpose-SAM (Pachitariu et al.,
  2025) · Read the paper") next to the feature, `CitationsPanel` for a full
  list.
* In exports: `methods_text()` writes a numbered methods paragraph for the
  steps a user actually ran, and `to_bibtex()` the matching BibTeX, so
  scientists can cite the methods behind their numbers.

Only enter citations checked against the publisher or Crossref, never from
memory: a wrong author list or DOI is worse than none.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

_MAX_AUTHORS_IN_SHORT = 2


@dataclass(frozen=True)
class Citation:
    """One published work.

    Args:
        key: Stable identifier, e.g. ``"cellpose2021"`` (also the BibTeX key).
        authors: ``"Family, G."`` strings in publication order. Use the full
            list where practical; ``methods_text`` and ``short`` abbreviate.
        title: Title as published.
        year: Publication year.
        venue: Journal, proceedings or preprint server.
        volume: Volume (and issue), if any.
        pages: Page range or article number, if any.
        doi: DOI without the ``https://doi.org/`` prefix.
        url: Link when there is no DOI (arXiv, project page).
        kind: BibTeX entry type: ``article``, ``inproceedings`` or ``misc``.
    """

    key: str
    authors: tuple[str, ...]
    title: str
    year: int
    venue: str
    volume: str = ""
    pages: str = ""
    doi: str = ""
    url: str = ""
    kind: str = "article"

    @property
    def link(self) -> str:
        return f"https://doi.org/{self.doi}" if self.doi else self.url

    def _family(self, author: str) -> str:
        return author.split(",")[0].strip()

    def short(self) -> str:
        """``Stringer et al., 2021`` / ``Pachitariu & Stringer, 2022``."""
        families = [self._family(a) for a in self.authors]
        if len(families) > _MAX_AUTHORS_IN_SHORT:
            who = f"{families[0]} et al."
        else:
            who = " & ".join(families) or "Anon."
        return f"{who}, {self.year}"

    def formatted(self) -> str:
        """A full reference line."""
        if len(self.authors) > _MAX_AUTHORS_IN_SHORT + 4:  # long lists: first author et al.
            authors = f"{self.authors[0]} et al."
        else:
            authors = ", ".join(self.authors[:-1]) + (" & " if len(self.authors) > 1 else "") + self.authors[-1]
        parts = [f"{authors} ({self.year}). {self.title}. {self.venue}"]
        if self.volume:
            parts.append(f" {self.volume}")
        if self.pages:
            parts.append(f", {self.pages}")
        parts.append(".")
        if self.link:
            parts.append(f" {self.link}")
        return "".join(parts)

    def to_bibtex(self) -> str:
        fields = {
            "author": " and ".join(self.authors),
            "title": "{" + self.title + "}",
            "year": str(self.year),
            (
                "booktitle" if self.kind == "inproceedings" else "journal" if self.kind == "article" else "howpublished"
            ): (self.venue),
            "volume": self.volume,
            "pages": self.pages.replace("–", "--"),
            "doi": self.doi,
            "url": self.url or (self.link if self.doi else ""),
        }
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
        return f"@{self.kind}{{{_bibtex_key(self.key)},\n{body}\n}}"


def _bibtex_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_:-]", "", key) or "ref"


# ── Registry ────────────────────────────────────────────────────────────────

_registry: dict[str, Citation] = {}


def register(*citations: Citation) -> None:
    """Add citations to this process's registry. Re-registering an identical entry is fine."""
    for c in citations:
        existing = _registry.get(c.key)
        if existing is not None and existing != c:
            raise ValueError(f"citation key {c.key!r} is already registered with different details")
        _registry[c.key] = c


def get(key: str) -> Citation:
    try:
        return _registry[key]
    except KeyError:
        raise KeyError(f"unknown citation {key!r}; register() it first") from None


def resolve(keys: Iterable[str]) -> list[Citation]:
    """Citations for `keys`, de-duplicated, first-mention order."""
    seen: dict[str, Citation] = {}
    for k in keys:
        if k not in seen:
            seen[k] = get(k)
    return list(seen.values())


# ── Methods text ────────────────────────────────────────────────────────────


@dataclass
class MethodStep:
    """One step a user ran, for the methods paragraph.

    ``description`` is a sentence fragment in the past tense ("Cells were
    segmented with Cellpose-SAM"); ``parameters`` are listed after it in
    parentheses; ``citations`` are keys of registered entries.
    """

    description: str
    citations: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)


def _format_parameters(parameters: Mapping[str, Any]) -> str:
    if not parameters:
        return ""
    return " (" + ", ".join(f"{k} = {v}" for k, v in parameters.items()) + ")"


def methods_text(
    software: str, version: str, steps: Iterable[MethodStep], software_citations: tuple[str, ...] = ()
) -> str:
    """A methods paragraph with numbered references, then the reference list.

    Example output::

        Images were analysed with Karcytics CytoMetrics 0.4.0. Cells were
        segmented with Cellpose-SAM (diameter = 30 µm) [1, 2].

    References:
        1. Pachitariu, M., Rariden, M. & Stringer, C. (2025). ...
    """
    steps = list(steps)
    order: list[str] = []

    def numbers(keys: Iterable[str]) -> str:
        nums = []
        for k in keys:
            get(k)  # fail early on unknown keys
            if k not in order:
                order.append(k)
            nums.append(order.index(k) + 1)
        return f" [{', '.join(str(n) for n in nums)}]" if nums else ""

    sentences = [f"Images were analysed with {software} {version}{numbers(software_citations)}."]
    for step in steps:
        sentences.append(f"{step.description}{_format_parameters(step.parameters)}{numbers(step.citations)}.")
    text = " ".join(sentences)
    if order:
        refs = "\n".join(f"{i}. {get(k).formatted()}" for i, k in enumerate(order, start=1))
        text += f"\n\nReferences\n{refs}"
    return text


def to_bibtex(keys: Iterable[str]) -> str:
    return "\n\n".join(c.to_bibtex() for c in resolve(keys)) + "\n"
