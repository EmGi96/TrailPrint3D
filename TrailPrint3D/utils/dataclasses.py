import threading
import time
from dataclasses import dataclass, field
from typing import Any

from bpy.types import Object
from shapely.geometry import MultiPolygon, Polygon


@dataclass
class GPXStats:
    start_time: str | None = ""
    length: float = 0
    elevation: float = 0
    time: float = 0
    avg_speed: float = 0
    date: str | None = None


@dataclass
class RunSettings:
    """User/scene config captured once during input validation (Phase 1)."""

    flags: frozenset[str]
    gpx_chain_path: str
    exportFormat: str
    shape: str
    modelname: str
    size: int
    autoExport: bool
    scaleElevation: float
    scalemode: str
    scaleLon1: float
    scaleLat1: float
    scaleLon2: float
    scaleLat2: float
    shapeRotation: int
    overwritePathElevation: bool
    elevationMode: str
    fixedHeightMM: float
    minThickness: float
    xTerrainOffset: float
    yTerrainOffset: float
    singleColorMode: bool
    elementChoice: bool
    elementMode: str
    elementSource: str
    disableCache: bool
    num_subdivisions: int
    plateThickness: float
    rectangleHeight: int = 100
    ellipseRatio: float = 0.75
    customFilePath: str = ""
    tolerance: float = 0.2
    shellWallThickness: float = 2.0
    plateInsertValue: float = 0.0
    pathThickness: float = 1.2
    genType: int = 0
    lockedScale: float | None = None
    smoothTerrainTop: bool = False
    smoothTerrainStrength: int = 2


@dataclass
class ElevationSettings:
    """Elevation smoothing/base/step (staircase) options."""

    el_Smoothing: float
    el_sHeight: float
    el_sCutTolerance: float = 0.2
    el_sCutDepth: float = 0.05


@dataclass
class JMapSettings:
    """JMap bbox/radius area-selection inputs."""

    jMapLat: float
    jMapLon: float
    jMapRadius: float
    jMapLat1: float
    jMapLon1: float
    jMapLat2: float
    jMapLon2: float


@dataclass
class TextureSettings:
    """MMU paint texture export options."""

    useTexture: bool = False
    texResolution: int = 2048
    texRoads: bool = False
    texTrail: bool = False


@dataclass
class FetchState:
    """Background OSM prefetch thread and its result, set in Phase 6/8."""

    fetchThread: threading.Thread | None = None
    fetchResult: dict[str, dict[Any, tuple[dict, bool]]] | None = None
    satelliteThread: threading.Thread | None = None
    satelliteResult: dict[str, Any] | None = None


@dataclass
class RuntimeState:
    """Working data populated/mutated by pipeline phases as generation proceeds."""

    mapObject: Object | None = None
    mapOutline: Polygon | MultiPolygon | None = None
    tbMinLat: float = 0
    tbMaxLat: float = 0
    tbMinLon: float = 0
    tbMaxLon: float = 0
    mapKm: float | None = None
    pathCoordinates: list[tuple[float, float, float, float]] | None = None
    flatCoordinates: list[tuple[float, float, float, float]] | None = None
    pathSegs: list[list[tuple[float, float, float, float]]] | None = None
    pathSegsByFile: list[list[list[tuple[float, float, float, float]]]] | None = None
    blenderCoords: list[tuple[float, float, float]] | None = None
    blenderPathSegs: list[list[tuple[float, float, float]]] | None = None
    blenderPathSegsByFile: list[list[list[tuple[float, float, float]]]] | None = None
    gpx_stats: GPXStats = field(default_factory=GPXStats)
    start_time: float = field(default_factory=time.time)
    curveObj: object | None = None
    curveObjs: list[Object] | None = None
    sScaleHor: float | None = None
    centerX: float | None = None
    centerY: float | None = None
    tileVerts: list[float] | None = None
    elDiff: float | None = None
    buggyData: int = 0
    addExtrusion: float | None = None
    autoScale: float | None = None
    elements: list | None = None
    textObj: Object | None = None
    plateObj: Object | None = None
    shellObj: Object | None = None
    roadObj: Object | None = None
    roadUnion: Polygon | MultiPolygon | None = None


@dataclass
class GenerationContext:
    """Composed from the grouped dataclasses above.

    Old flat access (`gen.runtime.mapObject`) is gone — use the group it now lives
    in instead (`gen.runtime.mapObject`).
    """

    settings: RunSettings
    elevation: ElevationSettings
    jmap: JMapSettings
    texture: TextureSettings = field(default_factory=TextureSettings)
    fetch: FetchState = field(default_factory=FetchState)
    runtime: RuntimeState = field(default_factory=RuntimeState)


class ValidationError(Exception):
    """Raised when input validation fails before generation starts."""


class GenerationError(Exception):
    """Raised by _rg_* helpers when a non-recoverable phase failure occurs.

    Caught by runGeneration's outer try/except and surfaced as a named warning
    rather than the generic 'check console' fallback.
    """
