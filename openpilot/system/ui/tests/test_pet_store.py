import io
import json
import stat
import warnings
import zipfile

import pytest
from PIL import Image

from openpilot.system.ui.lib.pet_store import DIMENSIONS, ORIGIN, PetStore, PetStoreError, _url

PET = {"id": "test-pet", "displayName": "Test Pet", "ownerName": "Artist", "spriteVersionNumber": 1}


def image_bytes(size=(1536, 1872), image_format="WEBP"):
  result = io.BytesIO()
  Image.new("RGBA", size, (255, 0, 0, 128)).save(result, image_format, lossless=True)
  return result.getvalue()


@pytest.fixture(scope="module")
def sprite():
  return image_bytes()


def package(sprite, manifest=None, entries=None):
  manifest = manifest if manifest is not None else {"id": PET["id"], "spritesheetPath": "spritesheet.webp"}
  result = io.BytesIO()
  with zipfile.ZipFile(result, "w") as archive:
    archive.writestr("pet.json", json.dumps(manifest))
    archive.writestr("spritesheet.webp", sprite)
    for name, data in entries or []:
      archive.writestr(name, data)
  return result.getvalue()


def test_install_offline_selection_remove(tmp_path, monkeypatch, sprite):
  store = PetStore(tmp_path / "pets")
  monkeypatch.setattr(store, "_download", lambda *args: package(sprite))
  result = store.install(PET)
  assert result["ownerName"] == "Artist"
  assert len(result["packageSha256"]) == 64
  with Image.open(store.sprite_path(PET["id"])) as image:
    assert image.format == "PNG"
    assert image.size == DIMENSIONS[1]
  offline = PetStore(store.root)
  monkeypatch.setattr(offline, "_download", lambda *args: pytest.fail("offline access performed network I/O"))
  assert offline.installed()[0]["id"] == PET["id"]
  assert offline.install(PET)["id"] == PET["id"]
  assert offline.sprite_path("../../elsewhere") is None
  offline.remove(PET["id"])
  assert offline.installed() == []
  assert offline.sprite_path(PET["id"]) is None


@pytest.mark.parametrize("extra", ["../escape", "/escape", "folder/pet.json", "spritesheet.webp"])
def test_reject_extra_traversal_duplicate(sprite, extra):
  with warnings.catch_warnings():
    warnings.simplefilter("ignore", UserWarning)
    archive = package(sprite, entries=[(extra, b"x")])
  with pytest.raises(PetStoreError):
    PetStore._unpack(archive, PET)


@pytest.mark.parametrize("manifest", [
  {"id": "other", "spritesheetPath": "spritesheet.webp"},
  {"id": "test-pet", "spritesheetPath": "../spritesheet.webp"},
  {"id": "test-pet", "spritesheetPath": "spritesheet.webp", "spriteVersionNumber": 3},
  {"id": "test-pet", "spritesheetPath": "spritesheet.webp", "spriteVersionNumber": True},
  {"id": "test-pet", "spritesheetPath": "spritesheet.webp", "spriteVersionNumber": 2},
])
def test_reject_manifest(sprite, manifest):
  with pytest.raises(PetStoreError):
    PetStore._unpack(package(sprite, manifest), PET)


def test_reject_dimensions_and_format():
  for data in [image_bytes((10, 10)), image_bytes(image_format="PNG")]:
    with pytest.raises(PetStoreError):
      PetStore._unpack(package(data), PET)


def test_v2():
  pet = {**PET, "spriteVersionNumber": 2}
  manifest = {"id": pet["id"], "spritesheetPath": "spritesheet.webp", "spriteVersionNumber": 2}
  result = PetStore._unpack(package(image_bytes(DIMENSIONS[2]), manifest), pet)
  with Image.open(io.BytesIO(result)) as image:
    assert image.size == DIMENSIONS[2]


def test_reject_symlink(sprite):
  output = io.BytesIO()
  with zipfile.ZipFile(output, "w") as archive:
    archive.writestr("pet.json", json.dumps({"id": "test-pet", "spritesheetPath": "spritesheet.webp"}))
    entry = zipfile.ZipInfo("spritesheet.webp")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive.writestr(entry, sprite)
  with pytest.raises(PetStoreError):
    PetStore._unpack(output.getvalue(), PET)


