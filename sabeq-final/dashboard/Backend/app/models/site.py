from .base import ApiModel
from .common import LocalizedText, Position


class ViewBox(ApiModel):
    w: int
    h: int


class SiteMap(ApiModel):
    view_box: ViewBox
    scale_bar_m: float


class Site(ApiModel):
    """§3.1"""

    id: str
    name: LocalizedText
    visitors: int | None = None  # SITE_VISITORS; null when not configured
    map: SiteMap
    hub: Position
    zones: list[str]
