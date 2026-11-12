// Shared rectangle draw/edit interaction -- reused by every picker page that
// lets the user draw and reshape a single rectangle (puzzleGenerator.html,
// premium/puzzleGenerator_pe.html, premium/slidingPuzzleGenerator.html,
// map_generator.html, premium/map_generator_pe.html,
// premium/multitile_generator.html) via __RECT_EDITOR_JS__ in
// picker_server.py. Covers both 'corner' draw mode (leaflet-draw's own
// corner-to-corner rectangle tool, edited via its native L.Edit.Rectangle
// handles) and 'center' draw mode (click-to-place-center, drag outward,
// edited via this file's own NE/NW/SE/SW marker handles that mirror the
// opposite edge around the fixed center) -- see armCenterDraw/bindCenterEdit.
//
// Expects the including page's own <script> block to already declare (order
// doesn't matter -- these functions only ever run later, in response to user
// interaction, well after the whole page has finished loading):
//   Globals:    map, drawnItems, moveHandleLayer, editHandles, rectLayer,
//               drawControl, coords, currentShape, shapeCenter, drawMode
//   Functions:  saveState, updateSendState, updateStatus, bindLiveEdit
//               (the 'corner' mode counterpart to this file's bindCenterEdit
//               -- enables leaflet-draw's own corner-handle editing on a
//               layer and wires its 'edit' event back into `coords`),
//               renderShapePreview
// and to define two small page-specific hooks used by this shared code:
//   function tp3dOnShapeChanged() { ... }
//       Called after any drag/resize of the shape (corner drag, edge-handle
//       drag, move) so the page can recompute whatever it derives from
//       `coords` -- e.g. renderShapePreview()/regeneratePuzzle()/
//       drawSubdivisionGrid() depending on the page.
//   function tp3dRectStyle() { return { weight, fill, fillOpacity }; }
//       The Leaflet style used for the drawn/previewed rectangle -- varies
//       per page (e.g. a lighter fillOpacity on puzzle pages so piece cuts
//       stay visible, or a weight/fill that depends on `currentShape` on
//       pages with more than one selectable shape).
// Rotation-capable pages may additionally provide:
//   TP3D_SHAPE_ROTATION_DEG, tp3dSetShapeRotation(degrees),
//   tp3dCanRotateShape(), tp3dRotationHandleBounds()
//       The optional rotation handle remains absent on pages without these
//       hooks, including puzzle configurators.
//   tp3dRotatedEditHandlePoints()
//       Optional rotated corner handles for pages that want edit markers to
//       follow the rotated preview rather than the axis-aligned edit layer.
//   tp3dEditAspectRatio()
//       Optional; projected width/height the rotated edit handles hold the
//       frame to (null = free resize). Without it, every shape but Rectangle
//       stays 1:1. Puzzle pages return Length/Width here.
//   tp3dKeepSquare()
//       Optional; returning true locks every draw/resize gesture to 1:1 as
//       if Shift were held (e.g. map pages for every shape but Rectangle).
//
// The two picker pages that offer an importable "svg" shape (map_generator.html,
// premium/map_generator_pe.html) additionally declare svgShapePath,
// svgShapeName, svgAspect, svgDataUrl and a matching updateSvgShapeList(),
// and call computeSvgAspectFromMarkup()/placeSvgBoxInViewport()/
// clearSvgShapeImport() (all below) from their own svgShapeInput 'change'/
// #clearSvgShape 'click' wiring (and, for a page that persists the SVG
// selection across reopens, from restoreState's re-fetched-content handler).
function boundsCenter(b) {
    return { lat: (b.north + b.south) / 2, lng: (b.east + b.west) / 2 };
}

var rotationHandleLayer = null;
var rotationHandleDragging = false;
if (typeof TP3D_SHAPE_ROTATION_DEG !== 'undefined' &&
        typeof tp3dSetShapeRotation === 'function') {
    rotationHandleLayer = new L.FeatureGroup();
    map.addLayer(rotationHandleLayer);
}

function tp3dProjectedBoundsCenter(bounds, zoom) {
    var northWest = map.project([bounds.north, bounds.west], zoom);
    var southEast = map.project([bounds.south, bounds.east], zoom);
    var center = map.unproject([
        (northWest.x + southEast.x) / 2,
        (northWest.y + southEast.y) / 2
    ], zoom);
    return { lat: center.lat, lng: center.lng };
}

function tp3dSquareProjectedBounds(bounds) {
    var northWest = map.project([bounds.north, bounds.west], 0);
    var southEast = map.project([bounds.south, bounds.east], 0);
    var centerX = (northWest.x + southEast.x) / 2;
    var centerY = (northWest.y + southEast.y) / 2;
    var halfSide = Math.min(
        Math.abs(southEast.x - northWest.x),
        Math.abs(southEast.y - northWest.y)
    ) / 2;
    var northWestSquare = map.unproject([centerX - halfSide, centerY - halfSide], 0);
    var southEastSquare = map.unproject([centerX + halfSide, centerY + halfSide], 0);
    return {
        north: northWestSquare.lat,
        south: southEastSquare.lat,
        east: southEastSquare.lng,
        west: northWestSquare.lng,
        type: bounds.type
    };
}

function tp3dProjectedShapePoints(bounds, shape, degrees) {
    var northWest = map.project([bounds.north, bounds.west], 0);
    var southEast = map.project([bounds.south, bounds.east], 0);
    var centerX = (northWest.x + southEast.x) / 2;
    var centerY = (northWest.y + southEast.y) / 2;
    var width = Math.abs(southEast.x - northWest.x);
    var height = Math.abs(southEast.y - northWest.y);
    var halfWidth = width / 2;
    var halfHeight = height / 2;
    var radius = Math.max(halfWidth, halfHeight);
    var points;

    if (shape === 'hexagon') {
        points = [];
        for (var i = 0; i < 6; i++) {
            var hexAngle = i * Math.PI / 3;
            points.push([radius * Math.cos(hexAngle), radius * Math.sin(hexAngle)]);
        }
    } else if (shape === 'octagon') {
        var bevel = radius * (Math.SQRT2 - 1);
        points = [
            [radius, bevel], [bevel, radius], [-bevel, radius], [-radius, bevel],
            [-radius, -bevel], [-bevel, -radius], [bevel, -radius], [radius, -bevel]
        ];
    } else if (shape === 'circle') {
        points = [];
        for (var circleIndex = 0; circleIndex < 48; circleIndex++) {
            var circleAngle = circleIndex * 2 * Math.PI / 48;
            points.push([radius * Math.cos(circleAngle), radius * Math.sin(circleAngle)]);
        }
    } else if (shape === 'square') {
        points = [
            [-radius, -radius], [radius, -radius], [radius, radius], [-radius, radius]
        ];
    } else {
        points = [
            [-halfWidth, -halfHeight], [halfWidth, -halfHeight],
            [halfWidth, halfHeight], [-halfWidth, halfHeight]
        ];
    }

    var degreesValue = shape === 'circle' ? 0 : Number(degrees || 0);
    var angle = degreesValue * Math.PI / 180;
    var cos = Math.cos(angle), sin = Math.sin(angle);
    var rotated = points.map(function(point) {
        return [
            point[0] * cos - point[1] * sin,
            point[0] * sin + point[1] * cos
        ];
    });
    return rotated.map(function(point) {
        var latlng = map.unproject([centerX + point[0], centerY - point[1]], 0);
        return [latlng.lat, latlng.lng];
    });
}

function tp3dPuzzleProjectedBounds(bounds) {
    var northWest = map.project([bounds.north, bounds.west], 0);
    var southEast = map.project([bounds.south, bounds.east], 0);
    return {
        minX: Math.min(northWest.x, southEast.x),
        maxX: Math.max(northWest.x, southEast.x),
        minY: Math.min(northWest.y, southEast.y),
        maxY: Math.max(northWest.y, southEast.y)
    };
}

function tp3dRotatePuzzleProjectedPoint(point, bounds, degrees) {
    var centerX = (bounds.minX + bounds.maxX) / 2;
    var centerY = (bounds.minY + bounds.maxY) / 2;
    var dx = point.x - centerX;
    var dy = centerY - point.y;
    var angle = Number(degrees || 0) * Math.PI / 180;
    var cos = Math.cos(angle), sin = Math.sin(angle);
    return {
        x: centerX + dx * cos - dy * sin,
        y: centerY - (dx * sin + dy * cos)
    };
}

function tp3dNormalizedPuzzleProjectedPoint(point, bounds, degrees) {
    return tp3dRotatePuzzleProjectedPoint({
        x: bounds.minX + point[0] * (bounds.maxX - bounds.minX),
        y: bounds.maxY - point[1] * (bounds.maxY - bounds.minY)
    }, bounds, degrees);
}

