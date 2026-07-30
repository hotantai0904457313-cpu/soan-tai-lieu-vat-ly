"""
Tạo icon app.ico chuyên nghiệp cho ứng dụng Soạn Tài Liệu Vật Lý
- Nền tròn navy (#0D2654)
- Biểu tượng nguyên tử 3 quỹ đạo màu cyan (#00D4FF)
- Hạt nhân trắng ở giữa
- Xuất ICO đa kích thước: 256, 128, 64, 48, 32, 16
"""
from PIL import Image, ImageDraw, ImageFilter
import os

def create_at(size):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))

    # --- Nền hình tròn navy ---
    bg = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bg)
    m = max(1, size // 12)
    bd.ellipse([m, m, size - m - 1, size - m - 1], fill=(13, 38, 84, 255))
    img = Image.alpha_composite(img, bg)

    # Lớp tô sáng nhẹ (gradient giả) ở góc trên trái
    shine = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shine)
    sr = int(size * 0.28)
    sd.ellipse([m + int(size*0.05), m + int(size*0.04),
                m + int(size*0.05) + sr, m + int(size*0.04) + sr],
               fill=(255, 255, 255, 18))
    img = Image.alpha_composite(img, shine)

    # --- 3 quỹ đạo nguyên tử ---
    cx, cy = size // 2, size // 2
    rx = int(size * 0.34)
    ry = int(size * 0.13)
    lw = max(1, size // 40)
    orbit_color = (0, 212, 255, 210)

    for angle in [0, 60, 120]:
        layer = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                  outline=orbit_color, width=lw)
        layer = layer.rotate(angle, center=(cx, cy), resample=Image.BICUBIC)
        img = Image.alpha_composite(img, layer)

    # --- Hạt nhân (glow + dot trắng) ---
    nr = max(2, size // 13)
    glow_r = nr + max(1, size // 20)

    # Glow cyan mờ
    glow = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r],
               fill=(0, 212, 255, 80))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(1, glow_r // 2)))
    img = Image.alpha_composite(img, glow)

    # Dot trắng
    nuc = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    nd = ImageDraw.Draw(nuc)
    nd.ellipse([cx - nr, cy - nr, cx + nr, cy + nr],
               fill=(255, 255, 255, 255))
    img = Image.alpha_composite(img, nuc)

    return img


sizes = [256, 128, 64, 48, 32, 16]
images = [create_at(s) for s in sizes]

out = os.path.join(os.path.dirname(__file__), 'app.ico')
images[0].save(
    out, format='ICO',
    sizes=[(s, s) for s in sizes],
    append_images=images[1:]
)
print(f'Icon da tao: {out}')
