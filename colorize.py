"""Local monochrome suggestions and colour-only reconstruction, no API calls."""
import io
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps

def classify(path):
    with Image.open(path) as source:
        image=ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((256,256))
    rgb=np.asarray(image)
    hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV).astype(float)
    useful=(hsv[:,:,2]>25)&(hsv[:,:,2]<245)
    pixels=hsv[useful]
    if len(pixels)<100: pixels=hsv.reshape(-1,3)
    neutral=np.mean(pixels[:,1]<22)
    if neutral>.96: return 'black-and-white'
    chromatic=pixels[pixels[:,1]>=22]
    if len(chromatic):
        warm=np.mean((chromatic[:,0]>=3)&(chromatic[:,0]<=35))
        angles=chromatic[:,0]*np.pi/90
        coherence=abs(np.mean(np.exp(1j*angles)))
        if warm>.94 and coherence>.97: return 'sepia / warm monochrome (check)'
    return 'already colour / uncertain'

def candidates(folder, links):
    from stage_files import same_path
    items=[]
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {'.jpg','.jpeg','.png','.tif','.tiff','.bmp','.webp'}: continue
        combined=any(same_path(path, link['composite_path']) for link in links) or 'front-back' in path.stem.lower() or 'front and back' in path.stem.lower()
        resolved=str(path.resolve())
        try:
            kind=classify(path)
            items.append({'path':resolved,'kind':kind,'suggested':not combined and kind!='already colour / uncertain','combined':combined})
        except (OSError,ValueError): items.append({'path':resolved,'kind':'unreadable','suggested':False,'combined':combined})
    return items

def colour_only(source, generated):
    with Image.open(source) as original:
        original=ImageOps.exif_transpose(original).convert('RGB')
        rgb=np.asarray(original)
    with Image.open(io.BytesIO(generated)) as result:
        if abs((result.width/result.height)/(original.width/original.height)-1)>.05:
            raise ValueError('AI changed the aspect ratio; colour result was not used.')
        colour=np.asarray(result.convert('RGB').resize(original.size,Image.Resampling.LANCZOS))
    # Original L channel fixes spatial detail; AI supplies only a/b colour channels.
    lab=cv2.cvtColor(colour.astype(np.float32)/255,cv2.COLOR_RGB2LAB)
    lab[:,:,0]=cv2.cvtColor(rgb.astype(np.float32)/255,cv2.COLOR_RGB2LAB)[:,:,0]
    output=np.clip(cv2.cvtColor(lab,cv2.COLOR_LAB2RGB)*255,0,255).round().astype(np.uint8)
    stream=io.BytesIO();Image.fromarray(output).save(stream,'PNG')
    return stream.getvalue()
