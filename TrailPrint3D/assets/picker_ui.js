// Shared dialog and form accessibility for map and 3D-preview generators.
function tp3dAlert(message, title) {
    var modal = document.getElementById('tp3dNotice');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'tp3dNotice';
        modal.className = 'tp3d-modal';
        modal.innerHTML = '<div class="tp3d-modal-box"><div class="modal-header">'
            + '<span id="tp3dNoticeTitle"></span><button type="button" class="tp3d-modal-close-btn" aria-label="Close notice">×</button>'
            + '</div><div class="notice-body"></div><button type="button" class="btn-send full-width">OK</button></div>';
        modal.querySelectorAll('button').forEach(function(button) {
            button.addEventListener('click', function() { modal.classList.remove('open'); });
        });
        modal.addEventListener('click', function(event) {
            if (event.target === modal) modal.classList.remove('open');
        });
        document.body.appendChild(modal);
    }
    modal.querySelector('#tp3dNoticeTitle').textContent = title || 'Notice';
    modal.querySelector('.notice-body').textContent = message;
    modal.classList.add('open');
}

// Two-choice dialog. Resolves true for the yes button, false for the no
// button, and null if dismissed (×, backdrop click, Escape).
function tp3dConfirm(message, title, yesLabel, noLabel) {
    var modal = document.getElementById('tp3dConfirm');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'tp3dConfirm';
        modal.className = 'tp3d-modal';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'tp3dConfirmTitle');
        modal.innerHTML = '<div class="tp3d-modal-box"><div class="modal-header">'
            + '<span id="tp3dConfirmTitle"></span><button type="button" class="tp3d-modal-close-btn" data-answer="dismiss" aria-label="Close">×</button>'
            + '</div><div class="notice-body"></div><div class="confirm-actions">'
            + '<button type="button" class="btn-plate-settings" data-answer="no"></button>'
            + '<button type="button" class="btn-send" data-answer="yes"></button></div></div>';
        document.body.appendChild(modal);
    }
    modal.querySelector('#tp3dConfirmTitle').textContent = title || 'Confirm';
    modal.querySelector('.notice-body').textContent = message;
    modal.querySelector('[data-answer="yes"]').textContent = yesLabel || 'Yes';
    modal.querySelector('[data-answer="no"]').textContent = noLabel || 'No';
    return new Promise(function(resolve) {
        function finish(answer) {
            modal.classList.remove('open');
            modal.onclick = null;
            modal.onkeydown = null;
            resolve(answer);
        }
        modal.onclick = function(event) {
            if (event.target === modal) return finish(null);
            var button = event.target.closest('[data-answer]');
            if (!button) return;
            var answer = button.getAttribute('data-answer');
            finish(answer === 'yes' ? true : answer === 'no' ? false : null);
        };
        modal.onkeydown = function(event) {
            if (event.key === 'Escape') finish(null);
        };
        modal.classList.add('open');
        modal.querySelector('[data-answer="yes"]').focus();
    });
}

