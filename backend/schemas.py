from typing import Literal, Optional, List
from pydantic import BaseModel

Cat = Literal["food","hidden_gem","event","traffic","flood","weather","safety","civic_issue","cleanliness","other"]

class Report(BaseModel):
    category: Cat
    title: str
    summary: str
    severity: int
    sentiment: Literal["positive","neutral","negative"]
    place_name: Optional[str] = None
    tags: List[str]
    ai_conf: float
    needs_emergency: bool
    abuse: bool

class Pick(BaseModel):
    kind: Literal["go","avoid","tip"]
    title: str
    why: str

class Insights(BaseModel):
    headline: str
    picks: List[Pick]
