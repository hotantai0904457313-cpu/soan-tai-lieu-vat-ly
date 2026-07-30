"""Thăm dò: datalab CDN có hỗ trợ resume không + model có trên HuggingFace không."""
import requests

URL = "https://models.datalab.to/text_recognition/2025_09_23/model.safetensors"

print("=== TEST 1: datalab CDN range/resume support ===")
try:
    h = requests.head(URL, timeout=30, allow_redirects=True)
    print(f"HEAD status: {h.status_code}")
    print(f"Accept-Ranges: {h.headers.get('Accept-Ranges')}")
    print(f"Content-Length: {h.headers.get('Content-Length')}")
except Exception as e:
    print(f"HEAD error: {e}")

# Try a real ranged GET (bytes 0-1023)
try:
    r = requests.get(URL, headers={"Range": "bytes=0-1023"}, timeout=30, stream=True)
    print(f"Ranged GET status: {r.status_code} (206 = resume supported)")
    print(f"Content-Range: {r.headers.get('Content-Range')}")
    r.close()
except Exception as e:
    print(f"Ranged GET error: {e}")

print()
print("=== TEST 2: HuggingFace repo names ===")
candidates = [
    "datalab-to/text_recognition",
    "datalab-to/surya_rec2",
    "datalab-to/surya_layout",
    "vikp/surya_rec2",
    "datalab-to/layout",
]
for repo in candidates:
    try:
        r = requests.get(f"https://huggingface.co/api/models/{repo}", timeout=20)
        print(f"{repo}: {r.status_code}")
    except Exception as e:
        print(f"{repo}: ERR {e}")
