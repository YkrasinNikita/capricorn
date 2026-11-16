"""Заметки: хранение, поиск по словоформам, дедупликация и устойчивость к битому файлу."""
from notes import Notes


def test_notes_survive_restart(tmp_path):
    path = tmp_path / "notes.json"
    first = Notes(path)
    first.add("купить молоко")
    second = Notes(path)
    found, total = second.search("молоко")
    assert total == 1
    assert found[0]["text"] == "купить молоко"


def test_duplicate_updates_instead_of_adding(tmp_path):
    notes = Notes(tmp_path / "notes.json")
    _, duplicate = notes.add("купить молоко")
    assert duplicate is False
    _, duplicate = notes.add("купить молоко!")
    assert duplicate is True
    assert notes.count() == 1


def test_search_finds_word_forms(tmp_path):
    notes = Notes(tmp_path / "notes.json")
    notes.add("завтра встреча с Аней")
    found, total = notes.search("встречу")
    assert total == 1
    assert "встреча" in found[0]["text"]


def test_search_limit_and_total(tmp_path):
    notes = Notes(tmp_path / "notes.json")
    notes.add("купить хлеб")
    notes.add("купить молоко")
    notes.add("позвонить маме")
    found, total = notes.search("купить", limit=1)
    assert total == 2
    assert len(found) == 1


def test_delete_by_text_and_id(tmp_path):
    notes = Notes(tmp_path / "notes.json")
    note, _ = notes.add("позвонить маме")
    notes.add("купить хлеб")
    assert [x["id"] for x in notes.delete("позвонить")] == [note["id"]]
    assert notes.count() == 1
    assert notes.delete("ничего такого") == []


def test_delete_strips_about_prefix(tmp_path):
    notes = Notes(tmp_path / "notes.json")
    notes.add("встреча с Аней")
    assert len(notes.delete("про Аней")) == 1
    assert notes.count() == 0


def test_storage_limit_drops_oldest(tmp_path):
    notes = Notes(tmp_path / "notes.json", max_notes=2)
    notes.add("первая")
    notes.add("вторая")
    notes.add("третья")
    assert notes.count() == 2


def test_tags_are_extracted(tmp_path):
    notes = Notes(tmp_path / "notes.json")
    note, _ = notes.add("встреча с Аней #работа")
    assert note["tags"] == ["работа"]
    assert note["text"] == "встреча с Аней"


def test_empty_and_broken_file_do_not_crash(tmp_path):
    path = tmp_path / "notes.json"
    assert Notes(path).count() == 0
    path.write_text('{"notes": [', encoding="utf-8")
    notes = Notes(path)
    assert notes.count() == 0
    assert path.with_name(path.name + ".bak").exists()
