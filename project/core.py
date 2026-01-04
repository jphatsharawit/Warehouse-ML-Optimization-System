# core.py — Warehouse Relocation (Part-Based Adaptation)
# Mode: Full Logic (Allocation + Gravity + ML + Routing)
# Note: Allocation Logic Preserved, Data Source Adapted.

from __future__ import annotations

import os
import json
import math
import threading
import logging
import itertools
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple, Set, Optional, Union

import numpy as np
import pandas as pd

# ---- Logging Setup ----
# Best practice: อย่า override logging ของโปรเจกต์หลักถ้ามี handler อยู่แล้ว
_root_logger = logging.getLogger()
if not _root_logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---- ML Dependency Check ----
try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.cluster import KMeans
    from sklearn.ensemble import RandomForestClassifier
    _HAS_SKLEARN = True
except ImportError:
    logger.critical("Missing 'scikit-learn'. ML features will fail.")
    # ไม่ Raise Error ทันที เพื่อให้ส่วนอื่นยังพอทำงานได้ แต่ฟีเจอร์ ML จะถูกปิด
    _HAS_SKLEARN = False

# ---------------------- Config ----------------------
@dataclass
class Config:
    """Centralized configuration."""
    random_state: int = int(os.getenv("RANDOM_STATE", "42"))

    # Geometry (Synced with Generator)
    base_to_first_aisle_m: float = 12.0
    per_aisle_m: float = 1.6
    per_bin_m: float = 0.6
    level_alpha_m: float = 0.4
    level_beta_m: float = 0.4

    # Logic Toggles
    use_blended_distance: bool = False  # Generator uses Manhattan logic mostly
    blend_w: float = 1.0
    gamma_calib_sample: int = 400

    # Constraints
    levels_per_bin: int = 7
    slot_pad: int = 2
    # ใช้ field(default_factory) เพื่อป้องกัน mutable default argument issue
    tier_capacity: Dict[str, int] = field(default_factory=dict)
    default_tier: str = "medium"

    # ML & Logic
    slots_all_top_k: int = 24
    recent_days: int = 30
    front_factor: float = 1.2

    # Logic Toggles (Default ON)
    after_equalize: bool = True
    use_pair_anchor: bool = True

    # [Improvement] Dynamic Map Limits (Defaults)
    max_aisles: int = 34
    max_bins_per_aisle: int = 60

    # [Improvement] Optional security root (ถ้าเอาไปใช้หลัง API)
    data_root: str = os.getenv("DATA_ROOT", "").strip()  # ถ้าว่าง = ไม่บังคับ

# Init Config & Default Capacities
CFG = Config()
_DEFAULT_TIER_CAP = {
    "small": 800,
    "medium": 120,
    "large": 30,      # ตรงกับ Flat Pack Panels
    "long": 40,       # ตรงกับ Beams, Rails
    "furniture": 30,  # Backward Compatibility
    "big": 40         # Backward Compatibility
}
CFG.tier_capacity = dict(_DEFAULT_TIER_CAP)

# NOTE: ถ้าอยาก deterministic แบบ thread-safe แนะนำย้ายไปใช้ rng = np.random.default_rng(...)
np.random.seed(CFG.random_state)

# Input Variants Mapping (Centralized)
INPUT_VARIANTS = {
    "Part_ID": ["part_id", "partid", "part_no", "partno", "part_code", "item_code", "item_id", "sku", "material"],
    "Order_ID": ["order_id", "orderid", "order_no", "orderno", "so", "so_no", "sales_order", "doc_no", "invoice"],
    "Order_Date": ["order_date", "date", "created_at", "time", "timestamp", "doc_date"],
    "Quantity_Sold": ["quantity_sold", "qty_sold", "quantity", "qty", "amount", "total_qty", "units", "sales_qty"],
    "Slot_Stock": ["slot_stock", "stock", "stock_qty", "on_hand", "balance", "inventory", "qty_available"],
    "Distance_to_Packing": ["distance_to_packing", "distance", "dist", "path_len", "length", "meters", "m"],
    "Aisle": ["aisle", "zone", "room", "area", "row", "bay", "lock"],
    "level": ["level", "level", "shelf"],
    "Bin": ["bin", "slot", "column", "pos", "position"],
    "Part_Name": ["part_name", "description", "desc", "name", "product_name", "material_name"]
}

# --- Sentinels ---
# ใช้ 999 เป็น “ไม่ย้าย/ไม่มีที่ลง” ให้สอดคล้องกับ downstream ที่เช็ค 999
OVERFLOW_SLOT: Tuple[int, int, int] = (999, 999, 999)

