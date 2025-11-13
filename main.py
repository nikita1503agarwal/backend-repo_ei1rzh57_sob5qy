import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import create_document, get_documents, db

app = FastAPI(title="Ecodrive API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Utility functions
# -----------------------------

def emission_factors(fuel_type: str) -> Dict[str, float]:
    """Return emission factors for given fuel type.
    Values approximate typical factors.
    - CO2: kg per liter
    - CO: grams per km baseline (varies strongly by engine/condition). We keep a simple heuristic using speed.
    """
    fuel_type = fuel_type.lower()
    if fuel_type not in {"petrol", "diesel"}:
        raise HTTPException(status_code=400, detail="fuel_type must be 'petrol' or 'diesel'")
    if fuel_type == "petrol":
        return {"co2_kg_per_l": 2.31, "co_base_g_per_km": 1.2}
    return {"co2_kg_per_l": 2.68, "co_base_g_per_km": 0.8}


def estimate_emissions(distance_km: float, fuel_type: str, fuel_used_l: Optional[float], efficiency_km_per_l: Optional[float], avg_speed_kmh: Optional[float]) -> Dict[str, float]:
    """Estimate CO and CO2 for a trip.
    - If fuel_used_l missing and efficiency provided, fuel_used_l = distance / efficiency
    - CO2 (kg) = fuel_used_l * factor
    - CO (g) uses a simple heuristic increasing at very low and very high speeds.
    """
    if distance_km < 0:
        raise HTTPException(status_code=400, detail="distance_km must be >= 0")

    factors = emission_factors(fuel_type)

    if fuel_used_l is None:
        if efficiency_km_per_l and efficiency_km_per_l > 0:
            fuel_used_l = distance_km / efficiency_km_per_l
        else:
            raise HTTPException(status_code=400, detail="Provide fuel_used_l or efficiency_km_per_l > 0")

    co2_kg = fuel_used_l * factors["co2_kg_per_l"]
    co2_g_per_km = (co2_kg * 1000.0) / distance_km if distance_km > 0 else 0.0

    # CO model: base + speed factor
    speed = avg_speed_kmh or 40.0
    # U-shaped curve: more CO at idle/low speed and at very high speeds
    # Normalize speed around 50 km/h
    base = factors["co_base_g_per_km"]
    speed_factor = 0.02 * abs(speed - 50)  # each 10 km/h away from 50 adds 0.2 g/km
    co_g_per_km = max(0.2, base + speed_factor)
    co_g_total = co_g_per_km * distance_km

    return {
        "co_g": round(co_g_total, 2),
        "co2_kg": round(co2_kg, 3),
        "co2_g_per_km": round(co2_g_per_km, 1),
    }


def check_thresholds(co_g: float, co2_g_per_km: float) -> (bool, str):
    """Simple thresholds to trigger alert/buzzer."""
    reasons = []
    if co2_g_per_km > 180:  # high intensity per km
        reasons.append("High CO2 intensity")
    if co_g > 20:  # total CO per trip
        reasons.append("High CO emission")
    return (len(reasons) > 0, ", ".join(reasons) if reasons else None)


# -----------------------------
# Request/Response models
# -----------------------------

class EstimateRequest(BaseModel):
    vehicle_id: str
    distance_km: float = Field(..., ge=0)
    fuel_used_l: Optional[float] = Field(None, ge=0)
    efficiency_km_per_l: Optional[float] = Field(None, gt=0)
    fuel_type: Literal["petrol", "diesel"] = "petrol"
    avg_speed_kmh: Optional[float] = Field(None, ge=0)

class EstimateResponse(BaseModel):
    co_g: float
    co2_kg: float
    co2_g_per_km: float
    alert: bool
    reason: Optional[str]


# -----------------------------
# Routes
# -----------------------------

@app.get("/")
def root():
    return {"message": "Ecodrive API running"}


@app.post("/api/estimate", response_model=EstimateResponse)
def estimate(data: EstimateRequest):
    metrics = estimate_emissions(
        distance_km=data.distance_km,
        fuel_type=data.fuel_type,
        fuel_used_l=data.fuel_used_l,
        efficiency_km_per_l=data.efficiency_km_per_l,
        avg_speed_kmh=data.avg_speed_kmh,
    )
    alert, reason = check_thresholds(metrics["co_g"], metrics["co2_g_per_km"])

    # Save to DB
    doc = {
        "vehicle_id": data.vehicle_id,
        "distance_km": data.distance_km,
        "fuel_used_l": data.fuel_used_l,
        "efficiency_km_per_l": data.efficiency_km_per_l,
        "fuel_type": data.fuel_type,
        "avg_speed_kmh": data.avg_speed_kmh,
        **metrics,
        "alert": alert,
        "reason": reason,
    }
    try:
        create_document("emissionreading", doc)
    except Exception as e:
        # If DB not available, continue without failing the request
        pass

    return EstimateResponse(**metrics, alert=alert, reason=reason)


@app.get("/api/weekly-analysis")
def weekly_analysis(vehicle_id: Optional[str] = None):
    """Return last 7 days aggregate: totals, averages, alert counts."""
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    try:
        # Filter by created_at >= seven_days_ago
        filter_q: Dict[str, Any] = {"created_at": {"$gte": seven_days_ago}}
        if vehicle_id:
            filter_q["vehicle_id"] = vehicle_id
        docs = db["emissionreading"].find(filter_q)
        readings = list(docs)
    except Exception:
        # If DB not available, return empty analysis
        readings = []

    total_trips = len(readings)
    sum_co_g = sum(d.get("co_g", 0) for d in readings)
    sum_co2_kg = sum(d.get("co2_kg", 0) for d in readings)
    alerts = sum(1 for d in readings if d.get("alert"))

    avg_co_g = round(sum_co_g / total_trips, 2) if total_trips else 0.0
    avg_co2_kg = round(sum_co2_kg / total_trips, 3) if total_trips else 0.0

    # Build day-wise buckets
    by_day: Dict[str, Dict[str, float]] = {}
    for d in readings:
        ts = d.get("created_at", now)
        day_key = ts.astimezone(timezone.utc).strftime("%Y-%m-%d") if hasattr(ts, 'astimezone') else now.strftime("%Y-%m-%d")
        b = by_day.setdefault(day_key, {"trips": 0, "co_g": 0.0, "co2_kg": 0.0, "alerts": 0})
        b["trips"] += 1
        b["co_g"] += float(d.get("co_g", 0))
        b["co2_kg"] += float(d.get("co2_kg", 0))
        b["alerts"] += 1 if d.get("alert") else 0

    # Ensure we return all 7 days even if empty
    days = [(seven_days_ago + timedelta(days=i)).astimezone(timezone.utc).strftime("%Y-%m-%d") for i in range(7)]
    day_series = [
        {
            "day": day,
            "trips": by_day.get(day, {}).get("trips", 0),
            "co_g": round(by_day.get(day, {}).get("co_g", 0.0), 2),
            "co2_kg": round(by_day.get(day, {}).get("co2_kg", 0.0), 3),
            "alerts": by_day.get(day, {}).get("alerts", 0),
        }
        for day in days
    ]

    return {
        "summary": {
            "total_trips": total_trips,
            "total_co_g": round(sum_co_g, 2),
            "total_co2_kg": round(sum_co2_kg, 3),
            "avg_co_g": avg_co_g,
            "avg_co2_kg": avg_co2_kg,
            "alerts": alerts,
        },
        "by_day": day_series,
    }


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    # Check environment variables
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