// Generate without closing the page: /confirm with stay_open answers with
// progressPort, where the progress window process serves Blender's progress
// card for this page instead of opening its own window (progress_win.py
// --embed). Shows that card, then follows Blender's hand-off
// (progress.SubprocessProgress.hand_off): switch this window to next_url --
// the Post-processing page for the new map -- or offer to close it.
// opts.wide: room for the card's progress column (360 px) next to its square
// tile preview, which progress_win.py sizes to the column's height -- in the
// default width the column gets squeezed (the Multi Tile Generator's grid).
function tp3dShowGenerationProgress(progressPort, opts) {
    opts = opts || {};
    var base = 'http://127.0.0.1:' + progressPort;
    var modal = document.createElement('div');
    modal.className = 'tp3d-modal tp3d-gen-progress open' + (opts.wide ? ' wide' : '');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-label', 'Generation progress');
    modal.innerHTML = '<div class="tp3d-modal-box"><div class="modal-header"><span>Generating in Blender…</span></div>'
        + '<iframe title="Generation progress"></iframe><div class="tp3d-gen-note" role="status"></div>'
        + '<button type="button" class="btn-send full-width">Close window</button></div>';
    document.body.appendChild(modal);
    var title = modal.querySelector('.modal-header span');
    var frame = modal.querySelector('iframe');
    var note = modal.querySelector('.tp3d-gen-note');
    var closeBtn = modal.querySelector('button');
    closeBtn.style.display = 'none';
    closeBtn.addEventListener('click', function() { window.close(); });

    var started = Date.now();
    var frameShown = false;
    function finished(text) {
        title.textContent = 'Done';
        note.textContent = text;
        closeBtn.style.display = '';
        closeBtn.focus();
    }
    function poll() {
        fetch(base + '/state', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(function(state) {
                if (!frameShown) { frameShown = true; frame.src = base + '/'; }
                if (state.handoff) {
                    if (state.next_url) {
                        note.textContent = 'Opening Post-processing…';
                        window.location.href = state.next_url;
                    } else {
                        finished('Generation finished. You can close this window.');
                    }
                    return;
                }
                setTimeout(poll, 500);
            })
            .catch(function() {
                // The progress process starts with the generation itself.
                if (!frameShown && Date.now() - started > 60000) {
                    note.textContent = 'No progress from Blender yet — it may still be busy.';
                    closeBtn.style.display = '';
                }
                setTimeout(poll, 700);
            });
    }
    poll();
}

// Free-vs-Premium comparison, opened by the "Upgrade" button next to the
// generator name on the free pages (any button with data-upgrade-panel --
// the premium pages simply don't have one). Links are the same Patreon /
// Thangs pair as the Blender sidebar's "Upgrade to Premium" box
// (operators.TP3D_OT_open_website / TP3D_OT_open_thangs).
var TP3D_UPGRADE_LINKS = [
    { label: 'Get it on Patreon', url: 'https://patreon.com/EmGi3D?utm_source=Generator' },
    { label: 'Get it on Thangs', url: 'https://thangs.com/designer/EmGi/3d-model/TrailPrint3D%20Premium%20Version%20%7C%20Blender%20Addon-1587024' }
];
// The Jigsaw Puzzle Generator's piece-shape icons (same SVGs as its
// shapeSquareBtn / shapeHexBtn / shapeRadialBtn).
var TP3D_PIECE_ICONS = {
    square: ['Square pieces', '<rect x="3" y="3" width="14" height="14"/>', 2],
    hex: ['Hexagonal pieces', '<polygon points="10,2.5 16.5,6.25 16.5,13.75 10,17.5 3.5,13.75 3.5,6.25"/>', 2],
    radial: ['Concentric-ring pieces', '<circle cx="10" cy="10" r="7.5"/><circle cx="10" cy="10" r="4"/>'
        + '<line x1="10" y1="2.5" x2="10" y2="17.5"/><line x1="2.5" y1="10" x2="17.5" y2="10"/>', 1.5]
};
function tp3dPieceIcons(names) {
    return '<span class="upgrade-icons">' + names.map(function(name) {
        var icon = TP3D_PIECE_ICONS[name];
        return '<svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="' + icon[2]
            + '" role="img" aria-label="' + icon[0] + '"><title>' + icon[0] + '</title>' + icon[1] + '</svg>';
    }).join('') + '</span>';
}
// [feature, free, premium, example image] -- true/false render as a
// check/dash; strings are HTML. The image (in trailprint3d.com's
// images/howto/) shows on hovering the "?" after the feature name.
var TP3D_UPGRADE_IMAGE_BASE = 'https://trailprint3d.com/images/howto/';
var TP3D_UPGRADE_ROWS = [
    ['GPX trails per map', '1', 'Multiple', 'PR_MultipleGPX.webp'],
    ['Shell, text icons & medal handles', false, true, 'PR_Plate.webp'],
    ['Jigsaw Puzzle Generator', tp3dPieceIcons(['square']), tp3dPieceIcons(['square', 'hex', 'radial']), 'PR_Jigzaw.webp'],
    ['Multi Tile Generator', false, true, 'PR_Multitile.webp'],
    ['Sliding Puzzle Generator', false, true, 'PR_Sliding.webp']
];
// *mapsGenerated*: set when opened for the every-100-generations support
// prompt (see below) -- adds a thank-you note above the comparison.
function tp3dShowUpgradePanel(mapsGenerated) {
    var modal = document.getElementById('tp3dUpgrade');
    if (!modal) {
        function cell(value) {
            if (value === true) return '<td class="upgrade-yes" aria-label="Included">✓</td>';
            if (value === false) return '<td class="upgrade-no" aria-label="Not included">–</td>';
            return '<td>' + value + '</td>';
        }
        modal = document.createElement('div');
        modal.id = 'tp3dUpgrade';
        modal.className = 'tp3d-modal';
        modal.innerHTML = '<div class="tp3d-modal-box upgrade-box"><div class="modal-header">'
            + '<span>Upgrade to Premium</span><button type="button" class="tp3d-modal-close-btn" title="Close">×</button></div>'
            + '<p class="upgrade-thanks" hidden></p>'
            + '<p class="upgrade-intro">Premium unlocks every generator and tool in TrailPrint3D, and supports its development.</p>'
            + '<table class="upgrade-table"><thead><tr><th scope="col">Feature</th><th scope="col">Free</th>'
            + '<th scope="col" class="upgrade-premium-col">Premium</th></tr></thead><tbody>'
            + TP3D_UPGRADE_ROWS.map(function(row) {
                var hint = row[3]
                    ? '<button type="button" class="preview-btn upgrade-preview-btn" data-preview-hover data-preview-img="'
                        + TP3D_UPGRADE_IMAGE_BASE + row[3] + '" aria-label="Show an example: ' + row[0].replace(/&/g, '&amp;') + '">?</button>'
                    : '';
                return '<tr><th scope="row"><span class="upgrade-feature">' + row[0] + hint + '</span></th>'
                    + cell(row[1]) + cell(row[2]) + '</tr>';
            }).join('')
            + '</tbody></table>'
            + '<p class="upgrade-intro">Download the Premium version from either link below and install it in Blender '
            + 'the same way as the free version.</p>'
            + '<div class="upgrade-links">'
            + TP3D_UPGRADE_LINKS.map(function(link) {
                return '<a class="btn-send upgrade-link" href="' + link.url + '" target="_blank" rel="noopener">' + link.label + '</a>';
            }).join('')
            + '</div></div>';
        modal.querySelector('.tp3d-modal-close-btn').addEventListener('click', function() { modal.classList.remove('open'); });
        modal.addEventListener('click', function(event) {
            if (event.target === modal) modal.classList.remove('open');
        });
        document.body.appendChild(modal);
    }
    var thanks = modal.querySelector('.upgrade-thanks');
    thanks.hidden = !mapsGenerated;
    if (mapsGenerated) {
        thanks.innerHTML = 'You\'ve generated <strong>' + Number(mapsGenerated).toLocaleString('en-US')
            + ' maps</strong> with TrailPrint3D, so it looks like you\'re enjoying it! 🎉 '
            + 'If you\'d like to support its development, getting Premium is the best way to do it.';
    }
    modal.classList.add('open');
}
document.addEventListener('click', function(event) {
    if (event.target.closest && event.target.closest('[data-upgrade-panel]')) tp3dShowUpgradePanel();
});

// Every 100 generations (free version only) the pages with an Upgrade button
// open the panel once with the thank-you note. Blender decides and records
// it (picker_server.claim_support_prompt), so each milestone shows once.
// Waits until no tutorial or other dialog is on screen.
(function() {
    function start() {
        if (typeof PORT === 'undefined' || !document.querySelector('[data-upgrade-panel]')) return;
        fetch('http://127.0.0.1:' + PORT + '/support_prompt', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data || !(data.count > 0)) return;
                var waited = 0;
                (function show() {
                    var busy = document.querySelector('#tp3dTutorial.active, .tp3d-modal.open');
                    // Re-check 4x a second; give up after 3 minutes rather
                    // than pop up mid-task.
                    if (busy && waited < 720) { waited++; setTimeout(show, 250); return; }
                    if (!busy) tp3dShowUpgradePanel(data.count);
                }());
            })
            .catch(function() {});
    }
    // A short delay so the page's own first dialogs (tutorial offer,
    // export-folder guide) get to open first and the prompt waits for them.
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function() { setTimeout(start, 300); });
    else setTimeout(start, 300);
}());

