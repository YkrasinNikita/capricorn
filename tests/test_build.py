"""Сборка и первый запуск: версия и установка модели распознавания речи."""
import zipfile

import model_setup
import version


def make_zip(path, name="vosk-model-small-ru-0.22"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{name}/am/final.mdl", b"x")
        z.writestr(f"{name}/conf/model.conf", "")


def no_network(*args, **kwargs):
    raise AssertionError("скачивание не должно запускаться, если есть bundled")


def test_version_is_set():
    assert version.__version__.count(".") >= 1
    assert version.APP_NAME in version.version_line()


def test_model_not_installed_in_empty_folder(tmp_path):
    assert not model_setup.installed(tmp_path)
    assert model_setup.bundled_archive(tmp_path) is None


def test_install_from_bundled_without_network(tmp_path, monkeypatch):
    (tmp_path / "bundled").mkdir()
    make_zip(tmp_path / "bundled" / "model.zip")
    monkeypatch.setattr(model_setup, "download", no_network)
    path = model_setup.install_model(tmp_path, log=lambda *_: None)
    assert model_setup.installed(tmp_path)
    assert path == model_setup.model_dir(tmp_path)


def test_install_downloads_when_bundle_missing(tmp_path, monkeypatch):
    def fake_download(archive, **kwargs):
        make_zip(archive)

    monkeypatch.setattr(model_setup, "download", fake_download)
    model_setup.install_model(tmp_path, log=lambda *_: None)
    assert model_setup.installed(tmp_path)


def test_broken_bundle_falls_back_to_download(tmp_path, monkeypatch):
    (tmp_path / "bundled").mkdir()
    (tmp_path / "bundled" / "model.zip").write_bytes(b"not a zip")

    def fake_download(archive, **kwargs):
        make_zip(archive)

    monkeypatch.setattr(model_setup, "download", fake_download)
    model_setup.install_model(tmp_path, log=lambda *_: None)
    assert model_setup.installed(tmp_path)


def test_archive_without_model_is_rejected(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("readme.txt", "тут нет модели")
    try:
        model_setup.unpack(bad, str(models), log=lambda *_: None)
    except IOError:
        pass
    else:
        raise AssertionError("архив без модели должен быть отвергнут")
    assert not list(models.iterdir())


def test_existing_model_is_not_touched(tmp_path, monkeypatch):
    target = tmp_path / "models" / "vosk-model-small-ru-0.22" / "am"
    target.mkdir(parents=True)
    (target / "final.mdl").write_bytes(b"x")
    monkeypatch.setattr(model_setup, "download", no_network)
    assert model_setup.install_model(tmp_path, log=lambda *_: None) == model_setup.model_dir(tmp_path)
