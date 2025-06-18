# requirements.txt
"""
fastapi==0.104.1
uvicorn[standard]==0.24.0
aiofiles==23.2.0
redis==5.0.1
asyncio-throttle==1.0.2
cryptography==41.0.8
pydantic==2.5.0
python-multipart==0.0.6
"""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
import uuid
import redis
import json
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiofiles
import uvicorn

# Configuration
class Config:
    CHUNK_SIZE = 50 * 1024 * 1024  # 50MB chunks
    MAX_FILE_SIZE = 20 * 1024 * 1024 * 1024  # 20GB
    TEMP_DIR = Path("/tmp/uploads")  # Use SSD-backed storage
    FINAL_DIR = Path("/storage/files")
    REDIS_URL = "redis://localhost:6379"
    UPLOAD_TIMEOUT = 3600  # 1 hour
    MAX_CONCURRENT_UPLOADS = 10

config = Config()
app = FastAPI(title="Optimized File Upload API")

# CORS for browser clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redis for session management
redis_client = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)

# Pydantic models
class UploadSession(BaseModel):
    upload_id: str
    filename: str
    total_size: int
    total_chunks: int
    uploaded_chunks: List[int] = []
    created_at: datetime
    expires_at: datetime

class ChunkInfo(BaseModel):
    chunk_index: int
    checksum: str
    size: int

class FinalizeRequest(BaseModel):
    upload_id: str
    filename: str
    original_checksum: str

# Utility functions
async def get_upload_session(upload_id: str) -> Optional[UploadSession]:
    """Retrieve upload session from Redis"""
    session_data = redis_client.get(f"upload:{upload_id}")
    if session_data:
        return UploadSession.parse_raw(session_data)
    return None

async def save_upload_session(session: UploadSession):
    """Save upload session to Redis with expiration"""
    redis_client.setex(
        f"upload:{session.upload_id}",
        config.UPLOAD_TIMEOUT,
        session.json()
    )

async def calculate_file_checksum(file_path: Path) -> str:
    """Calculate SHA-256 checksum of file"""
    sha256_hash = hashlib.sha256()
    async with aiofiles.open(file_path, "rb") as f:
        while chunk := await f.read(8192):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()

async def cleanup_temp_files(upload_id: str):
    """Clean up temporary chunk files"""
    temp_pattern = config.TEMP_DIR / f"{upload_id}_*.part"
    for chunk_file in config.TEMP_DIR.glob(f"{upload_id}_*.part"):
        try:
            chunk_file.unlink()
        except OSError:
            pass

# API Endpoints
@app.post("/initiate-upload")
async def initiate_upload(
    filename: str,
    total_size: int,
    chunk_size: int = config.CHUNK_SIZE
):
    """Initialize a new upload session"""
    if total_size > config.MAX_FILE_SIZE:
        raise HTTPException(400, "File too large")
    
    upload_id = str(uuid.uuid4())
    total_chunks = (total_size + chunk_size - 1) // chunk_size
    
    session = UploadSession(
        upload_id=upload_id,
        filename=filename,
        total_size=total_size,
        total_chunks=total_chunks,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(seconds=config.UPLOAD_TIMEOUT)
    )
    
    await save_upload_session(session)
    
    # Ensure temp directory exists
    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    
    return {
        "upload_id": upload_id,
        "total_chunks": total_chunks,
        "chunk_size": chunk_size
    }

@app.post("/upload-chunk")
async def upload_chunk(
    upload_id: str,
    chunk_index: int,
    file: UploadFile = File(...),
    checksum: Optional[str] = None
):
    """Upload a single chunk"""
    session = await get_upload_session(upload_id)
    if not session:
        raise HTTPException(404, "Upload session not found")
    
    if chunk_index >= session.total_chunks:
        raise HTTPException(400, "Invalid chunk index")
    
    if chunk_index in session.uploaded_chunks:
        return {"status": "already_uploaded", "chunk_index": chunk_index}
    
    # Save chunk to temp file
    chunk_path = config.TEMP_DIR / f"{upload_id}_{chunk_index:06d}.part"
    
    try:
        # Stream write with checksum validation
        sha256_hash = hashlib.sha256()
        chunk_size = 0
        
        async with aiofiles.open(chunk_path, "wb") as f:
            while chunk := await file.read(8192):
                await f.write(chunk)
                sha256_hash.update(chunk)
                chunk_size += len(chunk)
        
        # Validate checksum if provided
        calculated_checksum = sha256_hash.hexdigest()
        if checksum and checksum != calculated_checksum:
            chunk_path.unlink()  # Remove corrupted chunk
            raise HTTPException(400, "Chunk checksum mismatch")
        
        # Update session
        session.uploaded_chunks.append(chunk_index)
        await save_upload_session(session)
        
        return {
            "status": "uploaded",
            "chunk_index": chunk_index,
            "checksum": calculated_checksum,
            "size": chunk_size,
            "progress": len(session.uploaded_chunks) / session.total_chunks
        }
        
    except Exception as e:
        if chunk_path.exists():
            chunk_path.unlink()
        raise HTTPException(500, f"Upload failed: {str(e)}")

