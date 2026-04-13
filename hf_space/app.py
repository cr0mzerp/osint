import os
import subprocess
import logging
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="OSINTleaks Remote Search", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
MOUNT_PATH = os.environ.get("MOUNT_PATH", "/home/user/terabox_data")
SEARCH_TIMEOUT = int(os.environ.get("SEARCH_TIMEOUT", "300"))  # 5 dakika

# Pydantic modelleri
class SearchRequest(BaseModel):
    pattern: str = Field(..., description="Aranacak desen (regex destekli)")
    skill: Optional[str] = Field(None, description="Path keyword (örn: discord, email, wallet)")
    case_insensitive: bool = Field(True, description="Büyük/küçük harf duyarsız")
    max_results: int = Field(1000, description="Maksimum sonuç sayısı")
    file_types: Optional[List[str]] = Field(["txt"], description="Dosya türleri (örn: txt, csv, json)")

class SearchResponse(BaseModel):
    ok: bool
    matches: List[Dict[str, Any]]
    total: int
    search_time: float
    mount_status: str

class MountStatus(BaseModel):
    mounted: bool
    path: str
    files_count: int
    size_mb: float

# Yardımcı fonksiyonlar
def check_mount() -> bool:
    """Mount noktasının bağlı olup olmadığını kontrol et."""
    try:
        return os.path.ismount(MOUNT_PATH)
    except Exception as e:
        logger.error(f"Mount kontrol hatası: {e}")
        return False

def get_mount_stats() -> Dict[str, Any]:
    """Mount noktası istatistiklerini al."""
    try:
        if not check_mount():
            return {"mounted": False, "path": MOUNT_PATH, "files_count": 0, "size_mb": 0.0}
        
        files_count = 0
        total_size = 0
        
        for root, dirs, files in os.walk(MOUNT_PATH):
            files_count += len(files)
            for file in files:
                try:
                    total_size += os.path.getsize(os.path.join(root, file))
                except:
                    pass
        
        return {
            "mounted": True,
            "path": MOUNT_PATH,
            "files_count": files_count,
            "size_mb": round(total_size / (1024 * 1024), 2)
        }
    except Exception as e:
        logger.error(f"Mount istatistik hatası: {e}")
        return {"mounted": False, "path": MOUNT_PATH, "files_count": 0, "size_mb": 0.0}

def build_rg_command(pattern: str, skill: Optional[str], case_insensitive: bool, 
                     file_types: List[str], max_results: int) -> List[str]:
    """ripgrep komutunu oluştur."""
    cmd = ["rg", pattern, MOUNT_PATH]
    
    # Case insensitive
    if case_insensitive:
        cmd.append("-i")
    
    # File types
    for ext in file_types:
        cmd.extend(["-g", f"*.{ext}"])
    
    # Skill filter (path keyword)
    if skill:
        cmd.extend(["-g", f"*{skill}*"])
    
    # Output format (JSON)
    cmd.extend(["--json", "--no-heading", "--line-number"])
    
    # Max results
    cmd.extend(["-C", "0"])
    
    return cmd

# API Endpoint'leri
@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "OSINTleaks Remote Search",
        "mount_path": MOUNT_PATH,
        "mount_status": "mounted" if check_mount() else "not_mounted"
    }

@app.get("/mount/status", response_model=MountStatus)
async def get_mount_status():
    """Mount durumunu kontrol et."""
    return MountStatus(**get_mount_stats())

@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest, background_tasks: BackgroundTasks):
    """Mount noktasında arama yap."""
    # Mount kontrolü
    if not check_mount():
        raise HTTPException(status_code=503, detail="Mount noktası bağlı değil")
    
    # Komut oluştur
    cmd = build_rg_command(
        pattern=request.pattern,
        skill=request.skill,
        case_insensitive=request.case_insensitive,
        file_types=request.file_types,
        max_results=request.max_results
    )
    
    logger.info(f"Arama başlatılıyor: {' '.join(cmd)}")
    
    try:
        # Ripgrep çalıştır
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SEARCH_TIMEOUT
        )
        
        # Sonuçları parse et
        matches = []
        for line in result.stdout.split('\n'):
            if not line.strip():
                continue
            
            try:
                # JSON parse (rg --json output)
                import json
                data = json.loads(line)
                
                if data.get("type") == "match":
                    match = {
                        "path": data.get("data", {}).get("path", {}).get("text", ""),
                        "line": data.get("data", {}).get("line_number", 0),
                        "content": data.get("data", {}).get("lines", {}).get("text", ""),
                        "bytes_offset": data.get("data", {}).get("absolute_offset", 0)
                    }
                    matches.append(match)
                    
                    # Max results limit
                    if len(matches) >= request.max_results:
                        break
            except json.JSONDecodeError:
                # JSON değilse, ham satır olarak ekle
                matches.append({"raw": line})
        
        return SearchResponse(
            ok=True,
            matches=matches,
            total=len(matches),
            search_time=0.0,  # Gerçek zaman hesaplanabilir
            mount_status="mounted"
        )
        
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail=f"Arama zaman aşımı ({SEARCH_TIMEOUT}s)")
    except Exception as e:
        logger.error(f"Arama hatası: {e}")
        raise HTTPException(status_code=500, detail=f"Arama hatası: {str(e)}")

@app.get("/health")
async def health():
    """Health check endpoint."""
    mount_stats = get_mount_stats()
    return {
        "status": "healthy" if mount_stats["mounted"] else "degraded",
        "mount": mount_stats,
        "rg_available": True
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
