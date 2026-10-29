"""Local visual pair suggestions and durable, explicitly approved links."""
import json
from pathlib import Path
import threading
import io
import warnings
import numpy as np
from PIL import Image, ImageOps
from chatgpt_restore import reassemble, file_accepted
from app_settings import default_data_dir

ROOT = default_data_dir()
LOCK = threading.Lock()

def read_links(folder):
    path = ROOT / 'photo-links.json'
    records = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return [item for item in records.values() if Path(item['front_path']).parent == folder.resolve()]

def approve_link(front, composite, split, layout):
    if front == composite or front.parent != composite.parent:
        raise ValueError('Choose two different images in the same folder.')
    if not 10 <= int(split) <= 90 or layout not in ('right', 'below'):
        raise ValueError('Choose a valid split boundary and back position.')
    with LOCK:
        ROOT.mkdir(parents=True, exist_ok=True)
        path = ROOT / 'photo-links.json'
        records = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if any(v['composite_path'] == str(front) or (v['front_path'] == str(composite)) for v in records.values()):
            raise ValueError('A file cannot be both a standalone front and a combined image in different links.')
        if any(v['composite_path'] == str(composite) and k != str(front) for k,v in records.items()):
            raise ValueError('This combined image is already linked to another front.')
        record = dict(front_path=str(front), composite_path=str(composite), split=int(split), layout=layout)
        records[str(front)] = record
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(records), encoding='utf-8')
        temporary.replace(path)
    return record

def suggestions(folder):
    images = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in {'.jpg','.jpeg','.png','.tif','.tiff','.webp','.bmp'}: continue
        try:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert('L')
                image.thumbnail((320,320))
                images.append((path, image.copy()))
        except OSError: continue
    def signature(image):
        values = np.asarray(image.resize((40,40)), dtype=float).flatten()
        values -= values.mean()
        return values / max(np.linalg.norm(values), 1)
    fronts = [(path, image.width/image.height, signature(image)) for path,image in images]
    results = []
    for composite, image in images:
        best = None
        for layout in ('right','below'):
            for split in range(30,71,5):
                edge = round((image.width if layout == 'right' else image.height)*split/100)
                crop = image.crop((0,0,edge,image.height) if layout == 'right' else (0,0,image.width,edge))
                aspect = crop.width/crop.height
                vector = signature(crop)
                for front, ratio, reference in fronts:
                    if front == composite or abs(np.log(ratio/aspect)) > .25: continue
                    score = float(reference @ vector)
                    if score >= .75 and (best is None or score > best['score']):
                        best = dict(front_path=str(front), composite_path=str(composite), split=split, layout=layout, score=round(score,3))
        if best: results.append(best)
    return sorted(results, key=lambda item: -item['score'])

def accept_linked(record, png):
    link = record.get('link')
    operation=record.get('operation','restore')
    if not link: return file_accepted(Path(record['front_path']), png, operation=operation)
    composite = Path(link['composite_path'])
    combined = reassemble(png, composite, link['split'], link['layout'])
    outputs = file_accepted(Path(record['front_path']), png, composite, combined, operation=operation)
    try:
        approve_link(outputs[0], outputs[1], link['split'], link['layout'])
    except (OSError, ValueError) as error:
        warnings.warn(f'Images saved, but output pair link could not be carried forward: {error}')
    return outputs
