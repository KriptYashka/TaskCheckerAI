from __future__ import annotations

from core.ingest.grouping import group_submissions, student_key


def test_single_notebook_is_own_student():
    assert student_key("ivanov.ipynb") == "ivanov"


def test_archive_extension_is_stripped():
    assert student_key("ivanov.zip") == "ivanov"
    assert student_key("task.tar.gz") == "task"


def test_double_underscore_groups_files():
    assert student_key("ivanov__main.py") == "ivanov"
    assert student_key("ivanov__utils.py") == "ivanov"


def test_directory_prefix_is_student():
    assert student_key("ivanov/main.py") == "ivanov"


def test_backslash_paths_normalized():
    assert student_key("ivanov\\main.py") == "ivanov"


def test_group_submissions_splits_by_student():
    items = [
        ("ivanov.ipynb", b"{}"),
        ("petrov.ipynb", b"{}"),
        ("sidorov__main.py", b"print(1)"),
        ("sidorov__utils.py", b"x = 1"),
    ]
    groups = group_submissions(items)
    assert set(groups) == {"ivanov", "petrov", "sidorov"}
    assert len(groups["sidorov"]) == 2
    assert len(groups["ivanov"]) == 1
