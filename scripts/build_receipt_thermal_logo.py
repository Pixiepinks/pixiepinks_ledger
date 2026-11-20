"""Deterministic original-art conversion, never redraws or changes the color source.
Crop transparent padding; composite on white; enhance grayscale contrast;
resize for a 26 mm-high image at 203 dpi; black/white diffusion for thermal output.
"""
from pathlib import Path
from PIL import Image,ImageOps,ImageEnhance
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'static/images/login_logo.png'
TARGET=ROOT/'static/images/receipt_fairy_203dpi.png'


def build(source=SOURCE,target=TARGET):
    with Image.open(source) as opened:
        rgba=opened.convert('RGBA');bounds=rgba.getchannel('A').getbbox()
        if bounds is None:raise ValueError('Original artwork is empty')
        rgba=rgba.crop(bounds)
        white=Image.new('RGBA',rgba.size,'white');white.alpha_composite(rgba)
        gray=ImageOps.grayscale(white.convert('RGB'))
        gray=gray.point([round(255*(v/255)**1.35) for v in range(256)])
        gray=ImageEnhance.Contrast(gray).enhance(1.25)
        height=round(26*203/25.4);width=round(height*rgba.width/rgba.height)
        gray=gray.resize((width,height),Image.Resampling.LANCZOS)
        mono=gray.convert('1',dither=Image.Dither.FLOYDSTEINBERG)
        mono.save(target,dpi=(203,203),optimize=True)
    return target

if __name__=='__main__':print(build())
