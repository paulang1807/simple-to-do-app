# /// script
# requires-python = ">=3.11"
# dependencies = ["Pillow"]
# ///
"""
Assemble the captured screenshots into an annotated animated GIF.
Run with:  uv run docs/make_gif.py
"""

from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

OUT_DIR = Path("docs")
GIF_PATH = OUT_DIR / "demo.gif"

FRAMES = [
    ("01-main-view.png",          "Main view — task list with nested subtasks"),
    ("02-task-actions.png",       "Hover a task to reveal action buttons"),
    ("03-add-subtask.png",        "Add a subtask with the ⤷ button"),
    ("04-task-statuses.png",      "Task statuses: Done ✓, Partial ◑, Important ★"),
    ("05-collapsed-indicators.png","Collapse subtasks — badges show hidden partials/important"),
    ("06-recurring.png",          "Recurring task — rolls forward every day until closed"),
    ("07-context-panel.png",      "Context panel — notes, links (Jira/Slack/Docs) & attachments"),
    ("08-collapse-all.png",       "Collapse All / Expand All in one click"),
    ("09-move-button.png",        "Move incomplete tasks to next day with one click"),
    ("11-summary-result.png",     "Work summary — week, month, quarter or year"),
]

# Thumbnail width for the GIF (height scaled proportionally)
GIF_W = 1280
CAPTION_H = 52
HOLD_MS   = 2800   # ms per frame
TRANS_MS  = 80     # ms for blank transition frame

# Try to load a font; fall back to default if unavailable
def load_font(size):
    for name in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()

font = load_font(20)

def annotate(img: Image.Image, caption: str) -> Image.Image:
    w, h = img.size
    canvas = Image.new("RGB", (w, h + CAPTION_H), (15, 17, 23))
    canvas.paste(img, (0, 0))
    draw = ImageDraw.Draw(canvas)
    # Caption bar
    draw.rectangle([(0, h), (w, h + CAPTION_H)], fill=(26, 29, 39))
    draw.line([(0, h), (w, h)], fill=(58, 63, 92), width=1)
    # Centre the text
    bbox = draw.textbbox((0, 0), caption, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((w - tw) // 2, h + (CAPTION_H - th) // 2), caption, font=font, fill=(226, 230, 243))
    return canvas

frames = []
durations = []

for fname, caption in FRAMES:
    path = OUT_DIR / fname
    img = Image.open(path).convert("RGB")
    # Scale to GIF_W maintaining aspect ratio
    scale = GIF_W / img.width
    new_h = int(img.height * scale)
    img = img.resize((GIF_W, new_h), Image.LANCZOS)
    annotated = annotate(img, caption)
    # Convert to palette mode for GIF
    frames.append(annotated.convert("P", palette=Image.ADAPTIVE, colors=256))
    durations.append(HOLD_MS)
    # Add a brief dark transition frame
    blank = Image.new("RGB", annotated.size, (10, 12, 18))
    frames.append(blank.convert("P", palette=Image.ADAPTIVE, colors=256))
    durations.append(TRANS_MS)

print(f"Encoding {len(frames)} frames → {GIF_PATH} ...")
frames[0].save(
    GIF_PATH,
    save_all=True,
    append_images=frames[1:],
    duration=durations,
    loop=0,
    optimize=False,
)
size_mb = GIF_PATH.stat().st_size / 1_048_576
print(f"Done! {GIF_PATH}  ({size_mb:.1f} MB)")
