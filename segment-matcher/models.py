from pydantic import AnyHttpUrl, BaseModel


class MatchRequest(BaseModel):
    name: str
    x: float
    y: float
    orientation: float | None = None
    object_type: str | None = None
    road_type: int | None = None
    post_url: AnyHttpUrl


class Candidate(BaseModel):
    gid: int
    lvl: str
    score: float
    dist: float
    morphology: str
    typology: str
    length: float
    geom_wgs84: list[list[list[float]]]
    geom_3812: list[list[list[float]]]
    wkb_hex: str


class MatchResult(BaseModel):
    name: str
    segment_id: int
    wkb: str


class PendingMatch(BaseModel):
    id: str
    request: MatchRequest
    candidates: list[Candidate]
    point_wgs84: tuple[float, float]