function tp3dRotatedPuzzleProjectedBounds(bounds, degrees) {
    var corners = [
        { x: bounds.minX, y: bounds.minY },
        { x: bounds.maxX, y: bounds.minY },
        { x: bounds.maxX, y: bounds.maxY },
        { x: bounds.minX, y: bounds.maxY }
    ].map(function(point) { return tp3dRotatePuzzleProjectedPoint(point, bounds, degrees); });
    return {
        minX: Math.min.apply(null, corners.map(function(point) { return point.x; })),
        maxX: Math.max.apply(null, corners.map(function(point) { return point.x; })),
        minY: Math.min.apply(null, corners.map(function(point) { return point.y; })),
        maxY: Math.max.apply(null, corners.map(function(point) { return point.y; }))
    };
}

function tp3dPuzzleOutlineProjectedPoints(pieces, bounds, degrees) {
    var points = [];
    pieces.forEach(function(piece) {
        (piece.puzzlePoints || []).forEach(function(point) {
            points.push(tp3dNormalizedPuzzleProjectedPoint(point, bounds, degrees));
        });
    });
    points.sort(function(a, b) { return a.x - b.x || a.y - b.y; });
    function cross(origin, a, b) {
        return (a.x - origin.x) * (b.y - origin.y) - (a.y - origin.y) * (b.x - origin.x);
    }
    var lower = [];
    points.forEach(function(point) {
        while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) lower.pop();
        lower.push(point);
    });
    var upper = [];
    points.slice().reverse().forEach(function(point) {
        while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) upper.pop();
        upper.push(point);
    });
    return lower.slice(0, -1).concat(upper.slice(0, -1));
}

function tp3dPuzzleEditHandleLatLngs(pieces, bounds, degrees) {
    var points = tp3dPuzzleOutlineProjectedPoints(pieces, bounds, degrees);
    if (points.length < 4) return null;
    var rotatedBounds = tp3dRotatedPuzzleProjectedBounds(bounds, degrees);
    var centerX = (rotatedBounds.minX + rotatedBounds.maxX) / 2;
    var centerY = (rotatedBounds.minY + rotatedBounds.maxY) / 2;
    // The puzzle's own SW/SE/NE/NW diagonals (y-up), rotated with it --
    // applyRotatedEditCornerDrag treats each handle as that local corner, so
    // picking by screen diagonals instead mislabels them past ~45 degrees.
    var angle = Number(degrees || 0) * Math.PI / 180;
    var cos = Math.cos(angle), sin = Math.sin(angle);
    var directions = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(function(local) {
        return [local[0] * cos - local[1] * sin, -(local[0] * sin + local[1] * cos)];
    });
    return directions.map(function(direction) {
        var selected = points[0], best = -Infinity;
        points.forEach(function(point) {
            var score = direction[0] * (point.x - centerX) + direction[1] * (point.y - centerY);
            if (score > best) { selected = point; best = score; }
        });
        var latlng = map.unproject([selected.x, selected.y], 0);
        return [latlng.lat, latlng.lng];
    });
}

function tp3dPuzzleOutlineLatLngs(pieces, bounds, degrees) {
    return tp3dPuzzleOutlineProjectedPoints(pieces, bounds, degrees).map(function(point) {
        var latlng = map.unproject([point.x, point.y], 0);
        return [latlng.lat, latlng.lng];
    });
}

function tp3dPuzzleProjectedBoundsToGeographic(bounds) {
    var northWest = map.unproject([bounds.minX, bounds.minY], 0);
    var southEast = map.unproject([bounds.maxX, bounds.maxY], 0);
    return {
        north: northWest.lat, south: southEast.lat,
        east: southEast.lng, west: northWest.lng
    };
}

function tp3dApplySvgRotation(overlay) {
    if (!overlay) return;
    requestAnimationFrame(function() {
        var image = overlay.getElement();
        if (!image) return;
        var degrees = Number(TP3D_SHAPE_ROTATION_DEG || 0);
        var transform = image.style.transform.replace(/\s*rotate\([^)]*\)/g, '');
        image.style.transformOrigin = 'center center';
        image.style.transform = transform + ' rotate(' + (-degrees) + 'deg)';
    });
}

function previewDrawnRect(bounds) {
    drawnItems.clearLayers();
    var style = tp3dRectStyle();
    var rect = L.rectangle([[bounds.south, bounds.west], [bounds.north, bounds.east]], {
        color: '#E97826', weight: style.weight, opacity: 1,
        fill: style.fill, fillOpacity: style.fillOpacity
    });
    drawnItems.addLayer(rect);
    rectLayer = rect;
    return rect;
}

// Picks whichever of the shape's 4 corners is visually closest (in
// screen pixels, so it works the same at any zoom level/latitude) to
// a click -- lets the user grab the border itself to resize, not
// just the small handle markers.
function nearestCorner(latlng, b) {
    var p = map.latLngToLayerPoint(latlng);
    var corners = {
        NE: map.latLngToLayerPoint([b.north, b.east]),
        NW: map.latLngToLayerPoint([b.north, b.west]),
        SE: map.latLngToLayerPoint([b.south, b.east]),
        SW: map.latLngToLayerPoint([b.south, b.west])
    };
    var best = 'NE', bestDist = Infinity;
    Object.keys(corners).forEach(function(k) {
        var dx = p.x - corners[k].x, dy = p.y - corners[k].y;
        var d = dx * dx + dy * dy;
        if (d < bestDist) { bestDist = d; best = k; }
    });
    return best;
}

// Applies a dragged edge's new lat/lng, mirroring the opposite edge
// around shapeCenter in 'center' mode (so the center never moves) or
// leaving it fixed in 'corner' mode -- the same rule applyCornerDrag
// (each axis independently) and bindCenterEdit's handle markers use.
// Clamped so an edge can never get dragged past its own opposite (or,
// in 'center' mode, past the center point itself), which would
// otherwise invert the box.
var MIN_DEG = 1e-6;

function applyEdgeDrag(edge, value) {
    if (drawMode === 'center' && shapeCenter) {
        if (edge === 'north') {
            coords.north = Math.max(value, shapeCenter.lat + MIN_DEG);
            coords.south = 2 * shapeCenter.lat - coords.north;
        } else if (edge === 'south') {
            coords.south = Math.min(value, shapeCenter.lat - MIN_DEG);
            coords.north = 2 * shapeCenter.lat - coords.south;
        } else if (edge === 'east') {
            coords.east = Math.max(value, shapeCenter.lng + MIN_DEG);
            coords.west = 2 * shapeCenter.lng - coords.east;
        } else {
            coords.west = Math.min(value, shapeCenter.lng - MIN_DEG);
            coords.east = 2 * shapeCenter.lng - coords.west;
        }
    } else if (edge === 'north') {
        coords.north = Math.max(value, coords.south + MIN_DEG);
    } else if (edge === 'south') {
        coords.south = Math.min(value, coords.north - MIN_DEG);
    } else if (edge === 'east') {
        coords.east = Math.max(value, coords.west + MIN_DEG);
    } else {
        coords.west = Math.min(value, coords.east - MIN_DEG);
    }
}

// A corner drag moves both of its axes at once -- e.g. dragging the
// NE corner sets north from the new lat AND east from the new lng,
// each going through applyEdgeDrag above so 'center' mode still
// mirrors both the opposite lat edge and the opposite lng edge
// around shapeCenter.
function applyCornerDrag(corner, latlng) {
    if (tp3dSquareLocked()) {
        applySquareCornerDrag(corner, latlng);
        return;
    }
    applyEdgeDrag(corner[0] === 'N' ? 'north' : 'south', latlng.lat);
    applyEdgeDrag(corner[1] === 'E' ? 'east' : 'west', latlng.lng);
}

// ---------------------------------------------------------------------------
// Shift = lock the box to 1:1.
//
// "Square" here means square in the projected plane (map.project(_, 0)) --
// the same plane the backend scales from and #totalSizeInfo measures width and
// height in -- NOT equal lat/lng spans. A degree of longitude is only
// cos(lat) as long as a degree of latitude, so a box that is square in degrees
// is a visibly wide/tall rectangle on screen and in the printed result.
//
// Covers every path that draws or resizes the box:
//   - 'center' draw          -> centerDrawBounds (armCenterDraw)
//   - 'center' handle/border -> applySquareCornerDrag via applyCornerDrag
//   - 'corner' border drag   -> applySquareCornerDrag via applyCornerDrag
//   - 'corner' draw          -> L.Draw.Rectangle._drawShape patch below
//   - 'corner' leaflet-draw handles -> L.Edit.Rectangle._onMarkerDrag patch below
// Shift only constrains the gesture in progress; releasing it goes back to
// free-form resizing from wherever the box ended up.
// ---------------------------------------------------------------------------
var tp3dShiftHeld = false;
var tp3dAltHeld = false;
(function() {
    function track(e) {
        tp3dShiftHeld = !!e.shiftKey;
        tp3dAltHeld = !!e.altKey;
    }
    // Capture phase on document so this runs before Leaflet's own handlers,
    // and mouse events re-sync the flag in case Shift changed while the
    // window wasn't focused.
    ['keydown', 'keyup', 'mousedown', 'mousemove', 'mouseup'].forEach(function(type) {
        document.addEventListener(type, track, true);
    });
    window.addEventListener('blur', function() {
        tp3dShiftHeld = false;
        tp3dAltHeld = false;
    });
})();

