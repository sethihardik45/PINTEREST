import os
import io
import base64
import random
import uuid
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types

IMAGE_MODEL = "gemini-2.5-flash-image"
TEXT_MODEL = "gemini-2.5-flash"
TARGET_SIZE = (1000, 1000)
ROOT_DIR = Path(__file__).parent
OUTPUT_DIR = ROOT_DIR / "static" / "generated"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
BANNER_SETS = {
    "purple": (ROOT_DIR / "top-purple.png", ROOT_DIR / "bottom-purple.png"),
}

CAPTION_TOPICS = [
    "Horoscopes",
    "Moon Phase Updates",
    "Planetary Transits",
    "Zodiac Spotlights",
    "Pick-a-Card Readings",
    "Card of the Day or Week",
    "Rune or Charm Casting",
    "Intuition Tests",
    "Affirmations and Mantras",
    "Manifestation Techniques",
    "Chakra Education",
    "Energy Protection",
    "Angel Numbers",
    "Dream Interpretation",
    "Numerology",
    "Animal Totems and Spirit Guides",
    "Crystal Spotlights",
    "Rituals and Spellwork",
    "Herb and Plant Magic",
    "Ask a Psychic Q&A",
    "Client Testimonials",
    "Live Mini-Readings",
]

STYLE_SUFFIX = (
    "Pinterest-worthy aesthetic, square 1:1 composition, cinematic moody lighting, "
    "rich color palette of deep purples, midnight blues, burnt gold, soft candlelight "
    "and rose quartz pink, ethereal glow, shallow depth of field, dreamy bokeh, "
    "high detail editorial photography and fine art illustration hybrid, "
    "mystical and magical atmosphere, dark academia meets celestial witchcore. "
    "ABSOLUTELY NO text, NO letters, NO words, NO watermarks, NO logos, NO captions anywhere in the image."
)

THEMES = [
    "A mystical tarot card reader's hands spreading ornate illustrated tarot cards on a dark velvet cloth, crystal ball and burning beeswax candles in the background, wisps of incense smoke catching the light, amethyst crystals scattered around.",
    "A glowing crystal ball on an antique brass stand with swirling nebula and tiny constellations visible inside, mystical fog curling around the base, tarot cards and dried lavender scattered on weathered oak wood.",
    "An ornate golden zodiac wheel with all 12 astrological signs engraved around its circumference, glowing constellations and starlight radiating outward, set against a deep cosmic night sky with swirling galaxies.",
    "A serene ethereal woman with flowing dark hair and closed eyes, a glowing third-eye symbol softly on her forehead, holding a luminous quartz crystal, surrounded by floating stars and sacred geometry.",
    "A close-up of elegant hands tracing the life line on an open palm with golden light, delicate astrological symbols floating above, candle glow and incense smoke in the warm dreamy background.",
    "An elaborate natal astrology birth chart drawn in gold ink on aged parchment, planetary symbols and house divisions glowing softly, surrounded by brass compass, feather quill, and steaming herbal tea in a vintage cup.",
    "A glowing full moon over a mystical altar with tarot cards, raw crystals, sage bundle and a silver chalice, moonlight beams cutting through mist, star-sprinkled indigo sky, ferns in the foreground.",
    "A beautifully illustrated oracle card floating mid-air above a witch's wooden table, golden sparks swirling around it, amethyst and rose quartz clusters arranged below, dreamy bokeh background.",
    "A meditating silhouette seen from the side with a vibrant multi-colored aura — violet, magenta, teal and gold — radiating outward in concentric rings, chakra points glowing softly along the spine, cosmic background.",
    "An ethereal spirit guide rendered as a luminous figure made of stardust and soft light, hovering gently, wings of cosmic energy, ancient sigils glowing around her, deep midnight velvet background.",
    "A witch's weathered hands casting carved bone runes onto a dark velvet cloth, runes glowing faint gold, beeswax candles and a leather-bound grimoire nearby, firelit mysterious atmosphere.",
    "A flat lay of mystical items on dark moody wood: an open vintage tarot deck, raw amethyst and clear quartz crystals, dried pressed flowers, brass incense burner with curling smoke, antique key, and a small black mirror.",
    "A pair of delicate hands holding a smoking white sage bundle, tendrils of fragrant smoke curling upward into a dark atmospheric space, small glowing embers, soft candlelight.",
    "A dreamy celestial scene of a woman's profile silhouetted against a giant glowing moon, constellations forming a crown above her head, wisps of cosmic stardust, indigo and rose gold palette.",
    "Tarot cards fanned across an antique mirror reflecting candlelight, dried roses and a crystal pendant resting on top, rich red velvet and dark wood, baroque moody still life composition.",
    "A cozy witchy workspace from above: candles in vintage brass holders, spell jars with colorful herbs, crystal ball, open leather journal with cosmic drawings, tarot spread in progress, warm amber light.",
    "A single illuminated pentacle pendant on a fine chain, resting on an open page of ancient astrological text, soft golden candlelight, extremely shallow depth of field, romantic mystical macro photography.",
    "A glowing third eye symbol formed from constellations and golden sacred geometry, floating in a deep purple cosmic void, stars and nebula swirling around it, highly detailed celestial art.",
    "A witchy moon phase wall display carved from brass on dark textured wallpaper, crystals and dried botanicals hanging beside it, moody boho interior, soft afternoon light filtering through sheer curtains.",
    "A cup of fortune-telling tea leaves on a saucer, the leaves forming mystical symbols at the bottom, steam curling upward, tarot cards and a crystal resting nearby on a lace doily, warm nostalgic lighting.",
]


def _client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to your environment or .env file.")
    return genai.Client(api_key=api_key)