// Small self-dismissing note -- for hints that shouldn't block the page the
// way tp3dAlert does (e.g. clicking a Premium-only option in the free
// version). Shown just below *anchor* when given (kept inside the window),
// else at the bottom centre; never takes clicks.
var tp3dToastTimer = null;
function tp3dToast(message, anchor) {
    var toast = document.getElementById('tp3dToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'tp3dToast';
        toast.className = 'tp3d-toast';
        toast.setAttribute('role', 'status');
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.left = '';
    toast.style.top = '';
    toast.classList.toggle('anchored', !!anchor);
    toast.classList.add('show');
    if (anchor) {
        var rect = anchor.getBoundingClientRect();
        var width = toast.offsetWidth;
        var left = rect.left + rect.width / 2 - width / 2;
        left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
        toast.style.left = left + 'px';
        toast.style.top = (rect.bottom + 6) + 'px';
    }
    clearTimeout(tp3dToastTimer);
    tp3dToastTimer = setTimeout(function() { toast.classList.remove('show'); }, 3000);
}

// Image hint for controls outside the Settings popup (which has its own
// preview panel) -- e.g. the sidebar's Resolution/Size. Any button with
// data-preview-img="url" (and optionally data-preview-caption) opens a small
// floating card next to it; a click anywhere else, Escape, or the same button
// again closes it.
function tp3dToggleImagePopover(anchor) {
    var pop = document.getElementById('tp3dImagePopover');
    if (pop && pop.classList.contains('open') && pop.tp3dAnchor === anchor) {
        tp3dCloseImagePopover();
        return;
    }
    tp3dOpenImagePopover(anchor);
}
// *hover*: opened by pointing at / focusing a data-preview-hover button --
// the card then ignores the mouse, so it can't cover its own "?" and make
// the pointer "leave" it (which would close and reopen it in a loop).
function tp3dOpenImagePopover(anchor, hover) {
    var pop = document.getElementById('tp3dImagePopover');
    if (!pop) {
        pop = document.createElement('div');
        pop.id = 'tp3dImagePopover';
        pop.className = 'tp3d-img-popover';
        pop.setAttribute('role', 'dialog');
        document.body.appendChild(pop);
    }
    pop.innerHTML = '';
    var img = document.createElement('img');
    img.src = anchor.dataset.previewImg;
    img.alt = 'Preview';
    pop.appendChild(img);
    if (anchor.dataset.previewCaption) {
        var caption = document.createElement('div');
        caption.className = 'tp3d-img-popover-caption';
        caption.textContent = anchor.dataset.previewCaption;
        pop.appendChild(caption);
    }
    pop.tp3dAnchor = anchor;
    pop.classList.toggle('hover', !!hover);
    pop.classList.add('open');
    // Right of the "?" (the sidebar sits on the left), kept inside the window;
    // re-placed once the image knows its height.
    function place() {
        var rect = anchor.getBoundingClientRect();
        var left = Math.min(rect.right + 12, window.innerWidth - pop.offsetWidth - 8);
        var top = Math.min(Math.max(8, rect.top - 12), window.innerHeight - pop.offsetHeight - 8);
        pop.style.left = Math.max(8, left) + 'px';
        pop.style.top = Math.max(8, top) + 'px';
    }
    place();
    img.addEventListener('load', place);
}
function tp3dCloseImagePopover() {
    var pop = document.getElementById('tp3dImagePopover');
    if (pop) pop.classList.remove('open');
}
document.addEventListener('click', function(event) {
    var btn = event.target.closest && event.target.closest('[data-preview-img]');
    if (btn) {
        event.preventDefault();
        event.stopPropagation();
        // A hover "?" is already open by the time it's clicked (and a tap
        // fires mouseover first) -- a click keeps it open instead of
        // toggling it shut; tapping elsewhere closes it.
        if (btn.hasAttribute('data-preview-hover')) tp3dOpenImagePopover(btn, true);
        else tp3dToggleImagePopover(btn);
    } else if (!(event.target.closest && event.target.closest('#tp3dImagePopover'))) {
        tp3dCloseImagePopover();
    }
});
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') tp3dCloseImagePopover();
});
// data-preview-hover buttons (e.g. the Upgrade panel's feature rows) open on
// hover / keyboard focus too, and close again when left.
(function() {
    function hoverAnchor(node) {
        return node && node.closest ? node.closest('[data-preview-img][data-preview-hover]') : null;
    }
    function openFor(event) {
        var anchor = hoverAnchor(event.target);
        if (!anchor) return;
        var pop = document.getElementById('tp3dImagePopover');
        if (!(pop && pop.classList.contains('open') && pop.tp3dAnchor === anchor)) tp3dOpenImagePopover(anchor, true);
    }
    function closeFor(event) {
        var anchor = hoverAnchor(event.target);
        if (!anchor || anchor.contains(event.relatedTarget)) return;
        var pop = document.getElementById('tp3dImagePopover');
        if (pop && pop.tp3dAnchor === anchor) tp3dCloseImagePopover();
    }
    document.addEventListener('mouseover', openFor);
    document.addEventListener('focusin', openFor);
    document.addEventListener('mouseout', closeFor);
    document.addEventListener('focusout', closeFor);
}());