// Shift, or a page whose current shape is always 1:1 (tp3dKeepSquare).
function tp3dSquareLocked() {
    return tp3dShiftHeld || (typeof tp3dKeepSquare === 'function' && tp3dKeepSquare());
}

// Smallest box a Shift-drag can collapse to, in zoom-0 projected px (~15 m at
// the equator) -- keeps a 'center'-mode box from going zero-size, like
// applyEdgeDrag's MIN_DEG clamp does for free-form edits.
var TP3D_MIN_SQUARE_SIDE = 1e-4;

// The point that makes a projected-plane square with *anchor* as one corner and
// *latlng* steering the opposite one. signX/signY (+1/-1, in projected axes,
// so y grows southward) force which quadrant the square extends into -- used
// when the dragged corner is known; omitted, the quadrant follows the cursor.
// The square grows to whichever axis the cursor has moved further along.
function tp3dSquareFromAnchor(anchor, latlng, signX, signY, minSide) {
    var a = map.project(L.latLng(anchor), 0);
    var p = map.project(L.latLng(latlng), 0);
    var dx = p.x - a.x, dy = p.y - a.y;
    var sx = signX || (dx < 0 ? -1 : 1);
    var sy = signY || (dy < 0 ? -1 : 1);
    var side = Math.max(sx * dx, sy * dy, minSide || 0);
    return map.unproject([a.x + sx * side, a.y + sy * side], 0);
}

// applyCornerDrag's Shift branch. 'corner' mode: the opposite corner stays
// put and the dragged corner snaps to the square. 'center' mode: shapeCenter
// stays put and the opposite corner mirrors the dragged one through it in the
// projected plane (applyEdgeDrag mirrors in degrees, which can't stay square,
// so all four edges are set directly here).
function applySquareCornerDrag(corner, latlng) {
    var north = corner[0] === 'N', east = corner[1] === 'E';
    var signX = east ? 1 : -1;
    var signY = north ? -1 : 1;
    if (drawMode === 'center' && shapeCenter) {
        var dragged = tp3dSquareFromAnchor(shapeCenter, latlng, signX, signY, TP3D_MIN_SQUARE_SIDE);
        var c = map.project(L.latLng(shapeCenter), 0);
        var d = map.project(dragged, 0);
        var opposite = map.unproject([2 * c.x - d.x, 2 * c.y - d.y], 0);
        if (north) { coords.north = dragged.lat; coords.south = opposite.lat; }
        else { coords.south = dragged.lat; coords.north = opposite.lat; }
        if (east) { coords.east = dragged.lng; coords.west = opposite.lng; }
        else { coords.west = dragged.lng; coords.east = opposite.lng; }
    } else {
        var anchor = {
            lat: north ? coords.south : coords.north,
            lng: east ? coords.west : coords.east
        };
        var squared = tp3dSquareFromAnchor(anchor, latlng, signX, signY, TP3D_MIN_SQUARE_SIDE);
        applyEdgeDrag(north ? 'north' : 'south', squared.lat);
        applyEdgeDrag(east ? 'east' : 'west', squared.lng);
    }
}

// Bounds for 'center' draw mode: symmetric around *center*, independently per
// axis in degrees normally, a projected-plane square while Shift is held.
// Returns identical north/south when latlng === center either way, so
// finalizeCenterShape's zero-size click guard still works.
function centerDrawBounds(center, latlng) {
    if (tp3dSquareLocked()) {
        var c = map.project(L.latLng(center), 0);
        var p = map.project(L.latLng(latlng), 0);
        var half = Math.max(Math.abs(p.x - c.x), Math.abs(p.y - c.y));
        var ne = map.unproject([c.x + half, c.y - half], 0);
        var sw = map.unproject([c.x - half, c.y + half], 0);
        return { north: ne.lat, south: sw.lat, east: ne.lng, west: sw.lng };
    }
    var dLat = Math.abs(latlng.lat - center.lat);
    var dLng = Math.abs(latlng.lng - center.lng);
    return {
        north: center.lat + dLat, south: center.lat - dLat,
        east: center.lng + dLng, west: center.lng - dLng
    };
}

// 'corner' mode hands drawing and handle-dragging to leaflet-draw, which has
// no square constraint of its own, so both are wrapped. Both read the shared
// tp3dShiftHeld flag since leaflet-draw doesn't pass the mouse event through.
// Idempotent, and retried on page load in case leaflet-draw is loaded after
// this file. A marker patched mid-drag is also moved onto the squared corner,
// so the handle doesn't visibly sit off the box while Shift is held.
function tp3dPatchDrawLibs() {
    if (typeof L === 'undefined') return;
    if (L.Draw && L.Draw.Rectangle && !L.Draw.Rectangle.prototype._tp3dShiftSquare) {
        var origDrawShape = L.Draw.Rectangle.prototype._drawShape;
        L.Draw.Rectangle.prototype._drawShape = function(latlng) {
            if (tp3dSquareLocked() && this._startLatLng) {
                latlng = tp3dSquareFromAnchor(this._startLatLng, latlng);
            }
            return origDrawShape.call(this, latlng);
        };
        L.Draw.Rectangle.prototype._tp3dShiftSquare = true;
    }
    if (L.Edit && L.Edit.Rectangle && !L.Edit.Rectangle.prototype._tp3dShiftSquare) {
        var origMarkerDrag = L.Edit.Rectangle.prototype._onMarkerDrag;
        L.Edit.Rectangle.prototype._onMarkerDrag = function(e) {
            var marker = e.target;
            if (tp3dSquareLocked() && this._oppositeCorner && marker !== this._moveMarker) {
                marker.setLatLng(tp3dSquareFromAnchor(this._oppositeCorner, marker.getLatLng()));
            }
            return origMarkerDrag.call(this, e);
        };
        L.Edit.Rectangle.prototype._tp3dShiftSquare = true;
    }
    // L.Draggable refuses to start a drag while Shift is down (reserved for
    // box zoom, which tp3dInstallShiftSquare disables anyway), so Shift held
    // *before* grabbing a handle did nothing. Marker handles (rotation, move,
    // corners) only: shiftKey is shadowed for that one call so Shift-then-grab
    // works -- tp3dShiftHeld was already set by the capture-phase tracker.
    if (L.Draggable && !L.Draggable.prototype._tp3dShiftGrab) {
        var origDown = L.Draggable.prototype._onDown;
        L.Draggable.prototype._onDown = function(e) {
            if (!e.shiftKey || !L.DomUtil.hasClass(this._element, 'leaflet-marker-icon')) {
                return origDown.call(this, e);
            }
            Object.defineProperty(e, 'shiftKey', { value: false, configurable: true });
            try {
                return origDown.call(this, e);
            } finally {
                delete e.shiftKey;
            }
        };
        L.Draggable.prototype._tp3dShiftGrab = true;
    }
}

// Leaflet's default Shift+drag is box-zoom, which would fire alongside every
// Shift-draw/resize that starts on the map itself (e.g. dragging the border).
// `map` is only touched once the page has finished loading.
function tp3dInstallShiftSquare() {
    tp3dPatchDrawLibs();
    if (typeof map !== 'undefined' && map && map.boxZoom) map.boxZoom.disable();
}
tp3dPatchDrawLibs();
if (document.readyState === 'complete') tp3dInstallShiftSquare();
else window.addEventListener('load', tp3dInstallShiftSquare);

// Set by bindCenterEdit() while its handle markers exist, so a
// border-drag (bindCornerDrag below) can keep them in sync too
// without needing to know anything about them directly.
var syncCenterHandles = null;
var rotatedCornerHandles = [];
var rotatedCornerEditActive = false;

function getRotatedEditHandlePoints() {
    if (typeof tp3dRotatedEditHandlePoints !== 'function') return null;
    var points = tp3dRotatedEditHandlePoints();
    return points && points.length === 4 ? points : null;
}

// The true rotated frame corners (SW, SE, NE, NW) the edit handles stand in
// for. Puzzle handles sit on the piece outline, which can be inset from the
// frame (rounded corners, hex/radial pieces), so their frame is the rotated
// tp3dRotationHandleBounds() itself; elsewhere the handles are the corners.
function getRotatedEditFramePoints() {
    if (typeof tp3dPuzzleEditHandlePoints !== 'function') return getRotatedEditHandlePoints();
    return tp3dProjectedShapePoints(tp3dRotationHandleBounds(), 'rectangle', TP3D_SHAPE_ROTATION_DEG);
}

