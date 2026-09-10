"""The recently-opened list, decided without a window.

``QSettings`` satisfies the store protocol structurally, so all of this runs
against a dictionary — no application name, and nothing written into the
config of whoever runs the suite. The menu that draws it is tested in
``test_gui_widgets.py``; what a list of recent files *means* is here.
"""

from __future__ import annotations

from pathlib import Path

from comictrans.gui.recent import KEY, LIMIT, clear, forget, load, remember


class FakeStore:
    """The two lines of ``QSettings`` this module actually uses."""

    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = dict(values or {})

    def value(self, key: str, defaultValue: object = None) -> object:  # noqa: N803
        return self.values.get(key, defaultValue)

    def setValue(self, key: str, value: object) -> None:  # noqa: N802
        self.values[key] = value


def test_an_untouched_store_has_no_history() -> None:
    assert load(FakeStore()) == ()


def test_the_most_recently_opened_comes_first() -> None:
    store = FakeStore()
    remember(store, Path("/plans/one.yaml"))
    remember(store, Path("/plans/two.yaml"))

    assert load(store) == (Path("/plans/two.yaml"), Path("/plans/one.yaml"))


def test_opening_something_already_listed_moves_it_up_rather_than_repeating_it() -> None:
    """Otherwise the plan being worked through would push everything else out."""
    store = FakeStore()
    for name in ("one", "two", "three"):
        remember(store, Path(f"/plans/{name}.yaml"))

    remember(store, Path("/plans/one.yaml"))

    assert load(store) == (
        Path("/plans/one.yaml"),
        Path("/plans/three.yaml"),
        Path("/plans/two.yaml"),
    )


def test_the_same_file_spelled_two_ways_is_one_entry(tmp_path: Path) -> None:
    """The dialog gives an absolute path; a command line can give anything."""
    store = FakeStore()
    plan = tmp_path / "chapter" / "comic-plan.yaml"

    remember(store, plan)
    remember(store, Path(f"{tmp_path}/chapter/../chapter/comic-plan.yaml"))

    assert load(store) == (plan,)


def test_the_list_stops_at_the_limit() -> None:
    store = FakeStore()
    for index in range(LIMIT + 5):
        remember(store, Path(f"/plans/{index}.yaml"))

    listed = load(store)
    assert len(listed) == LIMIT
    assert listed[0] == Path(f"/plans/{LIMIT + 4}.yaml"), "newest kept"
    assert Path("/plans/0.yaml") not in listed, "oldest dropped"


def test_forgetting_one_leaves_the_rest() -> None:
    store = FakeStore()
    for name in ("one", "two", "three"):
        remember(store, Path(f"/plans/{name}.yaml"))

    forget(store, Path("/plans/two.yaml"))

    assert load(store) == (Path("/plans/three.yaml"), Path("/plans/one.yaml"))


def test_forgetting_something_absent_changes_nothing() -> None:
    store = FakeStore()
    remember(store, Path("/plans/one.yaml"))

    forget(store, Path("/plans/never-opened.yaml"))

    assert load(store) == (Path("/plans/one.yaml"),)


def test_clearing_empties_it_and_leaves_nothing_behind() -> None:
    """Clear Menu is a privacy control, so it has to actually clear."""
    store = FakeStore()
    for name in ("one", "two"):
        remember(store, Path(f"/plans/{name}.yaml"))

    clear(store)

    assert load(store) == ()
    assert store.values[KEY] == [], "the key is emptied, not left holding paths"


def test_a_hand_edited_file_cannot_break_the_menu() -> None:
    """``QSettings`` hands back whatever the file held, which need not be a list.

    A single-element list read back as a bare string is the classic INI
    round-trip, and a plan file list is not worth an exception in the middle
    of opening a menu.
    """
    assert load(FakeStore({KEY: "/plans/only.yaml"})) == (Path("/plans/only.yaml"),)
    assert load(FakeStore({KEY: 17})) == ()
    assert load(FakeStore({KEY: ["", "   ", "/plans/real.yaml"]})) == (Path("/plans/real.yaml"),)
