from PIL import Image
from pathlib import Path

src = Path("tray_streaming.png")

img = Image.open(src).convert("RGBA")

sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

img.save(
    src.with_suffix(".ico"),
    format="ICO",
    sizes=sizes
)