from PIL import Image, ImageDraw

SIZE = 512
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Rounded-rect background (dark slate w/ subtle gradient)
d.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=96, fill=(24, 26, 32, 255))

# Clapperboard body
d.rounded_rectangle([64, 128, 448, 400], radius=24, fill=(30, 32, 40, 255), outline=(90, 96, 120, 255), width=4)

# Clapperboard top stripes (white)
for i, y in enumerate(range(128, 224, 24)):
    color = (255, 255, 255, 255) if i % 2 == 0 else (0, 0, 0, 255)
    d.rounded_rectangle([64, y, 448, y + 24], radius=8, fill=color)

# Film-strip sprocket holes on left / right
for y in range(160, 400, 32):
    d.rounded_rectangle([88, y, 112, y + 20], radius=4, fill=(200, 200, 210, 255))
    d.rounded_rectangle([400, y, 424, y + 20], radius=4, fill=(200, 200, 210, 255))

# Play triangle (accent)
d.polygon([(200, 220), (200, 340), (320, 280)], fill=(52, 211, 153, 255))

# Save
img.save("assets/icon.png")
img.save("assets/icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon.png + icon.ico written")