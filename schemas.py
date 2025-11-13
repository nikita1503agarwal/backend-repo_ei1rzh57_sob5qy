"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal

# Example schemas (leave for reference):

class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    address: str = Field(..., description="Address")
    age: Optional[int] = Field(None, ge=0, le=120, description="Age in years")
    is_active: bool = Field(True, description="Whether user is active")

class Product(BaseModel):
    title: str = Field(..., description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price: float = Field(..., ge=0, description="Price in dollars")
    category: str = Field(..., description="Product category")
    in_stock: bool = Field(True, description="Whether product is in stock")

# --------------------------------------------------
# Ecodrive App Schemas
# Each class corresponds to a MongoDB collection whose name
# is the lowercase of the class name
# --------------------------------------------------

class EmissionReading(BaseModel):
    """
    Stores each emission estimation event
    Collection name: "emissionreading"
    """
    vehicle_id: str = Field(..., description="Unique identifier for the vehicle")
    distance_km: float = Field(..., ge=0, description="Trip distance in kilometers")
    fuel_used_l: Optional[float] = Field(None, ge=0, description="Fuel used in liters (optional if efficiency provided)")
    efficiency_km_per_l: Optional[float] = Field(None, gt=0, description="Vehicle efficiency (km per liter), used to infer fuel used if provided")
    fuel_type: Literal["petrol", "diesel"] = Field("petrol", description="Fuel type for emission factor")
    avg_speed_kmh: Optional[float] = Field(None, ge=0, description="Average speed used to estimate CO emissions")

    # Computed results
    co_g: float = Field(..., ge=0, description="Estimated CO emitted in grams for the trip")
    co2_kg: float = Field(..., ge=0, description="Estimated CO2 emitted in kilograms for the trip")
    co2_g_per_km: float = Field(..., ge=0, description="CO2 intensity in grams per kilometer")
    alert: bool = Field(..., description="Whether the reading exceeded thresholds")
    reason: Optional[str] = Field(None, description="Why the alert triggered, if any")