function applyRotatedEditCornerDrag(corner, latlng, mode) {
    var points = getRotatedEditHandlePoints();
    if (!points) return;
    var frame = getRotatedEditFramePoints();
    var originalBounds = tp3dRotationHandleBounds();
    var cornerIndex = { SW: 0, SE: 1, NE: 2, NW: 3 }[corner];
    // Moves the frame corner by the cursor, keeping the handle's own inset
    // from it -- treating the inset handle as the corner itself made the
    // shape creep by that inset on every drag event.
    var handle = map.project(points[cornerIndex], 0);
    var frameCorner = map.project(frame[cornerIndex], 0);
    var cursor = map.project(latlng, 0);
    var target = L.point(cursor.x + frameCorner.x - handle.x, cursor.y + frameCorner.y - handle.y);
    var angle = Number(TP3D_SHAPE_ROTATION_DEG) * Math.PI / 180;
    var cos = Math.cos(angle), sin = Math.sin(angle);
    var signs = { SW: [-1, -1], SE: [1, -1], NE: [1, 1], NW: [-1, 1] }[corner];
    // width/height to hold the frame to, or null for a free resize.
    var aspect = typeof tp3dEditAspectRatio === 'function'
        ? tp3dEditAspectRatio()
        : (currentShape !== 'rectangle' ? 1 : null);
    function lockAspect() {
        if (!(aspect > 0)) return;
        halfWidth = Math.max(halfWidth, aspect * halfHeight);
        halfHeight = halfWidth / aspect;
    }
    var center, halfWidth, halfHeight, dx, dy;
    if (mode === 'center') {
        center = map.project(tp3dProjectedBoundsCenter(originalBounds, 0), 0);
        dx = target.x - center.x;
        dy = center.y - target.y;
        halfWidth = Math.max(signs[0] * (dx * cos + dy * sin), 1e-4);
        halfHeight = Math.max(signs[1] * (-dx * sin + dy * cos), 1e-4);
        lockAspect();
    } else {
        // Sized from the fixed opposite frame corner, so an aspect-locked
        // shape grows away from it instead of around the drag's midpoint.
        var opposite = map.project(frame[(cornerIndex + 2) % 4], 0);
        dx = target.x - opposite.x;
        dy = opposite.y - target.y;
        halfWidth = Math.max(signs[0] * (dx * cos + dy * sin), 2e-4) / 2;
        halfHeight = Math.max(signs[1] * (-dx * sin + dy * cos), 2e-4) / 2;
        lockAspect();
        var offsetX = signs[0] * halfWidth, offsetY = signs[1] * halfHeight;
        center = L.point(
            opposite.x + offsetX * cos - offsetY * sin,
            opposite.y - (offsetX * sin + offsetY * cos)
        );
    }
    var northWest = map.unproject([center.x - halfWidth, center.y - halfHeight], 0);
    var southEast = map.unproject([center.x + halfWidth, center.y + halfHeight], 0);
    coords = {
        north: northWest.lat, south: southEast.lat,
        east: southEast.lng, west: northWest.lng, type: currentShape
    };
    if (mode === 'center') {
        var fixedCenter = map.unproject(center, 0);
        shapeCenter = { lat: fixedCenter.lat, lng: fixedCenter.lng };
    }
}

function clearRotatedCornerEditHandles() {
    rotatedCornerHandles = [];
    rotatedCornerEditActive = false;
    editHandles.clearLayers();
}

function syncRotatedCornerEditHandles() {
    var points = getRotatedEditHandlePoints();
    if (!coords || !rectLayer || drawMode !== 'corner' || !points) {
        if (rotatedCornerEditActive) {
            clearRotatedCornerEditHandles();
            if (drawMode === 'corner' && coords && rectLayer) bindLiveEdit(rectLayer);
        }
        if (syncCenterHandles) syncCenterHandles();
        return;
    }

    if (!rotatedCornerEditActive || !editHandles.hasLayer(rotatedCornerHandles[0])) {
        if (rectLayer.editing && rectLayer.editing.enabled()) rectLayer.editing.disable();
        editHandles.clearLayers();
        rotatedCornerHandles = [];
        rotatedCornerEditActive = true;
        ['SW', 'SE', 'NE', 'NW'].forEach(function(corner, index) {
            var marker = L.marker(points[index], {
                draggable: true,
                icon: L.divIcon({
                    className: 'edge-handle',
                    iconSize: [10, 10],
                    iconAnchor: [5, 5]
                })
            });
            marker.on('drag', function(e) {
                applyRotatedEditCornerDrag(corner, e.target.getLatLng(), 'corner');
                syncShapeAfterEdgeChange();
            });
            marker.on('dragend', saveState);
            rotatedCornerHandles.push(marker);
            editHandles.addLayer(marker);
        });
    } else {
        rotatedCornerHandles.forEach(function(marker, index) {
            marker.setLatLng(points[index]);
        });
    }
    if (syncCenterHandles) syncCenterHandles();
}

function syncShapeAfterEdgeChange() {
    if (rectLayer) rectLayer.setBounds([[coords.south, coords.west], [coords.north, coords.east]]);
    if (syncCenterHandles) syncCenterHandles();
    syncMoveHandlePosition();
    syncRotationHandlePosition();
    tp3dOnShapeChanged();
}

// Repositions the move handle onto the shape's current center without
// recreating it -- called after any resize (corner drag, edge-handle
// drag, leaflet-draw's own editing) so it doesn't drift off-center.
// bindMoveHandle() itself only runs when the shape is (re)created.
function syncMoveHandlePosition() {
    if (!coords) return;
    moveHandleLayer.eachLayer(function(m) { m.setLatLng(boundsCenter(coords)); });
}

function rotationHandlePosition() {
    var shapeBounds = typeof tp3dRotationHandleBounds === 'function'
        ? tp3dRotationHandleBounds() : coords;
    if (!shapeBounds) return null;
    var zoom = map.getZoom();
    var centerLatLng = tp3dProjectedBoundsCenter(shapeBounds, zoom);
    var center = map.project([centerLatLng.lat, centerLatLng.lng], zoom);
    var points = tp3dRotationHandlePoints();
    if (!points.length) return null;
    var angle = Number(TP3D_SHAPE_ROTATION_DEG || 0) * Math.PI / 180;
    var cos = Math.cos(angle), sin = Math.sin(angle);
    var right = -Infinity, top = -Infinity, bottom = Infinity;
    // Undo rotation to find the shape's own box, not its screen-aligned bbox.
    points.forEach(function(point) {
        var projected = map.project(point, zoom);
        var x = projected.x - center.x, y = center.y - projected.y;
        right = Math.max(right, x * cos + y * sin);
        var localY = -x * sin + y * cos;
        top = Math.max(top, localY);
        bottom = Math.min(bottom, localY);
    });
    var gap = 25;
    var x = right + gap, y = (top + bottom) / 2;
    return {
        center: centerLatLng,
        handle: map.unproject([
            center.x + x * cos - y * sin,
            center.y - x * sin - y * cos
        ], zoom)
    };
}

function syncRotationHandlePosition() {
    if (!rotationHandleLayer) return;
    rotationHandleLayer.eachLayer(function(marker) {
        var element = marker.getElement();
        var svg = element && element.querySelector('svg');
        if (!svg) return;
        svg.style.transformOrigin = 'center';
        svg.style.transformBox = 'view-box';
        svg.style.rotate = -Number(TP3D_SHAPE_ROTATION_DEG || 0) + 'deg';
    });
    if (rotationHandleDragging) return;
    if (!coords) {
        rotationHandleLayer.clearLayers();
        return;
    }
    if (typeof tp3dCanRotateShape === 'function' && !tp3dCanRotateShape()) {
        rotationHandleLayer.clearLayers();
        return;
    }
    var position = rotationHandlePosition();
    if (!position) return;
    var layers = [];
    rotationHandleLayer.eachLayer(function(layer) { layers.push(layer); });
    if (layers.length < 1) {
        bindRotationHandle();
        return;
    }
    layers[0].setLatLng(position.handle);
}

