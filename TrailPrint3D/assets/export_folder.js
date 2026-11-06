// Export folder handling shared by every generator page (via
// __EXPORT_FOLDER_JS__ in picker_server.py). Picking a folder here sets the
// Default Export Folder add-on preference only. Until a (still existing)
// export folder is in effect, the page's "Send to Blender" button is swapped for a
// "Select Export Folder" button in the same spot -- exports would have
// nowhere to go otherwise -- and a one-step guide in the tutorials' style
// (tutorial.js, so every page loads __TUTORIAL_JS__) points at it on open.
// Once set, "Send to Blender" is back, with a small "Edit · Open" row below it.
//
// The page's own Send button and its click handlers are left untouched,
// only hidden/shown: #exportFolderSlot sits right before it and holds the
// select button, the Edit/Open row is inserted right after it.
//
// Kept out of SETTINGS_STATE on purpose -- the history panel replays that on
// restore, which shouldn't switch the export folder. The current folder
// comes from GET /export_folder_status instead; POST /pick_export_folder
// runs the (blocking) OS dialog on the server's own thread, so this polls
// until it reports 'done' or 'cancelled'. Same-origin relative URLs, so it
// doesn't need the page's own PORT variable (a module-scoped const on some
// pages).
(function() {
    var slot = document.getElementById('exportFolderSlot');
    var sendBtn = slot && slot.nextElementSibling;
    if (!sendBtn || !sendBtn.classList.contains('btn-send')) return;

    // path: the folder exports actually use -- the scene's own Export Path
    // if set (sceneOverride), else the Default Export Folder preference,
    // which is the only thing a pick here changes.
    var state = { path: '', valid: false, picking: false, sceneOverride: false };

    var pickBtn = document.createElement('button');
    pickBtn.type = 'button';
    pickBtn.className = 'btn-send export-folder-pick';
    slot.appendChild(pickBtn);

    // A centered caption under the Send button: "Export to \STL\ · Edit · Open".
    var row = document.createElement('div');
    row.className = 'export-folder-row';
    var labelEl = document.createElement('span');
    labelEl.textContent = 'Export to';
    var nameEl = document.createElement('span');
    nameEl.className = 'export-folder-name';
    var editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'export-folder-link';
    var openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'export-folder-link';
    openBtn.textContent = 'Open';
    openBtn.title = 'Open the export folder';
    function dot() {
        var d = document.createElement('span');
        d.className = 'export-folder-dot';
        d.setAttribute('aria-hidden', 'true');
        d.textContent = '·';
        return d;
    }
    [labelEl, nameEl, dot(), editBtn, dot(), openBtn].forEach(function(el) { row.appendChild(el); });
    sendBtn.insertAdjacentElement('afterend', row);

    // Last folder wrapped in the path's own separator ("\STL\" on Windows,
    // "/STL/" elsewhere) so it reads as a folder, not a file name.
    function folderName(path) {
        var parts = path.split(/[\\/]+/).filter(Boolean);
        if (!parts.length) return path;
        var sep = path.indexOf('\\') >= 0 ? '\\' : '/';
        return sep + parts[parts.length - 1] + sep;
    }

    function render() {
        slot.hidden = state.valid;
        sendBtn.hidden = !state.valid;
        row.hidden = !state.valid;

        // Stays clickable while the dialog is open: a second click brings
        // the dialog back to the front if it went behind another window.
        pickBtn.textContent = state.picking ? 'Choose a folder…' : 'Select Export Folder';
        pickBtn.title = state.picking
            ? 'Pick a folder in the dialog that opened — click again to bring it to the front'
            : state.path
                ? 'The export folder no longer exists: ' + state.path + ' — choose a new Default Export Folder'
                : 'Choose the Default Export Folder (add-on preferences) the generated files are exported to';

        nameEl.textContent = folderName(state.path);
        nameEl.title = state.sceneOverride
            ? 'Exports go to ' + state.path + ' — this file\'s own Export Path (sidebar), which takes priority over the Default Export Folder'
            : 'Exports go to ' + state.path + ' (Default Export Folder)';
        editBtn.textContent = state.picking ? 'Choosing…' : 'Edit';
        editBtn.title = state.picking
            ? 'Pick a folder in the dialog that opened — click again to bring it to the front'
            : state.sceneOverride
                ? 'Change the Default Export Folder — this file still exports to its own Export Path (sidebar) while that is set'
                : 'Change the Default Export Folder (add-on preferences)';
    }

    function apply(s) {
        state.path = s.path || '';
        state.valid = !!s.valid;
        state.picking = s.status === 'picking';
        state.sceneOverride = !!s.sceneOverride;
        render();
        if (state.valid) offerTutorials();
    }

    // Once a folder is in effect, offer the page's tutorials (tutorial.js's
    // tp3dOfferTutorials -- only on pages with tutorials, and it only ever
    // shows once per generator). Waits for the folder guide above to close
    // first so the two popups never overlap.
    var offerQueued = false;
    function offerTutorials() {
        if (offerQueued || typeof window.tp3dOfferTutorials !== 'function') return;
        offerQueued = true;
        var tries = 0;
        (function whenFree() {
            if (typeof tp3dTutorial !== 'undefined' && tp3dTutorial.isActive() && tries++ < 100) {
                setTimeout(whenFree, 300);
                return;
            }
            window.tp3dOfferTutorials();
        })();
    }

    // Shown once per page open when no folder is set, in the tutorials' own
    // style (tutorial.js): spotlights "Select Export Folder" and finishes by
    // itself as soon as a folder is in effect. ✕/Esc dismiss it.
    var guideShown = false;
    function showGuide() {
        if (guideShown || typeof tp3dTutorial === 'undefined' || tp3dTutorial.isActive()) return;
        guideShown = true;
        tp3dTutorial.start([{
            target: pickBtn,
            placement: 'right',
            arrow: true,
            title: 'Set an export folder',
            text: 'Before generating, you have to set a folder where the <b>exported files will be saved</b>.' +
                  '<br><br>Click <b>Select Export Folder</b> and pick a folder in the window that opens. ' +
                  'You can change it later with <b>Edit</b> under ' +
                  '<b>Send to Blender</b>.',
            waitFor: tp3dTutorial.waitUntil(function() { return state.valid; }),
            waitText: 'Waiting for a folder…'
        }]);
    }

    var firstPoll = true;
    function poll() {
        fetch('/export_folder_status', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(function(s) {
                apply(s);
                if (firstPoll) {
                    firstPoll = false;
                    if (!state.valid && !state.picking) showGuide();
                }
                if (state.picking) setTimeout(poll, 400);
            })
            .catch(function() { setTimeout(poll, 1000); });
    }

    function pick() {
        state.picking = true;
        render();
        fetch('/pick_export_folder', { method: 'POST', body: '{}' })
            .then(function() { setTimeout(poll, 400); })
            .catch(function() { state.picking = false; render(); });
    }

    pickBtn.addEventListener('click', pick);
    editBtn.addEventListener('click', pick);
    openBtn.addEventListener('click', function() {
        fetch('/open_export_folder', { method: 'POST', body: '{}' }).catch(function() {});
    });

    // Leave the page's own Send button as-is until the first status reply,
    // instead of flashing "Select Export Folder" for an already-set folder.
    slot.hidden = true;
    row.hidden = true;
    poll();
})();
