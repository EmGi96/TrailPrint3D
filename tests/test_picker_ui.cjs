const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const {test} = require('node:test');
const {pages, renderPage} = require('./picker_ui_fixture.cjs');
const root = path.resolve(__dirname, '..');

test('every shared JavaScript asset parses', () => {
    const dir = path.join(root, 'TrailPrint3D', 'assets');
    for (const file of fs.readdirSync(dir).filter(file => file.endsWith('.js'))) {
        assert.doesNotThrow(() => new vm.Script(fs.readFileSync(path.join(dir, file), 'utf8'), {filename: file}));
    }
});

for (let index = 0; index < pages.length; index++) {
    test(pages[index] + ' has valid scripts and shared UI contracts', {skip: !fs.existsSync(path.join(root, 'TrailPrint3D', pages[index]))}, () => {
        const html = renderPage(index, 12345);
        const executable = html.replace(/\/\*[\s\S]*?\*\/|^\s*\/\/.*$/gm, '');
        assert.equal(/__[A-Z_]+__/.test(executable), false, 'All executable template tokens must be replaced');
        assert.match(html, /<html lang="en">/);
        assert.match(html, /name="viewport"/);
        assert.match(html, /class="skip-link"/);
        assert.match(html, /id="status" role="status"/);
        assert.match(html, /function tp3dAlert/);
        const markup = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
        assert.equal(/<[a-z]+>\s*class=/.test(markup), false, 'Attributes must stay inside tags');
        for (const match of markup.matchAll(/\bstyle="([^"]*)"/g)) {
            assert.match(match[1], /^display\s*:\s*none\s*;?$/, 'Only runtime visibility should remain inline');
        }
        const ids = [...markup.matchAll(/\bid="([^"]*)"/g)].map(match => match[1]);
        assert.equal(new Set(ids).size, ids.length, 'DOM IDs must stay unique');
        for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
            if (/type="importmap"/.test(match[1])) { assert.doesNotThrow(() => JSON.parse(match[2])); continue; }
            if (/type="module"/.test(match[1])) {
                const result = spawnSync(process.execPath, ['--check', '--input-type=module'], {input: match[2], encoding: 'utf8'});
                assert.equal(result.status, 0, result.stderr);
            } else assert.doesNotThrow(() => new vm.Script(match[2], {filename: pages[index]}));
        }
    });
}

test('theme has readable text and action contrast', () => {
    const css = fs.readFileSync(path.join(root, 'TrailPrint3D', 'assets', 'picker_common.css'), 'utf8');
    const tokens = Object.fromEntries([...css.matchAll(/--([\w-]+):\s*(#[\da-f]{6});/gi)].map(match => [match[1], match[2]]));
    function luminance(hex) {
        const rgb = hex.slice(1).match(/../g).map(value => parseInt(value, 16) / 255)
            .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
    }
    function contrast(a, b) {
        const values = [luminance(tokens[a]), luminance(tokens[b])].sort((x, y) => y - x);
        return (values[0] + 0.05) / (values[1] + 0.05);
    }
    for (const text of ['text-primary', 'text-secondary', 'text-muted']) {
        for (const surface of ['surface-base', 'surface-panel', 'surface-field']) {
            assert.ok(contrast(text, surface) >= 4.5, text + ' on ' + surface);
        }
    }
    assert.ok(contrast('text-primary', 'accent') >= 4.5, 'Primary action text');
    assert.ok(contrast('text-primary', 'send-action') >= 4.5, 'Send to Blender text');
    assert.ok(contrast('text-primary', 'send-action-hover') >= 4.5, 'Send to Blender hover text');
});