PURPLE_OVERLAY_RGBA = (20, 8, 45, 179)
CAPTION_SIDE_PADDING = 100
CAPTION_COLOR = (255, 255, 255)
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _resolve_font_path() -> str | None:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = (" ".join(current + [word])).strip()
        w = draw.textlength(trial, font=font)
        if w <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _ensure_square(image: Image.Image, size=TARGET_SIZE) -> Image.Image:
    w, h = image.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    cropped = image.crop((left, top, left + side, top + side))
    return cropped.resize(size, Image.LANCZOS)


def _pick_banner_set() -> tuple[str, Path, Path] | None:
    for name, (top, bottom) in BANNER_SETS.items():
        if top.exists() and bottom.exists():
            return name, top, bottom
    return None


def _stitch_banners(middle: Image.Image) -> tuple[Image.Image, str]:
    pick = _pick_banner_set()
    if not pick:
        return middle, ""
    name, top_path, bottom_path = pick
    top = Image.open(top_path).convert("RGB")
    bottom = Image.open(bottom_path).convert("RGB")
    if top.width != middle.width:
        top = top.resize((middle.width, top.height), Image.LANCZOS)
    if bottom.width != middle.width:
        bottom = bottom.resize((middle.width, bottom.height), Image.LANCZOS)
    total_h = top.height + middle.height + bottom.height
    canvas = Image.new("RGB", (middle.width, total_h), (255, 255, 255))
    canvas.paste(top, (0, 0))
    canvas.paste(middle, (0, top.height))
    canvas.paste(bottom, (0, top.height + middle.height))
    return canvas, name


def _apply_purple_overlay(image: Image.Image, rgba=PURPLE_OVERLAY_RGBA) -> Image.Image:
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, rgba)
    return Image.alpha_composite(base, overlay).convert("RGB")


def _draw_caption(
    image: Image.Image,
    caption: str,
    side_padding: int = CAPTION_SIDE_PADDING,
    color: tuple = CAPTION_COLOR,
) -> Image.Image:
    if not caption:
        return image

    img = image.copy()
    draw = ImageDraw.Draw(img)
    max_width = img.width - 2 * side_padding
    font_path = _resolve_font_path()

    chosen_font = None
    chosen_lines: list[str] = []
    for font_size in range(64, 22, -2):
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
        lines = _wrap_text(caption, font, max_width, draw)
        line_height = int(font_size * 1.25)
        total_height = line_height * len(lines)
        max_line_width = max(draw.textlength(l, font=font) for l in lines) if lines else 0
        if max_line_width <= max_width and total_height <= img.height - 2 * side_padding:
            chosen_font = font
            chosen_lines = lines
            break

    if chosen_font is None:
        chosen_font = ImageFont.truetype(font_path, 24) if font_path else ImageFont.load_default()
        chosen_lines = _wrap_text(caption, chosen_font, max_width, draw)

    font_size = chosen_font.size if hasattr(chosen_font, "size") else 24
    line_height = int(font_size * 1.25)
    block_height = line_height * len(chosen_lines)
    y = (img.height - block_height) // 2

    for line in chosen_lines:
        line_width = draw.textlength(line, font=chosen_font)
        x = (img.width - line_width) // 2
        draw.text((x, y), line, font=chosen_font, fill=color)
        y += line_height

    return img


def build_prompt() -> tuple[str, str]:
    base = random.choice(THEMES)
    return base, f"{base} {STYLE_SUFFIX}"


def generate_caption(client) -> tuple[str, str]:
    topic = random.choice(CAPTION_TOPICS)
    instruction = (
        f"Write one informative and engaging social caption about the topic: \"{topic}\".\n\n"
        "Deliver actual VALUE to the reader — a fact, tip, insight, mini-lesson, "
        "surprising detail, timely update, or thought-provoking question. "
        "Think of it like a Pinterest/Instagram educational post, not an ad.\n\n"
        "Hard rules:\n"
        "- Exactly 15 to 20 words.\n"
        "- One or two short sentences, conversational tone.\n"
        "- No promotion whatsoever. Do NOT sell a service, app, reading or psychic. "
        "Do NOT use words like: discover, unlock, book, ask a psychic, our reader, "
        "await, reveal your future, click, tap, DM, contact, visit.\n"
        "- No hashtags, no emojis, no quotation marks, no title prefix, no call-to-action.\n"
        "- Specific and concrete wherever possible (e.g. name the planet, crystal, "
        "number, moon phase, chakra, or element).\n"
        "- Output only the caption text."
    )
    resp = client.models.generate_content(model=TEXT_MODEL, contents=instruction)
    caption = (resp.text or "").strip().strip('"').strip("'")
    return topic, caption


def generate_image() -> dict:
    base, prompt = build_prompt()
    client = _client()

    response = client.models.generate_content(
        model=IMAGE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )

    image_bytes = None
    for part in response.candidates[0].content.parts:
        if getattr(part, "inline_data", None) and part.inline_data.data:
            image_bytes = part.inline_data.data
            break

    if image_bytes is None:
        raise RuntimeError("Gemini did not return an image. Try a different prompt.")

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = _ensure_square(img)
    img = _apply_purple_overlay(img)

    try:
        topic, caption = generate_caption(client)
    except Exception:
        topic, caption = "", ""

    if caption:
        img = _draw_caption(img, caption)

    img, banner_set = _stitch_banners(img)

    filename = f"lumus_{uuid.uuid4().hex[:10]}.png"
    out_path = OUTPUT_DIR / filename
    img.save(out_path, "PNG", optimize=True)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    return {
        "filename": filename,
        "url": f"/static/generated/{filename}",
        "data_uri": data_uri,
        "banner_set": banner_set,
        "theme": base,
        "topic": topic,
        "caption": caption,
        "width": img.width,
        "height": img.height,
    }
