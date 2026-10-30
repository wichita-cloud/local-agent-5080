"""make_assets.py: draws the 1997-style pixel art for the BoomerGT installer (banner, icon, message-box icons).
Everything is drawn with the 16 classic Windows colours on small pixel grids, so it looks like it came off a CD-ROM."""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "payload")
os.makedirs(OUT, exist_ok=True)

BLACK = (0, 0, 0); MAROON = (128, 0, 0); GREEN = (0, 128, 0); OLIVE = (128, 128, 0)
NAVY = (0, 0, 128); PURPLE = (128, 0, 128); TEAL = (0, 128, 128); SILVER = (192, 192, 192)
GRAY = (128, 128, 128); RED = (255, 0, 0); LIME = (0, 255, 0); YELLOW = (255, 255, 0)
BLUE = (0, 0, 255); FUCHSIA = (255, 0, 255); AQUA = (0, 255, 255); WHITE = (255, 255, 255)

GLYPHS = {   # 5x7 pixel letters for the logo
    "d": ["....#", "....#", ".####", "#...#", "#...#", "#...#", ".####"],
    "i": ["..#..", ".....", ".##..", "..#..", "..#..", "..#..", ".###."],
    "g": [".####", "#...#", "#...#", "#...#", ".####", "....#", ".###."],
    "n": [".....", "####.", "#...#", "#...#", "#...#", "#...#", "#...#"],
    "t": ["..#..", ".###.", "..#..", "..#..", "..#..", "..#.#", "...#."],
    "a": [".....", ".....", ".###.", "....#", ".####", "#...#", ".####"],
    "s": [".....", ".....", ".####", "#....", ".###.", "....#", "####."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
    "6": [".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "o": [".....", ".....", ".###.", "#...#", "#...#", "#...#", ".###."],
    "m": [".....", ".....", "##.#.", "#.#.#", "#.#.#", "#.#.#", "#.#.#"],
    "e": [".....", ".....", ".###.", "#...#", "#####", "#....", ".###."],
    "r": [".....", ".....", "#.##.", "##..#", "#....", "#....", "#...."],
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
}


def text(d, x, y, s, scale, fg, shadow=None):
    for ch in s:
        g = GLYPHS[ch]
        for r, row in enumerate(g):
            for c, v in enumerate(row):
                if v == "#":
                    if shadow:
                        d.rectangle([x + c * scale + 1, y + r * scale + 1, x + (c + 1) * scale, y + (r + 1) * scale], fill=shadow)
                    d.rectangle([x + c * scale, y + r * scale, x + (c + 1) * scale - 1, y + (r + 1) * scale - 1], fill=fg)
        x += 6 * scale


BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def dither_gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / float(h - 1)
        for x in range(w):
            thr = (BAYER[y % 4][x % 4] + 0.5) / 16.0
            # blend between two palette colours using ordered dithering (two-colour steps like a 16-colour display)
            px[x, y] = bottom if t > thr else top
    return img


def banner():
    w, h = 144, 300
    img = dither_gradient(w, h, TEAL, NAVY)
    d = ImageDraw.Draw(img)
    # stars
    for (x, y) in [(14, 14), (120, 30), (30, 52), (100, 70), (60, 10), (132, 96), (18, 96)]:
        d.point((x, y), fill=WHITE); d.point((x + 1, y), fill=AQUA); d.point((x, y + 1), fill=AQUA)
    # logo
    text(d, 12, 106, "Boom", 5, WHITE, shadow=BLACK)
    text(d, 12, 148, "er", 5, WHITE, shadow=BLACK)
    text(d, 12, 200, "GT", 5, YELLOW, shadow=BLACK)
    # little computer
    d.rectangle([34, 12, 110, 80], fill=SILVER, outline=BLACK)
    d.rectangle([34, 12, 109, 79], outline=WHITE)
    d.rectangle([40, 18, 104, 70], fill=BLACK, outline=GRAY)
    d.rectangle([44, 22, 100, 66], fill=NAVY)
    for i, row in enumerate([8, 22, 15, 28]):
        d.rectangle([48, 27 + i * 9, 48 + row + 14, 30 + i * 9], fill=LIME if i % 2 == 0 else AQUA)
    d.rectangle([62, 80, 82, 88], fill=SILVER, outline=BLACK)
    d.rectangle([48, 88, 96, 94], fill=SILVER, outline=BLACK)
    d.rectangle([98, 62, 102, 64], fill=LIME)
    # floppy disk
    d.rectangle([14, 262, 54, 292], fill=BLUE, outline=BLACK)
    d.rectangle([22, 262, 46, 272], fill=SILVER, outline=BLACK)
    d.rectangle([40, 264, 43, 270], fill=BLACK)
    d.rectangle([20, 278, 48, 292], fill=WHITE, outline=BLACK)
    d.line([24, 283, 44, 283], fill=GRAY); d.line([24, 287, 44, 287], fill=GRAY)
    # CD
    d.ellipse([78, 254, 128, 292], fill=SILVER, outline=BLACK)
    d.ellipse([96, 267, 110, 279], fill=TEAL, outline=BLACK)
    d.arc([82, 258, 124, 288], 200, 290, fill=WHITE)
    d.rectangle([0, 0, w - 1, h - 1], outline=BLACK)
    img.save(os.path.join(OUT, "banner.bmp"))


def icon_image(n):
    s = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    d = ImageDraw.Draw(s)
    d.rectangle([3, 3, 28, 22], fill=SILVER, outline=BLACK)
    d.rectangle([3, 3, 27, 21], outline=WHITE)
    d.rectangle([6, 6, 25, 19], fill=NAVY, outline=BLACK)
    d.rectangle([8, 8, 15, 9], fill=LIME); d.rectangle([8, 11, 20, 12], fill=AQUA); d.rectangle([8, 14, 13, 15], fill=YELLOW)
    d.rectangle([13, 22, 18, 25], fill=SILVER, outline=BLACK)
    d.rectangle([8, 25, 23, 28], fill=SILVER, outline=BLACK)
    d.point((23, 17), fill=LIME)
    return s


FROG = (96, 200, 64); DFROG = (44, 140, 44); LFROG = (176, 236, 128); BERET = (214, 30, 48); DBERET = (140, 16, 32)
GOLD = (236, 190, 20); DGOLD = (170, 120, 0); BOW = (30, 50, 190)


def frog32_rich():
    """A very eccentric French frog: red beret, gold monocle on a chain, waxed mustache, bow tie, a look of faint disapproval."""
    im = Image.new("RGBA", (32, 32), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    # head and the two bulging eyes
    d.ellipse([3, 12, 28, 29], fill=FROG, outline=BLACK)
    d.ellipse([5, 14, 26, 27], fill=FROG)
    d.ellipse([4, 7, 14, 17], fill=WHITE, outline=BLACK)
    d.ellipse([17, 7, 27, 17], fill=WHITE, outline=BLACK)
    d.rectangle([9, 11, 11, 14], fill=BLACK); d.rectangle([21, 11, 23, 14], fill=BLACK)      # pupils, looking down his nose
    d.point((9, 11), fill=WHITE); d.point((21, 11), fill=WHITE)
    d.line([6, 9, 12, 9], fill=BLACK); d.line([19, 8, 25, 10], fill=BLACK)                    # one raised, one lowered brow
    # monocle on the right eye, with its chain
    d.ellipse([15, 5, 29, 19], outline=GOLD, width=1)
    d.point((22, 19), fill=DGOLD)
    d.line([28, 15, 30, 19], fill=GOLD); d.line([30, 19, 30, 25], fill=GOLD); d.point((30, 26), fill=DGOLD)
    # nostrils, waxed mustache with curled tips, and a small smug mouth
    d.point((15, 17), fill=DFROG); d.point((17, 17), fill=DFROG)
    d.line([9, 20, 15, 19], fill=BLACK); d.line([17, 19, 23, 20], fill=BLACK)
    d.rectangle([14, 19, 18, 20], fill=BLACK)
    d.line([9, 20, 8, 18], fill=BLACK); d.point((8, 17), fill=BLACK); d.line([23, 20, 24, 18], fill=BLACK); d.point((24, 17), fill=BLACK)
    d.line([13, 23, 19, 23], fill=DFROG); d.point((12, 22), fill=DFROG); d.point((20, 22), fill=DFROG)
    # the beret (tilted, with its little stalk)
    d.ellipse([5, 1, 24, 9], fill=BERET, outline=BLACK)
    d.ellipse([7, 2, 20, 6], fill=(240, 70, 80))
    d.rectangle([22, 2, 25, 4], fill=BERET); d.point((14, 0), fill=BLACK); d.point((14, 1), fill=DBERET)
    d.line([6, 7, 23, 8], fill=DBERET)
    # bow tie
    d.polygon([(12, 28), (16, 30), (12, 31)], fill=BOW, outline=BLACK); d.polygon([(20, 28), (16, 30), (20, 31)], fill=BOW, outline=BLACK)
    d.rectangle([15, 29, 17, 31], fill=DBERET, outline=BLACK)
    return im


def frog16_rich():
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.ellipse([1, 6, 14, 15], fill=FROG, outline=BLACK)
    d.ellipse([1, 3, 6, 8], fill=WHITE, outline=BLACK); d.ellipse([9, 3, 14, 8], fill=WHITE, outline=BLACK)
    d.point((4, 6), fill=BLACK); d.point((11, 6), fill=BLACK)
    d.ellipse([8, 2, 15, 9], outline=GOLD)                                    # monocle
    d.line([3, 10, 12, 10], fill=BLACK); d.point((2, 9), fill=BLACK); d.point((13, 9), fill=BLACK)   # mustache
    d.ellipse([2, 0, 12, 4], fill=BERET, outline=BLACK); d.point((6, 0), fill=BLACK)              # beret
    d.rectangle([6, 14, 9, 15], fill=BOW)
    return im


PFROG = (112, 170, 70); PDFROG = (64, 112, 50); FADED = (150, 52, 60); PATCH = (60, 90, 160); CRUST = (214, 160, 84); DCRUST = (140, 90, 40); TAPE = (222, 200, 140); PENCIL = (70, 70, 70)


def frog32():
    """A very poor French frog: faded beret with a hole and a patch, taped-up cracked glasses, a pencilled mustache, a stale baguette heel, one fly."""
    im = Image.new("RGBA", (32, 32), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.ellipse([3, 12, 28, 29], fill=PFROG, outline=BLACK)
    d.ellipse([5, 14, 26, 27], fill=PFROG)
    d.ellipse([4, 7, 14, 17], fill=WHITE, outline=BLACK)
    d.ellipse([17, 7, 27, 17], fill=WHITE, outline=BLACK)
    d.rectangle([8, 12, 11, 14], fill=BLACK); d.rectangle([20, 12, 23, 14], fill=BLACK)         # pupils, looking at the baguette
    d.rectangle([5, 8, 13, 10], fill=PFROG); d.rectangle([18, 8, 26, 10], fill=PFROG)            # heavy tired eyelids
    d.line([5, 10, 13, 10], fill=BLACK); d.line([18, 10, 26, 10], fill=BLACK)
    # round glasses held together with tape, the right lens cracked
    d.ellipse([3, 6, 15, 18], outline=BLACK); d.ellipse([16, 6, 28, 18], outline=BLACK)
    d.line([15, 12, 16, 12], fill=BLACK); d.rectangle([14, 11, 17, 13], fill=TAPE)
    d.line([21, 8, 24, 11], fill=(200, 220, 255)); d.line([24, 11, 22, 14], fill=(200, 220, 255))
    # pencilled mustache, uneven, and a flat unimpressed mouth
    d.point((15, 17), fill=PDFROG); d.point((17, 17), fill=PDFROG)
    d.line([10, 20, 15, 19], fill=PENCIL); d.line([17, 19, 21, 20], fill=PENCIL); d.point((9, 21), fill=PENCIL); d.point((22, 21), fill=PENCIL)
    d.line([12, 24, 20, 24], fill=PDFROG)
    # faded beret with a hole and a patch, slumping to one side
    d.ellipse([4, 1, 23, 9], fill=FADED, outline=BLACK)
    d.ellipse([7, 2, 19, 5], fill=(176, 80, 84))
    d.rectangle([20, 3, 24, 6], fill=FADED); d.line([6, 7, 22, 8], fill=(100, 30, 40))
    d.rectangle([13, 3, 15, 5], fill=BLACK)                                                       # the hole
    d.rectangle([17, 4, 20, 7], fill=PATCH, outline=BLACK); d.point((18, 5), fill=WHITE)         # the patch, stitched
    d.point((13, 0), fill=BLACK); d.point((14, 0), fill=BLACK)
    # the baguette heel, one bite taken
    d.polygon([(6, 28), (26, 25), (27, 28), (7, 31)], fill=CRUST, outline=BLACK)
    d.line([10, 28, 12, 27], fill=DCRUST); d.line([15, 27, 17, 26], fill=DCRUST); d.line([20, 26, 22, 25], fill=DCRUST)
    d.ellipse([24, 24, 28, 28], fill=(0, 0, 0, 0)); d.point((26, 26), fill=BLACK)
    # one fly
    d.point((28, 3), fill=BLACK); d.point((27, 2), fill=WHITE); d.point((29, 2), fill=WHITE)
    return im


def frog16():
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.ellipse([1, 6, 14, 14], fill=PFROG, outline=BLACK)
    d.ellipse([1, 3, 6, 8], fill=WHITE, outline=BLACK); d.ellipse([9, 3, 14, 8], fill=WHITE, outline=BLACK)
    d.point((4, 6), fill=BLACK); d.point((11, 6), fill=BLACK); d.line([1, 5, 6, 5], fill=BLACK)
    d.line([4, 10, 11, 10], fill=PENCIL)
    d.ellipse([2, 0, 12, 4], fill=FADED, outline=BLACK); d.point((9, 2), fill=PATCH); d.point((6, 1), fill=BLACK)
    d.rectangle([3, 13, 13, 15], fill=CRUST, outline=BLACK)
    return im


def penrose(size):
    """The Penrose triangle, built as three real 3D bars whose ends meet in the picture but not in space. Drawn big, shrunk."""
    import math
    S = 1024; im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    c30 = math.cos(math.radians(30))
    def P(x, y, z):                       # isometric: +x right-down, +y left-down, +z up
        return ((x - y) * c30, (x + y) * 0.5 - z)
    L, w = 3.0, 1.0
    TOP, SIDEX, SIDEY = (150, 236, 255), (20, 160, 225), (0, 70, 160)
    def box(x0, x1, y0, y1, z0, z1):
        return [("z", [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], TOP),
                ("x", [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], SIDEX),
                ("y", [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], SIDEY)]
    pieces = []
    pieces += box(0, L + w, 0, w, 0, w)            # bar 1, along x
    pieces += box(L, L + w, 0, L + w, 0, w)        # bar 2, along y
    pieces += box(L, L + w, L, L + w, 0, L + w)    # bar 3, climbing along z, ends where bar 1 begins (in the picture)
    pieces += box(0, w, 0, w, 0, w)                # bar 1's first block again, drawn last so it sits in front of bar 3's end
    pts = [P(*v) for _, poly, _ in pieces for v in poly]
    minx = min(p[0] for p in pts); maxx = max(p[0] for p in pts); miny = min(p[1] for p in pts); maxy = max(p[1] for p in pts)
    scale = S * 0.86 / max(maxx - minx, maxy - miny)
    ox = (S - (maxx - minx) * scale) / 2 - minx * scale; oy = (S - (maxy - miny) * scale) / 2 - miny * scale
    # painter's order inside each bar (far to near); the bars themselves are drawn in the order above
    def draw_poly(poly, color):
        xy = [(P(*v)[0] * scale + ox, P(*v)[1] * scale + oy) for v in poly]
        d.polygon(xy, fill=color)
        d.line(xy + [xy[0]], fill=BLACK, width=int(S * 0.012), joint="curve")
    for k in range(0, len(pieces), 3):
        for kind, poly, color in pieces[k:k + 3]:
            draw_poly(poly, color)
    im = im.rotate(90, resample=Image.BICUBIC)       # the loop is drawn pointing right; stand it up
    return im.resize((size, size), Image.LANCZOS)


# ---- the icon: the dog, from dog-source.png (130x130, background and everything else already cut away)
DOG_EYES = [(30, 61), (68, 79)]; DOG_NOSE = (40, 81); DOG_TONGUE = (40, 110, 51, 122)   # in dog-source.png pixels


def dog(n):
    src = Image.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dog-source.png")).convert("RGBA")
    W = float(src.width); s = n / W
    r = src.convert("RGB").resize((n, n), Image.BOX); al = src.getchannel("A").resize((n, n), Image.BOX)
    pr = r.load(); pa = al.load()
    # four greys by brightness rank, so a black dog still shows its fur, eyes and muzzle at 16 colours
    ls = sorted(sum(pr[x, y]) / 3 for y in range(n) for x in range(n) if pa[x, y] >= 110)
    q1, q2, q3 = ls[int(len(ls) * .62)], ls[int(len(ls) * .85)], ls[int(len(ls) * .95)]
    out = Image.new("RGBA", (n, n), (0, 0, 0, 0)); po = out.load()
    for y in range(n):
        for x in range(n):
            if pa[x, y] < 110: continue
            l = sum(pr[x, y]) / 3
            po[x, y] = (BLACK if l < q1 else GRAY if l < q2 else SILVER if l < q3 else WHITE) + (255,)
    d = ImageDraw.Draw(out)
    PINK = (255, 128, 128)   # the stop icon's highlight colour
    t = [DOG_TONGUE[0] * s, DOG_TONGUE[1] * s, DOG_TONGUE[2] * s, DOG_TONGUE[3] * s]
    if t[2] - t[0] < 2: t[2] = t[0] + 2
    if t[3] - t[1] < 2: t[3] = t[1] + 2
    w, h = t[2] - t[0], t[3] - t[1]
    d.rectangle([t[0], t[1], t[2], t[1] + h * 0.45], fill=PINK + (255,)); d.ellipse(t, fill=PINK + (255,))
    d.line([t[0], t[1], t[2], t[1]], fill=BLACK + (255,))                       # mouth line above the tongue
    if n >= 32: d.line([t[0] + w / 2, t[1] + 1, t[0] + w / 2, t[3] - 1], fill=RED + (255,))
    if n >= 48: d.point([(t[0] + 1, t[3]), (t[2] - 1, t[3])], fill=MAROON + (255,))
    nx, ny = DOG_NOSE[0] * s, DOG_NOSE[1] * s; rr = max(1, n / 32)
    d.ellipse([nx - rr, ny - rr * .8, nx + rr, ny + rr * .8], fill=BLACK + (255,))
    for ex, ey in DOG_EYES:
        x, y = int(ex * s), int(ey * s)
        if n >= 24: d.rectangle([x - 1, y - 1, x + 1, y], fill=BLACK + (255,))
        po[x, y] = WHITE + (255,)                                                  # a glint so the eyes read
    # white rim: a black dog on a dark desktop would vanish otherwise
    o2 = out.copy(); p2 = o2.load()
    for y in range(n):
        for x in range(n):
            if po[x, y][3] == 0 and any(0 <= x + dx < n and 0 <= y + dy < n and po[x + dx, y + dy][3]
                                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                p2[x, y] = WHITE + (255,)
    return o2


def dog_frames():
    f = [(n, dog(n)) for n in (16, 24, 32, 48, 64)]
    big = f[-1][1]
    return f + [(128, big.resize((128, 128), Image.NEAREST)), (256, big.resize((256, 256), Image.NEAREST))]


def write_ico(path, frames):
    import io, struct
    blobs = []
    for sz, img in frames:
        buf = io.BytesIO(); img.save(buf, format="PNG"); blobs.append((sz, buf.getvalue()))
    head = struct.pack("<HHH", 0, 1, len(blobs))
    off = 6 + 16 * len(blobs); dirs = b""; body = b""
    for sz, data in blobs:
        dirs += struct.pack("<BBBBHHII", 0 if sz >= 256 else sz, 0 if sz >= 256 else sz, 0, 0, 1, 32, len(data), off + len(body))
        body += data
    open(path, "wb").write(head + dirs + body)


def icons():
    frames = dog_frames()
    w16, w32 = frames[0][1], frames[2][1]
    write_ico(os.path.join(OUT, "BoomerGT.ico"), frames)
    w16.save(os.path.join(OUT, "app.png"))
    w32.save(os.path.join(OUT, "_32.png"))

    def kind(name):
        s = Image.new("RGBA", (32, 32), (0, 0, 0, 0)); d = ImageDraw.Draw(s)
        if name == "info":
            d.ellipse([2, 2, 29, 29], fill=BLUE, outline=BLACK); d.ellipse([3, 3, 28, 28], outline=AQUA)
            d.rectangle([14, 8, 17, 11], fill=WHITE); d.rectangle([14, 14, 17, 24], fill=WHITE)
        if name == "warn":
            d.polygon([(16, 2), (30, 28), (2, 28)], fill=YELLOW, outline=BLACK)
            d.rectangle([15, 10, 17, 20], fill=BLACK); d.rectangle([15, 23, 17, 25], fill=BLACK)
        if name == "ask":
            d.ellipse([2, 2, 29, 29], fill=BLUE, outline=BLACK); d.ellipse([3, 3, 28, 28], outline=AQUA)
            d.rectangle([11, 8, 20, 10], fill=WHITE); d.rectangle([19, 10, 21, 15], fill=WHITE); d.rectangle([15, 15, 20, 17], fill=WHITE)
            d.rectangle([14, 17, 16, 20], fill=WHITE); d.rectangle([14, 23, 16, 25], fill=WHITE); d.rectangle([10, 10, 12, 12], fill=WHITE)
        if name == "stop":
            d.ellipse([2, 2, 29, 29], fill=RED, outline=BLACK); d.ellipse([3, 3, 28, 28], outline=(255, 128, 128))
            d.line([10, 10, 21, 21], fill=WHITE, width=3); d.line([21, 10, 10, 21], fill=WHITE, width=3)
        s.save(os.path.join(OUT, name + ".png"))
    for n in ("info", "warn", "ask", "stop"):
        kind(n)


banner()
icons()
print("assets written to", OUT)
