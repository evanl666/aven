"""Tests for skills."""

from pathlib import Path

import pytest

from aven.core import skills
from aven.tools import skill_tools

SKILL = """---
name: 报销整理
description: 把发票归档并生成清单。用户提到发票、报销、月度汇总时用这个。
---

# 报销整理

1. 先 list_dir 看 Downloads
2. 按 references/规则.md 归档
"""


def write_skill(home: Path, folder: str, text: str = SKILL) -> Path:
    where = home / ".aven" / "skills" / folder
    where.mkdir(parents=True, exist_ok=True)
    (where / "SKILL.md").write_text(text, encoding="utf-8")
    return where


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "work").mkdir()
    return tmp_path / "work"


# --- the frontmatter ---------------------------------------------------------


def test_name_and_description_come_from_the_frontmatter():
    assert skills.parse(SKILL, "别的名字")[0] == "报销整理"
    assert "月度汇总" in skills.parse(SKILL, "x")[1]


def test_a_file_with_no_frontmatter_falls_back_to_its_folder_name():
    name, description = skills.parse("# 就是一段说明\n", "写周报")

    assert name == "写周报"
    assert description == skills.NO_DESCRIPTION


def test_a_skill_with_no_description_is_listed_rather_than_dropped(box):
    """Routing will be poor, and that is visible. Dropping it silently is not."""
    write_skill(box, "无说明", "---\nname: 无说明\n---\n\n内容\n")

    found = skills.find(box)

    assert [s.name for s in found] == ["无说明"]
    assert skills.NO_DESCRIPTION in skills.catalogue(found)


# --- discovery ---------------------------------------------------------------


def test_the_persons_own_skills_and_this_folders_are_both_found(box):
    write_skill(Path.home(), "全局技能", SKILL.replace("报销整理", "全局技能"))
    write_skill(box, "项目技能", SKILL.replace("报销整理", "项目技能"))

    assert sorted(s.name for s in skills.find(box)) == ["全局技能", "项目技能"]


def test_the_declared_name_wins_over_the_folder_name(box):
    """Two folders declaring the same name are the same skill, whatever they
    are called on disk - which is how a project overrides a global one."""
    write_skill(box, "随便叫什么", SKILL)

    assert [s.name for s in skills.find(box)] == ["报销整理"]


def test_a_project_skill_replaces_a_global_one_of_the_same_name(box):
    write_skill(Path.home(), "报销整理", SKILL.replace("把发票归档", "全局版本"))
    write_skill(box, "报销整理", SKILL.replace("把发票归档", "项目版本"))

    found = skills.find(box)

    assert len(found) == 1
    assert "项目版本" in found[0].description


def test_a_folder_without_a_skill_file_is_not_a_skill(box):
    (box / ".aven" / "skills" / "随便一个目录").mkdir(parents=True)

    assert skills.find(box) == []


# --- two-stage loading -------------------------------------------------------


def test_the_catalogue_costs_a_line_and_the_body_is_not_in_it(box):
    write_skill(box, "报销整理")
    found = skills.find(box)

    listed = skills.catalogue(found)

    assert "报销整理" in listed and "月度汇总" in listed
    assert "list_dir" not in listed, "the instructions themselves stay out"
    assert len(listed) < len(skills.read(found[0]))


def test_nothing_is_added_when_there_are_no_skills(box):
    assert skills.catalogue([]) == ""
    assert skill_tools([]) == [], "and no tool the model could waste a turn on"


def test_loading_returns_the_instructions_and_names_what_is_bundled(box):
    home = write_skill(box, "报销整理")
    (home / "references").mkdir()
    (home / "references" / "规则.md").write_text("文件名格式:发票_商户_金额元.pdf")
    load = skill_tools(skills.find(box))[0]

    text = load(name="报销整理").output

    assert "list_dir 看 Downloads" in text
    assert "references/规则.md" in text, "so the model knows it can ask for it"


def test_a_bundled_file_can_be_asked_for_by_name(box):
    home = write_skill(box, "报销整理")
    (home / "references").mkdir()
    (home / "references" / "规则.md").write_text("文件名格式:发票_商户_金额元.pdf")
    load = skill_tools(skills.find(box))[0]

    assert "发票_商户_金额元" in load(name="报销整理", file="references/规则.md").output


def test_a_skill_cannot_read_outside_itself(box):
    """Skills may live outside --root, so their own folder is the boundary."""
    write_skill(box, "报销整理")
    (box / "秘密.txt").write_text("不该被读到")

    with pytest.raises(skills.Outside):
        skills.read(skills.find(box)[0], "../../../秘密.txt")


def test_asking_for_a_skill_that_is_not_there_says_what_is(box):
    write_skill(box, "报销整理")
    load = skill_tools(skills.find(box))[0]

    output = load(name="不存在的技能").output

    assert "no skill called" in output
    assert "报销整理" in output, "and lists what it could have meant"


def test_loading_is_read_only_so_it_never_needs_approval(box):
    write_skill(box, "报销整理")
    assert skill_tools(skills.find(box))[0].risk == "read"
