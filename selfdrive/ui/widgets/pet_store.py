"""Native, offroad pet browsing for both Mici and the large display."""
from concurrent.futures import ThreadPoolExecutor
import time
import threading

import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.pet_store import PetStore
from openpilot.system.ui.lib.pet_texture import PetTexture
from openpilot.system.ui.widgets import Widget, DialogResult
from openpilot.system.ui.widgets.button import Button
from openpilot.system.ui.widgets.label import gui_label

# A single worker bounds concurrency even if a panel is closed while downloading.
_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pet-store")
_SELECTION_LOCK = threading.Lock()


def open_pet_store():
  if not ui_state.started:
    gui_app.push_widget(PetStorePanel())


class PetStorePanel(Widget):
  def __init__(self):
    super().__init__()
    self._store = PetStore()
    self._mode = "installed"
    self._query = ""
    self._page = 1
    self._pages = 1
    self._index = 0
    self._pets = []
    self._installed_ids = set()
    self._posters = {}
    self._preview = PetTexture()
    self._preview_is_atlas = True
    self._future = None
    self._operation = ""
    self._status = ""
    self._closed = False
    self._confirm_remove = False
    self._font = 22 if not gui_app.big_ui() else 48
    self._buttons = {
      "back": Button("Back", gui_app.pop_widget, font_size=self._font, text_padding=5),
      "browse": Button("Browse", lambda: self._load("browse"), font_size=self._font, text_padding=5),
      "installed": Button("Installed", lambda: self._load("installed"), font_size=self._font, text_padding=5),
      "search": Button("Search", self._search, font_size=self._font, text_padding=5),
      "prev": Button("<", lambda: self._move(-1), font_size=self._font, text_padding=5),
      "next": Button(">", lambda: self._move(1), font_size=self._font, text_padding=5),
      "use": Button(lambda: "Use" if self._is_installed() else "Download", self._use, font_size=self._font, text_padding=5),
      "remove": Button(lambda: "Confirm" if self._confirm_remove else "Remove", self._remove, font_size=self._font, text_padding=5),
    }
    for button in self._buttons.values():
      self._child(button)

  @property
  def _pet(self):
    return self._pets[self._index] if self._pets else None

  def _is_installed(self):
    return self._pet is not None and self._pet["id"] in self._installed_ids

  def show_event(self):
    super().show_event()
    self._closed = False
    gui_app.add_nav_stack_tick(self._offroad_tick)
    self._load("installed")

  def hide_event(self):
    self._closed = True
    gui_app.remove_nav_stack_tick(self._offroad_tick)
    if self._future is not None:
      self._future.cancel()
    self._preview.close()
    super().hide_event()

  def _submit(self, operation, callback):
    if self._future is not None or ui_state.started or self._closed:
      return
    self._status = {"load": "Loading pets...", "install": "Downloading pet...", "remove": "Removing pet..."}[operation]
    self._operation = operation
    self._future = _WORKER.submit(callback)

  def _load(self, mode, page=1):
    if self._future is not None or ui_state.started:
      return
    self._mode, self._page = mode, page
    query = self._query

    def fetch():
      installed = self._store.installed()
      if mode == "installed":
        return installed, 1, installed, {}, ""
      catalog = self._store.catalog(query=query, page=page, page_size=6)
      posters = {}
      for pet in catalog["pets"]:
        if self._closed:
          break
        try:
          posters[pet["id"]] = self._store.poster(pet)
        except (OSError, ValueError):
          pass  # Text and download controls remain available without thumbnails.
      return catalog["pets"], catalog["totalPages"], installed, posters, ""

    self._submit("load", fetch)

  def _show_preview(self):
    self._preview.close()
    self._confirm_remove = False
    pet = self._pet
    if pet is None:
      return
    self._preview_is_atlas = True
    try:
      if (path := self._store.sprite_path(pet["id"])) is not None:
        self._preview.load(path)
      elif pet["id"] in self._posters:
        self._preview.load(self._posters[pet["id"]], atlas=False)
        self._preview_is_atlas = False
    except (OSError, ValueError):
      self._status = "Preview unavailable."

  def _move(self, direction):
    if self._future is not None:
      return
    next_index = self._index + direction
    if 0 <= next_index < len(self._pets):
      self._index = next_index
      self._status = ""
      self._show_preview()
    elif self._mode == "browse" and 1 <= self._page + direction <= self._pages:
      self._load("browse", self._page + direction)

  def _search(self):
    def search(query):
      self._query = query.strip()[:100]
      self._load("browse")

    if gui_app.big_ui():
      from openpilot.system.ui.widgets.keyboard import Keyboard

      def complete(result):
        if result == DialogResult.CONFIRM:
          search(keyboard.text)

      keyboard = Keyboard(max_text_size=100, callback=complete)
      keyboard.set_title("Search pets")
      keyboard.set_text(self._query)
      gui_app.push_widget(keyboard)
    else:
      from openpilot.selfdrive.ui.mici.widgets.dialog import BigInputDialog
      gui_app.push_widget(BigInputDialog("search pets", self._query, minimum_length=0, confirm_callback=search))

  def _use(self):
    if self._pet is None or self._future is not None or ui_state.started:
      return
    pet = self._pet.copy()
    if self._is_installed():
      with _SELECTION_LOCK:
        if self._store.sprite_path(pet["id"]) is None:
          self._status = "Pet missing. Download it again."
          self._installed_ids.discard(pet["id"])
          return
        ui_state.params.put("SelectedPetId", pet["id"])
        ui_state.params.put_bool("ShowPet", True)
      self._status = "Selected for the driving display."
    else:
      self._submit("install", lambda: self._store.install(pet))

  def _remove(self):
    if not self._is_installed() or not self._pet["id"] or self._future is not None or ui_state.started:
      return
    if not self._confirm_remove:
      self._confirm_remove = True
      self._status = "Tap Confirm to remove this pet."
      return
    pet_id = self._pet["id"]
    self._preview.close()
    self._submit("remove", lambda: self._remove_installed(pet_id))

  def _remove_installed(self, pet_id):
    # Complete persistence even if the panel was closed while the worker ran.
    # Synchronize local deletion with Use, including its installed-file check.
    with _SELECTION_LOCK:
      self._store.remove(pet_id)
      if ui_state.params.get("SelectedPetId") == pet_id:
        ui_state.params.put("SelectedPetId", "")
    return pet_id

  def _offroad_tick(self):
    # Navigation ticks run even while the search keyboard covers this panel.
    if ui_state.started and gui_app.widget_in_stack(self):
      gui_app.pop_widgets_to(self, instant=True)
      gui_app.pop_widget()

  def _update_state(self):
    if ui_state.started:
      return
    if self._future is None or not self._future.done() or self._closed:
      return
    future, self._future = self._future, None
    try:
      result = future.result()
      if self._operation == "load":
        self._pets, self._pages, installed, self._posters, self._status = result
        self._installed_ids = {p["id"] for p in installed}
        self._index = 0
        if not self._pets:
          self._status = "No installed pets. Tap Browse to find a companion." if self._mode == "installed" else "No matching pets. Try another search."
      elif self._operation == "install":
        self._installed_ids.add(result["id"])
        self._status = "Downloaded. Tap Use to select."
      else:
        self._installed_ids.discard(result)
        self._load(self._mode, self._page)
      self._show_preview()
    except Exception:
      self._status = "Could not complete request. Installed pets work offline."

  def _render(self, rect):
    if self._closed or ui_state.started:
      return
    rl.draw_rectangle_rec(rect, rl.Color(15, 18, 24, 255))
    scale = min(rect.width / 536, rect.height / 240)
    pad, gap, bar = 8 * scale, 5 * scale, 37 * scale
    width = (rect.width - 2 * pad - 3 * gap) / 4
    busy = self._future is not None
    for i, name in enumerate(("back", "browse", "installed", "search")):
      button = self._buttons[name]
      button.set_enabled(self.enabled and (name == "back" or not busy))
      button.render(rl.Rectangle(rect.x + pad + i * (width + gap), rect.y + pad, width, bar))
    bottom = rect.y + rect.height - pad - bar
    buttons = (("prev", .12), ("next", .12), ("use", .40), ("remove", .36))
    x = rect.x + pad
    for name, fraction in buttons:
      button = self._buttons[name]
      available = not busy and self._pet is not None
      if name == "remove":
        available = available and self._is_installed() and bool(self._pet["id"])
      if name == "prev":
        available = available and (self._index > 0 or self._page > 1)
      if name == "next":
        available = available and (self._index + 1 < len(self._pets) or self._page < self._pages)
      button.set_enabled(self.enabled and available)
      w = (rect.width - 2 * pad - 3 * gap) * fraction
      button.render(rl.Rectangle(x, bottom, w, bar))
      x += w + gap
    status_rect = rl.Rectangle(rect.x + pad, bottom - 24 * scale, rect.width - pad * 2, 22 * scale)
    status = self._status or f"{self._mode.title()} | {self._index + 1}/{len(self._pets)} | Page {self._page}/{max(1, self._pages)}"
    gui_label(status_rect, status, int(15 * scale), color=rl.LIGHTGRAY)
    pet = self._pet
    if pet is None:
      return
    top = rect.y + pad + bar + 8 * scale
    image_h = max(1, status_rect.y - top - 3 * scale)
    image_w = image_h * 192 / 208
    self._preview.draw(rl.Rectangle(rect.x + pad, top, image_w, image_h), int(time.monotonic() * 6) % 6, atlas=self._preview_is_atlas)
    text_x = rect.x + pad * 2 + image_w
    text_w = rect.x + rect.width - pad - text_x
    gui_label(rl.Rectangle(text_x, top, text_w, 31 * scale), pet["displayName"], int(25 * scale), font_weight=FontWeight.BOLD)
    gui_label(rl.Rectangle(text_x, top + 32 * scale, text_w, 22 * scale), pet.get("ownerName", ""), int(16 * scale), color=rl.LIGHTGRAY)
    gui_label(rl.Rectangle(text_x, top + 57 * scale, text_w, 23 * scale), pet.get("description", ""), int(16 * scale))
    selected = (ui_state.params.get("SelectedPetId") or "") == pet["id"]
    label = "Selected" if selected else "Installed" if self._is_installed() else "codex-pets.net"
    gui_label(rl.Rectangle(text_x, top + 84 * scale, text_w, 20 * scale), label, int(15 * scale), color=rl.SKYBLUE)
