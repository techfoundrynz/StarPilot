"""Real native uploads catch pixel-buffer ownership and binding API regressions."""
import io
import os

import pytest
from PIL import Image

from openpilot.starpilot.ui.raylib import rl
from openpilot.system.ui.lib.pet_texture import PetTexture


@pytest.fixture
def context():
  if not os.getenv("DISPLAY"):
    pytest.skip("Native pet uploads require a display / Xvfb")
  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
  rl.init_window(320, 240, "Pet texture regression")
  try:
    yield
  finally:
    rl.close_window()


@pytest.mark.parametrize("size,atlas", [((192, 208), False), ((1536, 1872), True), ((1536, 2288), True)])
@pytest.mark.parametrize("from_file", [False, True])
def test_native_pet_upload_draw_and_release(context, tmp_path, size, atlas, from_file):
  encoded = io.BytesIO()
  Image.new("RGBA", size, (20, 160, 240, 128)).save(encoded, format="PNG")
  source = encoded.getvalue()
  if from_file:
    source = tmp_path / "pet.png"
    source.write_bytes(encoded.getvalue())
  texture = PetTexture()
  texture.load(source, atlas=atlas)
  assert (texture.texture.width, texture.texture.height) == size
  assert texture.texture.id
  rl.begin_drawing()
  texture.draw(rl.Rectangle(0, 0, 192, 208), atlas=atlas)
  rl.end_drawing()
  texture.close()
  assert texture.texture is None


def test_real_pet_panel_and_search_keyboard_render(tmp_path, monkeypatch):
  if not os.getenv("DISPLAY"):
    pytest.skip("Native pet panel requires a display / Xvfb")
  from openpilot.selfdrive.ui.ui_state import ui_state
  from openpilot.selfdrive.ui.widgets.pet_store import PetStorePanel
  from openpilot.system.ui.lib.application import gui_app
  from openpilot.system.ui.lib.pet_store import PetStore
  from openpilot.system.ui.widgets.keyboard import Keyboard
  from openpilot.selfdrive.ui.mici.widgets.dialog import BigInputDialog
  monkeypatch.setattr(ui_state, "started", False)
  gui_app.init_window("Pet store native rendering regression")
  panel = PetStorePanel()
  panel._store = PetStore(tmp_path / "pets")
  gui_app.push_widget(panel)
  try:
    for index, _ in enumerate(gui_app.render()):
      if index >= 12:
        break
    assert "Browse" in panel._status
    panel._search()
    assert isinstance(gui_app.get_active_widget(), Keyboard if gui_app.big_ui() else BigInputDialog)
    for index, _ in enumerate(gui_app.render()):
      if index >= 3:
        break
    gui_app.pop_widget()
  finally:
    panel.hide_event()
    gui_app.close()
