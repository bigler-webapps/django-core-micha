"""Perceptual difference hashes for duplicate-image warnings."""
from io import BytesIO
from PIL import Image

def perceptual_hash(content):
    try:
        if len(content)>=12 and content[4:8]==b"ftyp":
            import pillow_heif
            pillow_heif.register_heif_opener()
        with Image.open(BytesIO(content)) as im:
            pixels=list(im.convert("L").resize((9,8)).tobytes());bits=0
            for y in range(8):
                for x in range(8):bits=(bits<<1)|(pixels[y*9+x]>pixels[y*9+x+1])
            return f"{bits:016x}"
    except Exception:return ""

def hamming_distance(a,b):return None if not a or not b else (int(a,16)^int(b,16)).bit_count()
def is_probable_duplicate(a,b,threshold=5):
    d=hamming_distance(a,b);return d is not None and d<=threshold

