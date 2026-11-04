"""Native onroad companion and border layers; camera and alert ownership stay with the shell."""
from dataclasses import replace
from typing import Any

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.onroad_state import AlertSize, BorderSignals, OnroadState
from openpilot.starpilot.ui.pip_sidecam import Signals
from openpilot.starpilot.ui.presentation import Profile
from openpilot.system.ui.widgets.pet import PetWidget


def open_pet_store(parked) -> bool:
  if not parked():
    return False
  from openpilot.selfdrive.ui.widgets.pet_store import open_pet_store as open_panel
  open_panel()
  return True


class PetLayer:
  def __init__(self, profile, previous, ui_state):
    self.previous = previous
    self.ui_state = ui_state
    self.pet = PetWidget(scale=0.5 if profile == Profile.COMPACT else 0.75)

  def render(self, rect, state):
    if self.previous is not None:
      self.previous(rect, state)
    if (self.ui_state.started and self.ui_state.params.get_bool("ShowPet") and
        state.alert.size == AlertSize.NONE and
        state.appearance.camera_view not in (CameraViewChoice.NONE, CameraViewChoice.DRIVER) and
        not state.reverse_driver_camera and not state.reversing):
      self.pet.set_position(rect.x + rect.width - self.pet.rect.width - 4,
                            rect.y + rect.height - self.pet.rect.height)
      self.pet.render()

  def close(self):
    self.pet.hide_event()


def visual_border_state(state: OnroadState, signals: Signals) -> OnroadState:
  border = state.border_signals
  if border is None or not (signals.vasm_left or signals.vasm_right):
    return state
  return replace(state, border_signals=BorderSignals(
    border.left_blinker, border.right_blinker,
    border.left_blindspot or signals.vasm_left, border.right_blindspot or signals.vasm_right))


def render_stock_confidence(camera_owner: Any, rect: rl.Rectangle, state: OnroadState) -> None:
  camera_owner.render_stock_confidence_layer(rect, lateral_active=state.lateral_active)

