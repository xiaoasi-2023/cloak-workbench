"""Render editable geometric brand artwork; requires Pillow on the build machine only."""
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets' / 'branding'


def icon(size=1024):
    scale = size / 256
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    def rect(box, radius, **kwargs):
        draw.rounded_rectangle(tuple(round(n * scale) for n in box), radius=round(radius * scale), **kwargs)
    rect((4, 4, 252, 252), 54, fill='#102B25')
    width = round(14 * scale)
    # The muted rear window uses the same composited color as the SVG.
    muted = (27, 120, 97, 255)
    points = [(51, 154), (51, 66), (53, 59), (59, 53), (66, 51), (154, 51)]
    draw.line([(round(x * scale), round(y * scale)) for x, y in points], fill=muted, width=width, joint='curve')
    for x, y in (points[0], points[-1]):
        draw.ellipse(tuple(round(n * scale) for n in (x - 7, y - 7, x + 7, y + 7)), fill=muted)
    # Pillow's outline lies inside its box; expand by half the SVG stroke.
    rect((70, 70, 212, 212), 27, outline='#28D6AA', width=width)
    draw.polygon([(round(x * scale), round(y * scale)) for x, y in [(111, 155), (171, 101), (156, 173), (138, 146)]], fill='#28D6AA')
    return image


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    master = icon()
    master.resize((256, 256), Image.Resampling.LANCZOS).save(OUT / 'qihang-256.png')
    sizes = [(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
    master.save(OUT / 'qihang.ico', sizes=sizes)
    # A compact actual-size strip makes small Windows icon variants reviewable.
    strip = Image.new('RGB', (600, 280), '#F2F5F3')
    x = 24
    for n in (16, 24, 32, 48, 64, 128, 192):
        tile = master.resize((n, n), Image.Resampling.LANCZOS)
        strip.paste(tile, (x, (280 - n) // 2), tile)
        x += n + 12
    strip.save(OUT / 'icon-sizes.png')
    with Image.open(OUT / 'qihang.ico') as result:
        assert result.ico.sizes() == set(sizes)
    print('Draft branding PNG and 9-size ICO rendered and verified')


if __name__ == '__main__':
    main()
