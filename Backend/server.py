from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os, aiofiles, hashlib

app = FastAPI()

UPLOAD_DIR = "temp_chunks"
FINAL_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(FINAL_DIR, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/upload-chunk")
async def upload_chunk(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    file: UploadFile = Form(...)
):
    filename = f"{upload_id}_{chunk_index}.part"
    file_path = os.path.join(UPLOAD_DIR, filename)
    async with aiofiles.open(file_path, 'wb') as out_file:
        content = await file.read()
        await out_file.write(content)
    return {"status": "ok", "chunk_index": chunk_index}

@app.post("/finalize-upload")
async def finalize_upload(upload_id: str = Form(...), filename: str = Form(...)):
    final_path = os.path.join(FINAL_DIR, filename)
    with open(final_path, 'wb') as output_file:
        index = 0
        while True:
            chunk_path = os.path.join(UPLOAD_DIR, f"{upload_id}_{index}.part")
            if not os.path.exists(chunk_path):
                break
            with open(chunk_path, 'rb') as chunk_file:
                output_file.write(chunk_file.read())
            os.remove(chunk_path)
            index += 1
    return {"status": "complete", "filename": filename}
