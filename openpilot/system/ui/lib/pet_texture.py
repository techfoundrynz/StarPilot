"""Small, explicitly owned pet textures. All methods run on the UI thread."""
from pathlib import Path

from openpilot.starpilot.ui.raylib import rl

CELL_WIDTH = 192
CELL_HEIGHT = 208
FRAME_COUNTS = (6, 8, 8, 4, 5, 8, 6, 6, 6)


class PetTexture:
  def __init__(self):
    self.texture = None
    self.owned = False

  def close(self):
    if self.texture is not None and self.owned:
      rl.unload_texture(self.texture)
    self.texture = None
    self.owned = False

  def load(self, source: Path | bytes, atlas: bool = True):
    """Load validated local PNG data, without adding it to the app's global cache."""
    # The v7 nanobind Image retains its Python pixel buffer; never unload that buffer through raylib.
    import io
    from PIL import Image
    with Image.open(io.BytesIO(source) if isinstance(source, bytes) else source) as decoded:
      expected = ((1536, 1872), (1536, 2288)) if atlas else ((192, 208),)
      if decoded.size not in expected:
        raise ValueError("Invalid pet image dimensions")
      pixels = decoded.convert("RGBA").tobytes()
      image = rl.Image(pixels, decoded.width, decoded.height, 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
      texture = rl.load_texture_from_image(image)
      if texture.id == 0:
        raise ValueError("Could not load pet texture")
      rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      rl.set_texture_wrap(texture, rl.TextureWrap.TEXTURE_WRAP_CLAMP)
    self.close()
    self.texture = texture
    self.owned = True

  def draw(self, rect, frame: int = 0, row: int = 0, atlas: bool = True):
    if self.texture is None:
      return
    src = rl.Rectangle(frame * CELL_WIDTH if atlas else 0, row * CELL_HEIGHT if atlas else 0, CELL_WIDTH, CELL_HEIGHT)
    rl.draw_texture_pro(self.texture, src, rect, rl.Vector2(0, 0), 0, rl.WHITE)
