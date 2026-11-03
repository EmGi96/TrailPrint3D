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
// [feature, free, premium] -- true/false render as a check/dash; strings
// are HTML.
var TP3D_UPGRADE_ROWS = [
    ['GPX trails per map', '1', 'Multiple'],
    ['GeoJSON boundary files', '1', 'Multiple (merged)'],
    ['Shell, text icons & medal handles', false, true],
    ['Jigsaw Puzzle Generator', tp3dPieceIcons(['square']), tp3dPieceIcons(['square', 'hex', 'radial'])],
    ['Multi Tile Generator', false, true],
    ['Sliding Puzzle Generator', false, true]
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
                return '<tr><th scope="row">' + row[0] + '</th>' + cell(row[1]) + cell(row[2]) + '</tr>';
            }).join('')
            + '</tbody></table><div class="upgrade-links">'
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
        tp3dToggleImagePopover(btn);
    } else if (!(event.target.closest && event.target.closest('#tp3dImagePopover'))) {
        tp3dCloseImagePopover();
    }
});
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') tp3dCloseImagePopover();
});

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
                        : element.id === 'citySearchInput' ? 'City or place name'
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
