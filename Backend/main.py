from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import aiofiles
import os
import hashlib

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CHUNK_DIR = Path("temp_chunks")
CHUNK_DIR.mkdir(exist_ok=True)

@app.post("/upload-chunk")
async def upload_chunk(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    file: UploadFile = File(...)
):
    chunk_path = CHUNK_DIR / f"{upload_id}_{chunk_index}.part"
    async with aiofiles.open(chunk_path, "wb") as out_file:
        content = await file.read()
        await out_file.write(content)
    return {"status": "ok", "chunk": chunk_index}

@app.post("/finalize-upload")
async def finalize_upload(upload_id: str = Form(...), filename: str = Form(...), original_checksum: str = Form(...)):
    output_path = Path(f"uploads/{filename}")
    os.makedirs(output_path.parent, exist_ok=True)
    with open(output_path, "wb") as output_file:
        for i in range(10000):  # Max 10000 chunks
            chunk_file = CHUNK_DIR / f"{upload_id}_{i}.part"
            if not chunk_file.exists():
                break
            with open(chunk_file, "rb") as cf:
                output_file.write(cf.read())
            os.remove(chunk_file)

    sha256 = hashlib.sha256()
    with open(output_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    computed_checksum = sha256.hexdigest()

    if computed_checksum != original_checksum:
        output_path.unlink(missing_ok=True)
        return {"status": "error", "detail": "Checksum mismatch"}

    return {"status": "complete", "file": filename}
