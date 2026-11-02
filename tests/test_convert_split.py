"""Tests for the format-neutral chapter splitter."""
from agent_wiki.convert.model import TocEntry
from agent_wiki.convert.split import (
    PlannedChapter,
    format_ordinal,
    normalise_label,
    plan_chapters,
)


def _e(title, level, target=""):
    return TocEntry(title=title, level=level, target=target or f"ch.xhtml#{title}")


def _no_text(parent, child):
    return False


def _text(parent, child):
    return True


# --- chapter level -----------------------------------------------------------

def test_two_parts_of_four_give_eight_chapters_at_level_two():
    toc = []
    for p in (1, 2):
        toc.append(_e(f"Part {p}", 1))
        for c in range(1, 5):
            toc.append(_e(f"Ch {p}.{c}", 2))
    plan = plan_chapters(toc, [])
    assert [c.title for c in plan] == [
        "Ch 1.1", "Ch 1.2", "Ch 1.3", "Ch 1.4",
        "Ch 2.1", "Ch 2.2", "Ch 2.3", "Ch 2.4",
    ]
    assert [c.ordinal for c in plan] == list(range(1, 9))
    assert all(c.start.level == 2 for c in plan)


def test_five_top_level_entries_give_five_chapters():
    toc = [_e(f"Ch {i}", 1) for i in range(1, 6)]
    plan = plan_chapters(toc, [])
    assert len(plan) == 5
    assert [c.start for c in plan] == toc
    assert all(isinstance(c, PlannedChapter) for c in plan)


def test_level_three_entries_merge_into_their_chapter():
    toc = [
        _e("A", 1), _e("A.1", 2), _e("A.1.a", 3), _e("A.1.b", 3), _e("A.2", 2),
        _e("B", 1), _e("B.1", 2), _e("C", 1),
    ]
    plan = plan_chapters(toc, [])
    # level 1 has 3 entries, so it is the chapter level
    assert [c.title for c in plan] == ["A", "B", "C"]
    assert [e.title for e in plan[0].merged] == ["A.1", "A.1.a", "A.1.b", "A.2"]
    assert [e.title for e in plan[1].merged] == ["B.1"]
    assert plan[2].merged == ()


def test_level_three_merge_under_level_two_chapters():
    toc = [
        _e("Part", 1), _e("One", 2), _e("Deep", 3), _e("Two", 2), _e("Three", 2),
    ]
    plan = plan_chapters(toc, [])
    assert [c.title for c in plan] == ["One", "Two", "Three"]
    assert [e.title for e in plan[0].merged] == ["Deep"]


# --- lone-child title join ---------------------------------------------------

def test_lone_child_with_no_text_between_joins_title():
    toc = []
    for i, name in enumerate(["My Parents and Early Life", "School", "Work"], 1):
        toc.append(_e(f"CHAPTER: {i}", 1, f"ch.xhtml#c{i}"))
        toc.append(_e(name, 2, f"ch.xhtml#s{i}"))
    plan = plan_chapters(toc, [], has_text_between=_no_text)
    assert [c.title for c in plan] == [
        "Chapter 1: My Parents and Early Life",
        "Chapter 2: School",
        "Chapter 3: Work",
    ]
    assert plan[0].start == toc[0]
    assert plan[0].merged == (toc[1],)


def test_no_join_when_text_sits_between():
    toc = []
    for i in range(1, 4):
        toc.append(_e(f"CHAPTER: {i}", 1))
        toc.append(_e(f"Child {i}", 2))
    plan = plan_chapters(toc, [], has_text_between=_text)
    assert [c.title for c in plan] == ["CHAPTER: 1", "CHAPTER: 2", "CHAPTER: 3"]
    assert len(plan) == 3


def test_no_join_without_a_predicate():
    toc = []
    for i in range(1, 4):
        toc.append(_e(f"Chapter {i}", 1))
        toc.append(_e(f"Child {i}", 2))
    assert [c.title for c in plan_chapters(toc, [])] == [
        "Chapter 1", "Chapter 2", "Chapter 3"]


