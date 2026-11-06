// Generic, page-agnostic guided-tutorial engine plus the "Tutorial" picker
// menu, reused by picker pages via a token in picker_server.py (same
// mechanism as history_panel.js). Knows nothing about any specific page --
// tutorial *content* (the step lists) lives elsewhere, e.g.
// assets/map_tutorials.js for the map generators.
//
// A tutorial is a plain array of step objects, run with
// tp3dTutorial.start(steps). Each step may define:
//
//   target       What to focus: a CSS selector, an array of selectors/
//                elements (focus box = their union), or a function returning
//                either. Everything outside the (padded) box is greyed out
//                and blocks clicks. Omitted, or nothing visible matched ->
//                the whole page is greyed and the popup is centered.
//   title, text  Popup heading and body (text is HTML).
//   placement    'auto' (default) | 'right' | 'left' | 'top' | 'bottom' |
//                'center'. 'auto' picks the first side with room; if none
//                has room (e.g. focusing the whole map) the popup is placed
//                inside the top of the focus box.
//   padding      Extra px around the focus box (default 6).
//   width        Popup width in px for this step (default 300, from CSS).
//   interactive  true lets the user click/type inside the focus box while
//                the popup waits for "Next". Default false (look, don't touch).
//   waitFor      Makes this an *action* step: no "Next" button -- the step
//                advances once the user does something. A function
//                (done, api) that arranges for done() to be called and may
//                return a cleanup function. The focus box is always
//                interactive on action steps. Ready-made helpers:
//                tp3dTutorial.waitForClick(selector),
//                tp3dTutorial.waitUntil(predicate[, intervalMs]),
//                tp3dTutorial.waitForEvent(target, eventName).
//   waitText     Hint shown in place of the Next button on action steps.
//   skippable    Action steps only: also show a "Skip step" button.
//   skipIf       function() -> true to silently skip this step (e.g. the
//                action it asks for has already been done).
//   arrow        A bouncing arrow pointing at something. true = at the
//                step's target; or a target spec (same forms as `target`);
//                or { target, side } where side is 'left' | 'right' |
//                'top' | 'bottom' (the side of the target the arrow sits
//                on) or 'auto' (default: the roomiest side the popup isn't
//                on).
//   demo         A looping "sample" cursor showing the user what to do. A
//                function returning viewport points [{x, y}, ...]: one
//                point = a click there; several = press at the first, drag
//                through the rest, release at the last. Re-evaluated every
//                frame (so it follows a map that moved), and may return
//                null to show nothing for now. Pauses while the
//                user has a mouse button down. demoDuration sets the drag
//                time in ms (default 1100). demoShape draws a dashed ghost
//                box while dragging: 'rect' (press point to cursor, like
//                drawing corner to corner) or 'rect-center' (mirrored around
//                the press point, like drawing from the center), or a
//                function returning one of those. demoGhost(start, current)
//                instead returns any box {left, top, width, height} for the
//                ghost (e.g. a shape being moved). demoGhostColor sets the
//                ghost's color (a CSS color, or a function returning one) so
//                it can match what the page really draws. demoHover: true
//                makes several points a glide with the button up followed by
//                a click at the last point (instead of a drag).
//   onEnter / onExit   function(api) hooks run when the step shows/leaves.
//   buttons      Extra popup buttons: [{ label, onClick(button, api) }],
//                shown at the left of the popup's footer -- e.g. an
//                alternative way to complete an action step.
//
// Esc (or the popup's close button) ends the tutorial at any point;
// Enter / Right-arrow presses Next on non-action steps.
var tp3dTutorial = (function() {
    var steps = null;
    var index = -1;
    var cleanupWait = null;
    var rafId = null;
    var lastRectKey = '';
    var popupSide = null;
    var root, shades, ring, blocker, pop, arrow, cursor, cursorDot, ripple, ghost;
    // Demo-cursor loop state: when the current loop started, the points it
    // is playing (fetched fresh at each loop start), and whether the user
    // is currently holding a mouse button (pauses the demo so it never
    // competes with the real thing).
    var demoStart = 0, demoPts = null, demoPaused = false;

    function build() {
        if (root) return;
        root = document.createElement('div');
        root.id = 'tp3dTutorial';
        shades = ['top', 'bottom', 'left', 'right'].map(function() {
            var s = document.createElement('div');
            s.className = 'tp3d-tut-shade';
            s.addEventListener('click', nudgePopup);
            root.appendChild(s);
            return s;
        });
        ring = document.createElement('div');
        ring.className = 'tp3d-tut-ring';
        root.appendChild(ring);
        // Transparent click-eater over the focus box itself, shown on
        // non-interactive steps so the box can be looked at but not used.
        blocker = document.createElement('div');
        blocker.className = 'tp3d-tut-blocker';
        blocker.addEventListener('click', nudgePopup);
        root.appendChild(blocker);
        // Arrow SVG points right; the outer box is rotated per side and the
        // inner one bobs along its own x axis, i.e. toward the target.
        arrow = document.createElement('div');
        arrow.className = 'tp3d-tut-arrow';
        arrow.innerHTML = '<div class="tp3d-tut-arrow-bob"><svg viewBox="0 0 48 48" width="48" height="48">' +
            '<path d="M4 20h24V9l17 15-17 15V28H4z" fill="#007bff" stroke="#fff" stroke-width="2.5" ' +
            'stroke-linejoin="round"/></svg></div>';
        root.appendChild(arrow);
        ghost = document.createElement('div');
        ghost.className = 'tp3d-tut-ghost';
        root.appendChild(ghost);
        ripple = document.createElement('div');
        ripple.className = 'tp3d-tut-ripple';
        root.appendChild(ripple);
        cursorDot = document.createElement('div');
        cursorDot.className = 'tp3d-tut-cursor-dot';
        root.appendChild(cursorDot);
        // Classic pointer, its tip at the box's top-left corner.
        cursor = document.createElement('div');
        cursor.className = 'tp3d-tut-cursor';
        cursor.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26">' +
            '<path d="M2 2l7.5 19 2.8-7.7L20 10.5z" fill="#fff" stroke="#111" stroke-width="1.6" ' +
            'stroke-linejoin="round"/></svg>';
        root.appendChild(cursor);
        pop = document.createElement('div');
        pop.className = 'tp3d-tut-pop';
        root.appendChild(pop);
        document.body.appendChild(root);
    }

    function nudgePopup() {
        pop.classList.remove('nudge');
        void pop.offsetWidth;  // restart the CSS animation
        pop.classList.add('nudge');
    }

    function resolveTargets(spec) {
        var t = spec;
        if (typeof t === 'function') t = t();
        if (!t) return [];
        if (!Array.isArray(t)) t = [t];
        var els = [];
        t.forEach(function(item) {
            var el = typeof item === 'string' ? document.querySelector(item) : item;
            if (el && el.getClientRects().length) els.push(el);
        });
        return els;
    }

    // Union of every target's bounding box, padded and clipped to the
    // viewport -- or null when nothing visible matched.
    function focusRect(step) {
        return unionRect(step.target, step.padding != null ? step.padding : 6);
    }

    function unionRect(spec, pad) {
        var els = resolveTargets(spec);
        if (!els.length) return null;
        var r = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity };
        els.forEach(function(el) {
            var b = el.getBoundingClientRect();
            r.left = Math.min(r.left, b.left); r.top = Math.min(r.top, b.top);
            r.right = Math.max(r.right, b.right); r.bottom = Math.max(r.bottom, b.bottom);
        });
        var vw = window.innerWidth, vh = window.innerHeight;
        r.left = Math.max(0, r.left - pad); r.top = Math.max(0, r.top - pad);
        r.right = Math.min(vw, r.right + pad); r.bottom = Math.min(vh, r.bottom + pad);
        if (r.right <= r.left || r.bottom <= r.top) return null;
        return r;
    }

    function setBox(el, left, top, width, height) {
        el.style.left = left + 'px'; el.style.top = top + 'px';
        el.style.width = Math.max(0, width) + 'px'; el.style.height = Math.max(0, height) + 'px';
    }

    function placePopup(step, r) {
        var vw = window.innerWidth, vh = window.innerHeight, gap = 14, m = 10;
        var pw = pop.offsetWidth, ph = pop.offsetHeight;
        var x, y;
        popupSide = null;
        if (!r || step.placement === 'center') {
            x = (vw - pw) / 2; y = (vh - ph) / 2;
        } else {
            var cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
            var fits = {
                right: vw - r.right - gap >= pw + m,
                left: r.left - gap >= pw + m,
                bottom: vh - r.bottom - gap >= ph + m,
                top: r.top - gap >= ph + m
            };
            var order = ['right', 'bottom', 'left', 'top'];
            if (step.placement && step.placement !== 'auto') {
                order = [step.placement].concat(order.filter(function(p) { return p !== step.placement; }));
            }
            var side = null;
            for (var i = 0; i < order.length; i++) { if (fits[order[i]]) { side = order[i]; break; } }
            popupSide = side;
            if (side === 'right') { x = r.right + gap; y = cy - ph / 2; }
            else if (side === 'left') { x = r.left - gap - pw; y = cy - ph / 2; }
            else if (side === 'bottom') { x = cx - pw / 2; y = r.bottom + gap; }
            else if (side === 'top') { x = cx - pw / 2; y = r.top - gap - ph; }
            else { x = cx - pw / 2; y = r.top + gap; }  // no room outside -- inside, at the top
        }
        x = Math.min(Math.max(m, x), vw - pw - m);
        y = Math.min(Math.max(m, y), vh - ph - m);
        pop.style.left = x + 'px';
        pop.style.top = y + 'px';
    }

    // Re-measures every frame while a tutorial runs, so the focus box
    // follows its target through layout changes, sidebar scrolling, window
    // resizes and elements appearing/disappearing -- but only touches the
    // DOM when something actually moved.
    function layout() {
        rafId = requestAnimationFrame(layout);
        update();
    }

    function update() {
        var step = steps && steps[index];
        if (!step) return;
        updateDemo(step);
        var r = focusRect(step);
        var ar = arrowRect(step);
        var key = (r ? [r.left, r.top, r.right, r.bottom].map(Math.round).join(',') : 'none') +
            '|' + (ar ? [ar.left, ar.top, ar.right, ar.bottom].map(Math.round).join(',') : 'none') +
            '|' + window.innerWidth + 'x' + window.innerHeight + '|' + pop.offsetWidth + 'x' + pop.offsetHeight;
        if (key === lastRectKey) return;
        lastRectKey = key;
        var vw = window.innerWidth, vh = window.innerHeight;
        if (r) {
            setBox(shades[0], 0, 0, vw, r.top);
            setBox(shades[1], 0, r.bottom, vw, vh - r.bottom);
            setBox(shades[2], 0, r.top, r.left, r.bottom - r.top);
            setBox(shades[3], r.right, r.top, vw - r.right, r.bottom - r.top);
            setBox(ring, r.left, r.top, r.right - r.left, r.bottom - r.top);
            setBox(blocker, r.left, r.top, r.right - r.left, r.bottom - r.top);
            ring.style.display = '';
        } else {
            setBox(shades[0], 0, 0, vw, vh);
            [shades[1], shades[2], shades[3], blocker].forEach(function(s) { setBox(s, 0, 0, 0, 0); });
            ring.style.display = 'none';
        }
        placePopup(step, r);
        placeArrow(step, ar);
    }

    function arrowRect(step) {
        var a = step.arrow;
        if (!a) return null;
        var spec = a === true ? step.target : (a.target !== undefined || a.side ? (a.target || step.target) : a);
        return unionRect(spec, 4);
    }

    var ARROW_SIZE = 48, ARROW_GAP = 6;
    var ARROW_ROTATION = { left: 0, right: 180, top: 90, bottom: -90 };

    function placeArrow(step, r) {
        if (!r) { arrow.style.display = 'none'; return; }
        var vw = window.innerWidth, vh = window.innerHeight;
        var side = step.arrow && step.arrow.side;
        if (!side || side === 'auto') {
            // Opposite the popup if that side has comfortable room (reads as
            // "popup on one side, arrow on the other"), else whichever side
            // the popup isn't on has the most room.
            var room = { left: r.left, right: vw - r.right, top: r.top, bottom: vh - r.bottom };
            var opposite = { left: 'right', right: 'left', top: 'bottom', bottom: 'top' }[popupSide];
            side = null;
            if (opposite && room[opposite] >= ARROW_SIZE + ARROW_GAP + 16) {
                side = opposite;
            } else {
                Object.keys(room).forEach(function(s) {
                    if (s !== popupSide && (!side || room[s] > room[side])) side = s;
                });
            }
        }
        var cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2, s2 = ARROW_SIZE / 2;
        var x, y;
        if (side === 'left') { x = r.left - ARROW_SIZE - ARROW_GAP; y = cy - s2; }
        else if (side === 'right') { x = r.right + ARROW_GAP; y = cy - s2; }
        else if (side === 'top') { x = cx - s2; y = r.top - ARROW_SIZE - ARROW_GAP; }
        else { side = 'bottom'; x = cx - s2; y = r.bottom + ARROW_GAP; }
        x = Math.min(Math.max(0, x), vw - ARROW_SIZE);
        y = Math.min(Math.max(0, y), vh - ARROW_SIZE);
        arrow.style.display = '';
        arrow.style.left = x + 'px';
        arrow.style.top = y + 'px';
        arrow.style.transform = 'rotate(' + ARROW_ROTATION[side] + 'deg)';
    }

    // One demo loop, in ms: fade in at the first point, press, drag along
    // the points (skipped for a single-point click), release, hold, fade
    // out, pause -- then start over with freshly fetched points.
    var DEMO_APPEAR = 350, DEMO_PRESS = 220, DEMO_RELEASE = 220, DEMO_HOLD = 350,
        DEMO_FADE = 350, DEMO_GAP = 650, DEMO_RIPPLE = 550;

    function hideDemo() {
        cursor.style.display = cursorDot.style.display = ripple.style.display = ghost.style.display = 'none';
    }

    function easeInOut(t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }

    // Position at fraction f (0..1) along the polyline through pts.
    function pointAlong(pts, f) {
        if (pts.length === 1) return pts[0];
        var lens = [], total = 0;
        for (var i = 1; i < pts.length; i++) {
            var l = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
            lens.push(l); total += l;
        }
        var d = f * total;
        for (var j = 0; j < lens.length; j++) {
            if (d <= lens[j] || j === lens.length - 1) {
                var t = lens[j] ? Math.min(1, d / lens[j]) : 1;
                return { x: pts[j].x + (pts[j + 1].x - pts[j].x) * t, y: pts[j].y + (pts[j + 1].y - pts[j].y) * t };
            }
            d -= lens[j];
        }
        return pts[pts.length - 1];
    }

    function updateDemo(step) {
        if (typeof step.demo !== 'function' || demoPaused) { hideDemo(); return; }
        var now = performance.now();
        var drag = step.demoDuration != null ? step.demoDuration : 1100;
        // Re-read every frame so the demo stays glued to its target even if
        // the page moves it mid-loop (map re-layout, pan, zoom).
        var pts = null;
        try { pts = step.demo(); } catch (e) {}
        if (!pts || !pts.length) { demoPts = null; demoStart = 0; hideDemo(); return; }
        demoPts = pts;
        if (!demoStart) demoStart = now;
        if (step.demoHover && demoPts.length > 1) { updateHoverDemo(now, drag); return; }
        var move = demoPts.length > 1 ? drag : 0;
        var tPress = DEMO_APPEAR, tMove = tPress + DEMO_PRESS, tRelease = tMove + move,
            tHold = tRelease + DEMO_RELEASE, tFade = tHold + DEMO_HOLD, tEnd = tFade + DEMO_FADE + DEMO_GAP;
        var t = now - demoStart;
        if (t >= tEnd) { demoPts = null; demoStart = 0; hideDemo(); return; }

        var p = pointAlong(demoPts, t < tMove ? 0 : t < tRelease ? easeInOut((t - tMove) / move) : 1);
        var opacity = t < tPress ? t / DEMO_APPEAR : t < tFade ? 1 : Math.max(0, 1 - (t - tFade) / DEMO_FADE);
        var pressed = t >= tPress && t < tRelease + DEMO_RELEASE / 2;
        var scale = t < tPress ? 1 : t < tMove ? 1 - 0.18 * ((t - tPress) / DEMO_PRESS)
            : t < tRelease ? 0.82 : t < tHold ? 0.82 + 0.18 * ((t - tRelease) / DEMO_RELEASE) : 1;

        cursor.style.display = 'block';
        cursor.style.opacity = opacity;
        cursor.style.transform = 'translate(' + p.x + 'px,' + p.y + 'px) scale(' + scale + ')';
        cursorDot.style.display = pressed ? 'block' : 'none';
        cursorDot.style.transform = 'translate(' + p.x + 'px,' + p.y + 'px)';
        // Ghost box from the press point to the cursor (or mirrored around
        // the press point), kept until the fade so the result stays visible.
        var box = null;
        if (demoPts.length > 1 && t >= tMove) {
            var a = demoPts[0];
            if (typeof step.demoGhost === 'function') {
                try { box = step.demoGhost(a, p); } catch (e) {}
            } else {
                var shape = typeof step.demoShape === 'function' ? step.demoShape() : step.demoShape;
                if (shape) {
                    var mirrored = shape === 'rect-center';
                    var hw = Math.abs(p.x - a.x), hh = Math.abs(p.y - a.y);
                    box = {
                        left: mirrored ? a.x - hw : Math.min(a.x, p.x),
                        top: mirrored ? a.y - hh : Math.min(a.y, p.y),
                        width: mirrored ? hw * 2 : hw,
                        height: mirrored ? hh * 2 : hh
                    };
                }
            }
        }
        if (box) {
            var color = typeof step.demoGhostColor === 'function' ? step.demoGhostColor() : step.demoGhostColor;
            ghost.style.color = color || '';
            ghost.style.display = 'block';
            ghost.style.opacity = opacity;
            setBox(ghost, box.left, box.top, box.width, box.height);
        } else {
            ghost.style.display = 'none';
        }
        // Expanding ring at the press point, once per loop.
        var tr = t - tPress;
        if (tr >= 0 && tr < DEMO_RIPPLE) {
            var k = tr / DEMO_RIPPLE, p0 = demoPts[0];
            ripple.style.display = 'block';
            ripple.style.opacity = 0.8 * (1 - k);
            ripple.style.transform = 'translate(' + p0.x + 'px,' + p0.y + 'px) scale(' + (0.3 + 1.2 * k) + ')';
        } else {
            ripple.style.display = 'none';
        }
    }

    // demoHover variant: glide with the button *up* from the first point to
    // the last, then click there (ripple at the end, not the start) -- e.g.
    // "go over there and click this", rather than a drag.
    function updateHoverDemo(now, glide) {
        var tMove = DEMO_APPEAR, tPress = tMove + glide, tRelease = tPress + DEMO_PRESS,
            tHold = tRelease + DEMO_RELEASE, tFade = tHold + DEMO_HOLD, tEnd = tFade + DEMO_FADE + DEMO_GAP;
        var t = now - demoStart;
        if (t >= tEnd) { demoPts = null; demoStart = 0; hideDemo(); return; }

        var p = pointAlong(demoPts, t < tMove ? 0 : t < tPress ? easeInOut((t - tMove) / glide) : 1);
        var opacity = t < tMove ? t / DEMO_APPEAR : t < tFade ? 1 : Math.max(0, 1 - (t - tFade) / DEMO_FADE);
        var pressed = t >= tPress && t < tRelease;
        var scale = t < tPress ? 1 : t < tRelease ? 1 - 0.18 * ((t - tPress) / DEMO_PRESS)
            : t < tHold ? 0.82 + 0.18 * ((t - tRelease) / DEMO_RELEASE) : 1;

        cursor.style.display = 'block';
        cursor.style.opacity = opacity;
        cursor.style.transform = 'translate(' + p.x + 'px,' + p.y + 'px) scale(' + scale + ')';
        cursorDot.style.display = pressed ? 'block' : 'none';
        cursorDot.style.transform = 'translate(' + p.x + 'px,' + p.y + 'px)';
        ghost.style.display = 'none';
        var tr = t - tPress, end = demoPts[demoPts.length - 1];
        if (tr >= 0 && tr < DEMO_RIPPLE) {
            var k = tr / DEMO_RIPPLE;
            ripple.style.display = 'block';
            ripple.style.opacity = 0.8 * (1 - k);
            ripple.style.transform = 'translate(' + end.x + 'px,' + end.y + 'px) scale(' + (0.3 + 1.2 * k) + ')';
        } else {
            ripple.style.display = 'none';
        }
    }

    function onPointerDown() { demoPaused = true; }
    function onPointerUp() { demoPaused = false; demoPts = null; demoStart = 0; }

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    function renderPopup(step) {
        pop.innerHTML = '';
        pop.style.width = step.width ? step.width + 'px' : '';
        var isAction = typeof step.waitFor === 'function';

        var head = el('div', 'tp3d-tut-head');
        // Steps skipped so far (skipIf) are left out of both numbers, so the
        // counter doesn't jump.
        var skippedBefore = 0, skippedTotal = 0;
        Object.keys(skipped).forEach(function(k) {
            skippedTotal++;
            if (+k < index) skippedBefore++;
        });
        head.appendChild(el('span', 'tp3d-tut-count',
            'Step ' + (index + 1 - skippedBefore) + ' of ' + (steps.length - skippedTotal)));
        var close = el('button', 'tp3d-modal-close-btn', '✕');
        close.type = 'button';
        close.title = 'Exit tutorial (Esc)';
        close.addEventListener('click', stop);
        head.appendChild(close);
        pop.appendChild(head);

        if (step.title) pop.appendChild(el('div', 'tp3d-tut-title', step.title));
        if (step.text) {
            var body = el('div', 'tp3d-tut-text');
            body.innerHTML = step.text;
            pop.appendChild(body);
        }

        var foot = el('div', 'tp3d-tut-foot');
        if (!isAction && index > 0) {
            var back = el('button', 'tp3d-tut-btn secondary', 'Back');
            back.type = 'button';
            back.addEventListener('click', function() { go(index - 1, -1); });
            foot.appendChild(back);
        }
        (step.buttons || []).forEach(function(b) {
            var extra = el('button', 'tp3d-tut-btn secondary', b.label);
            extra.type = 'button';
            extra.addEventListener('click', function() { if (b.onClick) b.onClick(extra, api); });
            foot.appendChild(extra);
        });
        foot.appendChild(el('span', 'tp3d-tut-spacer'));
        if (isAction) {
            var wait = el('span', 'tp3d-tut-wait');
            wait.appendChild(el('span', 'tp3d-tut-pulse'));
            wait.appendChild(document.createTextNode(step.waitText || 'Waiting for you…'));
            foot.appendChild(wait);
            if (step.skippable) {
                var skip = el('button', 'tp3d-tut-btn secondary', index === steps.length - 1 ? 'Finish' : 'Skip step');
                skip.type = 'button';
                skip.addEventListener('click', next);
                foot.appendChild(skip);
            }
        } else {
            var nextBtn = el('button', 'tp3d-tut-btn', index === steps.length - 1 ? 'Finish' : 'Next');
            nextBtn.type = 'button';
            nextBtn.addEventListener('click', next);
            foot.appendChild(nextBtn);
        }
        pop.appendChild(foot);
    }

    function scrollTargetsIntoView(step) {
        resolveTargets(step.target).forEach(function(t) {
            if (t.scrollIntoView) t.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        });
    }

    function leaveStep() {
        if (cleanupWait) { try { cleanupWait(); } catch (e) {} cleanupWait = null; }
        var step = steps && steps[index];
        if (step && step.onExit) { try { step.onExit(api); } catch (e) {} }
    }

    // Moves to step i, walking further in direction dir (+1/-1) past any
    // step whose skipIf says so. Running off the end finishes the tutorial.
    function go(i, dir) {
        leaveStep();
        while (i >= 0 && i < steps.length && steps[i].skipIf && steps[i].skipIf()) {
            skipped[i] = true;
            i += dir;
        }
        if (i >= 0 && i < steps.length) delete skipped[i];
        if (i < 0) i = 0;
        if (i >= steps.length) {
            var done = onComplete;
            stop();
            if (done) { try { done(); } catch (e) {} }
            return;
        }
        index = i;
        var step = steps[index];
        var isAction = typeof step.waitFor === 'function';
        blocker.style.pointerEvents = (isAction || step.interactive) ? 'none' : 'auto';
        if (step.onEnter) { try { step.onEnter(api); } catch (e) {} }
        scrollTargetsIntoView(step);
        renderPopup(step);
        lastRectKey = '';
        demoPts = null;
        demoStart = 0;
        hideDemo();
        update();
        if (isAction) {
            var myIndex = index, fired = false;
            var res = step.waitFor(function() {
                // Ignore late/duplicate calls from a step that's no longer current.
                if (fired || !steps || index !== myIndex) return;
                fired = true;
                next();
            }, api);
            if (typeof res === 'function') cleanupWait = res;
        }
    }

    function next() { if (steps) go(index + 1, 1); }

    function onKey(e) {
        if (!steps) return;
        if (e.key === 'Escape') { e.preventDefault(); stop(); return; }
        var step = steps[index];
        var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '');
        if (!typing && step && typeof step.waitFor !== 'function' && (e.key === 'Enter' || e.key === 'ArrowRight')) {
            e.preventDefault();
            next();
        }
    }

    // opts.onComplete runs only when the tutorial is finished -- its last
    // step passed -- not when it's exited early (✕, Esc, stop()).
    var onComplete = null;
    var skipped = {};  // step indices skipped via skipIf this run

    function start(stepList, opts) {
        if (!stepList || !stepList.length) return;
        if (steps) stop();
        build();
        steps = stepList;
        onComplete = (opts && opts.onComplete) || null;
        skipped = {};
        root.classList.add('active');
        document.addEventListener('keydown', onKey, true);
        document.addEventListener('pointerdown', onPointerDown, true);
        document.addEventListener('pointerup', onPointerUp, true);
        demoPaused = false;
        go(0, 1);
        if (steps && rafId === null) rafId = requestAnimationFrame(layout);
    }

    function stop() {
        if (!steps) return;
        leaveStep();
        steps = null;
        index = -1;
        onComplete = null;
        if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
        document.removeEventListener('keydown', onKey, true);
        document.removeEventListener('pointerdown', onPointerDown, true);
        document.removeEventListener('pointerup', onPointerUp, true);
        root.classList.remove('active');
    }

    // Resolves (once) when the user clicks anything matching *selector*.
    // Listens on the document in the capture phase so it works even for
    // elements re-rendered after the step began, and defers done() a tick
    // so the page's own click handler has already run by the time the next
    // step (and its skipIf/onEnter) looks at page state.
    function waitForClick(selector) {
        return function(done) {
            function onClick(e) {
                if (e.target.closest && e.target.closest(selector)) setTimeout(done, 0);
            }
            document.addEventListener('click', onClick, true);
            return function() { document.removeEventListener('click', onClick, true); };
        };
    }

    // Resolves once predicate() returns truthy, polled every intervalMs.
    function waitUntil(predicate, intervalMs) {
        return function(done) {
            var id = setInterval(function() {
                var ok = false;
                try { ok = predicate(); } catch (e) {}
                if (ok) done();
            }, intervalMs || 250);
            return function() { clearInterval(id); };
        };
    }

    // Resolves on the next *eventName* fired by *target* -- a DOM element /
    // selector, or anything with on()/off() (e.g. a Leaflet map).
    function waitForEvent(target, eventName) {
        return function(done) {
            var t = typeof target === 'string' ? document.querySelector(target) : target;
            if (!t) return null;
            function handler() { setTimeout(done, 0); }
            if (typeof t.on === 'function' && typeof t.off === 'function') {
                t.on(eventName, handler);
                return function() { t.off(eventName, handler); };
            }
            t.addEventListener(eventName, handler);
            return function() { t.removeEventListener(eventName, handler); };
        };
    }

    var api = {
        start: start,
        stop: stop,
        next: next,
        isActive: function() { return !!steps; },
        waitForClick: waitForClick,
        waitUntil: waitUntil,
        waitForEvent: waitForEvent
    };
    return api;
})();

