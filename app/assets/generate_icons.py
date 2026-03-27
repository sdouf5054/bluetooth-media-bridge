from PIL import Image
from pathlib import Path

src_dir = Path(r"C:\Users\slamt\Project\bluetooth-media-bridge\app\assets\simple")

sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

for png in src_dir.glob("*.png"):
    img = Image.open(png).convert("RGBA")
    ico_path = png.with_suffix(".ico")
    img.save(
        ico_path,
        format="ICO",
        sizes=sizes
    )
