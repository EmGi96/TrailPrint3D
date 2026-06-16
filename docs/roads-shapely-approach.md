# Roads Generation — Shapely Approach Retrospective

> Status: **Reverted.** As of 2026-06-13 the road generator was rolled back to
> the pre-Shapely per-segment ribbon implementation (the version committed at
> `HEAD` on the `shapely` branch). This document records what the Shapely-based
> approach attempted, why it was explored, and the concrete reasons it was
> abandoned, so the same ground is not re-tread without new information.

---

## 1. The approach we reverted *to* (pre-Shapely, current)

For each OSM `way`:

1. Convert node lat/lon to Blender coordinates.
2. Walk the polyline, compute a per-node 2D perpendicular (averaged across the
   two adjacent segments), and emit a **left/right vertex pair** offset by
   `streetWidth`.
3. Stitch consecutive pairs into quads → a flat ribbon mesh per width-group.
4. `extrude_plane` to give thickness, apply modifiers, `join` all groups.
5. `remeshClearing(roads, 0.2, 0)` to weld the overlapping ribbons into a single
   manifold solid.
6. `boolean_operation(roads, map, "INTERSECT")` (MANIFOLD solver) to clip to the
   map footprint.
7. `selectBottomFacesByZ` + extrude up by `default_height` to seat the roads on
   the terrain.

**Why it works:** the remesh step turns the messy self-overlapping ribbon soup
into one watertight manifold, so the MANIFOLD boolean against the map succeeds,
and the result drapes/clips correctly. It is not the cleanest topology, but
every downstream operation is fed geometry it can actually handle.

---

## 2. What the Shapely approach tried

Goal: replace the per-segment ribbon + remesh + boolean pipeline with a single
2D polygon-offset operation, then a clean extrusion — aiming for faster,
better-looking, fully manifold roads without a heavy voxel remesh.

Pipeline:

1. Accumulate every road centerline as a list of `(x, y)` polylines.
2. `MultiLineString(lines).buffer(half_width, ...)` — a single Minkowski
   dilation that produces the entire road network as one (multi)polygon. No
   pre-`unary_union` (buffer doesn't need noded input).
3. `simplify(tol)` before buffering to cut segment count.
4. Clip with `intersection(map_footprint_polygon)`.
5. Triangulate the polygon caps with `mapbox_earcut` (shared vertices by index →
   manifold caps), then build walls and a floor to make a solid.
6. Sample terrain height to seat/drape the slab.

### Variants attempted (in order)

| Variant | Idea | Outcome |
|---|---|---|
| buffer + `unary_union` | node the whole network first | **113 s hang** — union of 65k segments |
| single buffer, no union | buffer alone is enough | faster, but produced pinch points |
| `set_precision` snapping | quantize coords to weld near-coincident verts | **created zero-width slivers** → 14k non-manifold errors |
| tall prism + boolean | extrude huge, boolean against terrain | MANIFOLD silently no-op'd on non-manifold input |
| voxel remesh of the slab | force manifold via remesh | **hung** — sub-cm-wide roads at map scale need billions of voxels |
| per-vertex raycast drape | seat each vertex on terrain via BVH | **30 s** (no batch raycast API) + jagged, ugly walls, non-manifold |
| flat-topped slabs + pinch-weld + cleanup | one flat top/bottom per part, morphological weld, bmesh cleanup, guarded remesh fallback | **final state before revert — still looked terrible** |

---

## 3. The Ups (what genuinely worked / was learned)

- **`MultiLineString.buffer()` is the right primitive** for a road network
  outline and is fast (Munich's 6203 lines buffered in ~1.5 s once the union was
  removed).
- **`unary_union` before buffering is unnecessary** and was the single biggest
  performance trap (113 s → 1.5 s by removing it).
- **earcut with index-shared vertices** gives manifold polygon caps cheaply.
- **Grid-baked terrain height sampler** (`_build_terrain_height_sampler`): one
  fixed-resolution pass of BVH raycasts + vectorized bilinear numpy lookup
  decouples height-sampling cost from vertex count. *This helper was kept* — it
  is now used by `create_buildings`.
- **`map_footprint_polygon` closed-solid fallback** (top-surface silhouette when
  there are no boundary edges) was a real fix and is also kept for buildings.
- Confirmed the failure mode of Blender's **MANIFOLD boolean solver**: it
  *silently no-ops* when either input isn't watertight-manifold, which is what
  made roads vanish or span the whole bbox.

## 4. The Downs (why it was abandoned)

- **Pinch points are intrinsic.** Where road centerlines meet, the buffered
  polygon touches itself at a single point (bow-tie). Shapely treats these as
  valid, but earcut turns each into a non-manifold vertex. A morphological weld
  (`buffer(+small)`) reduced but did not reliably eliminate them across a real
  city network.
- **`set_precision` makes it worse**, not better — quantization collapses
  near-coincident vertices into zero-width slivers, *manufacturing* new pinches
  and 14k+ manifold errors.
- **No good manifold-repair option at road scale.** Voxel remesh cannot handle
  ~0.012-unit-wide roads on a map-scale bounding box (billions of voxels →
  multi-minute hang). The 60M-voxel guard just skips it, leaving the mesh dirty.
- **Seating on terrain is the real killer.**
  - Per-vertex draping → jagged walls, non-manifold, slow (no batch raycast).
  - Flat-topped slabs → a single connected network becomes one flat Z, so on any
    slope the slab either floats above or **punches straight through the terrain
    to the far side** (the exact failure the user observed), and everything looks
    "too flat."
- **Net result:** more code, more fragile, and worse-looking than the
  remesh-based ribbon approach it was meant to replace.

## 5. Decision

Every *other* change in this effort (buildings vectorization, the height
sampler, the closed-solid footprint fallback, ocean/coastline fixes) was a clear
improvement and is retained. Only the road generator regressed, so it was
reverted to the proven ribbon + remesh + boolean pipeline.

### If revisited, address these first

1. A topology-aware road **graph** (junction nodes shared, widths per edge)
   rather than a blind network buffer, to avoid pinch points by construction.
2. **Per-edge terrain draping** with a coarse longitudinal resampling so slabs
   follow slopes without per-vertex raycasts and without flattening.
3. A manifold-repair path that does **not** rely on voxel remesh at sub-cm width
   (e.g. constrained 2D triangulation of the already-clean offset polygon, then
   a controlled vertical extrude seated on the sampled heightfield).
