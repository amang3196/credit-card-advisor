"""Offline tests for the v2 chunker (no PDFs, no network)."""

from app import chunking_v2 as cv

CARDS = {"ornge": "Swiggy Ornge HDFC Bank Credit Card", "blck": "Swiggy BLCK HDFC Bank Credit Card"}


def _pages():
    p1 = """Classification - Internal

# Terms and Conditions - Swiggy Ornge HDFC Bank Credit Card

# **_2.3_** **_<u>5% cashback on Online spends</u>_**

- 5% Cashback only on the brands in Table 2.1

## Table 2.1

|**Merchant/Category**|**MCC**|
|---|---|
|Apparels|5137,5139|
|Pharmacies|5122,5912|
"""
    p2 = """# <u>Classification - Internal</u>

|Amazon, Flipkart,|All online transactions|
|---|---|
|Nykaa, ClearTrip,||

# 2.4 1% cashback on Other spends

- Exclusion for 1% cashback category - Fuel, Rent
"""
    p3 = """Classification - Internal

# Terms and Conditions - Swiggy BLCK HDFC Bank Credit Card

# 2.2 10% cashback on Swiggy App

- Earning 10% Cashback on Swiggy
"""
    return [(1, p1), (2, p2), (3, p3)]


def test_boilerplate_dropped_and_table_continues_across_pages():
    blocks = cv.to_blocks(_pages())
    assert not any("classification" in b.text.lower() for b in blocks)
    tables = [b for b in blocks if b.kind == "table"]
    assert len(tables) == 1  # page-2 continuation glued onto Table 2.1
    t = tables[0]
    assert "Apparels" in t.text and "Nykaa" in t.text
    assert (t.page, t.page_end) == (1, 2)
    assert "|---|---|\n|Amazon" not in t.text  # fake separator of the continuation removed


def test_caption_is_not_a_heading():
    blocks = cv.to_blocks(_pages())
    assert not any(b.kind == "heading" and b.text.startswith("Table") for b in blocks)


def test_sections_are_scoped_to_the_card_named_in_the_title():
    chunks = cv.chunk_blocks(cv.to_blocks(_pages()), CARDS)
    ornge = [c for c in chunks if "Nykaa" in c.body][0]
    blck = [c for c in chunks if "10% Cashback" in c.body][0]
    assert ornge.card_ids == ["ornge"]
    assert blck.card_ids == ["blck"]
    assert not any("ornge" in c.card_ids and "10% Cashback" in c.body for c in chunks)
    assert "Section:" in cv.context_line(blck, CARDS) and "BLCK" in cv.context_line(blck, CARDS)


def test_big_table_split_between_rows_with_header_repeated():
    rows = "\n".join(f"|Card {i}|{i}99|{i}99 (waived on spends of Rs.{i} Lakh)|" for i in range(120))
    table = "|Credit Card Name|Annual Fee|Renewal Fee|\n|---|---|---|\n" + rows
    chunks = cv.chunk_blocks(cv.to_blocks([(1, "# A. Annual Fees\n\n" + table)]), {"x": "X Card"})
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.body) <= cv.TABLE_MAX_CHARS + 50
        assert "|Credit Card Name|Annual Fee|Renewal Fee|" in c.body
        for line in c.body.splitlines():
            if line.startswith("|Card"):
                assert line.endswith("Lakh)|")  # no row cut in half


def test_merged_cell_is_written_into_every_row_it_covers():
    # Swiggy Ornge Table 1.1: one "Capping" cell spans rows 1-4 (shared cap); "Exclusions" rows 2-4.
    lines = [
        "|Type|Cashback|Capping|Exclusions|",
        "|---|---|---|---|",
        "|Swiggy|5%|Rs.1500 per cycle|Wallet|",
        "|Online merchants|5%||Refer Table 2.3|",
        "|Cleartrip|5%|||",
        "|Nykaa|5%|||",
        "|Other|1%|Rs.1000 per cycle|Fuel|",
    ]
    groups = [[(1, 2), (2, 2), (3, 2), (4, 2)], [(2, 3), (3, 3), (4, 3)]]
    out = cv.fill_table(lines, groups)
    assert out[1] == "|---|---|---|---|"
    for k in (2, 3, 4, 5):
        assert "Rs.1500 per cycle [one merged cell across 4 rows]" in out[k]
    assert out[3].endswith("|Refer Table 2.3 [one merged cell across 3 rows]|")
    assert out[6] == lines[6]  # untouched row
    assert out[2].endswith("|Wallet|")  # the Swiggy row's own exclusion is not part of a group


def test_merged_cell_text_found_in_a_covered_row():
    # The MCC table prints "Fuel" in the middle of its merged cell, i.e. in a covered row.
    lines = ["|Category|MCC|", "|---|---|", "||5172|", "|Fuel|5541|", "|Rent|6513|"]
    out = cv.fill_table(lines, [[(1, 0), (2, 0)]])
    assert out[2].startswith("|Fuel [one merged cell across 2 rows]|5172")
    assert out[3].startswith("|Fuel [one merged cell across 2 rows]|5541")
    assert out[4] == "|Rent|6513|"
