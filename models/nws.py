# models/nws.py
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class NWSAlertParameters(BaseModel):
    VTEC: Optional[List[str]] = None
    maxHailSize: Optional[List[str]] = None
    maxWindGust: Optional[List[str]] = None
    tornadoDetection: Optional[List[str]] = None
    tornadoDamageThreat: Optional[List[str]] = None
    thunderstormDamageThreat: Optional[List[str]] = None
    flashFloodDamageThreat: Optional[List[str]] = None
    flashFloodDetection: Optional[List[str]] = None

class NWSAlertProperties(BaseModel):
    id: str
    areaDesc: str
    event: str
    headline: Optional[str] = None
    description: str
    instruction: Optional[str] = None
    response: Optional[str] = None
    parameters: Optional[NWSAlertParameters] = None
    effective: Optional[datetime] = None
    onset: Optional[datetime] = None
    expires: Optional[datetime] = None
    ends: Optional[datetime] = None
    status: str
    messageType: str
    category: str
    severity: str
    certainty: str
    urgency: str
    senderName: str

class NWSAlertFeature(BaseModel):
    id: str
    type: str = "Feature"
    properties: NWSAlertProperties

class NWSAlertResponse(BaseModel):
    type: str = "FeatureCollection"
    features: List[NWSAlertFeature] = Field(default_factory=list)
    title: Optional[str] = None
    updated: Optional[datetime] = None