@app.get("/upload-status/{upload_id}")
async def get_upload_status(upload_id: str):
    """Get current upload progress"""
    session = await get_upload_session(upload_id)
    if not session:
        raise HTTPException(404, "Upload session not found")
    
    missing_chunks = [
        i for i in range(session.total_chunks) 
        if i not in session.uploaded_chunks
    ]
    
    return {
        "upload_id": upload_id,
        "total_chunks": session.total_chunks,
        "uploaded_chunks": len(session.uploaded_chunks),
        "missing_chunks": missing_chunks,
        "progress": len(session.uploaded_chunks) / session.total_chunks,
        "expires_at": session.expires_at
    }

@app.post("/finalize-upload")
async def finalize_upload(
    request: FinalizeRequest,
    background_tasks: BackgroundTasks
):
    """Assemble chunks into final file"""
    session = await get_upload_session(request.upload_id)
    if not session:
        raise HTTPException(404, "Upload session not found")
    
    # Check all chunks are uploaded
    if len(session.uploaded_chunks) != session.total_chunks:
        missing = set(range(session.total_chunks)) - set(session.uploaded_chunks)
        raise HTTPException(400, f"Missing chunks: {list(missing)}")
    
    # Ensure final directory exists
    config.FINAL_DIR.mkdir(parents=True, exist_ok=True)
    final_path = config.FINAL_DIR / request.filename
    
    try:
        # Assemble file using efficient concatenation
        async with aiofiles.open(final_path, "wb") as final_file:
            for chunk_idx in range(session.total_chunks):
                chunk_path = config.TEMP_DIR / f"{request.upload_id}_{chunk_idx:06d}.part"
                
                if not chunk_path.exists():
                    raise HTTPException(500, f"Chunk {chunk_idx} not found")
                
                async with aiofiles.open(chunk_path, "rb") as chunk_file:
                    while chunk := await chunk_file.read(1024 * 1024):  # 1MB buffer
                        await final_file.write(chunk)
        
        # Verify final file checksum
        final_checksum = await calculate_file_checksum(final_path)
        if request.original_checksum and final_checksum != request.original_checksum:
            final_path.unlink()
            raise HTTPException(400, "Final file checksum mismatch")
        
        # Clean up in background
        background_tasks.add_task(cleanup_temp_files, request.upload_id)
        background_tasks.add_task(lambda: redis_client.delete(f"upload:{request.upload_id}"))
        
        return {
            "status": "completed",
            "filename": request.filename,
            "size": final_path.stat().st_size,
            "checksum": final_checksum,
            "path": str(final_path)
        }
        
    except Exception as e:
        # Clean up on failure
        if final_path.exists():
            final_path.unlink()
        raise HTTPException(500, f"Assembly failed: {str(e)}")

@app.delete("/upload/{upload_id}")
async def cancel_upload(upload_id: str, background_tasks: BackgroundTasks):
    """Cancel upload and clean up"""
    session = await get_upload_session(upload_id)
    if not session:
        raise HTTPException(404, "Upload session not found")
    
    # Clean up in background
    background_tasks.add_task(cleanup_temp_files, upload_id)
    background_tasks.add_task(lambda: redis_client.delete(f"upload:{upload_id}"))
    
    return {"status": "cancelled", "upload_id": upload_id}

# Health check
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "temp_dir": str(config.TEMP_DIR),
        "final_dir": str(config.FINAL_DIR),
        "redis_connected": redis_client.ping()
    }

# Startup/shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize directories and validate configuration"""
    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    config.FINAL_DIR.mkdir(parents=True, exist_ok=True)
    
    # Test Redis connection
    try:
        redis_client.ping()
        print("✅ Redis connection established")
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        raise

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    redis_client.close()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        workers=4,  # Adjust based on CPU cores
        loop="uvloop",  # Faster event loop
        http="httptools",  # Faster HTTP parser
        access_log=False,  # Disable for performance
        reload=False
    )
