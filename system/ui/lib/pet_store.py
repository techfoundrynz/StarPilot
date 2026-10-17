"""Bounded, offline-capable Codex pet packages. Call network methods from a worker."""

import hashlib
import io
import json
import re
import shutil
import stat
import tempfile
import threading
import time
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from PIL import Image
from urllib3.exceptions import HTTPError

ORIGIN = "https://codex-pets.net"
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_SPRITE_BYTES = 16 * 1024 * 1024
MAX_INSTALLED_BYTES = 256 * 1024 * 1024
MAX_INSTALLED = 32
MAX_MANIFEST_BYTES = 16 * 1024
DIMENSIONS = {1: (1536, 1872), 2: (1536, 2288)}
ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class PetStoreError(ValueError):
  pass


def _pet_id(value: object) -> str:
  if not isinstance(value, str) or len(value) > 80 or not ID_PATTERN.fullmatch(value):
    raise PetStoreError("Invalid pet ID")
  return value


def _text(value: object, limit: int) -> str:
  if not isinstance(value, str):
    return ""
  return " ".join("".join(c for c in value[:limit * 4] if not unicodedata.category(c).startswith("C")).split())[:limit]


def _url(value: object) -> str:
  if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value) or "\\" in value:
    raise PetStoreError("Invalid pet URL")
  result = urljoin(ORIGIN + "/", value)
  parts = urlsplit(result)
  if parts.scheme != "https" or parts.netloc != "codex-pets.net" or parts.fragment:
    raise PetStoreError("Pet URL must use codex-pets.net HTTPS")
  return result


def _normalize(pet: object) -> dict:
  if not isinstance(pet, dict):
    raise PetStoreError("Invalid pet metadata")
  pet_id = _pet_id(pet.get("id"))
  version = pet.get("spriteVersionNumber", 1)
  if type(version) is not int or version not in DIMENSIONS:
    raise PetStoreError("Unsupported pet sprite version")
  return {
    "id": pet_id,
    "displayName": _text(pet.get("displayName"), 80) or pet_id,
    "description": _text(pet.get("description"), 512),
    "ownerName": _text(pet.get("ownerName"), 80) or "Anonymous",
    "spriteVersionNumber": version,
    "posterUrl": _url(pet["posterUrl"]) if pet.get("posterUrl") else "",
    "downloadUrl": _url(pet.get("downloadUrl") or f"/api/pets/{pet_id}/download"),
    "sourceUrl": f"{ORIGIN}/share/{pet_id}",
  }


def _image_png(data: bytes, dimensions: tuple[int, int], source_format: str) -> bytes:
  try:
    with Image.open(io.BytesIO(data)) as image:
      if image.format != source_format or image.size != dimensions or getattr(image, "n_frames", 1) != 1:
        raise PetStoreError("Invalid pet image format or dimensions")
      image.load()
      output = io.BytesIO()
      image.convert("RGBA").save(output, format="PNG")
      result = output.getvalue()
      if len(result) > MAX_SPRITE_BYTES:
        raise PetStoreError("Pet image is too large")
      return result
  except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
    raise PetStoreError("Cannot decode pet image") from e