// Wires a page's own "Tutorial" button to a popup listing the tutorials
// available on that page, numbered in list order. opts:
//   button     selector/element of the button that opens the menu
//   title      menu heading (default 'Tutorials')
//   tutorials  [{ id, title, description, steps }] -- steps is a function
//              returning a step array (built fresh at click time, so it can
//              look at the page's current state), or null for a placeholder
//              entry that's listed but not available yet. Optional prepare
//              is called right before the tutorial starts (e.g. to reset
//              the page to a known state).
//   offerId    optional: enables window.tp3dOfferTutorials(), which -- the
//              first time this generator is used -- shows a one-step guide
//              pointing out the button (a demo mouse glides over and clicks
//              it), saying the tutorials are interactive and optional.
//              offerId is recorded in the same progress file as finished
//              tutorials (not counted as one), so it's only ever offered
//              once. export_folder.js calls it once an export folder is set.
//   offerTitle, offerText  optional: that guide's heading and body (HTML),
//              to describe this generator's own tutorials instead of the
//              generic wording.
//
// Finishing a tutorial (running past its last step, not exiting early)
// records its id via picker_server.py's /complete_tutorial, and completed
// tutorials are shaded green in the menu. Stored server-side (a JSON file
// in Blender's config folder) because each picker window runs in a fresh,
// throwaway browser profile, so browser storage wouldn't survive a reopen.
// Needs PORT (set by every picker page); without it, nothing is recorded.
function tp3dTutorialMenu(opts) {
    var btn = typeof opts.button === 'string' ? document.querySelector(opts.button) : opts.button;
    if (!btn) return;
    var hasServer = typeof PORT !== 'undefined';

    var modal = document.createElement('div');
    modal.className = 'tp3d-modal tp3d-tut-menu';
    var box = document.createElement('div');
    box.className = 'tp3d-modal-box';
    modal.appendChild(box);

    var header = document.createElement('div');
    header.className = 'modal-header';
    var title = document.createElement('span');
    title.textContent = opts.title || 'Tutorials';
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'tp3d-modal-close-btn';
    closeBtn.title = 'Close';
    closeBtn.textContent = '✕';
    // "N/M done" counter -- M counts every listed tutorial, including
    // "Coming soon" placeholders, so the total reflects the whole set.
    var counter = document.createElement('span');
    counter.className = 'tp3d-tut-menu-progress';
    counter.title = 'Tutorials completed';
    var titleWrap = document.createElement('div');
    titleWrap.className = 'tp3d-tut-menu-titlewrap';
    titleWrap.appendChild(title);
    titleWrap.appendChild(counter);
    header.appendChild(titleWrap);
    header.appendChild(closeBtn);
    box.appendChild(header);

    var intro = document.createElement('div');
    intro.className = 'tp3d-tut-menu-intro';
    intro.textContent = 'Pick a step-by-step guide. You can exit any time with Esc.';
    box.appendChild(intro);

    function close() { modal.classList.remove('open'); }

    var itemsById = {};
    var total = (opts.tutorials || []).length;

    function updateCounter() {
        var done = Object.keys(itemsById).filter(function(id) {
            return itemsById[id].classList.contains('done');
        }).length;
        counter.textContent = done + '/' + total + ' done';
        counter.classList.toggle('all-done', total > 0 && done === total);
    }

    function applyProgress(progress) {
        Object.keys(itemsById).forEach(function(id) {
            var done = !!(progress && progress[id]);
            itemsById[id].classList.toggle('done', done);
            itemsById[id].title = done ? 'Completed' : '';
        });
        updateCounter();
    }

    function loadProgress() {
        if (!hasServer) return;
        fetch('http://127.0.0.1:' + PORT + '/get_tutorial_progress', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(applyProgress)
            .catch(function() {});
    }

    // keepalive: a tutorial's last step can be the click that closes the
    // whole window (e.g. "Send to Blender"), which would otherwise cancel
    // a normal in-flight request.
    function markComplete(id) {
        if (itemsById[id]) itemsById[id].classList.add('done');
        updateCounter();
        if (!hasServer) return;
        fetch('http://127.0.0.1:' + PORT + '/complete_tutorial', {
            method: 'POST', keepalive: true,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: id })
        }).catch(function() {});
    }

    (opts.tutorials || []).forEach(function(tut, i) {
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'tp3d-tut-menu-item';
        var name = document.createElement('div');
        name.className = 'tp3d-tut-menu-name';
        var num = document.createElement('span');
        num.className = 'tp3d-tut-menu-num';
        num.textContent = (i + 1) + '.';
        name.appendChild(num);
        name.appendChild(document.createTextNode(tut.title));
        var check = document.createElement('span');
        check.className = 'tp3d-tut-menu-check';
        check.textContent = '✓';
        name.appendChild(check);
        item.appendChild(name);
        if (tut.description) {
            var desc = document.createElement('div');
            desc.className = 'tp3d-tut-menu-desc';
            desc.textContent = tut.description;
            item.appendChild(desc);
        }
        if (typeof tut.steps !== 'function') {
            item.disabled = true;
            var soon = document.createElement('span');
            soon.className = 'tp3d-tut-menu-soon';
            soon.textContent = 'Coming soon';
            name.appendChild(soon);
        } else {
            if (tut.id) itemsById[tut.id] = item;
            item.addEventListener('click', function() {
                close();
                if (typeof tut.prepare === 'function') {
                    try { tut.prepare(); } catch (e) { console.error('[TP3D tutorial] prepare failed:', e); }
                }
                tp3dTutorial.start(tut.steps(), {
                    onComplete: tut.id ? function() { markComplete(tut.id); } : null
                });
            });
        }
        box.appendChild(item);
    });

    btn.addEventListener('click', function() {
        loadProgress();
        modal.classList.add('open');
    });

    // First-use offer: a one-step guide spotlighting the Tutorials button,
    // with a demo mouse gliding over to it and clicking (demoHover). Clicking
    // the real button opens this menu as usual and ends the guide; "Maybe
    // later" / ✕ / Esc just dismiss it.
    function offerSteps() {
        return [{
            target: btn,
            placement: 'right',
            title: opts.offerTitle || 'Interactive tutorials',
            text: opts.offerText ||
                  '<b>New here?</b> There are <b>interactive tutorials</b> that walk you through the ' +
                  'generator step by step, right on this page.<br><br>They\'re completely ' +
                  '<b>optional</b> — click <b>Tutorials</b> to pick one, or skip them and start on your ' +
                  'own. The button stays here at the top of the map for later.',
            demo: function() {
                var r = btn.getBoundingClientRect();
                if (!r.width) return null;
                var end = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                return [{ x: end.x - 190, y: end.y + 230 }, end];
            },
            demoHover: true,
            demoDuration: 1300,
            // btn's own listener (above) opens the menu; this just ends the guide.
            waitFor: function(done) {
                function onClick() { setTimeout(done, 0); }
                btn.addEventListener('click', onClick);
                return function() { btn.removeEventListener('click', onClick); };
            },
            waitText: 'Click Tutorials',
            buttons: [{ label: 'Maybe later', onClick: function(b, api) { api.stop(); } }]
        }];
    }

    if (opts.offerId && hasServer) {
        var offered = false;
        window.tp3dOfferTutorials = function() {
            if (offered) return;
            offered = true;
            fetch('http://127.0.0.1:' + PORT + '/get_tutorial_progress', { cache: 'no-store' })
                .then(function(r) { return r.json(); })
                .then(function(progress) {
                    if (progress && progress[opts.offerId]) return;
                    if (tp3dTutorial.isActive() || modal.classList.contains('open')) return;
                    tp3dTutorial.start(offerSteps());
                    fetch('http://127.0.0.1:' + PORT + '/complete_tutorial', {
                        method: 'POST', keepalive: true,
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: opts.offerId })
                    }).catch(function() {});
                })
                .catch(function() {});
        };
    }
    closeBtn.addEventListener('click', close);
    modal.addEventListener('click', function(e) { if (e.target === modal) close(); });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && modal.classList.contains('open')) close();
    });
    document.body.appendChild(modal);
    updateCounter();
    loadProgress();
}
