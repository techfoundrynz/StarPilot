"""Selected companion animation; no built-in pet or replacement artwork."""
import time

import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.pet_store import PetStore
from openpilot.system.ui.lib.pet_texture import PetTexture, FRAME_COUNTS, CELL_WIDTH, CELL_HEIGHT
from openpilot.system.ui.widgets import Widget


class PetWidget(Widget):
  def __init__(self, scale=1.0):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, CELL_WIDTH * scale, CELL_HEIGHT * scale))
    self._image = PetTexture()
    self._store = PetStore()
    self._selection = None
    self._next_check = 0.0
    self._animation = (0, None)
    self._animation_started = time.monotonic()
    self._last_direction = 0
    self._direction_until = 0.0

  def hide_event(self):
    self._image.close()
    self._selection = None
    self._next_check = 0.0
    super().hide_event()

  def _refresh_selection(self, now):
    if now < self._next_check:
      return
    self._next_check = now + 2
    selected = ui_state.params.get("SelectedPetId") or ""
    try:
      path = self._store.sprite_path(selected) if selected else None
      key = (selected, path.stat().st_mtime_ns if path is not None else None)
      if key == self._selection:
        return
      self._selection = key
      self._image.close()
      if path is not None:
        self._image.load(path)
      self._animation = (0, None)
      self._animation_started = now
    except (OSError, ValueError):
      # A removed or damaged selection is simply absent; never substitute artwork.
      self._image.close()

  def _desired_animation(self, now):
    if not ui_state.sm.valid["carState"]:
      return (0, None), 6
    car = ui_state.sm["carState"]
    moving = car.vEgo >= 0.1
    direction = -1 if car.leftBlinker else 1 if car.rightBlinker else 0
    if direction:
      self._last_direction = direction
      self._direction_until = now + 0.8
    elif now < self._direction_until:
      direction = self._last_direction
    fps = min(12, max(6, int(abs(car.vEgo) * 0.5)))
    if ui_state.status == UIStatus.OVERRIDE:
      return (5, None), fps
    if direction:
      if not moving and self._image.texture.height == 2288:
        # V2's clockwise look cells: right=4, left=12. These are still poses.
        return (9 if direction > 0 else 10, 4), fps
      return (1 if direction > 0 else 2, None), fps
    if ui_state.status != UIStatus.DISENGAGED:
      return (4 if moving else 3, None), fps
    return (7 if moving else 0, None), fps

  def _render(self, _):
    now = time.monotonic()
    self._refresh_selection(now)
    if self._image.texture is None:
      return
    desired, fps = self._desired_animation(now)
    # Small hold avoids flicker from transient vehicle states; overrides respond immediately.
    if desired != self._animation and (desired[0] == 5 or now - self._animation_started >= 0.35):
      self._animation = desired
      self._animation_started = now
    row, still_frame = self._animation
    frame = still_frame if still_frame is not None else int((now - self._animation_started) * fps) % FRAME_COUNTS[row]
    self._image.draw(self.rect, frame, row)