class PetStore:
  def __init__(self, root: Path = Path("/data/pets")):
    self.root = Path(root).absolute()
    self._lock = threading.RLock()

  def _download(self, url: str, limit: int, timeout_seconds: float = 30) -> bytes:
    url = _url(url)
    deadline = time.monotonic() + timeout_seconds
    try:
      with requests.get(url, stream=True, timeout=(5, 5), allow_redirects=False, headers={"Accept-Encoding": "identity"}) as response:
        if response.status_code != 200:
          raise PetStoreError(f"Pet server returned HTTP {response.status_code}")
        length = response.headers.get("Content-Length")
        if length and (not length.isdecimal() or int(length) > limit):
          raise PetStoreError("Pet download is too large")
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
          raise PetStoreError("Unsupported pet response encoding")
        chunks = bytearray()
        while True:
          # read1 returns available socket data, letting the deadline stop slow trickles.
          chunk = response.raw.read1(16384)
          if time.monotonic() > deadline:
            raise PetStoreError("Pet download timed out")
          if not chunk:
            break
          if len(chunks) + len(chunk) > limit:
            raise PetStoreError("Pet download is too large")
          chunks.extend(chunk)
        return bytes(chunks)
    except (requests.RequestException, HTTPError, OSError) as e:
      raise PetStoreError("Unable to contact pet store") from e

  def catalog(self, query: str = "", page: int = 1, page_size: int = 6) -> dict:
    page = max(1, min(int(page), 10000))
    page_size = max(1, min(int(page_size), 12))
    params = urlencode({"q": _text(query, 100), "page": page, "pageSize": page_size, "sort": "popular", "content": "safe"})
    try:
      payload = json.loads(self._download(f"{ORIGIN}/api/pets?{params}", 256 * 1024))
      if not isinstance(payload, dict) or not isinstance(payload.get("pets"), list):
        raise PetStoreError("Invalid pet catalog")
      pets = []
      for item in payload["pets"][:page_size]:
        try:
          pets.append(_normalize(item))
        except PetStoreError:
          continue
      return {"pets": pets, "page": page, "totalPages": max(1, min(int(payload.get("totalPages", 1)), 10000))}
    except (ValueError, TypeError, RecursionError) as e:
      raise PetStoreError("Invalid pet catalog response") from e

  def poster(self, pet: dict) -> bytes:
    metadata = _normalize(pet)
    if not metadata["posterUrl"]:
      raise PetStoreError("Pet has no poster")
    return _image_png(self._download(metadata["posterUrl"], 1024 * 1024, timeout_seconds=8), (192, 208), "WEBP")

  def _directory(self, pet_id: str) -> Path:
    directory = self.root / _pet_id(pet_id)
    if self.root.is_symlink() or directory.is_symlink() or directory.resolve().parent != self.root.resolve():
      raise PetStoreError("Unsafe pet directory")
    return directory

  def _read_installed(self, pet_id: str) -> dict:
    directory = self._directory(pet_id)
    metadata_path = directory / "metadata.json"
    sprite = directory / "sprite.png"
    if (metadata_path.is_symlink() or sprite.is_symlink() or not metadata_path.is_file() or not sprite.is_file()
        or metadata_path.stat().st_size > MAX_MANIFEST_BYTES
        or not 0 < sprite.stat().st_size <= MAX_SPRITE_BYTES):
      raise PetStoreError("Invalid installed pet files")
    metadata = _normalize(json.loads(metadata_path.read_text(encoding="utf-8")))
    if metadata["id"] != pet_id:
      raise PetStoreError("Installed pet ID mismatch")
    return metadata

  def installed(self) -> list[dict]:
    with self._lock:
      if not self.root.is_dir() or self.root.is_symlink():
        return []
      pets = []
      for directory in self.root.iterdir():
        try:
          pets.append(self._read_installed(directory.name))
        except (ValueError, OSError, TypeError, RecursionError):
          continue
      return sorted(pets, key=lambda pet: pet["displayName"].casefold())

  def sprite_path(self, pet_id: str) -> Path | None:
    with self._lock:
      try:
        self._read_installed(pet_id)
        return self._directory(pet_id) / "sprite.png"
      except (ValueError, OSError, TypeError, RecursionError):
        return None

  @staticmethod
  def _unpack(data: bytes, pet: dict) -> bytes:
    if len(data) > MAX_PACKAGE_BYTES:
      raise PetStoreError("Pet package is too large")
    try:
      with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) != 2 or {entry.filename for entry in entries} != {"pet.json", "spritesheet.webp"}:
          raise PetStoreError("Pet package must contain only pet.json and spritesheet.webp")
        for entry in entries:
          mode = entry.external_attr >> 16
          limit = MAX_MANIFEST_BYTES if entry.filename == "pet.json" else MAX_SPRITE_BYTES
          if (entry.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
              or entry.flag_bits & 1 or entry.file_size > limit or entry.file_size < 1
              or entry.file_size > max(entry.compress_size, 1) * 200):
            raise PetStoreError("Unsafe pet package entry")
        # ZipFile.read verifies each entry's CRC before returning bytes.
        manifest = json.loads(archive.read("pet.json"))
        if not isinstance(manifest, dict) or manifest.get("id") != pet["id"] or manifest.get("spritesheetPath") != "spritesheet.webp":
          raise PetStoreError("Pet manifest does not match package")
        version = manifest.get("spriteVersionNumber", 1)
        if type(version) is not int or version not in DIMENSIONS or version != pet["spriteVersionNumber"]:
          raise PetStoreError("Pet manifest version mismatch")
        return _image_png(archive.read("spritesheet.webp"), DIMENSIONS[version], "WEBP")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError, json.JSONDecodeError) as e:
      raise PetStoreError("Invalid pet package") from e

  def install(self, pet: dict) -> dict:
    metadata = _normalize(pet)
    with self._lock:
      if self._directory(metadata["id"]).exists():
        return self._read_installed(metadata["id"])
    # Network/decode work must not block local selection or offline listing.
    data = self._download(metadata["downloadUrl"], MAX_PACKAGE_BYTES)
    sprite = self._unpack(data, metadata)
    with self._lock:
      destination = self._directory(metadata["id"])
      if destination.exists():
        return self._read_installed(metadata["id"])
      self.root.mkdir(parents=True, exist_ok=True)
      directories = list(self.root.iterdir())
      if len(directories) >= MAX_INSTALLED:
        raise PetStoreError("Pet storage is full; remove a pet first")
      used = sum(p.stat().st_size for d in directories if d.is_dir() and not d.is_symlink()
                 for p in d.iterdir() if p.is_file() and not p.is_symlink())
      if used + len(sprite) + MAX_MANIFEST_BYTES > MAX_INSTALLED_BYTES:
        raise PetStoreError("Pet storage limit reached")
      if shutil.disk_usage(self.root).free < len(sprite) + 32 * 1024 * 1024:
        raise PetStoreError("Not enough disk space for pet")
      metadata["packageSha256"] = hashlib.sha256(data).hexdigest()
      metadata["spriteSha256"] = hashlib.sha256(sprite).hexdigest()
      temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=self.root))
      try:
        (temporary / "sprite.png").write_bytes(sprite)
        (temporary / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        temporary.rename(destination)
      finally:
        if temporary.exists():
          shutil.rmtree(temporary)
      return metadata

  def remove(self, pet_id: str) -> None:
    with self._lock:
      directory = self._directory(pet_id)
      if not directory.exists():
        return
      entries = list(directory.iterdir())
      if any(p.name not in {"metadata.json", "sprite.png"} or p.is_symlink() or not p.is_file() for p in entries):
        raise PetStoreError("Unsafe installed pet files")
      for entry in entries:
        entry.unlink()
      directory.rmdir()