function bindRotationHandle() {
    if (!rotationHandleLayer) return;
    rotationHandleLayer.clearLayers();
    if (!coords || (typeof tp3dCanRotateShape === 'function' && !tp3dCanRotateShape())) return;
    var position = rotationHandlePosition();
    if (!position) return;
    var marker = L.marker(position.handle, {
        draggable: true,
        icon: L.divIcon({
            className: 'rotation-handle',
            iconSize: [30, 30],
            iconAnchor: [15, 15],
            html: '<svg fill="#ffffff" version="1.1" id="Layer_1" width="86.153366" height="181.74644" viewBox="-1.2 -1.2 11.315666 23.871175" enable-background="new 0 0 24 24" xml:space="preserve" stroke="#ffffff" stroke-width="0.00024" xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg" xmlns:ns="&amp;#38;ns_sfw;"><defs   id="defs22" /><g   id="SVGRepo_iconCarrier"   transform="translate(-7.5400526,-1.2641641)"> <metadata id="metadata11"> <ns:sfw> <ns:slices> </ns:slices> <ns:slicesourcebounds width="505" height="984" bottomleftorigin="true" x="0" y="-984"> </ns:slicesourcebounds> </ns:sfw> </metadata>  <path id="path11" d="m 14.606411,23.008225 c 0.152893,-0.152893 0.244742,-0.366913 0.244743,-0.611542 -10e-7,-0.489256 -0.367029,-0.856284 -0.856285,-0.856285 l -4.0363662,1e-6 5.2594512,-5.259451 c 2.385125,-2.385125 2.385125,-6.176777 -1e-6,-8.5619022 L 9.9585028,2.4595956 h 4.0363662 c 0.489257,0 0.856285,-0.367027 0.856284,-0.856284 0,-0.489257 -0.367027,-0.85628397 -0.856284,-0.85628497 H 8.0884208 c -0.253988,-0.07247 -0.52237,-0.01245 -0.751955,0.181518 -0.02305,0.01923 -0.04594,0.04029 -0.06888,0.06322 -0.02293,0.02293 -0.04399,0.04583 -0.06322,0.06888 -0.193914,0.22951 -0.253874,0.497725 -0.181517,0.751642 v 5.9067615 c 0,0.4892571 0.367027,0.8562844 0.856284,0.8562844 0.489257,7e-7 0.856285,-0.3670273 0.856285,-0.8562844 l -1e-6,-4.0363655 5.2594502,5.2594504 c 1.712398,1.712398 1.712398,4.403337 1e-6,6.115734 l -5.2594512,5.25945 v -4.036366 c 0,-0.489256 -0.367028,-0.856284 -0.856284,-0.856284 -0.244629,0 -0.428071,0.06127 -0.611542,0.244743 -0.183471,0.183471 -0.244741,0.366915 -0.244743,0.611541 l 1e-6,5.906762 c -0.07973,0.279773 0.0012,0.576981 0.244743,0.820514 0.243607,0.243607 0.540981,0.32459 0.820827,0.244743 h 5.9064482 c 0.244628,0 0.458649,-0.09185 0.611542,-0.244742 z" style="fill:#ffffff;fill-opacity:1;stroke:#000000;stroke-width:1.31343284;stroke-linecap:square;stroke-linejoin:miter;stroke-dasharray:none;stroke-opacity:1;paint-order:markers stroke fill" /></g></svg>'
        })
    });
    marker.on('click', function(e) {
        if (!e.originalEvent || !e.originalEvent.altKey) return;
        e.originalEvent.preventDefault();
        e.originalEvent.stopPropagation();
        tp3dSetShapeRotation(0, true);
    });
    var rotationDragStart = null;
    marker.on('dragstart', function(e) {
        position = rotationHandlePosition();
        rotationHandleDragging = true;
        var center = map.project(position.center, map.getZoom());
        var point = map.project(e.target.getLatLng(), map.getZoom());
        rotationDragStart = {
            angle: Math.atan2(point.y - center.y, point.x - center.x),
            degrees: Number(TP3D_SHAPE_ROTATION_DEG || 0)
        };
    });
    marker.on('drag', function(e) {
        var bounds = typeof tp3dRotationHandleBounds === 'function'
            ? tp3dRotationHandleBounds() : coords;
        if (!bounds || !rotationDragStart) return;
        var center = map.project(position.center, map.getZoom());
        var point = map.project(e.target.getLatLng(), map.getZoom());
        var angle = Math.atan2(point.y - center.y, point.x - center.x);
        var delta = (angle - rotationDragStart.angle) * 180 / Math.PI;
        // Shift snaps the final angle to 5 degrees; Shift+Alt snaps to 30.
        var step = tp3dShiftHeld ? (tp3dAltHeld ? 30 : 5) : 1;
        var snapped = Math.round((rotationDragStart.degrees - delta) / step) * step;
        var degrees = ((snapped + 180) % 360 + 360) % 360 - 180;
        tp3dSetShapeRotation(degrees);
    });
    marker.on('dragend', function() {
        rotationHandleDragging = false;
        rotationDragStart = null;
        syncRotationHandlePosition();
        saveState();
        if (typeof updateStatus === 'function') updateStatus();
    });
    rotationHandleLayer.addLayer(marker);
    syncRotationHandlePosition();
}

if (rotationHandleLayer) {
    map.on('zoomend moveend', syncRotationHandlePosition);
}

function moveHandleIcon() {
    return L.divIcon({
        className: 'move-handle', iconSize: [22, 22], iconAnchor: [11, 11],
        html: '<svg viewBox="0 0 20 20" width="14" height="14" fill="#fff"><path d="M10 1l-3 3h2v4H5V6l-3 3 3 3v-2h4v4H7l3 3 3-3h-2v-4h4v2l3-3-3-3v2h-4V4h2z"/></svg>'
    });
}

// A single draggable marker at the shape's center that translates the
// whole rectangle (and shapeCenter, in 'center' mode) without resizing
// it -- unlike bindCornerDrag, which always resizes from the nearest
// corner no matter where on the shape you grab it. In 'corner' mode,
// leaflet-draw's own corner-handle editing is disabled for the drag's
// duration and re-enabled after, same reasoning as bindCornerDrag: its
// markers only reposition themselves via their own drag handlers, so
// they'd otherwise go stale while this moves the layer's bounds
// directly.
function bindMoveHandle() {
    moveHandleLayer.clearLayers();
    bindRotationHandle();
    if (!coords) return;
    var wasCornerEditing = false;
    var dragStart = null, startCoords = null, startCenter = null;
    var marker = L.marker(boundsCenter(coords), { draggable: true, icon: moveHandleIcon() });
    marker.on('dragstart', function(e) {
        wasCornerEditing = drawMode === 'corner' && rectLayer.editing && rectLayer.editing.enabled();
        if (wasCornerEditing) rectLayer.editing.disable();
        dragStart = e.target.getLatLng();
        startCoords = { north: coords.north, south: coords.south, east: coords.east, west: coords.west };
        startCenter = shapeCenter ? { lat: shapeCenter.lat, lng: shapeCenter.lng } : null;
    });
    marker.on('drag', function(e) {
        var cur = e.target.getLatLng();
        var dLat = cur.lat - dragStart.lat, dLng = cur.lng - dragStart.lng;
        coords.north = startCoords.north + dLat;
        coords.south = startCoords.south + dLat;
        coords.east = startCoords.east + dLng;
        coords.west = startCoords.west + dLng;
        if (startCenter) shapeCenter = { lat: startCenter.lat + dLat, lng: startCenter.lng + dLng };
        rectLayer.setBounds([[coords.south, coords.west], [coords.north, coords.east]]);
        if (syncCenterHandles) syncCenterHandles();
        tp3dOnShapeChanged();
        map.fire('tp3d:shapemove'); // map_tutorials.js's tp3dWaitForMove
    });
    marker.on('dragend', function() {
        if (wasCornerEditing) bindLiveEdit(rectLayer);
        saveState();
    });
    moveHandleLayer.addLayer(marker);
}

// Lets the user grab the rectangle's own border (not just a handle
// marker) to resize it by whichever corner is nearest -- works in
// both draw modes, mirroring in 'center' mode via applyCornerDrag
// above. In 'corner' mode, leaflet-draw's own corner-handle editing
// is disabled for the duration of the drag and cleanly re-enabled
// after (which re-reads the layer's bounds fresh) rather than left
// running alongside this -- leaflet-draw only repositions its corner
// markers from their OWN drag handlers, so this layer's bounds
// changing externally would otherwise leave them silently out of
// sync with the shape.
function bindCornerDrag(layer) {
    layer.off('mousedown', layer._tp3dCornerDown);
    layer._tp3dCornerDown = function(e) {
        L.DomEvent.stopPropagation(e);
        if (typeof tp3dPuzzleEditHandlePoints === 'function' && getRotatedEditHandlePoints()) return;
        var wasCornerEditing = drawMode === 'corner' && layer.editing && layer.editing.enabled();
        if (wasCornerEditing) layer.editing.disable();
        var corner = nearestCorner(e.latlng, coords);
        map.dragging.disable();
        function onMove(e2) {
            applyCornerDrag(corner, e2.latlng);
            syncShapeAfterEdgeChange();
        }
        function onUp() {
            map.off('mousemove', onMove);
            map.off('mouseup', onUp);
            map.dragging.enable();
            if (wasCornerEditing) bindLiveEdit(layer);
            saveState();
        }
        map.on('mousemove', onMove);
        map.on('mouseup', onUp);
    };
    layer.on('mousedown', layer._tp3dCornerDown);
}

// Tracks whether a draw tool is currently armed -- i.e. the Draw
// button was clicked and the map is waiting for the user to place a
// shape, but nothing has been finalized yet. Needed so switching the
// corner/center mode mid-arm can cancel the OLD mode's still-active
// handler and re-arm the NEW one -- otherwise the stale handler (e.g.
// leaflet-draw's corner-to-corner rectangle tool) silently completes
// in the wrong mode on the next click/drag, ignoring the mode switch.
var drawArmed = false;
var cancelCenterDraw = null; // set while armCenterDraw's mousedown listener is pending