(function() {
    var dialogSelector = '.tp3d-modal';
    var toggleSelector = 'button.shape-btn, button.shape-toggle-btn, button.element-mode-btn, button.source-switch-btn, button[data-element-toggle]';
    var stack = [];
    var inertElements = new Map();
    var nextId = 0;
    var scheduled = false;

    function visible(element) {
        return !element.disabled && !element.closest('[inert]') && element.getClientRects().length > 0;
    }

    function focusable(dialog) {
        return Array.from(dialog.querySelectorAll('button, input, select, textarea, a[href], summary, [tabindex]'))
            .filter(function(element) { return element.tabIndex >= 0 && visible(element); });
    }

    function prepare(root) {
        if (!(root instanceof Element)) return;
        var elements = [root].concat(Array.from(root.querySelectorAll('button, input, select, ' + dialogSelector)));
        elements.forEach(function(element) {
            if (element.matches(dialogSelector)) {
                element.setAttribute('role', 'dialog');
                element.setAttribute('aria-modal', 'true');
                element.tabIndex = -1;
                var title = element.querySelector('.modal-header span');
                if (title && !element.hasAttribute('aria-labelledby')) {
                    if (!title.id) title.id = 'tp3dDialogTitle' + (++nextId);
                    element.setAttribute('aria-labelledby', title.id);
                }
            }
            if (element.matches('button') && !element.hasAttribute('aria-label')
                && (!element.textContent.trim() || element.matches('.shape-toggle-btn')) && element.title) {
                element.setAttribute('aria-label', element.title);
            }
            if (element.matches('button.tp3d-modal-close-btn')) {
                element.setAttribute('aria-label', element.title || 'Close');
            }
            if (element.matches('input:not([type=hidden]):not([type=file]), select') && !element.hasAttribute('aria-label')) {
                var row = element.closest('.field-row, .adv-field-row, .card-field');
                var label = row && row.querySelector('label');
                if (element.id && label && !label.htmlFor && row.querySelectorAll('input, select').length === 1) {
                    label.htmlFor = element.id;
                } else if (!element.labels || !element.labels.length) {
                    var name = element.id === 'resolutionSlider' ? 'Terrain mesh resolution'
                        : element.id === 'subdivisionSlider' ? 'Segments'
                        : element.id === 'tileSpacing' ? 'Tile spacing'
                        : element.id === 'latInput' ? 'Latitude'
                        : element.id === 'lonInput' ? 'Longitude'
                        : element.id === 'citySearchInput' ? 'Location'
                        : element.id === 'textValue' ? 'Text'
                        : null;
                    element.setAttribute('aria-label', name || element.title || (label && label.textContent.trim()) || element.placeholder || 'Value');
                }
            }
            if (element.matches(toggleSelector)) {
                element.setAttribute('aria-pressed', String(element.classList.contains('active') || element.classList.contains('enabled')));
            }
        });
    }

    function restoreBackground() {
        inertElements.forEach(function(value, element) { element.inert = value; });
        inertElements.clear();
    }

    function syncDialogs() {
        scheduled = false;
        restoreBackground();
        var closed = stack.filter(function(entry) { return !entry.modal.isConnected || !entry.modal.classList.contains('open'); });
        stack = stack.filter(function(entry) { return entry.modal.isConnected && entry.modal.classList.contains('open'); });
        if (closed.length) {
            var returnTo = closed[closed.length - 1].returnTo;
            if (returnTo && returnTo.isConnected && visible(returnTo)) returnTo.focus();
        }
        document.querySelectorAll(dialogSelector).forEach(function(modal) {
            if (modal.classList.contains('open') && !stack.some(function(entry) { return entry.modal === modal; })) {
                prepare(modal);
                stack.push({ modal: modal, returnTo: document.activeElement });
                if (!modal.contains(document.activeElement)) (focusable(modal)[0] || modal).focus();
            }
        });
        if (!stack.length || document.querySelector('#tp3dTutorial.active')) return;
        var top = stack[stack.length - 1].modal;
        // A text dialog lives inside the 3D workspace; inert only siblings
        // along its ancestor chain, never an ancestor of the dialog itself.
        var branch = top;
        while (branch.parentElement) {
            Array.from(branch.parentElement.children).forEach(function(sibling) {
                if (sibling === branch || sibling.matches('script, style, #tp3dTutorial')) return;
                inertElements.set(sibling, sibling.inert);
                sibling.inert = true;
            });
            if (branch.parentElement === document.body) break;
            branch = branch.parentElement;
        }
    }

    function scheduleDialogs() {
        if (scheduled) return;
        scheduled = true;
        queueMicrotask(syncDialogs);
    }

    document.addEventListener('keydown', function(event) {
        if (!stack.length || document.querySelector('#tp3dTutorial.active')) return;
        var top = stack[stack.length - 1].modal;
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopImmediatePropagation();
            top.classList.remove('open');
        } else if (event.key === 'Tab') {
            var items = focusable(top);
            var first = items[0] || top;
            var last = items[items.length - 1] || top;
            if (!items.length || !top.contains(document.activeElement) || (event.shiftKey && document.activeElement === first)) {
                event.preventDefault();
                (event.shiftKey ? last : first).focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    }, true);

    function init() {
        prepare(document.body);
        var observer = new MutationObserver(function(records) {
            records.forEach(function(record) {
                if (record.type === 'childList') {
                    record.addedNodes.forEach(prepare);
                    if (Array.from(record.addedNodes).concat(Array.from(record.removedNodes)).some(function(node) {
                        return node instanceof Element && (node.matches(dialogSelector) || node.querySelector(dialogSelector));
                    })) scheduleDialogs();
                } else if (record.target.matches(dialogSelector + ', #tp3dTutorial')) {
                    scheduleDialogs();
                } else if (record.target.matches(toggleSelector)) {
                    record.target.setAttribute('aria-pressed', String(record.target.classList.contains('active') || record.target.classList.contains('enabled')));
                }
            });
        });
        observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
        syncDialogs();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();

// Generator switcher: the header's generator name ("Map Generator", ...)
// becomes a button that opens a panel of every generator, as a grid of
// square tiles with an icon each. Picking one asks Blender to open it in
// this same window (POST /switch_generator, picker_server.
// process_generator_switch) and then goes to its page.
var TP3D_GENERATOR_ICONS = (function() {
    function svg(body) {
        return '<svg viewBox="0 0 48 48" width="44" height="44" fill="none" stroke="currentColor" stroke-width="2.2" '
            + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>';
    }
    return {
        // A framed mountain with a trail.
        map_generator: svg('<rect x="5" y="7" width="38" height="34" rx="3"/>'
            + '<path d="M8 36 L19 19 L26 28 L31 22 L40 36"/>'
            + '<path d="M14 36 C18 33 20 34 23 31 S29 29 33 27" stroke-dasharray="2.5 2.5"/>'),
        // A 2 x 2 block of tiles with the mountain running across.
        map_picker: svg('<rect x="5" y="5" width="18" height="18" rx="2"/><rect x="25" y="5" width="18" height="18" rx="2"/>'
            + '<rect x="5" y="25" width="18" height="18" rx="2"/><rect x="25" y="25" width="18" height="18" rx="2"/>'
            + '<path d="M8 39 L15 30 L19 35"/><path d="M28 39 L34 30 L40 39"/>'),
        // A jigsaw piece.
        puzzle_configurator: svg('<path d="M9 13 H18 C18 9 20 7 23 7 S28 9 28 13 H37 V22 C41 22 43 24 43 27 S41 32 37 32 V41 H28 '
            + 'C28 37 26 35 23 35 S18 37 18 41 H9 V32 C13 32 15 30 15 27 S13 22 9 22 Z"/>'),
        // A 3 x 3 sliding puzzle with one empty slot.
        sliding_puzzle_configurator: svg('<rect x="5" y="5" width="11" height="11" rx="1.5"/><rect x="18.5" y="5" width="11" height="11" rx="1.5"/>'
            + '<rect x="32" y="5" width="11" height="11" rx="1.5"/><rect x="5" y="18.5" width="11" height="11" rx="1.5"/>'
            + '<rect x="18.5" y="18.5" width="11" height="11" rx="1.5"/><rect x="5" y="32" width="11" height="11" rx="1.5"/>'
            + '<rect x="18.5" y="32" width="11" height="11" rx="1.5"/><rect x="32" y="32" width="11" height="11" rx="1.5"/>'
            + '<path d="M34 24 H41 M38 21 L41 24 L38 27" stroke-width="1.8"/>'),
        // A medal on its ribbon.
        medal_holder_generator: svg('<path d="M16 5 L22 20 M32 5 L26 20"/><path d="M13 5 H20 M28 5 H35"/>'
            + '<circle cx="24" cy="31" r="11"/><path d="M24 25 L25.8 28.6 L29.7 29.1 L26.9 31.8 L27.5 35.7 L24 33.9 L20.5 35.7 '
            + 'L21.1 31.8 L18.3 29.1 L22.2 28.6 Z" stroke-width="1.6"/>'),
        // A plate with the map's recess and a text line.
        backplate_generator: svg('<rect x="5" y="7" width="38" height="34" rx="4"/><rect x="11" y="12" width="26" height="18" rx="2"/>'
            + '<path d="M13 27 L20 19 L25 24 L29 20 L35 27"/><path d="M16 36 H32"/>'),
        // A wand with sparkles.
        post_process_generator: svg('<path d="M8 40 L30 18"/><path d="M27 15 L33 21"/>'
            + '<path d="M36 6 V12 M33 9 H39"/><path d="M41 21 V25 M39 23 H43"/><path d="M20 7 V11 M18 9 H22"/>'),
    };
})();

(function() {
    function switchTo(target, done, from) {
        fetch('/switch_generator', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: target, from: from || '' })
        })
            .then(function(r) { return r.json(); })
            .then(function(reply) {
                if (reply && reply.ok && reply.next_url) {
                    window.location.href = reply.next_url;
                    return;
                }
                done();
                tp3dAlert((reply && reply.message) || 'Could not open that generator.', 'Generators');
            })
            .catch(function() {
                done();
                tp3dAlert('Could not reach Blender — is it still running?', 'Generators');
            });
    }

    function buildPanel(menu) {
        var modal = document.createElement('div');
        modal.className = 'tp3d-modal tp3d-generator-panel';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-label', 'Generators');
        modal.innerHTML = '<div class="tp3d-modal-box"><div class="modal-header"><span>Generators</span>'
            + '<button type="button" class="tp3d-modal-close-btn" aria-label="Close">×</button></div>'
            + '<div class="tp3d-generator-grid"></div>'
            + '<div class="tp3d-library"><div class="tp3d-library-head"><span>My Maps</span>'
            + '<span class="tp3d-library-folder"></span></div><div class="tp3d-library-grid"></div></div></div>';
        var grid = modal.querySelector('.tp3d-generator-grid');
        function close() { modal.classList.remove('open'); }
        modal.tp3dLoadLibrary = function() { loadLibrary(modal); };
        menu.items.forEach(function(item) {
            var tile = document.createElement('button');
            tile.type = 'button';
            tile.className = 'tp3d-generator-tile' + (item.id === menu.current ? ' current' : '');
            tile.innerHTML = (TP3D_GENERATOR_ICONS[item.id] || '') + '<span></span>';
            tile.querySelector('span').textContent = item.label;
            if (item.id === menu.current) tile.setAttribute('aria-current', 'page');
            tile.addEventListener('click', function() {
                if (item.id === menu.current) { close(); return; }
                grid.querySelectorAll('.tp3d-generator-tile').forEach(function(t) { t.disabled = true; });
                tile.classList.add('busy');
                switchTo(item.id, function() {
                    grid.querySelectorAll('.tp3d-generator-tile').forEach(function(t) { t.disabled = false; });
                    tile.classList.remove('busy');
                });
            });
            grid.appendChild(tile);
        });
        modal.querySelector('.tp3d-modal-close-btn').addEventListener('click', close);
        modal.addEventListener('click', function(e) { if (e.target === modal) close(); });
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && modal.classList.contains('open')) close();
        });
        document.body.appendChild(modal);
        return modal;
    }

    // ── My Maps: every generated map, saved by Blender into the library
    // folder (library.py). Click one to open it in Post-processing.
    function formatSize(bytes) {
        return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';
    }
    function formatDate(seconds) {
        var d = new Date(seconds * 1000);
        return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    function libraryRequest(path, id) {
        return fetch(path, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id })
        }).then(function(r) { return r.json(); });
    }
    function loadLibrary(modal) {
        var grid = modal.querySelector('.tp3d-library-grid');
        var folder = modal.querySelector('.tp3d-library-folder');
        grid.innerHTML = '<div class="tp3d-library-note">Loading…</div>';
        fetch('/library', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var maps = (data && data.maps) || [];
                folder.textContent = data && data.folder ? data.folder : '';
                folder.title = folder.textContent;
                grid.innerHTML = '';
                if (!maps.length) {
                    grid.innerHTML = '<div class="tp3d-library-note">Nothing saved yet — every map you generate shows up here.</div>';
                    return;
                }
                maps.forEach(function(m) {
                    var tile = document.createElement('div');
                    tile.className = 'tp3d-library-tile';
                    tile.tabIndex = 0;
                    tile.setAttribute('role', 'button');
                    tile.title = 'Open ' + m.name + ' in Post-processing';
                    var pic = document.createElement('div');
                    pic.className = 'tp3d-library-pic';
                    if (m.thumb) {
                        var img = document.createElement('img');
                        img.alt = '';
                        img.loading = 'lazy';
                        img.src = '/library_thumb?id=' + encodeURIComponent(m.id) + '&t=' + Math.round(m.modified);
                        pic.appendChild(img);
                    } else {
                        var kindIcon = { map: 'map_generator', multitile: 'map_picker', puzzle: 'puzzle_configurator',
                                         sliding_puzzle: 'sliding_puzzle_configurator', medal_holder: 'medal_holder_generator',
                                         backplate: 'backplate_generator' }[m.kind];
                        pic.innerHTML = TP3D_GENERATOR_ICONS[kindIcon] || TP3D_GENERATOR_ICONS.map_generator;
                    }
                    var name = document.createElement('div');
                    name.className = 'tp3d-library-name';
                    name.textContent = m.name;
                    var meta = document.createElement('div');
                    meta.className = 'tp3d-library-meta';
                    meta.textContent = formatDate(m.modified) + ' · ' + formatSize(m.size);
                    var del = document.createElement('button');
                    del.type = 'button';
                    del.className = 'tp3d-library-del';
                    del.title = 'Delete from My Maps';
                    del.setAttribute('aria-label', 'Delete ' + m.name + ' from My Maps');
                    del.textContent = '×';
                    var ren = document.createElement('button');
                    ren.type = 'button';
                    ren.className = 'tp3d-library-del tp3d-library-ren';
                    ren.title = 'Rename';
                    ren.setAttribute('aria-label', 'Rename ' + m.name);
                    ren.textContent = '✎';
                    tile.append(pic, name, meta, ren, del);
                    ren.addEventListener('click', function(e) {
                        e.stopPropagation();
                        var input = document.createElement('input');
                        input.type = 'text';
                        input.className = 'tp3d-library-rename';
                        input.maxLength = 60;
                        input.value = m.name;
                        name.replaceWith(input);
                        input.focus();
                        input.select();
                        var done = false;
                        function finish(save) {
                            if (done) return;
                            done = true;
                            var wanted = input.value.trim();
                            input.replaceWith(name);
                            if (!save || !wanted || wanted === m.name) return;
                            name.textContent = wanted;
                            fetch('/library_rename', {
                                method: 'POST', headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ id: m.id, name: wanted })
                            }).then(function(r) { return r.json(); }).then(function(reply) {
                                if (reply && reply.ok) { m.name = reply.name; name.textContent = reply.name; return; }
                                name.textContent = m.name;
                                tp3dAlert((reply && reply.message) || 'Could not rename that map.', 'My Maps');
                            }).catch(function() {
                                name.textContent = m.name;
                                tp3dAlert('Could not reach Blender — is it still running?', 'My Maps');
                            });
                        }
                        input.addEventListener('click', function(ev) { ev.stopPropagation(); });
                        input.addEventListener('keydown', function(ev) {
                            ev.stopPropagation();
                            if (ev.key === 'Enter') finish(true);
                            if (ev.key === 'Escape') finish(false);
                        });
                        input.addEventListener('blur', function() { finish(true); });
                    });
                    function open() {
                        if (tile.classList.contains('busy')) return;
                        // A page with unsaved work (Post-processing) can offer
                        // to save it first, or call the switch off (false).
                        var before = typeof window.tp3dBeforeOpenMap === 'function'
                            ? Promise.resolve(window.tp3dBeforeOpenMap(m)) : Promise.resolve(true);
                        before.then(function(go) { if (go) openNow(); });
                    }
                    function openNow() {
                        tile.classList.add('busy');
                        libraryRequest('/library_open', m.id).then(function(reply) {
                            if (reply && reply.ok && reply.next_url) { window.location.href = reply.next_url; return; }
                            tile.classList.remove('busy');
                            tp3dAlert((reply && reply.message) || 'Could not open that map.', 'My Maps');
                        }).catch(function() {
                            tile.classList.remove('busy');
                            tp3dAlert('Could not reach Blender — is it still running?', 'My Maps');
                        });
                    }
                    tile.addEventListener('click', open);
                    tile.addEventListener('keydown', function(e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
                    del.addEventListener('click', function(e) {
                        e.stopPropagation();
                        tp3dConfirm('Delete "' + m.name + '" from My Maps? Its saved file is removed for good — a copy that is open in Blender stays there.',
                                    'Delete map', 'Delete', 'Keep').then(function(yes) {
                            if (!yes) return;
                            libraryRequest('/library_delete', m.id).then(function(reply) {
                                if (reply && reply.ok) {
                                    loadLibrary(modal);
                                    // The page may show that map (Post-processing's
                                    // Save button says "Saved" until it hears).
                                    if (typeof window.tp3dAfterLibraryDelete === 'function') window.tp3dAfterLibraryDelete(m.id);
                                    return;
                                }
                                tp3dAlert((reply && reply.message) || 'Could not delete that map.', 'My Maps');
                            }).catch(function() { tp3dAlert('Could not reach Blender — is it still running?', 'My Maps'); });
                        });
                    });
                    grid.appendChild(tile);
                });
            })
            .catch(function() {
                grid.innerHTML = '<div class="tp3d-library-note">Could not load My Maps.</div>';
            });
    }

    // Pages show the browser elsewhere too (Post-processing's top-right
    // corner): fills any element holding .tp3d-library-grid / -folder.
    window.tp3dLoadLibrary = loadLibrary;

    // Configuration / Model switch under the heading: a generator page
    // whose map is in Blender goes to it in Post-processing ("Model"), and
    // Post-processing goes back to the map's generator with the settings it
    // was made with ("Configuration" -- the page's own window.tp3dModeSwitch).
    function buildModeSwitch(heading, menu) {
        var onModel = menu.current === 'post_process_generator';
        var page = window.tp3dModeSwitch || {};
        if (onModel ? !page.available : !menu.hasModel) return;
        var wrap = document.createElement('div');
        wrap.className = 'tp3d-mode-switch';
        wrap.setAttribute('role', 'group');
        wrap.setAttribute('aria-label', 'Configuration or model');
        function segment(label, active, title, go) {
            var b = document.createElement('button');
            b.type = 'button';
            b.textContent = label;
            b.title = title;
            b.className = active ? 'active' : '';
            b.setAttribute('aria-pressed', active ? 'true' : 'false');
            if (!active) {
                b.addEventListener('click', function() {
                    wrap.querySelectorAll('button').forEach(function(x) { x.disabled = true; });
                    b.classList.add('busy');
                    go(function() {
                        wrap.querySelectorAll('button').forEach(function(x) { x.disabled = false; });
                        b.classList.remove('busy');
                    });
                });
            }
            return b;
        }
        wrap.append(
            segment('Configuration', !onModel, 'The generator settings this map was made with', function(done) {
                if (typeof page.toConfiguration === 'function') page.toConfiguration(done); else done();
            }),
            segment('Model', onModel, 'This map in Post-processing', function(done) {
                switchTo('post_process_generator', done, menu.current);
            })
        );
        heading.after(wrap);
    }

    function init() {
        var label = document.querySelector('.sidebar-subtitle');
        if (!label) return;
        fetch('/generator_menu', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(function(menu) {
                var items = (menu && menu.items) || [];
                if (items.length < 2 || !menu.current) return;
                var current = items.filter(function(i) { return i.id === menu.current; })[0];
                var button = document.createElement('button');
                button.type = 'button';
                button.className = 'tp3d-generator-switch';
                button.title = 'Switch to another generator';
                button.setAttribute('aria-haspopup', 'dialog');
                button.innerHTML = (TP3D_GENERATOR_ICONS[menu.current] || '') + '<span></span>';
                button.querySelector('span').textContent = current ? current.label : label.textContent;
                var panel = null;
                button.addEventListener('click', function() {
                    panel = panel || buildPanel(menu);
                    panel.classList.add('open');
                    panel.tp3dLoadLibrary();
                    var here = panel.querySelector('.tp3d-generator-tile.current') || panel.querySelector('.tp3d-generator-tile');
                    if (here) here.focus();
                });
                // The button is the heading now: drop the "TrailPrint3D" brand
                // line above it (and its line break).
                var heading = label.parentElement;
                if (heading) {
                    heading.querySelectorAll(':scope > .brand-link, :scope > br').forEach(function(el) { el.remove(); });
                }
                label.replaceWith(button);
                if (heading) buildModeSwitch(heading, menu);
            })
            .catch(function() {});
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
