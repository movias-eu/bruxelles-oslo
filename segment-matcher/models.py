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
    # Bidirectional bearing scoring (set only when an orientation was provided).
    # The candidate is scored against the request orientation AND its reverse;
    # `score` holds the better of the two. These expose which one won so the UI
    # can flag candidates whose best match assumes the bearing was entered
    # reversed. None when no orientation was provided (no bearing comparison).
    score_forward: float | None = None
    score_reversed: float | None = None
    bearing_reversed: bool = False


class MatchResult(BaseModel):
    name: str
    segment_id: int
    wkb: str


class PendingMatch(BaseModel):
    id: str
    request: MatchRequest
    candidates: list[Candidate]
    point_wgs84: tuple[float, float]