function armDraw() {
    disarmDraw();
    drawArmed = true;
    if (drawMode === 'center') {
        armCenterDraw();
    } else {
        map.removeControl(drawControl);
        drawControl = makeDrawControl();
        map.addControl(drawControl);
        setTimeout(function() {
            var rb = document.querySelector('.leaflet-draw-draw-rectangle');
            if (rb) rb.click();
        }, 50);
    }
}

function disarmDraw() {
    if (!drawArmed) return;
    drawArmed = false;
    if (cancelCenterDraw) {
        cancelCenterDraw();
        cancelCenterDraw = null;
    }
    var handler = drawControl._toolbars && drawControl._toolbars.draw &&
        drawControl._toolbars.draw._modes.rectangle && drawControl._toolbars.draw._modes.rectangle.handler;
    if (handler && handler.enabled()) handler.disable();
}

// 'Center' draw mode: click sets the center point, dragging outward
// grows the box symmetrically around it (mirrors bindCenterEdit's own
// "opposite edge moves too" editing behavior for the initial draw).
// Bypasses leaflet-draw's own rectangle tool entirely -- that tool is
// inherently corner-to-corner, with no supported way to redefine it
// as center-out.
function armCenterDraw() {
    map.dragging.disable();
    map.getContainer().style.cursor = 'crosshair';
    function onFirstMouseDown(e) {
        cancelCenterDraw = null;
        drawArmed = false;
        var center = e.latlng;
        function onMove(e2) {
            previewDrawnRect(centerDrawBounds(center, e2.latlng));
        }
        function onUp(e3) {
            map.off('mousemove', onMove);
            map.off('mouseup', onUp);
            map.dragging.enable();
            map.getContainer().style.cursor = '';
            finalizeCenterShape(centerDrawBounds(center, e3.latlng), center);
        }
        map.on('mousemove', onMove);
        map.on('mouseup', onUp);
    }
    map.once('mousedown', onFirstMouseDown);
    cancelCenterDraw = function() {
        map.off('mousedown', onFirstMouseDown);
        map.dragging.enable();
        map.getContainer().style.cursor = '';
    };
}

function finalizeCenterShape(bounds, center) {
    if (bounds.north === bounds.south || bounds.east === bounds.west) {
        drawnItems.clearLayers();
        return; // Zero-size click-without-dragging -- nothing to keep.
    }
    coords = { north: bounds.north, south: bounds.south, east: bounds.east, west: bounds.west, type: currentShape };
    shapeCenter = { lat: center.lat, lng: center.lng };
    var rect = previewDrawnRect(coords);
    bindCornerDrag(rect);
    bindCenterEdit();
    bindMoveHandle();
    tp3dOnShapeChanged();
    updateSendState();
    updateStatus();
    saveState();
}

// Custom edit handles for 'center' mode -- NE/NW/SE/SW corner markers
// that, when dragged, move both of that corner's edges AND mirror
// both opposite edges the same distance the other way so shapeCenter
// never moves. Deliberately not leaflet-draw's own L.Edit.Rectangle
// corner handles (used in 'corner' mode via bindLiveEdit) -- those
// are hardcoded to keep the OPPOSITE corner fixed, the opposite of
// what 'center' mode needs, and aren't designed to be reprogrammed
// to mirror instead.
function bindCenterEdit() {
    editHandles.clearLayers();
    syncCenterHandles = null;
    if (!coords || !shapeCenter) return;

    function handleIcon() {
        return L.divIcon({ className: 'edge-handle', iconSize: [10, 10], iconAnchor: [5, 5] });
    }

    var neHandle = L.marker([coords.north, coords.east], { draggable: true, icon: handleIcon() });
    var nwHandle = L.marker([coords.north, coords.west], { draggable: true, icon: handleIcon() });
    var seHandle = L.marker([coords.south, coords.east], { draggable: true, icon: handleIcon() });
    var swHandle = L.marker([coords.south, coords.west], { draggable: true, icon: handleIcon() });

    syncCenterHandles = function() {
        var points = getRotatedEditHandlePoints();
        if (points) {
            swHandle.setLatLng(points[0]);
            seHandle.setLatLng(points[1]);
            neHandle.setLatLng(points[2]);
            nwHandle.setLatLng(points[3]);
        } else {
            neHandle.setLatLng([coords.north, coords.east]);
            nwHandle.setLatLng([coords.north, coords.west]);
            seHandle.setLatLng([coords.south, coords.east]);
            swHandle.setLatLng([coords.south, coords.west]);
        }
    };

    function onCenterCornerDrag(corner, event) {
        if (getRotatedEditHandlePoints()) {
            applyRotatedEditCornerDrag(corner, event.target.getLatLng(), 'center');
        } else {
            applyCornerDrag(corner, event.target.getLatLng());
        }
        syncShapeAfterEdgeChange();
    }
    neHandle.on('drag', function(e) { onCenterCornerDrag('NE', e); });
    nwHandle.on('drag', function(e) { onCenterCornerDrag('NW', e); });
    seHandle.on('drag', function(e) { onCenterCornerDrag('SE', e); });
    swHandle.on('drag', function(e) { onCenterCornerDrag('SW', e); });
    [neHandle, nwHandle, seHandle, swHandle].forEach(function(h) {
        h.on('dragend', saveState);
        editHandles.addLayer(h);
    });
}

// Switching modes applies immediately to whatever's currently drawn,
// not just the next draw action -- corner-to-center adopts the
// current bounding box's own center as the new fixed shapeCenter;
// center-to-corner just drops back to leaflet-draw's own handles.
function setDrawMode(mode) {
    var wasArmed = drawArmed;
    clearRotatedCornerEditHandles();
    drawMode = mode;
    document.querySelectorAll('.draw-mode-btn').forEach(function(b) {
        b.classList.toggle('active', b.getAttribute('data-mode') === mode);
    });
    if (wasArmed) {
        // Draw button was already clicked and the map is still waiting
        // for the user to place a shape -- re-arm with the newly
        // selected mode instead of leaving the old mode's handler
        // active underneath.
        armDraw();
    }
    if (coords && rectLayer) {
        if (mode === 'center') {
            shapeCenter = boundsCenter(coords);
            if (rectLayer.editing) rectLayer.editing.disable();
            bindCenterEdit();
        } else {
            editHandles.clearLayers();
            syncCenterHandles = null;
            shapeCenter = null;
            bindLiveEdit(rectLayer);
        }
        bindCornerDrag(rectLayer);
        bindMoveHandle();
        syncRotatedCornerEditHandles();
    }
    saveState();
}

function makeDrawControl() {
    return new L.Control.Draw({
        draw: { polyline: false, polygon: false, circle: false, marker: false, circlemarker: false, rectangle: true },
        // No edit toolbar — the rectangle is already always draggable via
        // bindLiveEdit(), and drawing a new one clears the old via
        // drawnItems.clearLayers(), so the separate Edit/Delete buttons
        // would just be redundant UI.
        edit: false
    });
}

// Measures an imported SVG's TRUE geometric aspect ratio (width/height of its
// actual drawn content), for the live preview to match what the backend will
// actually generate.
//
// Deliberately does NOT trust the SVG's own declared viewBox/width/height --
// those describe the nominal canvas, not the artwork's real extent (a common
// mismatch: icon/logo SVGs often have padding, or hand-authored viewBoxes
// that don't tightly fit the actual paths). The backend
// (primitives.polygon_from_svg, via Blender's own curve importer) scales
// strictly from the REAL geometry's bounding box, so previewing from the
// declared canvas size instead can show a visibly different aspect ratio
// than what actually gets generated -- confirmed with a 100x100 (1:1)
// viewBox containing only a 40x20 (2:1) rect: the backend generates a 2:1
// shape while a viewBox-based preview would have shown 1:1, i.e. a shape
// that doesn't line up with the box the user actually drew/positioned.
//
// Works by briefly inserting the SVG into the live DOM (invisible, zero
// layout footprint) and reading SVGGraphicsElement.getBBox() off the root
// <svg> element, which computes the tight bounding box of its rendered
// content -- the same kind of real-geometry measurement Blender's own
// importer performs, just via the browser's SVG engine instead of Blender's.
// Falls back to 1:1 if the markup fails to parse/render.
function computeSvgAspectFromMarkup(svgText) {
    var container = document.createElement('div');
    container.style.cssText = 'position:absolute; width:0; height:0; overflow:hidden; visibility:hidden;';
    document.body.appendChild(container);
    var aspect = 1;
    try {
        container.innerHTML = svgText;
        var svgEl = container.querySelector('svg');
        if (svgEl && typeof svgEl.getBBox === 'function') {
            var bbox = svgEl.getBBox();
            if (bbox.width > 0 && bbox.height > 0) aspect = bbox.width / bbox.height;
        }
    } catch (err) { /* malformed/empty SVG -- keep the 1:1 fallback */ }
    document.body.removeChild(container);
    return aspect;
}

