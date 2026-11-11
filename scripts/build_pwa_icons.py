"""Render the supplied brand SVG exactly; add safe white padding for Android masks.

Development-only tool: requires Inkscape, not an application runtime dependency.
"""
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SVG = 'http://www.w3.org/2000/svg'


def render(source, target, size):
    subprocess.run(['inkscape', str(source), '--export-type=png', f'--export-filename={target}',
        f'--export-width={size}', f'--export-height={size}'], check=True)


def main():
    original = ROOT / 'static/pixiepinks-logo.svg'
    for size in (192, 512):
        render(original, ROOT / f'static/pwa-icon-{size}.png', size)
    outer = ET.Element(f'{{{SVG}}}svg', {'width': '1024', 'height': '1024', 'viewBox': '0 0 1024 1024'})
    ET.SubElement(outer, f'{{{SVG}}}rect', {'width': '1024', 'height': '1024', 'fill': '#fff'})
    logo = ET.parse(original).getroot()
    # This supplied artwork fits inside the central safe circle at 70% scale
    # (verified against the rendered non-white pixels; retain its original layout).
    logo.set('x', '154'); logo.set('y', '154'); logo.set('width', '716'); logo.set('height', '716')
    outer.append(logo)
    with tempfile.TemporaryDirectory(prefix='pixiepinks-icons-') as folder:
        padded = Path(folder) / 'maskable.svg'
        ET.ElementTree(outer).write(padded, encoding='utf-8', xml_declaration=True)
        render(padded, ROOT / 'static/pwa-icon-maskable-512.png', 512)


if __name__ == '__main__':
    main()
