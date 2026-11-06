// Page translation for the picker pages, using Chromium's built-in on-device
// Translator API (Chrome / Edge 138+). Inlined at serve time into each page
// (the __TRANSLATE_JS__ token in picker_server.py). Needs no extension and
// no flags: the picker window is launched with --app=, which has no browser
// chrome, so Chromium's own "Translate this page" UI is never available --
// this adds our own control instead.
//
// The control is a small <select> in the top-right of the sidebar's title area.
// Choosing a language is a user gesture, which the first-time language-pack
// download requires. The UI text is English (<html lang="en">); the browser
// translates en -> target locally, so nothing is sent to a server.
//
// Hides itself entirely when `Translator` is missing (older Chromium, Brave).
(function () {
    'use strict';
    if (!('Translator' in self)) return;

    var SOURCE_LANG = 'en';
    var STORAGE_KEY = 'tp3dTranslateLang';
    // Candidates; only those the browser reports as available/downloadable are
    // offered. Labels are endonyms (Intl.DisplayNames in the language itself),
    // so the list stays readable after the page itself has been translated.
    var CANDIDATES = ['de', 'es', 'fr', 'it', 'pt', 'nl', 'pl', 'ru', 'tr', 'ja', 'ko', 'zh', 'zh-Hant', 'ar', 'hi', 'vi', 'th', 'cs', 'sv', 'da', 'fi', 'no', 'uk'];
    var ATTRS = ['title', 'placeholder', 'aria-label'];
    var SKIP_SELECTOR = 'script, style, textarea, code, pre, svg, canvas, [translate="no"], [contenteditable=""], [contenteditable="true"], .leaflet-container, .tp3d-translate';
    var HAS_LETTER = /\p{L}/u;

    var currentLang = '';          // '' = original English
    var translator = null;         // Translator instance for currentLang
    var generation = 0;            // bumped on every apply/revert to drop stale async work
    var textState = new Map();     // Text node -> {orig, written}
    var attrState = new Map();     // Element -> {attr: {orig, written}}
    var cache = {};                // lang -> Map(original string -> translation)
    var observer = null;
    var pending = new Set();       // nodes/elements queued by the MutationObserver
    var flushTimer = null;

    function storageGet() { try { return localStorage.getItem(STORAGE_KEY) || ''; } catch (e) { return ''; } }
    function storageSet(v) { try { if (v) localStorage.setItem(STORAGE_KEY, v); else localStorage.removeItem(STORAGE_KEY); } catch (e) { /* storage can be unavailable */ } }

    function skipped(el) {
        return !el || !!(el.closest && el.closest(SKIP_SELECTOR));
    }

    // ── Terms that must stay as written ──────────────────────────────────
    // Product / format / service names. The Translator API has no glossary, so
    // each term is swapped for a numbered placeholder before translating and
    // swapped back afterwards (keeps the sentence context intact). If the
    // model drops or mangles a placeholder, that string falls back to
    // translating only the text between the terms. Extend via
    // tp3dTranslate.addTerms([...]); whole elements can also use translate="no".
    var NO_TRANSLATE = ['TrailPrint3D', 'trailprint3d.com', 'Blender', 'OpenStreetMap', 'OSM', 'ESA WorldCover',
        'WorldCover', 'ESA', 'Overpass', 'GPX', 'GeoJSON', 'STL', 'OBJ', '3MF', 'SVG'];
    var termRe = null;
    function buildTermRe() {
        var parts = NO_TRANSLATE.slice().sort(function (a, b) { return b.length - a.length; })
            .map(function (t) { return t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); });
        termRe = parts.length
            ? new RegExp('(?<![\\p{L}\\p{N}])(?:' + parts.join('|') + ')(?![\\p{L}\\p{N}])', 'gu')
            : null;
    }
    buildTermRe();

    function rawTranslate(str) { return translator.translate(str); }

    function protectedTranslate(str) {
        if (!termRe) return rawTranslate(str);
        var found = [];
        var masked = str.replace(termRe, function (m) { found.push(m); return '[' + found.length + ']'; });
        if (!found.length) return rawTranslate(str);
        // Nothing left to translate once the terms are masked.
        if (!HAS_LETTER.test(masked.replace(/\[\d+\]/g, ''))) return Promise.resolve(str);
        return rawTranslate(masked).then(function (out) {
            var ok = found.every(function (_, i) { return out.split('[' + (i + 1) + ']').length === 2; });
            if (ok) return out.replace(/\[(\d+)\]/g, function (m, n) { return found[+n - 1] !== undefined ? found[+n - 1] : m; });
            // Fallback: translate only the pieces between the terms.
            var pieces = str.split(termRe), terms = str.match(termRe) || [];
            return Promise.all(pieces.map(function (piece) {
                return HAS_LETTER.test(piece) ? rawTranslate(piece) : piece;
            })).then(function (done) {
                return done.map(function (d, i) { return d + (terms[i] || ''); }).join('');
            });
        });
    }

    // ── Translation primitive (deduplicated + cached per language) ──────
    function translateString(str) {
        var map = cache[currentLang] || (cache[currentLang] = new Map());
        if (map.has(str)) return Promise.resolve(map.get(str));
        return protectedTranslate(str).then(function (out) { map.set(str, out); return out; });
    }

    // ── Text nodes ───────────────────────────────────────────────────────
    function collectTextNodes(root, out) {
        if (root.nodeType === Node.TEXT_NODE) {
            if (HAS_LETTER.test(root.nodeValue) && !skipped(root.parentElement)) out.push(root);
            return;
        }
        if (root.nodeType !== Node.ELEMENT_NODE || skipped(root)) return;
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) {
            var n = walker.currentNode;
            if (HAS_LETTER.test(n.nodeValue) && !skipped(n.parentElement)) out.push(n);
        }
    }

    function applyText(node, gen) {
        var st = textState.get(node);
        // Our own write coming back through the observer: nothing to do.
        if (st && node.nodeValue === st.written) return Promise.resolve();
        var orig = node.nodeValue;
        return translateString(orig).then(function (out) {
            if (gen !== generation || !node.isConnected || node.nodeValue !== orig) return;
            textState.set(node, { orig: orig, written: out });
            node.nodeValue = out;
        });
    }

    // ── Attributes ───────────────────────────────────────────────────────
    function collectAttrElements(root, out) {
        if (root.nodeType !== Node.ELEMENT_NODE || skipped(root)) return;
        var sel = ATTRS.map(function (a) { return '[' + a + ']'; }).join(',');
        if (root.matches(sel)) out.push(root);
        root.querySelectorAll(sel).forEach(function (el) { if (!skipped(el)) out.push(el); });
    }

    function applyAttrs(el, gen) {
        var states = attrState.get(el) || {};
        var jobs = ATTRS.map(function (attr) {
            if (!el.hasAttribute(attr)) return Promise.resolve();
            var cur = el.getAttribute(attr);
            var st = states[attr];
            if (st && cur === st.written) return Promise.resolve();
            if (!HAS_LETTER.test(cur)) return Promise.resolve();
            return translateString(cur).then(function (out) {
                if (gen !== generation || !el.isConnected || el.getAttribute(attr) !== cur) return;
                states[attr] = { orig: cur, written: out };
                attrState.set(el, states);
                el.setAttribute(attr, out);
            });
        });
        return Promise.all(jobs);
    }

    // ── Whole-subtree pass, in small parallel batches ───────────────────
    function translateSubtree(root, gen) {
        var texts = [], els = [];
        collectTextNodes(root, texts);
        collectAttrElements(root, els);
        var jobs = texts.map(function (n) { return function () { return applyText(n, gen); }; })
            .concat(els.map(function (e) { return function () { return applyAttrs(e, gen); }; }));
        var i = 0, BATCH = 8;
        function next() {
            if (gen !== generation || i >= jobs.length) return Promise.resolve();
            var slice = jobs.slice(i, i + BATCH);
            i += BATCH;
            return Promise.all(slice.map(function (j) { return j(); })).then(next);
        }
        return next();
    }

    // ── Dynamic content (modals, history panel, status messages) ────────
    function onMutations(records) {
        if (!currentLang) return;
        records.forEach(function (r) {
            if (r.type === 'childList') {
                r.addedNodes.forEach(function (n) { pending.add(n); });
            } else if (r.type === 'characterData') {
                pending.add(r.target);
            } else if (r.type === 'attributes') {
                pending.add(r.target);
            }
        });
        if (pending.size && !flushTimer) flushTimer = setTimeout(flush, 50);
    }

    function flush() {
        flushTimer = null;
        var gen = generation;
        var batch = Array.from(pending);
        pending.clear();
        batch.forEach(function (n) {
            if (!n.isConnected) return;
            if (n.nodeType === Node.TEXT_NODE) {
                var arr = [];
                collectTextNodes(n, arr);
                arr.forEach(function (t) { applyText(t, gen); });
            } else if (n.nodeType === Node.ELEMENT_NODE) {
                translateSubtree(n, gen);
            }
        });
    }

    function startObserving() {
        if (!observer) observer = new MutationObserver(onMutations);
        observer.observe(document.body, {
            childList: true, subtree: true, characterData: true,
            attributes: true, attributeFilter: ATTRS,
        });
    }

    function stopObserving() {
        if (observer) { observer.disconnect(); observer.takeRecords(); }
        pending.clear();
        if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
    }

    // ── Public operations ────────────────────────────────────────────────
    function revert() {
        generation++;
        stopObserving();
        textState.forEach(function (st, node) {
            // Only restore nodes still showing our translation; if the page
            // rewrote it since, its own value wins.
            if (node.isConnected && node.nodeValue === st.written) node.nodeValue = st.orig;
        });
        attrState.forEach(function (states, el) {
            Object.keys(states).forEach(function (attr) {
                if (el.isConnected && el.getAttribute(attr) === states[attr].written) el.setAttribute(attr, states[attr].orig);
            });
        });
        textState.clear();
        attrState.clear();
        currentLang = '';
        translator = null;
        document.documentElement.lang = SOURCE_LANG;
        storageSet('');
    }

    function apply(lang, onProgress) {
        if (!lang) { revert(); return Promise.resolve(true); }
        if (lang === currentLang) return Promise.resolve(true);
        if (currentLang) revert();
        var opts = { sourceLanguage: SOURCE_LANG, targetLanguage: lang };
        return Translator.availability(opts).then(function (avail) {
            if (avail === 'unavailable') return false;
            return Translator.create({
                sourceLanguage: SOURCE_LANG,
                targetLanguage: lang,
                monitor: function (m) {
                    m.addEventListener('downloadprogress', function (e) { if (onProgress) onProgress(e.loaded); });
                },
            }).then(function (t) {
                var gen = ++generation;
                translator = t;
                currentLang = lang;
                document.documentElement.lang = lang;
                return translateSubtree(document.body, gen).then(function () {
                    if (gen !== generation) return true;
                    startObserving();
                    storageSet(lang);
                    return true;
                });
            });
        });
    }

    // ── UI ───────────────────────────────────────────────────────────────
    function langLabel(code) {
        try { return new Intl.DisplayNames([code], { type: 'language' }).of(code); } catch (e) { return code; }
    }

    function buildControl(langs) {
        var wrap = document.createElement('div');
        wrap.className = 'tp3d-translate';
        wrap.setAttribute('translate', 'no');
        var sel = document.createElement('select');
        sel.className = 'tp3d-translate-select';
        sel.title = 'Translate this page (runs locally in your browser)';
        sel.setAttribute('aria-label', 'Translate this page');

        function fill() {
            sel.innerHTML = '';
            var head = document.createElement('option');
            head.value = '__head';
            head.textContent = currentLang ? '\uD83C\uDF10 ' + currentLang.toUpperCase() : '\uD83C\uDF10' + 'EN';
            head.disabled = true;
            sel.appendChild(head);
            if (currentLang) {
                var orig = document.createElement('option');
                orig.value = '';
                orig.textContent = '\u21A9 English (original)';
                sel.appendChild(orig);
            }
            langs.forEach(function (c) {
                if (c === currentLang) return;
                var o = document.createElement('option');
                o.value = c;
                o.textContent = langLabel(c);
                sel.appendChild(o);
            });
            sel.value = '__head';
        }

        sel.addEventListener('change', function () {
            var lang = sel.value;
            if (lang === '__head') return;
            sel.disabled = true;
            var head = sel.options[0];
            head.textContent = '\uD83C\uDF10 \u2026';
            sel.value = '__head';
            apply(lang, function (p) { head.textContent = '\uD83C\uDF10 ' + Math.round(p * 100) + '%'; })
                .catch(function (e) { console.warn('[TP3D translate] failed:', e); })
                .then(function () { sel.disabled = false; fill(); });
        });

        fill();
        wrap.appendChild(sel);
        return wrap;
    }

    // Top-right of the sidebar's title area (.sidebar-title-row on the map
    // page, .sidebar-heading on the others), absolutely positioned so neither
    // heading layout has to change.
    function mount(control) {
        var host = document.querySelector('#sidebar .sidebar-title-row, #sidebar .sidebar-heading');
        if (host) {
            host.classList.add('tp3d-translate-host');
            host.appendChild(control);
        } else {
            control.classList.add('tp3d-translate-floating');
            document.body.appendChild(control);
        }
    }

    function init() {
        // Keep only languages this browser can actually do en -> X for.
        Promise.all(CANDIDATES.map(function (c) {
            return Translator.availability({ sourceLanguage: SOURCE_LANG, targetLanguage: c })
                .then(function (a) { return a === 'unavailable' ? null : { code: c, avail: a }; })
                .catch(function () { return null; });
        })).then(function (res) {
            var ok = res.filter(Boolean);
            if (!ok.length) return;
            mount(buildControl(ok.map(function (r) { return r.code; })));
            // Restore last choice only when no download (= no user gesture) is needed.
            var saved = storageGet();
            var hit = ok.filter(function (r) { return r.code === saved && r.avail === 'available'; })[0];
            if (hit) apply(hit.code).catch(function (e) { console.warn('[TP3D translate] restore failed:', e); });
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();

    window.tp3dTranslate = {
        apply: apply, revert: revert,
        addTerms: function (terms) {
            terms.forEach(function (t) { if (t && NO_TRANSLATE.indexOf(t) < 0) NO_TRANSLATE.push(t); });
            buildTermRe();
            cache = {};
        },
    };
})();
