# app.py — FastAPI service for Warehouse ML-Only Optimizer
from __future__ import annotations

import os, io, time, threading
from typing import Any, Dict, List, Optional, Literal
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

# --- Load Config & Core ---
load_dotenv()
import core  # ML-Only core module (Part-Based)

app = FastAPI(title="Warehouse Relocation Recommender (ML-Only)")

# --- CORS ---
_env_allow_origins = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
ALLOW_ORIGINS = [o.strip() for o in _env_allow_origins.split(",") if o.strip()]
if not ALLOW_ORIGINS: ALLOW_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- UI Mount ---
DEFAULT_UI_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "warehouse-ui", "dist"))
FRONTEND_DIST = os.getenv("FRONTEND_DIST", DEFAULT_UI_PATH)
if os.path.isdir(FRONTEND_DIST) and os.path.isfile(os.path.join(FRONTEND_DIST, "index.html")):
    app.mount("/ui", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
    @app.get("/", include_in_schema=False)
    def root_index(): return RedirectResponse(url="/ui/")
else:
    @app.get("/", include_in_schema=False)
    def root_index(): return {"ok": True, "msg": "API ML-Only Running", "ui_path": FRONTEND_DIST}

# --- Global App State ---
class AppConfig:
    DATA_CSV: str = os.getenv("DATA_CSV", "C:\dev\WHO-project\project\data_SC1_BASE.csv")
    INVENTORY_CSV: str = os.getenv("INVENTORY_CSV", "C:\dev\WHO-project\project\inventory.csv")
    MAP_PATH: str = os.getenv("MAP_PATH", "warehouse_map.json")

APP_CFG = AppConfig()

# --- Cache ---
app.state.last_alloc = None
app.state.last_summary = None

# --- Helpers ---
def _stats():
    with core.ENGINE._lock:
        agg = core.ENGINE.state.get("agg")
        allocated = core.ENGINE.state.get("allocated")
    return {
        "n_parts": int(len(agg)) if agg is not None else 0,
        "allocated": bool(allocated)
    }

def _ensure_trained():
    with core.ENGINE._lock:
        if core.ENGINE.state.get("agg") is None:
            raise HTTPException(status_code=400, detail="Model not trained. Call /train first.")

def _df_json_safe(df: Optional[pd.DataFrame]) -> JSONResponse:
    if df is None: return JSONResponse(content={"ok": True, "rows": []})
    # Replace infinite/nan for JSON safety
    safe = df.replace([np.inf, -np.inf], np.nan).where(pd.notna(df), None)
    return JSONResponse(content={"ok": True, "rows": safe.to_dict(orient="records")})

# --- Pydantic Models ---
class ConfigPathsRequest(BaseModel):
    map_path: Optional[str] = None
    inventory_csv: Optional[str] = None
    data_csv: Optional[str] = None
    retrain: bool = False

class PathsOut(BaseModel):
    project_root: str
    data_csv: str
    inventory_csv: str
    map_path: str

class TrainRequest(BaseModel):
    csv_path: Optional[str] = None
    inventory_path: Optional[str] = None

class SummaryRequest(BaseModel):
    part_ids: Optional[List[str]] = None

class ExportRequest(BaseModel):
    kind: Literal["alloc", "summary"] = "alloc"
    fmt: Literal["csv", "json"] = "csv"
    filename: Optional[str] = None

# --- Endpoints ---

@app.get("/health")
def health():
    h = core.health()
    return {"status": "ok", **h, **_stats()}

@app.get("/config/paths", response_model=PathsOut)
def config_paths_get():
    return PathsOut(
        project_root=str(Path.cwd()),
        data_csv=APP_CFG.DATA_CSV,
        inventory_csv=APP_CFG.INVENTORY_CSV,
        map_path=APP_CFG.MAP_PATH
    )

@app.post("/config/paths")
def config_paths_set(req: ConfigPathsRequest):
    if req.map_path: APP_CFG.MAP_PATH = req.map_path
    if req.data_csv: APP_CFG.DATA_CSV = req.data_csv
    if req.inventory_csv: APP_CFG.INVENTORY_CSV = req.inventory_csv
    
    if req.retrain:
        if not os.path.exists(APP_CFG.DATA_CSV) or not os.path.exists(APP_CFG.INVENTORY_CSV):
             raise HTTPException(status_code=400, detail="Files not found for retrain.")
        try:
            core.train(APP_CFG.DATA_CSV, APP_CFG.INVENTORY_CSV)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Retrain failed: {e}")
            
    return {"ok": True, **_stats()}

@app.post("/train")
def train(req: TrainRequest):
    order_path = req.csv_path or APP_CFG.DATA_CSV
    inv_path = req.inventory_path or APP_CFG.INVENTORY_CSV
    
    if not os.path.isfile(order_path):
        raise HTTPException(status_code=404, detail=f"Order CSV not found: {order_path}")
    
    try:
        # Core now handles defaults if inv_path missing, but we pass it explicitly
        info = core.train(order_path, inv_path)
        return {"ok": True, "info": info, **_stats()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Train failed: {e}")

@app.get("/recommend")
def recommend(top_k: int = Query(default=5000, ge=1)): # <-- แก้เป็น 5000 หรือเลขเยอะๆ
    _ensure_trained()
    try:
        # Use core.get_recommendations
        df = core.get_recommendations(top_k=top_k)
        return _df_json_safe(df)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recommend failed: {e}")
    
@app.post("/allocate")
def allocate(top_k: int = Query(default=None)):
    _ensure_trained()
    try:
        info = core.run_allocator()
        
        # --- แก้บรรทัดนี้ครับ ---
        # เปลี่ยนจาก 1000 เป็น 5000 (หรือ 10000 เผื่ออนาคต)
        df = core.get_recommendations(top_k=top_k or 5000) 
        # ---------------------
        
        rows = df.to_dict(orient="records") if df is not None else []
        app.state.last_alloc = rows
        return {"ok": True, **info, "rows": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Allocate failed: {e}")
    
@app.post("/summary")
def summary(
    req: SummaryRequest = Body(default=SummaryRequest()),
    weight: str = Query(default="total"),
    top_k_skus: Optional[int] = Query(default=None, ge=1)
):
    _ensure_trained()
    try:
        # ดึงข้อมูลที่คำนวณเสร็จแล้วจาก Core
        with core.ENGINE._lock:
            df = core.ENGINE.state.get("agg")
            
        if df is None or len(df) == 0:
            return {"ok": False, "msg": "No data available"}

        # --- [NEW LOGIC] ดึงค่าที่คำนวณไว้แล้วมาตอบเลย (ไม่ต้อง Sim ใหม่) ---
        # 1. คำนวณผลรวมระยะทาง (Total Distance)
        total_before = df["Before_Dist_m"].sum()
        total_after = df["After_Dist_m"].sum()
        
        # 2. คำนวณระยะที่ประหยัดได้
        saving_m = total_before - total_after
        saving_pct = (saving_m / total_before * 100.0) if total_before > 0 else 0.0
        
        res = {
            "n_parts": int(len(df)),
            
            # ส่งค่าระยะทางรวม (หน่วยเมตร)
            "before_efficiency": float(total_before), 
            "after_efficiency": float(total_after),
            
            "reallocate_efficiency": float(saving_m),
            "saved_pct": float(saving_pct),
            
            "weight_used": "Graph Routing Distance (Meters)",
            "debug_top_k": "All Parts"
        }
        
        app.state.last_summary = res
        return {"ok": True, **res}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summary failed: {e}")
    
@app.post("/export")
def export_data(req: ExportRequest):
    src = app.state.last_alloc if req.kind == "alloc" else app.state.last_summary
    if src is None:
        raise HTTPException(status_code=400, detail=f"No {req.kind} data available.")
    
    if isinstance(src, list): df = pd.DataFrame(src)
    elif isinstance(src, dict): df = pd.DataFrame([src])
    else: raise HTTPException(status_code=500, detail="Invalid data format")

    filename = req.filename or f"export_{req.kind}_{int(time.time())}.{req.fmt}"
    path = os.path.join(os.getcwd(), filename)
    
    if req.fmt == "csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
        media = "text/csv"
    else:
        df.to_json(path, orient="records", force_ascii=False)
        media = "application/json"
        
    return FileResponse(path=path, media_type=media, filename=filename)