// Load only through picker_ui_fixture.cjs, then call tp3dRunBrowserChecks().
async function tp3dRunBrowserChecks() {
    var results = [];
    var tick = function() { return new Promise(function(resolve) { requestAnimationFrame(function() { requestAnimationFrame(resolve); }); }); };
    function check(name, condition) {
        results.push({ name: name, passed: !!condition });
    }
    function inside(element) {
        var rect = element.getBoundingClientRect();
        return rect.left >= -1 && rect.top >= -1 && rect.right <= innerWidth + 1 && rect.bottom <= innerHeight + 1;
    }
    function key(element, value, shift) {
        element.dispatchEvent(new KeyboardEvent('keydown', {key: value, shiftKey: !!shift, bubbles: true}));
    }
    async function waitFor(predicate) {
        for (var attempt = 0; attempt < 100; attempt++) {
            if (predicate()) return true;
            await new Promise(function(resolve) { setTimeout(resolve, 10); });
        }
        return false;
    }
    check('No document horizontal overflow', document.documentElement.scrollWidth <= innerWidth + 1);
    check('Sidebar scrolls without horizontal clipping', document.querySelector('#sidebar').scrollWidth <= document.querySelector('#sidebar').clientWidth + 1);
    check('Workspace stays visible', document.querySelector('[role=main]').getBoundingClientRect().height > 50);
    check('Resolution label is not a generic Value label', !document.querySelector('#resolutionSlider') || document.querySelector('#resolutionSlider').getAttribute('aria-label') !== 'Value');
    var send = document.querySelector('#sidebar .btn-send');
    var wasDisabled = send.disabled;
    send.disabled = false;
    await waitFor(function() { return getComputedStyle(send).backgroundColor === 'rgb(40, 120, 62)'; });
    check('Send to Blender retains its green action color', getComputedStyle(send).backgroundColor === 'rgb(40, 120, 62)');
    send.disabled = wasDisabled;
    var sourceSwitch = document.querySelector('.element-source-switch-compact');
    if (sourceSwitch) {
        var outerRadius = parseFloat(getComputedStyle(sourceSwitch).borderTopLeftRadius);
        var innerRadius = parseFloat(getComputedStyle(sourceSwitch.querySelector('button')).borderTopLeftRadius);
        check('Source switch has concentric corners', Math.abs(outerRadius - innerRadius - 3) < 0.1);
    }
    var zoom = document.querySelector('.leaflet-control-zoom');
    if (zoom) {
        var draw = document.querySelector('#drawActions');
        var zoomButton = zoom.querySelector('a');
        var drawButton = draw.querySelector('button');
        check('Zoom controls match draw control surfaces', ['backgroundColor', 'borderColor', 'borderWidth', 'borderRadius', 'width', 'height']
            .every(function(property) { return getComputedStyle(zoomButton)[property] === getComputedStyle(drawButton)[property]; }));
        check('Map control groups do not overlap', zoom.getBoundingClientRect().bottom < draw.getBoundingClientRect().top
            && draw.getBoundingClientRect().bottom < document.querySelector('#drawModeToggle').getBoundingClientRect().top);
    }

    var trigger = document.querySelector('.settings-gear-btn');
    var modal = document.querySelector('#settingsModal') || document.querySelector('#textModal');
    var opener = trigger || document.querySelector('#confirmBtn');
    opener.focus();
    if (trigger) trigger.click();
    else modal.classList.add('open');
    await tick();
    check('Dialog receives focus', modal.contains(document.activeElement));
    check('Dialog has an accessible title', modal.hasAttribute('aria-labelledby'));
    check('Dialog fits viewport', inside(modal.querySelector('.tp3d-modal-box')));
    check('Configuration behind dialog is inert', document.querySelector('#sidebar').inert);

    var last = Array.from(modal.querySelectorAll('button,input,select,[tabindex]')).filter(function(element) {
        return !element.disabled && element.tabIndex >= 0 && element.getClientRects().length;
    }).pop();
    last.focus();
    key(last, 'Tab');
    check('Tab wraps inside dialog', document.activeElement !== last && modal.contains(document.activeElement));

    if (trigger) {
        var tab = modal.querySelector('[role=tab][aria-selected=true]');
        tab.focus();
        key(tab, 'ArrowRight');
        await tick();
        check('Arrow keys activate settings tabs', document.activeElement !== tab && document.activeElement.getAttribute('aria-selected') === 'true');
        check('Map fields do not overflow their panel', Array.from(modal.querySelectorAll('.settings-modal-tab-panel.active')).every(function(panel) {
            return panel.scrollWidth <= panel.clientWidth + 1;
        }));
        tp3dShowPreview('data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="120" height="80"%3E%3C/svg%3E', 'Fixture preview');
        await tick();
        check('Preview-open dialog fits viewport', inside(modal.querySelector('.tp3d-modal-box')));
        check('Preview layout avoids horizontal overflow', modal.querySelector('.settings-modal-body').scrollWidth <= modal.querySelector('.settings-modal-body').clientWidth + 1);
    }
    tp3dAlert('Fixture notice', 'Test notice');
    await tick();
    check('Nested notice receives focus', document.querySelector('#tp3dNotice').contains(document.activeElement));
    key(document.activeElement, 'Escape');
    await tick();
    check('Escape closes only the top dialog', !document.querySelector('#tp3dNotice').classList.contains('open') && modal.classList.contains('open'));
    key(document.activeElement, 'Escape');
    await tick();
    check('Dialog returns focus to opener', document.activeElement === opener);
    check('Closing restores background interaction', !document.querySelector('#sidebar').inert);

    var location = document.querySelector('#coordSearchToggle');
    if (location) {
        location.click();
        await tick();
        document.querySelector('#latInput').value = '91';
        document.querySelector('#lonInput').value = 'invalid';
        document.querySelector('#coordSearchBtn').click();
        check('Invalid coordinates have explanatory text', document.querySelector('#coordSearchError').textContent.includes('latitude'));
        check('Invalid coordinates are exposed to assistive tech', document.querySelector('#latInput').getAttribute('aria-invalid') === 'true');
        key(document.querySelector('#latInput'), 'Escape');
        check('Location panel supports Escape', !document.querySelector('#coordSearchBody').classList.contains('open'));
    }

    var flyoutTrigger = document.querySelector('.element-flyout-trigger');
    if (flyoutTrigger) {
        check('Extra settings use the element chip without a separate arrow', flyoutTrigger.matches('.element-chip')
            && !flyoutTrigger.textContent.includes('▾') && flyoutTrigger.parentElement.querySelectorAll('button').length === 1);
        flyoutTrigger.focus();
        key(flyoutTrigger, 'ArrowDown');
        await tick();
        check('Flyout opens and focuses a checkbox by keyboard', document.activeElement.matches('.element-flyout input'));
        check('Flyout is bounded by viewport', inside(document.querySelector('.element-flyout')));
        key(document.activeElement, 'Escape');
        check('Flyout Escape returns focus', document.activeElement === flyoutTrigger && !document.querySelector('.element-flyout'));
    }

    var history = document.querySelector('.history-toggle-btn');
    if (history) {
        history.click();
        await waitFor(function() { return document.querySelector('.history-action'); });
        check('History entries are native buttons', document.querySelector('.history-action') instanceof HTMLButtonElement);
        var group = document.querySelector('.history-action');
        group.click();
        check('History group reports expanded state', group.getAttribute('aria-expanded') === 'true');
        key(document.querySelector('#historyPanel'), 'Escape');
        check('History Escape returns focus', document.activeElement === history);
        var originalFetch = window.fetch;
        window.fetch = function(url, options) {
            if (String(url).endsWith('/get_history')) return Promise.resolve(new Response('Unavailable', {status: 503}));
            return originalFetch(url, options);
        };
        try {
            history.click();
            await waitFor(function() { return document.querySelector('.history-message button'); });
            check('Failed history load is not an empty state', document.querySelector('.history-message').textContent.includes('Could not load'));
            check('Failed history load offers retry', !!document.querySelector('.history-message button'));
        } finally {
            window.fetch = originalFetch;
            key(document.querySelector('#historyPanel'), 'Escape');
        }
    }
    return results;
}
