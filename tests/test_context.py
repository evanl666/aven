"""Tests for standing instruction files."""

from pathlib import Path

from aven.harness.context import find, read


def test_files_are_ordered_outermost_first(tmp_path, monkeypatch):
    """The most specific instruction should be the last thing the model reads."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    deep = tmp_path / "工作" / "报销"
    deep.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("外层")
    (tmp_path / "工作" / "AVEN.md").write_text("中层")
    (deep / "AVEN.md").write_text("内层")

    assert [p.read_text() for p in find(deep)] == ["外层", "中层", "内层"]


def test_the_global_file_comes_first_of_all(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".aven").mkdir()
    (tmp_path / ".aven" / "AVEN.md").write_text("全局")
    (tmp_path / "AVEN.md").write_text("家目录")

    assert [p.read_text() for p in find(tmp_path)] == ["全局", "家目录"]


def test_the_walk_stops_at_home(tmp_path, monkeypatch):
    """Above home are other people's folders; a stray AGENTS.md there was not
    written for this person."""
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    (tmp_path / "AGENTS.md").write_text("不该被读到")
    (home / "AVEN.md").write_text("该读到")

    assert [p.read_text() for p in find(home / "work")] == ["该读到"]


def test_one_file_per_folder_with_aven_winning(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "AVEN.md").write_text("aven 的")
    (tmp_path / "AGENTS.md").write_text("别人的")

    assert [p.read_text() for p in find(tmp_path)] == ["aven 的"]


def test_each_block_says_which_file_it_came_from(tmp_path, monkeypatch):
    """When two files disagree the model needs to see which is more specific,
    and the person needs to know which file to edit."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "AVEN.md").write_text("- 邮件写短一点")

    rendered = read(find(tmp_path))

    assert "AVEN.md" in rendered
    assert "- 邮件写短一点" in rendered
    assert rendered.startswith("<instructions from=")


def test_a_long_file_is_truncated_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "AVEN.md").write_text("x" * 50_000)

    rendered = read(find(tmp_path))

    assert len(rendered) < 25_000
    assert "truncated, 50000 chars total" in rendered


def test_empty_and_missing_files_contribute_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "AVEN.md").write_text("   \n\n  ")

    assert read(find(tmp_path)) == ""