# ---------------------- Warehouse Engine Class ----------------------
class WarehouseEngine:
    """
    Core Engine: Handles Geometry, ML Training, Allocation, and Routing Logic.
    Thread-safe implementation.
    """
    def __init__(self):
        self._lock = threading.RLock()

        # Reserved slots ต้อง lock แยก เพราะถูกแตะระหว่างทำ equalize หลายครั้ง
        self._reserved_lock = threading.Lock()
        self._reserved_slots: Set[Tuple[int, int, int]] = set()

        # Cache bins per aisle เพื่อลดการ lookup map ซ้ำ ๆ
        self._bins_cache: Dict[int, int] = {}

        self.state: Dict[str, Any] = {
            "df": None, "agg": None, "reco": None,
            "model": None, "allocated": False, "slot_loads": None
        }
        self.map_data = {}
        self.aisles_data = {}
        self.spine_x = 0.0
        self.pack_y = 0.0
        self._gamma = 1.0

        self._load_map()

    # =========================================================
    # 0. SMALL UTILITIES (I/O + VALIDATION)
    # =========================================================
    def _resolve_path(self, p: str) -> str:
        """
        [Security Optional] ถ้าตั้ง DATA_ROOT จะบังคับให้ไฟล์อยู่ใต้โฟลเดอร์นั้น
        เพื่อลดความเสี่ยง path traversal เมื่อเอาไปใช้หลัง API
        """
        if not p:
            return p
        pp = Path(p)

        # ถ้าไม่ได้ตั้ง data_root ก็ปล่อยผ่าน
        if not CFG.data_root:
            return str(pp)

        root = Path(CFG.data_root).resolve()
        rp = pp.resolve()
        if root not in rp.parents and rp != root:
            raise PermissionError(f"Path not allowed (outside DATA_ROOT): {rp}")
        return str(rp)

    @staticmethod
    def _read_csv_safe(path: str) -> pd.DataFrame:
        """
        Read CSV with sensible defaults + encoding fallback.
        - utf-8-sig รองรับไฟล์จาก Excel/Windows ได้ดี
        """
        last_err = None
        for enc in ("utf-8-sig", "utf-8"):
            try:
                return pd.read_csv(path, encoding=enc, low_memory=False)
            except Exception as e:
                last_err = e
        raise RuntimeError(f"Cannot read CSV: {path} ({last_err})")

    @staticmethod
    def _ensure_cols(df: pd.DataFrame, cols: List[str], ctx: str, optional: bool = False) -> None:
        missing = [c for c in cols if c not in df.columns]
        if missing and not optional:
            raise ValueError(f"{ctx} missing columns: {missing}")
        if missing and optional:
            logger.warning(f"⚠️ {ctx} missing optional columns: {missing}")

    @staticmethod
    def _coerce_numeric(df: pd.DataFrame, cols: List[str]) -> None:
        for c in cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

    def _release_reserved(self, slots: List[Tuple[int, int, int]]) -> None:
        """คืน reserved slots เมื่อแผน After ถูก reject (เช่น ย้ายแล้วแย่ลง)"""
        if not slots:
            return
        with self._reserved_lock:
            for s in slots:
                self._reserved_slots.discard(s)

    # =========================================================
    # 1. INITIALIZATION & I/O
    # =========================================================
    def _load_map(self):
        """Loads warehouse map JSON safely with error handling."""
        env_path = os.getenv("MAP_PATH", "warehouse_map.json")
        try:
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    self.map_data = json.load(f) or {}

                # --- Map Parsing Logic ---
                raw_aisles = self.map_data.get("aisles", {})
                if isinstance(raw_aisles, list):
                    self.aisles_data = {}
                    for i, item in enumerate(raw_aisles):
                        if isinstance(item, dict):
                            key = str(item.get("id") or item.get("name") or f"A{i+1}")
                            self.aisles_data[key] = item
                else:
                    self.aisles_data = raw_aisles if isinstance(raw_aisles, dict) else {}

                meta = self.map_data.get("meta", {})
                self.spine_x = float(meta.get("spine_x", 0.0))
                # Fallback to 0.0 if pack_main is missing
                self.pack_y = float(meta.get("pack_main", {}).get("y", 0.0))

                # Dynamic Limits Detection
                self._detect_map_limits()
            else:
                logger.warning(f"Map file not found at {env_path}. Using defaults.")
        except Exception as e:
            logger.error(f"Failed to load map: {e}")
            self.map_data = {}
            self.aisles_data = {}

    def _detect_map_limits(self):
        """Helper to set CFG.max_aisles based on map data."""
        if self.aisles_data:
            try:
                keys_nums = []
                for k in self.aisles_data.keys():
                    # Robust extraction of numbers from "A1", "Aisle 05", "1"
                    nums = [int(s) for s in str(k).split() if s.isdigit()]
                    if not nums:
                        clean = str(k).lower().replace('aisle', '').replace('a', '')
                        clean = clean.strip()
                        if clean.isdigit():
                            nums = [int(clean)]
                    if nums:
                        keys_nums.append(nums[0])

                if keys_nums:
                    CFG.max_aisles = max(keys_nums)
                    logger.info(f" Auto-detected Max Aisles: {CFG.max_aisles}")
            except Exception:
                # อย่าพังเพราะ map แปลก ๆ
                pass

    def _sanitize_columns(self, df: pd.DataFrame, file_type: str) -> pd.DataFrame:
        """
        [God Mode] Auto-renames columns to match internal standard (INPUT_VARIANTS).
        """
        # 1. Basic Cleanup
        df.columns = [str(c).strip() for c in df.columns]

        def normalize(text: str) -> str:
            return str(text).lower().replace("_", "").replace("-", "").replace(" ", "")

        current_cols_norm = {c: normalize(c) for c in df.columns}

        required: List[str] = []
        if file_type == "order":
            required = ["Part_ID", "Order_ID", "Quantity_Sold"]
        elif file_type == "inventory":
            required = ["Part_ID", "Slot_Stock", "Distance_to_Packing"]

        for target_col, variants in INPUT_VARIANTS.items():
            if target_col in df.columns:
                continue

            target_variants_norm = set(normalize(v) for v in variants)
            found_match = None
            for original_col, norm_col in current_cols_norm.items():
                if norm_col in target_variants_norm:
                    found_match = original_col
                    break

            if found_match:
                logger.info(f" Auto-Fix: Renamed '{found_match}' -> '{target_col}'")
                df.rename(columns={found_match: target_col}, inplace=True)
                current_cols_norm = {c: normalize(c) for c in df.columns}

        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.error(f" {file_type.capitalize()} CSV Missing: {missing}")
            raise ValueError(f"{file_type.capitalize()} CSV missing columns: {missing}")

        return df

    def run_simulation_comparison(self, order_path: str) -> Dict[str, float]:
        """
        อ่านไฟล์ Order แล้วจำลองการเดินจริงๆ (Start->A->B->End) เทียบกันระหว่าง
        1. Before: ตำแหน่งเดิมในไฟล์ CSV
        2. After: ตำแหน่งใหม่ที่ AI แนะนำ
        """
        if self.state["agg"] is None:
            return {"before_m": 0.0, "after_m": 0.0, "saving_pct": 0.0}

        try:
            order_path = self._resolve_path(order_path)

            # 1. เตรียมข้อมูล
            df = self._read_csv_safe(order_path)
            df = self._sanitize_columns(df, "order")

            # simulation ต้องมีตำแหน่งด้วย
            self._ensure_cols(df, ["Aisle", "level", "Bin"], ctx="Order CSV (for simulation)", optional=False)

            # 2. สร้างแผนที่ตำแหน่งใหม่ (Part_ID -> Slot ใหม่)
            new_slot_map: Dict[str, Tuple[int, int, int]] = {}
            for row in self.state["agg"].itertuples(index=False):
                # เอาเฉพาะที่มีที่ลงจริง
                if getattr(row, "After_Aisle", 999) != 999:
                    pid = str(getattr(row, "Part_ID"))
                    new_slot_map[pid] = (
                        int(getattr(row, "After_Aisle")),
                        int(getattr(row, "After_level")),
                        int(getattr(row, "After_Bin")),
                    )

            total_dist_before = 0.0
            total_dist_after = 0.0

            # 3. วนลูปคำนวณทีละ Order
            for _, group in df.groupby("Order_ID"):

                # --- A. จำลองการเดินแบบเก่า (Before) ---
                before_path: List[Tuple[int, int, int]] = []
                sorted_group = group.sort_values(["Aisle", "Bin", "level"])
                for r in sorted_group.itertuples(index=False):
                    before_path.append((int(r.Aisle), int(r.level), int(r.Bin)))
                total_dist_before += self.calculate_path_distance(before_path)

                # --- B. จำลองการเดินแบบใหม่ (After) ---
                after_path: List[Tuple[int, int, int]] = []
                for r in group.itertuples(index=False):
                    pid = str(getattr(r, "Part_ID"))
                    if pid in new_slot_map:
                        after_path.append(new_slot_map[pid])
                    else:
                        after_path.append((int(r.Aisle), int(r.level), int(r.Bin)))

                after_path.sort(key=lambda x: (x[0], x[2], x[1]))  # Aisle, Bin, level
                total_dist_after += self.calculate_path_distance(after_path)

            saving_pct = ((total_dist_before - total_dist_after) / total_dist_before * 100.0) if total_dist_before > 0 else 0.0
            return {"before_m": float(total_dist_before), "after_m": float(total_dist_after), "saving_pct": float(saving_pct)}

        except Exception as e:
            logger.error(f"Simulation Failed: {e}")
            return {"before_m": 0.0, "after_m": 0.0, "saving_pct": 0.0}

    # =========================================================
    # 2. GEOMETRY HELPER (DO NOT DELETE)
    # =========================================================
    def _slot_str_from_row(self, r) -> str:
        return self.slot_str(r.get("Aisle", 0), r.get("level", 0), r.get("Bin", 0))

    def _get_coords(self, a: int, r: int, b: int) -> Tuple[float, float, int]:
        """Convert Grid (A, R, B) to Physical Coordinates (X, Y, Z_Penalty)."""
        dx = CFG.base_to_first_aisle_m + (int(a) - 1) * CFG.per_aisle_m
        dy = (int(b) - 1) * CFG.per_bin_m
        return float(dx), float(dy), int(r)

    def _calc_dist_l1_l2(self, a, r, b) -> Tuple[float, float]:
        dx, dy, rr = self._get_coords(a, r, b)
        level_penalty = max(0, rr - 1) * CFG.level_alpha_m
        l1 = dx + dy + level_penalty
        l2 = math.hypot(dx, dy) + level_penalty
        return l1, l2

    def slot_distance(self, a: int, r: int, b: int) -> float:
        """Calculate weighted distance from Packing Station to Slot."""
        a, r, b = self.clamp_slot(a, r, b)
        l1, l2 = self._calc_dist_l1_l2(a, r, b)

        if not CFG.use_blended_distance:
            return l1

        g = max(self._gamma, 1e-9)
        return (CFG.blend_w * l1) + ((1.0 - CFG.blend_w) * (l2 / g))

    def bins_in_aisle(self, a: int) -> int:
        """Returns bin count for a specific aisle, with safety fallbacks."""
        aa = int(a)
        if aa in self._bins_cache:
            return self._bins_cache[aa]

        SAFE_DEFAULT = int(CFG.max_bins_per_aisle)  # ใช้ค่า CFG เป็น base
        val = SAFE_DEFAULT

        if self.aisles_data:
            key_variants = [f"A{aa}", str(aa)]
            for key in key_variants:
                data = self.aisles_data.get(str(key))
                if data:
                    b = data.get("bins")
                    if b is None:
                        continue
                    try:
                        if isinstance(b, list):
                            val = len(b)
                        else:
                            val = int(b)
                    except Exception:
                        val = SAFE_DEFAULT
                    break

        # FIX: ไม่ควรบังคับ val=6 ให้กลายเป็น 10
        val = max(1, min(int(CFG.max_bins_per_aisle), int(val)))
        self._bins_cache[aa] = val
        return val

    @staticmethod
    def clamp_slot(a, r, b):
        a = max(1, min(CFG.max_aisles, int(a)))
        r = max(1, min(CFG.levels_per_bin, int(r)))
        # FIX: clamp bin ด้วย max_bins_per_aisle เพื่อกัน overflow/ข้อมูลหลุด
        b = max(1, min(CFG.max_bins_per_aisle, int(b)))
        return a, r, b

    @staticmethod
    def slot_str(a, r, b):
        pad = CFG.slot_pad
        return f"{int(a):0{pad}d}-{int(r):0{pad}d}-{int(b):0{pad}d}"

    def _parse_slot_list(self, slot_str: str) -> List[Tuple[int, int, int]]:
        slots: List[Tuple[int, int, int]] = []
        if not slot_str or not isinstance(slot_str, str):
            return slots
        for s in slot_str.split("•"):
            try:
                parts = s.strip().split("-")
                if len(parts) == 3:
                    slots.append((int(parts[0]), int(parts[1]), int(parts[2])))
            except ValueError:
                pass
        return slots

    # =========================================================
    # 3. ROUTING LOGIC (NEW: SIMULATION)
    # =========================================================
    def calculate_path_distance(self, path_slots: List[Tuple[int, int, int]]) -> float:
        """
        [Simulation] Calculate actual walking path distance (Traversal).
        Route: Start(Packing) -> Slot 1 -> Slot 2 -> ... -> Slot N

        Args:
            path_slots: List of slots [(a,r,b), ...] already sorted by sequence.
        """
        if not path_slots:
            return 0.0

        total_dist = 0.0
        # Start point (Packing Station)
        current_x, current_y = 0.0, self.pack_y

        for (a, r, b) in path_slots:
            a, r, b = self.clamp_slot(a, r, b)
            target_x, target_y, target_r = self._get_coords(a, r, b)

            # Walk Distance (Manhattan)
            walk_dist = abs(target_x - current_x) + abs(target_y - current_y)

            # Picking Penalty (Height)
            pick_penalty = max(0, target_r - 1) * CFG.level_alpha_m

            total_dist += walk_dist + pick_penalty

            # Update current position
            current_x, current_y = target_x, target_y

        # Note: Return-to-base logic is omitted as per modern picking standards
        return total_dist

    # =========================================================
    # 4. CORE LOGIC (SEARCH & ALLOCATION)
    # =========================================================
    def _collect_slots_history(self, df: pd.DataFrame, top_k: Optional[int] = None) -> pd.DataFrame:
        """Aggregates historical slot locations for each part."""
        if top_k is None: top_k = CFG.slots_all_top_k
        x = df.copy()

        # [FIXED] จัดการค่าว่าง (NaN) และค่า Infinity ก่อนแปลงเป็น Int
        for c in ("Aisle", "Level", "Bin"):
            if c not in x.columns:
                x[c] = 0
            else:
                # 1. แปลงเป็นตัวเลข (ถ้าไม่ได้เป็น NaN)
                x[c] = pd.to_numeric(x[c], errors='coerce')
                # 2. แทนค่า Infinity ด้วย 0
                x[c] = x[c].replace([np.inf, -np.inf], 0)
                # 3. แทนค่า NaN ด้วย 0
                x[c] = x[c].fillna(0)
                # 4. แปลงเป็น Int อย่างปลอดภัย
                x[c] = x[c].astype(int)

        pad = CFG.slot_pad
        x["slot"] = (
            x["Aisle"].astype(int).map(lambda v: f"{v:0{pad}d}") + "-" +
            x["level"].astype(int).map(lambda v: f"{v:0{pad}d}") + "-" +
            x["Bin"].astype(int).map(lambda v: f"{v:0{pad}d}")
        )

        # Create stable sort key
        if "Order_Date" in x.columns:
            ord_ = pd.to_datetime(x["Order_Date"], errors="coerce")
            x["_ord"] = ord_ if not ord_.isna().all() else np.arange(len(x))
        else:
            x["_ord"] = np.arange(len(x))

        # Ensure Part_Name exists for grouping stability
        if "Part_Name" not in x.columns:
            x["Part_Name"] = ""

        first_seen = (
            x.groupby(["Part_ID", "Part_Name", "slot"], as_index=False)
            .agg(first=("_ord", "min"))
        )

        first_seen = first_seen.sort_values(
            ["Part_ID", "Part_Name", "first"],
            kind="mergesort"
        )

        first_seen = first_seen.groupby(["Part_ID", "Part_Name"], as_index=False).head(int(top_k))

        out = first_seen.groupby("Part_ID", as_index=False).agg(
            Before_Slot_All=("slot", lambda s: " • ".join(map(str, pd.unique(s))))
        )
        return out

    def _get_pair_anchors(self, df_orders: pd.DataFrame, target_ids: set) -> Dict[str, int]:
        """Finds aisle affinity based on Co-Ordering (Market Basket Analysis)."""
        # ถ้าไม่มีคอลัมน์ที่ต้องใช้ ก็ปิด feature นี้แบบปลอดภัย
        if "Order_ID" not in df_orders.columns or "Part_ID" not in df_orders.columns:
            return {}
        if "Aisle" not in df_orders.columns:
            return {}

        subset = df_orders[["Order_ID", "Part_ID", "Aisle"]].copy()
        subset["Part_ID"] = subset["Part_ID"].astype(str)
        subset["Aisle"] = pd.to_numeric(subset["Aisle"], errors="coerce")
        subset = subset.dropna(subset=["Aisle"])
        subset["Aisle"] = subset["Aisle"].astype(int).clip(1, CFG.max_aisles)

        order_counts = subset["Order_ID"].value_counts()
        valid_orders = order_counts[order_counts > 1].index
        subset = subset[subset["Order_ID"].isin(valid_orders)]

        pair_counter = Counter()
        for _, group in subset.groupby("Order_ID"):
            parts = sorted(group["Part_ID"].unique())
            if len(parts) < 2:
                continue
            pair_counter.update(itertools.combinations(parts, 2))

        # Latest aisle per part (robust: ถ้ามี Order_Date ก็ใช้, ไม่มีก็ใช้ลำดับแถว)
        tmp = subset.copy()
        if "Order_Date" in df_orders.columns:
            tmp["Order_Date"] = pd.to_datetime(df_orders.get("Order_Date"), errors="coerce")
            tmp = tmp.sort_values("Order_Date", kind="mergesort")
        latest_aisle = tmp.groupby("Part_ID")["Aisle"].last().to_dict()
        latest_aisle = {str(k): int(v) for k, v in latest_aisle.items()}

        suggested_aisle: Dict[str, int] = {}
        target_ids_str = {str(t) for t in target_ids}
        adjacency: Dict[str, List[Tuple[str, int]]] = {}

        for (p1, p2), count in pair_counter.items():
            if count < 2:
                continue
            if p1 in target_ids_str:
                adjacency.setdefault(p1, []).append((p2, count))
            if p2 in target_ids_str:
                adjacency.setdefault(p2, []).append((p1, count))

        for pid, neighbors in adjacency.items():
            friends_aisles: List[int] = []
            for friend, count in neighbors:
                if friend in latest_aisle:
                    friends_aisles.extend([latest_aisle[friend]] * count)

            if friends_aisles:
                avg_aisle = int(sum(friends_aisles) / len(friends_aisles))
                suggested_aisle[pid] = max(1, min(CFG.max_aisles, avg_aisle))

        return suggested_aisle

    def _equalized_after_seeds(
        self,
        anchor: Tuple[int, int, int],
        before_all: str,
        return_reserved: bool = False
    ) -> Union[List[Tuple[int, int, int]], Tuple[List[Tuple[int, int, int]], List[Tuple[int, int, int]]]]:
        """
        Algorithm to find available slots near an 'Anchor' point.
        Strategy 1: Spiral Search (Aesthetics - keep close)
        Strategy 2: Linear Sweep (Guarantee - find any empty slot)
        Strategy 3: Overflow (Safety - 999-999-999)

        IMPORTANT:
        - ตอนนี้รองรับ "คืน reserved" ได้ผ่าน return_reserved
          เพื่อแก้ bug: จองแล้วไม่คืนเมื่อย้ายแล้วแย่ลงและ revert
        """
        anchor_aisle, anchor_level, anchor_bin = anchor
        before_slots = self._parse_slot_list(before_all)
        k_needed = max(1, len(before_slots))

        anchor_aisle, anchor_level, anchor_bin = self.clamp_slot(anchor_aisle, anchor_level, anchor_bin)
        max_r = CFG.levels_per_bin

        seeds_slots: List[Tuple[int, int, int]] = []
        reserved_taken: List[Tuple[int, int, int]] = []

        def _try_reserve(aisle: int, level: int, bin_idx: int) -> bool:
            if aisle < 1 or level < 1 or bin_idx < 1:
                return False
            slot = (int(aisle), int(level), int(bin_idx))
            with self._reserved_lock:
                if slot in self._reserved_slots:
                    return False
                self._reserved_slots.add(slot)
            seeds_slots.append(slot)
            reserved_taken.append(slot)
            return True

        # --- Strategy 1: Spiral (รอบ anchor ในแกน level/bin) ---
        nb_target = self.bins_in_aisle(anchor_aisle)
        radius = 0
        max_radius = min(nb_target, 20)

        while len(seeds_slots) < k_needed and radius <= max_radius:
            for dr in range(-radius, radius + 1):
                db_remain = radius - abs(dr)
                check_dbs = {db_remain, -db_remain} if db_remain != 0 else {0}
                for db in check_dbs:
                    r_try, b_try = anchor_level + dr, anchor_bin + db
                    if 1 <= r_try <= max_r and 1 <= b_try <= nb_target:
                        _try_reserve(anchor_aisle, r_try, b_try)
                        if len(seeds_slots) >= k_needed:
                            break
                if len(seeds_slots) >= k_needed:
                    break
            radius += 1

        # --- Strategy 2: Linear Sweep (optimized: search near anchor bin/level first) ---
        if len(seeds_slots) < k_needed:
            search_queue: List[int] = [anchor_aisle]
            for d in range(1, CFG.max_aisles + 1):
                if anchor_aisle + d <= CFG.max_aisles:
                    search_queue.append(anchor_aisle + d)
                if anchor_aisle - d >= 1:
                    search_queue.append(anchor_aisle - d)

            def _near_order(center: int, lo: int, hi: int) -> List[int]:
                out = []
                out.append(center)
                step = 1
                while len(out) < (hi - lo + 1):
                    if center + step <= hi:
                        out.append(center + step)
                    if center - step >= lo:
                        out.append(center - step)
                    step += 1
                # unique + clamp range
                out2 = []
                seen = set()
                for v in out:
                    if lo <= v <= hi and v not in seen:
                        out2.append(v); seen.add(v)
                return out2

            for a_scan in search_queue:
                if len(seeds_slots) >= k_needed:
                    break
                nb = self.bins_in_aisle(a_scan)
                b_order = _near_order(anchor_bin, 1, nb)
                r_order = _near_order(anchor_level, 1, max_r)

                for b_scan in b_order:
                    if len(seeds_slots) >= k_needed:
                        break
                    for r_scan in r_order:
                        if len(seeds_slots) >= k_needed:
                            break
                        _try_reserve(a_scan, r_scan, b_scan)

        # --- Strategy 3: Overflow ---
        if len(seeds_slots) < k_needed:
            logger.warning(f" FULL: Anchor {anchor} -> Overflow to {OVERFLOW_SLOT}")
            while len(seeds_slots) < k_needed:
                seeds_slots.append(OVERFLOW_SLOT)

        # clamp เฉพาะ slot ปกติ; sentinel 999 ให้คงไว้
        def _clamp_or_keep(s: Tuple[int, int, int]) -> Tuple[int, int, int]:
            if s == OVERFLOW_SLOT:
                return s
            return self.clamp_slot(*s)

        final_seeds = [_clamp_or_keep(s) for s in seeds_slots[:k_needed]]
        if return_reserved:
            # reserved_taken ไม่มี sentinel อยู่แล้ว
            return final_seeds, reserved_taken
        return final_seeds

    # =========================================================
    # 5. MACHINE LEARNING & TRAIN
    # =========================================================
    def _run_ml(self, agg: pd.DataFrame) -> Tuple[Any, pd.DataFrame]:
        if not _HAS_SKLEARN:
            return None, agg

        feat_cols = ["order_frequency", "Total_Qty", "Weighted_Dist"]
        try:
            # Prepare X
            for c in feat_cols:
                if c not in agg.columns:
                    agg[c] = 0.0
            X = agg[feat_cols].fillna(0.0).values

            scaler = StandardScaler()
            X_std = scaler.fit_transform(X)

            # Clustering
            n_clusters = min(4, len(agg))
            if n_clusters > 1:
                km = KMeans(n_clusters=n_clusters, random_state=CFG.random_state, n_init=10)
                agg["ML_Cluster"] = km.fit_predict(X_std)
            else:
                agg["ML_Cluster"] = 0

            # Classification Target: Is saving significant?
            if "Dist_Saving_m" not in agg.columns:
                agg["Dist_Saving_m"] = 0.0
            y = (agg["Dist_Saving_m"] > 0.5).astype(int)

            clf = RandomForestClassifier(n_estimators=50, random_state=CFG.random_state)
            if len(np.unique(y)) > 1:
                clf.fit(X_std, y)
                agg["ML_Prob_Relocate"] = clf.predict_proba(X_std)[:, 1]
            else:
                agg["ML_Prob_Relocate"] = 0.0

            return clf, agg

        except Exception as e:
            logger.error(f"ML Pipeline Failed: {e}")
            agg["ML_Prob_Relocate"] = 0.0
            return None, agg

    def train(self, order_path: str, inventory_path: str = "inventory.csv"):
        """Main Pipeline: Data -> Geometry -> ML -> Optimization."""
        # reset reserved slots
        with self._reserved_lock:
            self._reserved_slots = set()

        # 1. Path Safety Check
        if not os.path.exists(inventory_path):
            found = False
            for p in ["inventory.csv", "project/inventory.csv", "../inventory.csv"]:
                if os.path.exists(p):
                    inventory_path = p
                    found = True
                    break
            if not found:
                raise FileNotFoundError(f"Inventory not found: {inventory_path}")

        order_path = self._resolve_path(order_path)
        inventory_path = self._resolve_path(inventory_path)

        logger.info(f" Demand Source: {order_path}")
        logger.info(f" Relocation Scope: {inventory_path}")

        df_ord = self._read_csv_safe(order_path)
        df_inv = self._read_csv_safe(inventory_path)

        logger.info(" Sanitizing Input Data (God Mode)...")
        df_ord = self._sanitize_columns(df_ord, "order")
        df_inv = self._sanitize_columns(df_inv, "inventory")

        # --- Type Coercion (CRITICAL) ---
        self._coerce_numeric(df_ord, ["Quantity_Sold", "Aisle", "level", "Bin"])
        self._coerce_numeric(df_inv, ["Slot_Stock", "Distance_to_Packing", "Aisle", "level", "Bin"])

        # ensure Part_Name exists
        if "Part_Name" not in df_inv.columns:
            df_inv["Part_Name"] = ""

        # --- Aggregation Logic ---
        # demand side
        agg_demand = df_ord.groupby("Part_ID", dropna=False).agg(
            Order_Count=("Order_ID", "nunique"),
            Total_Qty=("Quantity_Sold", "sum")
        ).reset_index()

        # inventory side
        df_inv["Slot_Stock"] = df_inv["Slot_Stock"].fillna(0.0)
        df_inv["Distance_to_Packing"] = df_inv["Distance_to_Packing"].fillna(0.0)
        df_inv["Moment"] = df_inv["Slot_Stock"] * df_inv["Distance_to_Packing"]

        agg_inv = df_inv.groupby("Part_ID", dropna=False).agg(
            Current_Total_Stock=("Slot_Stock", "sum"),
            Total_Moment=("Moment", "sum"),
            Avg_Dist=("Distance_to_Packing", "mean"),
            Part_Name=("Part_Name", "first")
        ).reset_index()

        # Weighted Distance (Prevent Div by Zero)
        agg_inv["Weighted_Dist"] = np.where(
            agg_inv["Current_Total_Stock"] > 0,
            agg_inv["Total_Moment"] / agg_inv["Current_Total_Stock"],
            agg_inv["Avg_Dist"]
        )
        agg_inv.drop(columns=["Total_Moment", "Avg_Dist"], inplace=True)

        # Get Historical Slots (optional แต่ช่วย allocator/equalize)
        agg_slots = self._collect_slots_history(df_inv)
        agg_inv = pd.merge(agg_inv, agg_slots, on="Part_ID", how="left")

        # Master Merge
        agg = pd.merge(agg_inv, agg_demand, on="Part_ID", how="left")
        agg.fillna({"Order_Count": 0, "Total_Qty": 0}, inplace=True)
        agg["order_frequency"] = agg["Order_Count"]
        agg["Recent_Qty"] = agg["Total_Qty"]

        # Categorization
        def guess_tier(name):
            if not isinstance(name, str):
                return CFG.default_tier
            n = name.lower()
            if any(x in n for x in ["nut", "fitting", "screw"]):
                return "small"
            if any(x in n for x in ["beam", "rail"]):
                return "long"
            if any(x in n for x in ["flat pack", "panel", "body"]):
                return "large"
            if any(x in n for x in ["leg", "drawer"]):
                return "medium"
            return "medium"

        agg["Category"] = agg["Part_Name"].apply(guess_tier)
        agg["Before_Dist_m"] = agg["Weighted_Dist"].fillna(120.0)

        # --- Optimization Loop ---
        agg = agg.sort_values("order_frequency", ascending=False).reset_index(drop=True)

        # pair anchor ใช้ได้ต่อเมื่อ orders มี Aisle
        pairs = self._get_pair_anchors(df_ord, set(agg["Part_ID"].astype(str)))

        after_rows: List[Dict[str, Any]] = []
        sku_density = 50  # TODO: อาจทำให้ aisle ท้ายกอง ถ้า n_parts ใหญ่มาก

        # PERF: itertuples เร็วกว่า iterrows
        for i, row in enumerate(agg.itertuples(index=False)):
            pid = str(getattr(row, "Part_ID"))
            before_dist = float(getattr(row, "Before_Dist_m", 0.0))
            before_all = getattr(row, "Before_Slot_All", "") or ""

            # Default Strategy: Move high freq to front (Aisle 1...)
            target_aisle = min(CFG.max_aisles, 1 + int(i / sku_density))

            # Pair Strategy: Override if paired
            if CFG.use_pair_anchor and i > 20 and pid in pairs:
                target_aisle = pairs[pid]

            target_anchor = (target_aisle, 1, 1)

            reserved_taken: List[Tuple[int, int, int]] = []
            if CFG.after_equalize:
                seeds, reserved_taken = self._equalized_after_seeds(target_anchor, before_all, return_reserved=True)
            else:
                seeds = [target_anchor]

            s0 = seeds[0]
            if s0 == OVERFLOW_SLOT:
                after_dist = before_dist  # ถือว่า “ไม่มีที่ลง”
            else:
                after_dist = float(self.slot_distance(*s0))

            # CRITICAL FIX: ถ้าย้ายแล้วแย่ลง → reject ทันที และ “คืน reserved”
            if after_dist > before_dist:
                self._release_reserved(reserved_taken)
                after_rows.append({
                    "After_Aisle": 999, "After_level": 999, "After_Bin": 999,
                    # เก็บ After_Slot_All ว่างเพื่อไม่ให้ allocator เอาไปใช้ผิด
                    "After_Slot_All": "",
                    "After_Dist_m": before_dist
                })
                continue

            after_rows.append({
                "After_Aisle": int(s0[0]) if s0 != OVERFLOW_SLOT else 999,
                "After_level": int(s0[1]) if s0 != OVERFLOW_SLOT else 999,
                "After_Bin": int(s0[2]) if s0 != OVERFLOW_SLOT else 999,
                "After_Slot_All": " • ".join([self.slot_str(*s) if s != OVERFLOW_SLOT else "999-999-999" for s in seeds]),
                "After_Dist_m": after_dist
            })

        after_df = pd.DataFrame(after_rows)
        agg = pd.concat([agg, after_df], axis=1)

        # Saving Calculation
        agg["Dist_Saving_m"] = agg["Before_Dist_m"] - agg["After_Dist_m"]

        # Run ML
        model, agg = self._run_ml(agg)

        # Best practice: clip score 0..1
        if "ML_Prob_Relocate" in agg.columns:
            agg["Rec_Score"] = agg["ML_Prob_Relocate"].fillna(0.0).clip(0.0, 1.0)
        else:
            agg["Rec_Score"] = 0.0

        with self._lock:
            self.state["agg"] = agg.sort_values("Rec_Score", ascending=False)
            self.state["model"] = model
            self.state["allocated"] = False

        logger.info(f" Training Complete. Optimized {len(agg)} parts.")
        return {"n_parts": len(agg), "status": "Optimized"}

    def run_allocator(self):
        with self._lock:
            if self.state["agg"] is None:
                raise RuntimeError("Train first")
            a = self.state["agg"].copy()

        ml_conf = a["Rec_Score"].fillna(0).clip(0, 1)
        a["Plan_Front_Units"] = (a["Recent_Qty"] * CFG.front_factor * (1.0 + 0.5 * ml_conf)).round().astype(int)

        def get_caps(cat):
            caps_map = CFG.tier_capacity or {}
            t = CFG.default_tier
            cat_str = str(cat).lower()
            for k in caps_map.keys():
                if k in cat_str:
                    t = k
                    break
            cap = int(caps_map.get(t, 120))
            return cap, max(1, int(math.ceil(cap / 100)))

        alloc_results: List[str] = []

        for row in a.itertuples(index=False):
            demand = int(getattr(row, "Plan_Front_Units", 0))
            if demand <= 0:
                alloc_results.append("")
                continue

            cap_bin, _ = get_caps(getattr(row, "Category", ""))
            seeds_str = str(getattr(row, "After_Slot_All", "") or "")
            seeds = self._parse_slot_list(seeds_str)

            if not seeds:
                # ถ้าไม่มี After_Slot_All ให้ fallback (แต่ถ้า After_* เป็น 999 ก็ถือว่าไม่มีแผน)
                aa = int(getattr(row, "After_Aisle", 999))
                ar = int(getattr(row, "After_level", 999))
                ab = int(getattr(row, "After_Bin", 999))
                if aa == 999:
                    alloc_results.append("")
                    continue
                seeds = [(aa, ar, ab)]

            allocated_strs: List[str] = []
            remaining_demand = demand

            # Simple greedy fill
            for s in seeds:
                if remaining_demand <= 0:
                    break
                a_, r_, b_ = s
                if (a_, r_, b_) == OVERFLOW_SLOT:
                    continue
                take = min(cap_bin, remaining_demand)
                allocated_strs.append(f"{self.slot_str(a_, r_, b_)}×{take}")
                remaining_demand -= take

            alloc_results.append(" • ".join(allocated_strs))

        a["Alloc_Slot_Summary"] = alloc_results

        with self._lock:
            self.state["agg"] = a
            self.state["allocated"] = True

        return {"status": "Allocated", "count": len(a)}

# ---------------------- Global Singleton ----------------------
ENGINE = WarehouseEngine()

def train(order_csv: str, inventory_csv: str = "inventory.csv"):
    return ENGINE.train(order_csv, inventory_csv)

def run_allocator():
    return ENGINE.run_allocator()

def get_recommendations(top_k=1000):
    with ENGINE._lock:
        if ENGINE.state["agg"] is None:
            return None
        cols = [
            "Part_ID", "Part_Name", "Category",
            "Before_Slot_All", "After_Slot_All",
            "Alloc_Slot_Summary",
            "Rec_Score", "Dist_Saving_m",
            "Before_Dist_m", "After_Dist_m",
            "Plan_Front_Units"
        ]
        act_cols = [c for c in cols if c in ENGINE.state["agg"].columns]
        return ENGINE.state["agg"][act_cols].head(top_k)

def health():
    return {"status": "ok", "mode": "PART_BASED_FULL_LOGIC"}