def test_no_join_with_two_children():
    toc = [
        _e("A", 1), _e("A.1", 2), _e("A.2", 2), _e("B", 1), _e("C", 1),
    ]
    plan = plan_chapters(toc, [], has_text_between=_no_text)
    assert plan[0].title == "A"


def test_join_predicate_gets_parent_and_child():
    seen = []
    toc = [_e("A", 1), _e("A.1", 2), _e("B", 1), _e("C", 1)]

    def pred(parent, child):
        seen.append((parent.title, child.title))
        return False

    plan_chapters(toc, [], has_text_between=pred)
    assert seen == [("A", "A.1")]


def test_roman_numeral_label_joins():
    toc = []
    for i, roman in enumerate(["I", "II", "III"], 1):
        toc.append(_e(f"PART {roman}.", 1))
        toc.append(_e(f"Name {i}", 2))
    plan = plan_chapters(toc, [], has_text_between=_no_text)
    assert [c.title for c in plan] == [
        "Part I: Name 1", "Part II: Name 2", "Part III: Name 3"]


# --- label normalisation -----------------------------------------------------

def test_normalise_label_forms():
    assert normalise_label("CHAPTER: 1") == "Chapter 1"
    assert normalise_label("PART IV.") == "Part IV"
    assert normalise_label("Book ii") == "Book II"
    assert normalise_label("Section 3") == "Section 3"
    assert normalise_label("chapter xlii") == "Chapter XLII"
    assert normalise_label("  section - 12 ") == "Section 12"


def test_normalise_label_leaves_other_titles_alone():
    assert normalise_label("chapter one") == "chapter one"
    assert normalise_label("My Parents") == "My Parents"
    assert normalise_label("Part iiv") == "Part iiv"
    assert normalise_label("Chapter 1 begins") == "Chapter 1 begins"


# --- spine fallback ----------------------------------------------------------

def test_no_toc_falls_back_to_one_chapter_per_spine_item():
    spine = ["a.xhtml", "b.xhtml", "c.xhtml"]
    headings = {"a.xhtml": "Opening", "b.xhtml": "Middle"}
    plan = plan_chapters([], spine, first_heading=headings.get)
    assert [c.title for c in plan] == ["Opening", "Middle", "Part 03"]
    assert [c.start.target for c in plan] == spine
    assert [c.ordinal for c in plan] == [1, 2, 3]


def test_fallback_without_heading_callable_uses_part_titles():
    plan = plan_chapters([], ["a", "b"])
    assert [c.title for c in plan] == ["Part 01", "Part 02"]


def test_low_toc_coverage_falls_back_to_spine():
    spine = ["a.xhtml", "b.xhtml", "c.xhtml", "d.xhtml", "e.xhtml"]
    toc = [_e("X", 1, "a.xhtml#x"), _e("Y", 1, "a.xhtml#y"),
           _e("Z", 1, "b.xhtml")]
    plan = plan_chapters(toc, spine)
    assert len(plan) == 5
    assert plan[0].title == "Part 01"


def test_half_coverage_keeps_the_toc():
    spine = ["a.xhtml", "b.xhtml", "c.xhtml", "d.xhtml"]
    toc = [_e("X", 1, "a.xhtml#x"), _e("Y", 1, "b.xhtml"),
           _e("Z", 1, "b.xhtml#z")]
    assert [c.title for c in plan_chapters(toc, spine)] == ["X", "Y", "Z"]


# --- ordinals ----------------------------------------------------------------

def test_fifty_three_chapters_pad_to_two_digits():
    assert [format_ordinal(i, 53) for i in (1, 9, 53)] == ["01", "09", "53"]


def test_one_hundred_twenty_chapters_pad_to_three_digits():
    assert [format_ordinal(i, 120) for i in (1, 53, 120)] == ["001", "053", "120"]


def test_small_totals_still_use_two_digits():
    assert format_ordinal(1, 1) == "01"
    assert format_ordinal(3, 5) == "03"


def test_part_title_padding_follows_the_total():
    spine = [f"s{i}.xhtml" for i in range(120)]
    plan = plan_chapters([], spine)
    assert plan[0].title == "Part 001"
    assert plan[-1].title == "Part 120"
    assert plan[-1].ordinal == 120