def test_crc_failure(sprite):
  data = bytearray(package(sprite))
  offset = data.index(sprite)
  data[offset + len(sprite) // 2] ^= 255
  with pytest.raises(PetStoreError, match="Invalid pet package"):
    PetStore._unpack(bytes(data), PET)


def test_failed_install_is_atomic(tmp_path, monkeypatch):
  store = PetStore(tmp_path / "pets")
  monkeypatch.setattr(store, "_download", lambda *args: b"invalid zip")
  with pytest.raises(PetStoreError):
    store.install(PET)
  assert not store.root.exists()


def test_catalog_sanitizes_caps_and_safe_mode(tmp_path, monkeypatch):
  store = PetStore(tmp_path)
  urls = []

  def download(url, limit):
    urls.append(url)
    return json.dumps({"pets": [{**PET, "displayName": "Hello\u202e\x00", "description": "x" * 1000}] * 20,
                       "totalPages": 2}).encode()

  monkeypatch.setattr(store, "_download", download)
  result = store.catalog("cats & dogs", page_size=100)
  assert len(result["pets"]) == 12
  assert result["pets"][0]["displayName"] == "Hello"
  assert len(result["pets"][0]["description"]) == 512
  assert "content=safe" in urls[0]
  assert "cats+%26+dogs" in urls[0]


@pytest.mark.parametrize("url", ["http://codex-pets.net/a", "https://evil.test/a", "//evil.test/a",
                                  "https://codex-pets.net@evil.test/a", "https://codex-pets.net:443/a"])
def test_reject_foreign_origins(url):
  with pytest.raises(PetStoreError):
    _url(url)
  assert _url("/api/pets/test-pet/download").startswith(ORIGIN)


def test_remove_does_not_follow_unexpected_content(tmp_path):
  directory = tmp_path / PET["id"]
  directory.mkdir()
  unexpected = directory / "do-not-remove.txt"
  unexpected.write_text("keep")
  with pytest.raises(PetStoreError):
    PetStore(tmp_path).remove(PET["id"])
  assert unexpected.read_text() == "keep"


def test_poster_png(tmp_path, monkeypatch):
  store = PetStore(tmp_path)
  monkeypatch.setattr(store, "_download", lambda *args, **kwargs: image_bytes((192, 208)))
  result = store.poster({**PET, "posterUrl": f"{ORIGIN}/poster.webp"})
  with Image.open(io.BytesIO(result)) as image:
    assert image.format == "PNG"
    assert image.size == (192, 208)


class Response:
  def __init__(self, body=b"ok", status=200, headers=None):
    self.status_code = status
    self.headers = headers or {}
    self.raw = io.BytesIO(body)

  def __enter__(self):
    return self

  def __exit__(self, *args):
    self.raw.close()


@pytest.mark.parametrize("response,limit", [
  (Response(status=302, headers={"Location": "https://evil.test"}), 10),
  (Response(body=b"too large"), 3),
  (Response(headers={"Content-Length": "100"}), 10),
  (Response(headers={"Content-Encoding": "gzip"}), 10),
])
def test_download_limits_and_redirects(tmp_path, monkeypatch, response, limit):
  from openpilot.system.ui.lib import pet_store

  def get(url, **kwargs):
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (5, 5)
    return response

  monkeypatch.setattr(pet_store.requests, "get", get)
  with pytest.raises(PetStoreError):
    PetStore(tmp_path)._download(ORIGIN + "/a", limit)


def test_download_total_deadline(tmp_path, monkeypatch):
  from openpilot.system.ui.lib import pet_store
  times = iter([0, 31])
  monkeypatch.setattr(pet_store.time, "monotonic", lambda: next(times))
  monkeypatch.setattr(pet_store.requests, "get", lambda *args, **kwargs: Response())
  with pytest.raises(PetStoreError, match="timed out"):
    PetStore(tmp_path)._download(ORIGIN + "/a", 100)


def test_zip_ratio_rejected(sprite):
  output = io.BytesIO()
  with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("pet.json", b" " * 16000)
    archive.writestr("spritesheet.webp", sprite)
  with pytest.raises(PetStoreError, match="Unsafe"):
    PetStore._unpack(output.getvalue(), PET)