// Inscribes a WxH box (aspect = width/height, from computeSvgAspectFromMarkup)
// centered within *b*, preserving the SVG's true proportions instead of
// stretching it to fill a square box -- matches how the backend's own
// create_custom_svg (primitives.polygon_from_svg -> clean_and_union_geometry)
// scales the SVG uniformly to its longest dimension rather than independently
// on each axis, using the same projected plane as the generated map.
function svgInscribedBounds(b, aspect) {
    var northWest = map.project([b.north, b.west], 0);
    var southEast = map.project([b.south, b.east], 0);
    var centerX = (northWest.x + southEast.x) / 2;
    var centerY = (northWest.y + southEast.y) / 2;
    var side = Math.min(
        Math.abs(southEast.x - northWest.x),
        Math.abs(southEast.y - northWest.y)
    );
    var width = aspect >= 1 ? side : side * aspect;
    var height = aspect >= 1 ? side / aspect : side;
    var imageNorthWest = map.unproject([centerX - width / 2, centerY - height / 2], 0);
    var imageSouthEast = map.unproject([centerX + width / 2, centerY + height / 2], 0);
    return {
        north: imageNorthWest.lat,
        south: imageSouthEast.lat,
        east: imageSouthEast.lng,
        west: imageNorthWest.lng
    };
}

// Fills the sidebar's #totalSizeInfo readout (map_generator.html,
// premium/map_generator_pe.html, premium/multitile_generator.html and the
// puzzle pages, see tp3dUpdatePuzzleSizeReadout) --
// empties it (hidden via :empty) when there's nothing to size yet.
// rotationDeg null = rotation doesn't apply to the current shape.
// scaleDenominator = ground meters per printed meter (the N in 1:N).
function tp3dSetSizeReadout(widthMm, heightMm, rotationDeg, scaleDenominator) {
    var el = document.getElementById('totalSizeInfo');
    if (!el) return;
    if (!(widthMm > 0) || !(heightMm > 0)) { el.textContent = ''; return; }
    var text = 'Total size: ' + Math.round(widthMm) + ' × ' + Math.round(heightMm) + ' mm'
        + '\nRotation: ' + (rotationDeg == null ? 'n/a' : rotationDeg + '°');
    if (scaleDenominator > 0) {
        // 3 significant figures -- a print scale is never meaningful beyond that.
        var magnitude = Math.pow(10, Math.max(0, Math.floor(Math.log10(scaleDenominator)) - 2));
        text += '\nScale: 1:' + (Math.round(scaleDenominator / magnitude) * magnitude).toLocaleString('en-US');
    }
    el.textContent = text;
}

// 1:N print scale at latitude *lat* (degrees), for a map whose Mercator
// x-axis (longitude in radians) prints at *mmPerRadian*. Mercator is only
// true-scale along one parallel, so this is the scale at that latitude.
function tp3dScaleDenominator(mmPerRadian, lat) {
    var earthRadiusM = 6371000; // same as constants.R (backend Mercator projection)
    if (!(mmPerRadian > 0)) return 0;
    return earthRadiusM * Math.cos(lat * Math.PI / 180) * 1000 / mmPerRadian;
}

// Printed footprint of the single-tile map pages (map_generator.html,
// premium/map_generator_pe.html), mirroring the backend's map picker
// (operators.py): sScaleHor = objSize / max(width, height) of the sent
// bounds in Web Mercator -- the same linear plane as map.project(_, 0) --
// and the rotated outline retains its drawn dimensions
// (tp3dProjectedShapePoints applies that same rotation). A GeoJSON boundary wins
// over a drawn area there, and is sized from its combined bbox.
function tp3dUpdateMapSizeReadout() {
    var objSize = parseFloat(document.getElementById('objSizeInput').value) || 0;
    var frame = null, points = null, rotation = null;
    var geojsonBounds = geojsonLayer.getBounds();
    if (geojsonPaths.length && geojsonBounds.isValid()) {
        frame = { north: geojsonBounds.getNorth(), south: geojsonBounds.getSouth(),
                  east: geojsonBounds.getEast(), west: geojsonBounds.getWest() };
        points = [[frame.north, frame.west], [frame.south, frame.east]];
    } else if (coords && (currentShape !== 'svg' || svgShapePath)) {
        frame = effectiveBounds(coords);
        rotation = tp3dCanRotateShape() ? TP3D_SHAPE_ROTATION_DEG : null;
        points = currentShape === 'svg'
            ? tp3dProjectedShapePoints(svgInscribedBounds(frame, svgAspect), 'rectangle', rotation || 0)
            : tp3dProjectedShapePoints(frame, currentShape, rotation || 0);
    }
    if (!frame || !(objSize > 0)) { tp3dSetSizeReadout(0, 0, null); return; }
    var northWest = map.project([frame.north, frame.west], 0);
    var southEast = map.project([frame.south, frame.east], 0);
    var extent = Math.max(Math.abs(southEast.x - northWest.x), Math.abs(southEast.y - northWest.y));
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    points.forEach(function(point) {
        var p = map.project(point, 0);
        minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
        minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    });
    var scale = extent > 0 ? objSize / extent : 0;
    // Zoom 0 spans the full 2*PI radians of longitude across 256 px.
    var centerLat = tp3dProjectedBoundsCenter(frame, 0).lat;
    tp3dSetSizeReadout((maxX - minX) * scale, (maxY - minY) * scale, rotation,
        tp3dScaleDenominator(scale * 256 / (2 * Math.PI), centerLat));
}

// Printed footprint of the puzzle pages (puzzleGenerator.html,
// premium/puzzleGenerator_pe.html, premium/slidingPuzzleGenerator.html):
// the puzzle is exactly Length x Width however it's rotated over the map
// (the round shapes keep both fields equal to their Diameter), and the
// page's tp3dPuzzleBaseBounds() is that footprint on the map -- Length
// along x -- so its projected width gives the print scale.
function tp3dUpdatePuzzleSizeReadout() {
    var lengthMm = parseFloat(document.getElementById('lengthInput').value) || 0;
    var widthMm = parseFloat(document.getElementById('widthInput').value) || 0;
    var base = tp3dPuzzleBaseBounds();
    if (!base) { tp3dSetSizeReadout(0, 0, null); return; }
    var projected = tp3dPuzzleProjectedBounds(base);
    var spanX = projected.maxX - projected.minX;
    var scale = spanX > 0 ? lengthMm / spanX : 0; // mm per zoom-0 px
    tp3dSetSizeReadout(lengthMm, widthMm, TP3D_SHAPE_ROTATION_DEG,
        tp3dScaleDenominator(scale * 256 / (2 * Math.PI), tp3dProjectedBoundsCenter(base, 0).lat));
}

// Auto-places a fresh box centered on the current viewport, sized to
// *fraction* of it on each axis, so an imported SVG shape is immediately
// visible without the user having to scroll to find it or draw an area
// themselves first -- dragging/resizing/moving afterward works exactly like
// any other freshly-drawn shape (same bind* calls the CREATED/finalizeCenterShape
// paths use). Replaces whatever was previously drawn, same as importing a
// GeoJSON boundary already replaces a drawn rectangle elsewhere.
function placeSvgBoxInViewport(fraction) {
    var vb = map.getBounds();
    var centerLat = (vb.getNorth() + vb.getSouth()) / 2;
    var centerLng = (vb.getEast() + vb.getWest()) / 2;
    var halfLat = (vb.getNorth() - vb.getSouth()) / 2 * fraction;
    var halfLng = (vb.getEast() - vb.getWest()) / 2 * fraction;
    editHandles.clearLayers();
    moveHandleLayer.clearLayers();
    syncCenterHandles = null;
    coords = {
        north: centerLat + halfLat, south: centerLat - halfLat,
        east: centerLng + halfLng, west: centerLng - halfLng,
        type: currentShape
    };
    var rect = previewDrawnRect(coords);
    if (drawMode === 'center') {
        shapeCenter = boundsCenter(coords);
        bindCenterEdit();
    } else {
        shapeCenter = null;
        bindLiveEdit(rect);
    }
    bindCornerDrag(rect);
    bindMoveHandle();
}

// Every vertex of a layer's polylines, recursing into groups (e.g. a
// multi-file gpxLayer holding one FeatureGroup per trail).
function tp3dLayerLatLngs(layer) {
    var out = [];
    (function collect(l) {
        if (l.getLatLngs) {
            (function flatten(arr) {
                arr.forEach(function(item) {
                    if (Array.isArray(item)) flatten(item); else out.push(item);
                });
            })(l.getLatLngs());
        } else if (l.eachLayer) {
            l.eachLayer(collect);
        }
    })(layer);
    return out;
}

