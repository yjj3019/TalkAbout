// Accessibility contracts for the light theme and keyboard/reduced-motion paths.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const css = readFileSync(new URL('../talkabout/web/style.css', import.meta.url), 'utf8');
const app = readFileSync(new URL('../talkabout/web/app.mjs', import.meta.url), 'utf8');

function lightVars() {
  const start = css.indexOf('@media (prefers-color-scheme: light)');
  assert.ok(start > 0);
  const block = css.slice(start, start + 2600);
  const read = name => {
    const match = new RegExp('--' + name + ':\\s*(#[0-9a-fA-F]{6})').exec(block);
    assert.ok(match, name);
    return match[1];
  };
  return {faint: read('faint'), accent: read('accent'), panel: read('panel'), panel3: read('panel-3')};
}

function luminance(hex) {
  const value = hex.replace('#', '');
  const channels = [0, 2, 4].map(index => parseInt(value.slice(index, index + 2), 16) / 255);
  const linear = channels.map(channel => channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(foreground, background) {
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

test('light theme body text and accents meet WCAG AA contrast', () => {
  const vars = lightVars();
  const faint = contrast(vars.faint, vars.panel3);
  const accent = contrast(vars.accent, vars.panel);
  assert.ok(faint >= 4.5, 'faint on panel-3: ' + faint.toFixed(2));
  assert.ok(accent >= 4.5, 'accent on panel: ' + accent.toFixed(2));
  const button = /color: #f4fffb;\s*background: linear-gradient\(180deg, #([0-9a-f]{6})/.exec(css);
  assert.ok(button, 'light button rule');
  const buttonContrast = contrast('#f4fffb', '#' + button[1]);
  assert.ok(buttonContrast >= 4.5, 'button text on gradient top: ' + buttonContrast.toFixed(2));
});

test('form controls keep a visible keyboard focus ring', () => {
  const focus = css.indexOf('input:focus, textarea:focus, select:focus');
  const ring = css.indexOf('input:focus-visible, textarea:focus-visible, select:focus-visible');
  assert.ok(focus > 0 && ring > focus);
  assert.match(css.slice(ring, ring + 200), /outline: 2px solid var\(--accent\)/);
});

test('the stage loop honours prefers-reduced-motion instead of animating anyway', () => {
  assert.match(app, /function prefersReducedMotion\(\)/);
  assert.match(app, /matchMedia\('\(prefers-reduced-motion: reduce\)'\)/);
  const start = app.indexOf('function startStageAnimation(room)');
  const reduced = app.indexOf('if (prefersReducedMotion())', start);
  const raf = app.indexOf('requestAnimationFrame(frame)', start);
  assert.ok(reduced > start && raf > reduced, 'reduced-motion guard must run before the first frame');
  const block = app.slice(reduced, app.indexOf('let last = performance.now()', reduced));
  assert.match(block, /stageAnimation = \{ raf: 0 \};/);
});

test('narrow layouts wrap long task metadata and separate the stage overlays', () => {
  const narrow = css.lastIndexOf('@media (max-width: 800px)');
  assert.ok(narrow > 0);
  const block = css.slice(narrow, narrow + 2200);
  assert.match(block, /\.task-list \.task-meta \{ white-space: normal; overflow-wrap: anywhere; \}/);
  assert.match(block, /\.stage-camera \{ top: auto; bottom: 10px; right: 10px; \}/);
});
