"""Unit tests for agent/services/qbo_api/dedup.py — TMS-008 dedup helper."""

from pathlib import Path
import sys

# Add agent/ to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.qbo_api.dedup import dedupe_attachments


def _att(att_id, filename, size, doc_type="pod"):
    return {
        "id": att_id,
        "fileName": filename,
        "size": size,
        "contentType": "application/pdf",
        "tempDownloadUri": None,
        "docType": doc_type,
    }


class TestDedupeAttachments:
    def test_screenshot_pattern_5x_same_pod_keeps_highest_id(self):
        """The exact bug from the screenshot: 5x identical filename+size, distinct IDs."""
        atts = [
            _att("1001", "mm2603020032_ite_1775833088165.pdf", 13_312),
            _att("1002", "mm2603020032_ite_1775833088165.pdf", 13_312),
            _att("1003", "mm2603020032_ite_1775833088165.pdf", 13_312),
            _att("1004", "mm2603020032_ite_1775833088165.pdf", 13_312),
            _att("1005", "mm2603020032_ite_1775833088165.pdf", 13_312),
        ]
        kept, skipped = dedupe_attachments(atts)
        assert len(kept) == 1
        assert kept[0]["id"] == "1005"
        assert len(skipped) == 4
        assert {a["id"] for a in skipped} == {"1001", "1002", "1003", "1004"}

    def test_same_filename_different_size_both_kept(self):
        """Real revision (different size) is not a duplicate — both kept."""
        atts = [
            _att("100", "pod.pdf", 5_000),
            _att("101", "pod.pdf", 7_500),
        ]
        kept, skipped = dedupe_attachments(atts)
        assert len(kept) == 2
        assert skipped == []

    def test_empty_list(self):
        kept, skipped = dedupe_attachments([])
        assert kept == []
        assert skipped == []

    def test_filename_casing_and_whitespace_normalized(self):
        """Match key is (filename.lower().strip(), size) — casing and whitespace ignored."""
        atts = [
            _att("200", "POD.pdf", 1234),
            _att("201", "pod.pdf", 1234),
            _att("202", "  pod.pdf  ", 1234),
        ]
        kept, skipped = dedupe_attachments(atts)
        assert len(kept) == 1
        assert kept[0]["id"] == "202"
        assert len(skipped) == 2

    def test_id_compared_as_int_not_string(self):
        """QBO IDs are strings of digits; '100' < '99' lexicographically but 100 > 99 as int."""
        atts = [
            _att("99",  "pod.pdf", 1000),
            _att("100", "pod.pdf", 1000),
            _att("9",   "pod.pdf", 1000),
        ]
        kept, skipped = dedupe_attachments(atts)
        assert len(kept) == 1
        assert kept[0]["id"] == "100"
        assert {a["id"] for a in skipped} == {"99", "9"}

    def test_stable_order_preserved_for_kept(self):
        """When no duplicates, output order matches input order."""
        atts = [
            _att("1", "a.pdf", 100),
            _att("2", "b.pdf", 200),
            _att("3", "c.pdf", 300),
        ]
        kept, skipped = dedupe_attachments(atts)
        assert [a["id"] for a in kept] == ["1", "2", "3"]
        assert skipped == []

    def test_kept_order_preserves_first_appearance_position(self):
        """For mixed dupes + uniques, kept retains the position of the first occurrence
        of each match key (even if a later occurrence wins the ID tie-breaker)."""
        atts = [
            _att("10", "a.pdf", 100),       # group A — first appearance
            _att("20", "b.pdf", 200),
            _att("30", "a.pdf", 100),       # group A — wins tie-breaker (highest id), same position as id=10
            _att("40", "c.pdf", 300),
        ]
        kept, skipped = dedupe_attachments(atts)
        # 'a.pdf' winner is id=30 but slots into position 0 (where id=10 was)
        assert [a["fileName"] for a in kept] == ["a.pdf", "b.pdf", "c.pdf"]
        assert kept[0]["id"] == "30"
        assert len(skipped) == 1
        assert skipped[0]["id"] == "10"