// How much the current shape -- drawn on a square box of projected *side*
// centered at (centerX, centerY), zoom 0 -- must be scaled so its outline
// just contains every point in *latlngs*. All outlines tp3dProjectedShapePoints
// draws are convex and centered, so each edge is a half-plane n.p <= d and
// the answer is the largest n.p / d over all points and edges.
function tp3dShapeCoverRatio(centerX, centerY, side, latlngs) {
    var nw = map.unproject([centerX - side / 2, centerY - side / 2], 0);
    var se = map.unproject([centerX + side / 2, centerY + side / 2], 0);
    var box = { north: nw.lat, south: se.lat, east: se.lng, west: nw.lng };
    var rotation = typeof TP3D_SHAPE_ROTATION_DEG !== 'undefined' ? TP3D_SHAPE_ROTATION_DEG : 0;
    var outline = currentShape === 'svg' && typeof svgAspect !== 'undefined'
        ? tp3dProjectedShapePoints(svgInscribedBounds(box, svgAspect), 'rectangle', rotation)
        : tp3dProjectedShapePoints(box, currentShape, rotation);
    var verts = outline.map(function(ll) {
        var p = map.project(ll, 0);
        return [p.x - centerX, p.y - centerY];
    });
    var planes = [];
    for (var i = 0; i < verts.length; i++) {
        var a = verts[i], c = verts[(i + 1) % verts.length];
        var nx = c[1] - a[1], ny = a[0] - c[0];
        var d = nx * a[0] + ny * a[1];
        if (Math.abs(d) < 1e-12) continue;
        if (d < 0) { nx = -nx; ny = -ny; d = -d; }
        planes.push([nx / d, ny / d]);
    }
    var ratio = 0;
    latlngs.forEach(function(ll) {
        var p = map.project(ll, 0);
        var x = p.x - centerX, y = p.y - centerY;
        planes.forEach(function(n) { ratio = Math.max(ratio, n[0] * x + n[1] * y); });
    });
    return ratio;
}

// Auto-places a drawn shape of the current type centered on *b* (an
// L.LatLngBounds, e.g. an imported GPX trail's) so that *b* takes up about
// *fraction* of it -- e.g. 0.8 leaves a 10% margin on each side. The box is
// always square in the projected map plane, sized off *b*'s longer side.
// With *latlngs* (e.g. every trail point), the box is instead grown until the
// current shape's actual outline -- hexagon, octagon, circle, rotation,
// SVG aspect -- contains all of them, *fraction* again setting the margin.
// Replaces whatever was previously drawn; callers decide whether that's appropriate.
function placeShapeAroundBounds(b, fraction, latlngs) {
    var northWest = map.project([b.getNorth(), b.getWest()], 0);
    var southEast = map.project([b.getSouth(), b.getEast()], 0);
    var width = Math.abs(southEast.x - northWest.x);
    var height = Math.abs(southEast.y - northWest.y);
    var side = Math.max(width, height) / fraction;
    if (!(side > 0)) return;
    var centerX = (northWest.x + southEast.x) / 2;
    var centerY = (northWest.y + southEast.y) / 2;
    if (latlngs && latlngs.length) {
        var ratio = tp3dShapeCoverRatio(centerX, centerY, side, latlngs);
        if (ratio > 0) side *= ratio / fraction;
    }
    var north = map.unproject([centerX - side / 2, centerY - side / 2], 0);
    var south = map.unproject([centerX + side / 2, centerY + side / 2], 0);
    editHandles.clearLayers();
    moveHandleLayer.clearLayers();
    syncCenterHandles = null;
    coords = {
        north: north.lat, south: south.lat,
        east: south.lng, west: north.lng,
        type: currentShape
    };
    var rect = previewDrawnRect(coords);
    if (drawMode === 'center') {
        shapeCenter = boundsCenter(coords);
        bindCenterEdit();
    } else {
        shapeCenter = null;
        bindLiveEdit(rect);
    }
    bindCornerDrag(rect);
    bindMoveHandle();
    tp3dOnShapeChanged();
    updateSendState();
    updateStatus();
    saveState();
}

// Drops the imported SVG shape and its file, reverting to Rectangle if it
// was the active shape (there's nothing meaningful left to show once the
// file backing it is gone) -- shared 'Clear SVG Shape' button / per-item
// "-" remove button logic for both picker pages that offer this shape.
function clearSvgShapeImport() {
    svgShapePath = null; svgShapeName = null; svgDataUrl = null; svgAspect = 1;
    updateSvgShapeList();
    if (currentShape === 'svg') {
        currentShape = 'rectangle';
        document.querySelectorAll('.shape-btn[data-shape]').forEach(function(b) {
            b.classList.toggle('active', b.getAttribute('data-shape') === 'rectangle');
        });
        if (coords) {
            coords.type = 'rectangle';
            drawnItems.eachLayer(function(layer) { layer.setStyle({ weight: 3, fill: true }); });
        }
        renderShapePreview();
    }
    updateStatus();
    updateSendState();
    saveState();
}
// ── Overlap with existing maps (Map Generator) ───────────────────────────────
// On Generate, the Map Generator asks whether existing maps (the purple
// outlines) that overlap the new area should be removed first. Overlap is
// measured as real shared area in projected (zoom-0) coordinates, so maps
// that merely touch along an edge don't count, and a map with the exact same
// outline as the new one isn't asked about either.

function tp3dLayerRings(layer) {
    var rings = [];
    (function walk(latlngs) {
        if (!latlngs || !latlngs.length) return;
        if (latlngs[0] instanceof L.LatLng) {
            rings.push(latlngs.map(function(ll) { return map.project(ll, 0); }));
        } else {
            latlngs.forEach(walk);
        }
    })(layer.getLatLngs());
    return rings;
}

function tp3dRingArea(ring) {
    var area = 0;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        area += (ring[j].x * ring[i].y) - (ring[i].x * ring[j].y);
    }
    return area / 2;
}

// Sutherland-Hodgman: *subject* may be concave, *clip* must be convex.
function tp3dClipToConvex(subject, clip) {
    if (tp3dRingArea(clip) < 0) clip = clip.slice().reverse();
    var output = subject;
    for (var i = 0; i < clip.length && output.length; i++) {
        var a = clip[i], b = clip[(i + 1) % clip.length];
        var input = output;
        output = [];
        var side = function(p) { return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x); };
        for (var k = 0; k < input.length; k++) {
            var p = input[k], q = input[(k + 1) % input.length];
            var sp = side(p), sq = side(q);
            if (sp >= 0) output.push(p);
            if ((sp >= 0) !== (sq >= 0)) {
                var t = sp / (sp - sq);
                output.push(L.point(p.x + t * (q.x - p.x), p.y + t * (q.y - p.y)));
            }
        }
    }
    return output;
}

// Names of existing maps whose outline shares area with *newLayer* (a convex
// Leaflet polygon/rectangle) without being the same outline.
function tp3dOverlappingExistingMaps(newLayer) {
    if (typeof existingMaps === 'undefined' || !existingMaps.length) return [];
    var newRing = tp3dLayerRings(newLayer)[0];
    if (!newRing || newRing.length < 3) return [];
    var newArea = Math.abs(tp3dRingArea(newRing));
    if (!newArea) return [];
    var names = [];
    existingMaps.forEach(function(m) {
        if (!m.name || !m.bounds) return;
        var rings = tp3dLayerRings(makeShapeLayer(m.bounds, m.shape, {}, m.polygon, m.rotation));
        var area = 0, shared = 0;
        rings.forEach(function(ring) {
            area += Math.abs(tp3dRingArea(ring));
            var cut = tp3dClipToConvex(ring, newRing);
            if (cut.length >= 3) shared += Math.abs(tp3dRingArea(cut));
        });
        if (!area || shared <= 0.001 * Math.min(area, newArea)) return;
        var identical = Math.abs(shared - area) <= 0.01 * area
            && Math.abs(shared - newArea) <= 0.01 * newArea;
        if (!identical) names.push(m.name);
    });
    return names;
}

// Resolves to the list of map names to remove (empty to keep them all), or
// null if the user dismissed the prompt -- the caller then aborts Generate.
function tp3dAskReplaceOverlappingMaps(newLayer) {
    var names = tp3dOverlappingExistingMaps(newLayer);
    if (!names.length) return Promise.resolve([]);
    var which = names.length === 1
        ? 'The existing map "' + names[0] + '" overlaps'
        : names.length + ' existing maps (' + names.join(', ') + ') overlap';
    return tp3dConfirm(
        which + ' the area you drew.\n\nRemove ' + (names.length === 1 ? 'it' : 'them')
            + ' and ' + (names.length === 1 ? 'its' : 'their') + ' map parts before generating?',
        'Overlapping map', 'Remove', 'Keep'
    ).then(function(answer) {
        if (answer === null) return null;
        return answer ? names : [];
    });
}
