"""Citations: formatting, registry, methods text, BibTeX and widgets."""

from __future__ import annotations

import pytest

from karcytics_sdk.plugin import citations
from karcytics_sdk.plugin.citation_widgets import CitationLabel, CitationsPanel
from karcytics_sdk.plugin.citations import Citation, MethodStep, methods_text, to_bibtex

CELLPOSE = Citation(
    key="test_cellpose2021",
    authors=("Stringer, C.", "Wang, T.", "Michaelos, M.", "Pachitariu, M."),
    title="Cellpose: a generalist algorithm for cellular segmentation",
    year=2021,
    venue="Nature Methods",
    volume="18",
    pages="100–106",
    doi="10.1038/s41592-020-01018-x",
)
CP2 = Citation(
    key="test_cellpose2022",
    authors=("Pachitariu, M.", "Stringer, C."),
    title="Cellpose 2.0: how to train your own model",
    year=2022,
    venue="Nature Methods",
    volume="19",
    pages="1634–1641",
    doi="10.1038/s41592-022-01663-4",
)
PREPRINT = Citation(
    key="test_trackastra",
    authors=("Gallusser, B.", "Weigert, M."),
    title="Trackastra: Transformer-based cell tracking for live-cell microscopy",
    year=2024,
    venue="ECCV",
    url="https://arxiv.org/abs/2405.15700",
    kind="inproceedings",
)


@pytest.fixture(autouse=True)
def registered():
    citations.register(CELLPOSE, CP2, PREPRINT)


class TestCitation:
    def test_short_forms(self):
        assert CELLPOSE.short() == "Stringer et al., 2021"
        assert CP2.short() == "Pachitariu & Stringer, 2022"

    def test_formatted_reference(self):
        assert CELLPOSE.formatted() == (
            "Stringer, C., Wang, T., Michaelos, M. & Pachitariu, M. (2021). Cellpose: a generalist "
            "algorithm for cellular segmentation. Nature Methods 18, 100–106. "
            "https://doi.org/10.1038/s41592-020-01018-x"
        )

    def test_link_prefers_doi_then_url(self):
        assert CELLPOSE.link == "https://doi.org/10.1038/s41592-020-01018-x"
        assert PREPRINT.link == "https://arxiv.org/abs/2405.15700"

    def test_bibtex(self):
        bib = CELLPOSE.to_bibtex()
        assert bib.startswith("@article{test_cellpose2021,")
        assert "author = {Stringer, C. and Wang, T. and Michaelos, M. and Pachitariu, M.}" in bib
        assert "pages = {100--106}" in bib
        assert "journal = {Nature Methods}" in bib
        assert "booktitle = {ECCV}" in PREPRINT.to_bibtex()


class TestRegistry:
    def test_conflicting_reregistration_is_rejected(self):
        with pytest.raises(ValueError):
            citations.register(Citation(key=CELLPOSE.key, authors=("X, Y.",), title="t", year=1, venue="v"))

    def test_unknown_key(self):
        with pytest.raises(KeyError, match="register"):
            citations.get("nope")

    def test_resolve_deduplicates_in_first_mention_order(self):
        assert [c.key for c in citations.resolve([CP2.key, CELLPOSE.key, CP2.key])] == [CP2.key, CELLPOSE.key]


class TestMethodsText:
    def test_numbers_references_in_order_of_first_use(self):
        text = methods_text(
            "Karcytics CytoMetrics",
            "0.4.0",
            [
                MethodStep("Cells were segmented with Cellpose-SAM", (CELLPOSE.key,), {"diameter": "30 µm"}),
                MethodStep("The model was fine-tuned on 12 corrected images", (CP2.key, CELLPOSE.key)),
            ],
        )

        paragraph, refs = text.split("\n\nReferences\n")
        assert paragraph == (
            "Images were analysed with Karcytics CytoMetrics 0.4.0. "
            "Cells were segmented with Cellpose-SAM (diameter = 30 µm) [1]. "
            "The model was fine-tuned on 12 corrected images [2, 1]."
        )
        assert refs.splitlines()[0].startswith("1. Stringer, C.")
        assert refs.splitlines()[1].startswith("2. Pachitariu, M. & Stringer, C. (2022)")

    def test_without_citations_there_is_no_reference_list(self):
        assert methods_text("X", "1", [MethodStep("Thresholded")]) == "Images were analysed with X 1. Thresholded."

    def test_unknown_key_fails_loudly(self):
        with pytest.raises(KeyError):
            methods_text("X", "1", [MethodStep("Did a thing", ("missing",))])

    def test_bibtex_file(self):
        bib = to_bibtex([CELLPOSE.key, CP2.key])
        assert bib.count("@article{") == 2


class TestWidgets:
    def test_label_names_the_method_and_links_its_papers(self, qtbot):
        label = CitationLabel("Cellpose", [CELLPOSE.key, CP2.key])
        qtbot.addWidget(label)

        text = label.text()
        assert "Powered by <b>Cellpose</b> (Stringer et al., 2021)" in text
        assert "https://doi.org/10.1038/s41592-020-01018-x" in text
        assert "Pachitariu &amp; Stringer, 2022" in text
        assert label.openExternalLinks()

    def test_panel_lists_full_references(self, qtbot):
        panel = CitationsPanel([CELLPOSE.key, PREPRINT.key])
        qtbot.addWidget(panel)

        html = panel._label.text()
        assert html.count("<li") == 2
        assert "https://arxiv.org/abs/2405.15700" in html
