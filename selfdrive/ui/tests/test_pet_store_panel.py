"""State/lifecycle regression tests without device messaging or an OpenGL context."""
from concurrent.futures import Future
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def panel_env(monkeypatch):
  class Widget:
    def __init__(self):
      self.enabled = True

    def _child(self, child):
      return child

    def set_rect(self, rect):
      self.rect = rect

    def show_event(self):
      pass

    def hide_event(self):
      pass

  class Button(Widget):
    def __init__(self, text, callback, **kwargs):
      super().__init__()
      self.callback = callback

  class Texture:
    def __init__(self):
      self.closed = False
      self.texture = None

    def close(self):
      self.closed = True
      self.texture = None

    def load(self, *args, **kwargs):
      self.closed = False
      self.texture = SimpleNamespace(height=2288)

    def draw(self, *args, **kwargs):
      pass

  class Params(dict):
    def put(self, key, value):
      self[key] = value

    put_bool = put

  class App:
    def __init__(self):
      self.stack = [Widget()]
      self.ticks = []

    def big_ui(self):
      return False

    def pop_widget(self):
      self.stack.pop().hide_event()

    def widget_in_stack(self, widget):
      return widget in self.stack

    def pop_widgets_to(self, widget, **kwargs):
      while self.stack[-1] is not widget:
        self.pop_widget()

    def add_nav_stack_tick(self, callback):
      self.ticks.append(callback)

    def remove_nav_stack_tick(self, callback):
      self.ticks.remove(callback)

  class Store:
    def remove(self, pet_id):
      pass

    def sprite_path(self, pet_id):
      return Path("/data/pets") / pet_id / "sprite.png"

  app = App()
  state = SimpleNamespace(started=False, params=Params())
  modules = {
    "openpilot.selfdrive.ui.ui_state": {"ui_state": state, "UIStatus": SimpleNamespace(DISENGAGED=0, OVERRIDE=1)},
    "openpilot.system.ui.lib.application": {"gui_app": app, "FontWeight": SimpleNamespace(BOLD=1)},
    "openpilot.system.ui.lib.pet_store": {"PetStore": Store},
    "openpilot.system.ui.lib.pet_texture": {"PetTexture": Texture, "CELL_WIDTH": 192, "CELL_HEIGHT": 208,
                                            "FRAME_COUNTS": (6, 8, 8, 4, 5, 8, 6, 6, 6)},
    "openpilot.system.ui.widgets": {"Widget": Widget, "DialogResult": SimpleNamespace(CONFIRM=1)},
    "openpilot.system.ui.widgets.button": {"Button": Button},
    "openpilot.system.ui.widgets.label": {"gui_label": lambda *args, **kwargs: None},
  }
  for name, attrs in modules.items():
    module = ModuleType(name)
    module.__dict__.update(attrs)
    monkeypatch.setitem(sys.modules, name, module)
  path = Path(__file__).parents[1] / "widgets" / "pet_store.py"
  spec = importlib.util.spec_from_file_location("pet_panel_under_test", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  panel = module.PetStorePanel()
  yield panel, app, state, module
  module._WORKER.shutdown(wait=False, cancel_futures=True)


def test_select_installed_pet_enables_display(panel_env):
  panel, _, state, _ = panel_env
  panel._pets = [{"id": "duck", "displayName": "Duck"}]
  panel._installed_ids = {"duck"}
  panel._use()
  assert state.params == {"SelectedPetId": "duck", "ShowPet": True}


def test_download_requires_explicit_use(panel_env):
  panel, _, state, _ = panel_env
  panel._pets = [{"id": "duck", "displayName": "Duck"}]
  panel._operation = "install"
  panel._future = Future()
  panel._future.set_result({"id": "duck"})
  panel._update_state()
  assert "duck" in panel._installed_ids
  assert "SelectedPetId" not in state.params


def test_start_driving_closes_keyboard_and_panel_releases_resources(panel_env):
  panel, app, state, _ = panel_env
  app.stack.extend([panel, SimpleNamespace(hide_event=lambda: None)])
  app.add_nav_stack_tick(panel._offroad_tick)
  state.started = True
  app.ticks[0]()
  assert len(app.stack) == 1
  assert not app.ticks
  assert panel._closed and panel._preview.closed


def test_remove_active_pet_clears_selection(panel_env, monkeypatch):
  panel, _, state, _ = panel_env
  state.params["SelectedPetId"] = "duck"
  panel._operation = "remove"
  panel._future = Future()
  panel._future.set_result(panel._remove_installed("duck"))
  monkeypatch.setattr(panel, "_load", lambda *args: None)
  panel._update_state()
  assert state.params["SelectedPetId"] == ""


def test_missing_pet_cannot_be_selected(panel_env, monkeypatch):
  panel, _, state, _ = panel_env
  panel._pets = [{"id": "duck"}]
  panel._installed_ids = {"duck"}
  monkeypatch.setattr(panel._store, "sprite_path", lambda pet_id: None)
  panel._use()
  assert "SelectedPetId" not in state.params
  assert "duck" not in panel._installed_ids


def test_failed_catalog_preserves_offline_selection(panel_env):
  panel, _, state, _ = panel_env
  state.params["SelectedPetId"] = "duck"
  panel._future = Future()
  panel._future.set_exception(OSError("offline"))
  panel._update_state()
  assert state.params["SelectedPetId"] == "duck"
  assert panel._pets == []
  assert panel._future is None


def load_display():
  path = Path(__file__).parents[3] / "system" / "ui" / "widgets" / "pet.py"
  spec = importlib.util.spec_from_file_location("pet_display_under_test", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module.PetWidget()


def test_display_without_selection_loads_no_artwork(panel_env, monkeypatch):
  display = load_display()
  monkeypatch.setattr(display._store, "sprite_path", lambda _: pytest.fail("No asset should be looked up"))
  monkeypatch.setattr(display._image, "load", lambda _: pytest.fail("No asset should be loaded"))
  display._render(None)
  assert display._image.texture is None


def test_missing_selected_pet_draws_nothing(panel_env, monkeypatch):
  _, _, state, _ = panel_env
  state.params["SelectedPetId"] = "missing"
  display = load_display()
  monkeypatch.setattr(display._store, "sprite_path", lambda _: None)
  monkeypatch.setattr(display._image, "draw", lambda *args: pytest.fail("Missing pet must not draw"))
  display._render(None)
  assert display._image.texture is None


def test_corrupt_selected_pet_draws_nothing(panel_env, monkeypatch, tmp_path):
  _, _, state, _ = panel_env
  state.params["SelectedPetId"] = "bad"
  path = tmp_path / "sprite.png"
  path.write_bytes(b"corrupt")
  display = load_display()
  monkeypatch.setattr(display._store, "sprite_path", lambda _: path)

  def corrupt(_):
    raise ValueError("Invalid image")

  monkeypatch.setattr(display._image, "load", corrupt)
  monkeypatch.setattr(display._image, "draw", lambda *args: pytest.fail("Corrupt pet must not draw"))
  display._render(None)
  assert display._image.texture is None


def test_empty_installed_store_prompts_browse(panel_env):
  panel, _, _, _ = panel_env
  panel._operation = "load"
  panel._future = Future()
  panel._future.set_result(([], 1, [], {}, ""))
  panel._update_state()
  assert panel._pets == []
  assert "Browse" in panel._status


def test_background_remove_clears_selection_after_panel_closed(panel_env, monkeypatch):
  panel, app, state, _ = panel_env
  state.params["SelectedPetId"] = "duck"
  panel._pets = [{"id": "duck"}]
  panel._installed_ids = {"duck"}
  panel._confirm_remove = True
  entered, release = threading.Event(), threading.Event()

  def remove(_):
    entered.set()
    assert release.wait(2)

  monkeypatch.setattr(panel._store, "remove", remove)
  app.add_nav_stack_tick(panel._offroad_tick)
  panel._remove()
  future = panel._future
  try:
    assert entered.wait(2)
    panel.hide_event()
  finally:
    release.set()
  assert future.result(timeout=2) == "duck"
  assert state.params["SelectedPetId"] == ""


def test_remove_preserves_new_selection(panel_env, monkeypatch):
  panel, _, state, _ = panel_env
  state.params["SelectedPetId"] = "duck"

  def remove(_):
    state.params.put("SelectedPetId", "cat")

  monkeypatch.setattr(panel._store, "remove", remove)
  panel._remove_installed("duck")
  assert state.params["SelectedPetId"] == "cat"


def test_failed_remove_preserves_selection(panel_env, monkeypatch):
  panel, _, state, _ = panel_env
  state.params["SelectedPetId"] = "duck"

  def remove(_):
    raise OSError("Cannot remove pet")

  monkeypatch.setattr(panel._store, "remove", remove)
  with pytest.raises(OSError):
    panel._remove_installed("duck")
  assert state.params["SelectedPetId"] == "duck"


@pytest.fixture
def display_env(panel_env, monkeypatch):
  _, _, state, _ = panel_env
  car = SimpleNamespace(vEgo=0.0, leftBlinker=False, rightBlinker=False)

  class Messages(dict):
    valid = {"carState": True}

  state.sm = Messages(carState=car)
  state.status = 0  # DISENGAGED
  display = load_display()
  display._image.texture = SimpleNamespace(height=2288)
  display._animation_started = 100.0
  monkeypatch.setattr(display, "_refresh_selection", lambda _: None)
  return display, state, car


@pytest.mark.parametrize("status,speed,left,right,height,expected", [
  (0, 0, False, False, 2288, (0, None)),  # idle
  (0, 10, False, False, 2288, (7, None)),  # moving
  (2, 0, False, False, 2288, (3, None)),  # engaged, stationary
  (2, 10, False, False, 2288, (4, None)),  # engaged, moving
  (1, 10, True, False, 2288, (5, None)),  # override wins over turn
  (0, 0, False, True, 2288, (9, 4)),  # v2 right look pose
  (0, 0, True, False, 2288, (10, 4)),  # v2 left look pose
  (0, 10, True, False, 2288, (2, None)),  # moving uses animation
  (0, 0, False, True, 1872, (1, None)),  # v1 has no look rows
  (0, 0, True, False, 1872, (2, None)),
])
def test_vehicle_state_selects_animation(display_env, status, speed, left, right, height, expected):
  display, state, car = display_env
  state.status = status
  car.vEgo, car.leftBlinker, car.rightBlinker = speed, left, right
  display._image.texture.height = height
  assert display._desired_animation(100)[0] == expected


def test_turn_pose_holds_through_blinker_gap(display_env):
  display, _, car = display_env
  car.rightBlinker = True
  assert display._desired_animation(100)[0] == (9, 4)
  car.rightBlinker = False
  assert display._desired_animation(100.7)[0] == (9, 4)
  assert display._desired_animation(100.81)[0] == (0, None)


def test_state_hold_prevents_flicker_but_override_is_immediate(display_env, monkeypatch):
  display, state, car = display_env
  clock = SimpleNamespace(now=100.2)
  monkeypatch.setitem(display._render.__globals__, "time", SimpleNamespace(monotonic=lambda: clock.now))
  state.status, car.vEgo = 2, 10
  display._render(None)
  assert display._animation == (0, None)
  clock.now = 100.36
  display._render(None)
  assert display._animation == (4, None)
  clock.now, state.status = 100.4, 1
  display._render(None)
  assert display._animation == (5, None)
  clock.now, state.status, car.vEgo = 100.5, 0, 0
  display._render(None)
  assert display._animation == (5, None)
  clock.now = 100.76
  display._render(None)
  assert display._animation == (0, None)


def test_invalid_car_state_uses_idle_and_speed_rate_is_bounded(display_env):
  display, state, car = display_env
  car.vEgo = 100
  assert display._desired_animation(100)[1] == 12
  car.vEgo = 0
  assert display._desired_animation(100)[1] == 6
  state.sm.valid["carState"] = False
  state.status = 1
  assert display._desired_animation(100) == ((0, None), 6)
