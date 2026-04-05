/* Vanna chat widget — injected into Lightdash by nginx sub_filter.
 * Self-contained IIFE: no global pollution, no external dependencies. */
(function () {
  'use strict';
  if (document.getElementById('vanna-fab')) return; // prevent double-injection

  /* ── Styles ─────────────────────────────────────────────────────── */
  var PANEL_WIDTH = '420px';
  var EASE = '.28s cubic-bezier(.4,0,.2,1)';

  var style = document.createElement('style');
  style.textContent = [
    'body{transition:margin-right ' + EASE + ';}',

    '#vanna-fab{',
      'position:fixed;bottom:24px;right:24px;',
      'width:52px;height:52px;border-radius:50%;',
      'background:linear-gradient(135deg,#7262ff,#4f46e5);',
      'color:#fff;border:none;cursor:pointer;',
      'z-index:2147483646;',
      'box-shadow:0 4px 20px rgba(114,98,255,.55);',
      'display:flex;align-items:center;justify-content:center;',
      'transition:transform .2s,box-shadow .2s,right ' + EASE + ';',
      'font-size:22px;line-height:1;',
    '}',
    '#vanna-fab:hover{transform:scale(1.08);box-shadow:0 6px 28px rgba(114,98,255,.7);}',

    '#vanna-panel{',
      'position:fixed;top:0;right:0;bottom:0;',
      'width:' + PANEL_WIDTH + ';',
      'background:#fff;',
      'box-shadow:-4px 0 32px rgba(0,0,0,.18);',
      'z-index:2147483645;',
      'display:flex;flex-direction:column;',
      'transform:translateX(100%);',
      'transition:transform ' + EASE + ';',
    '}',
    '#vanna-panel.open{transform:translateX(0);}',

    '#vanna-panel-header{',
      'background:#7262ff;color:#fff;',
      'padding:14px 16px;',
      'display:flex;align-items:center;justify-content:space-between;',
      'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;',
      'font-size:14px;font-weight:600;flex-shrink:0;',
    '}',
    '#vanna-panel-header button{',
      'background:none;border:none;color:#fff;',
      'font-size:18px;cursor:pointer;line-height:1;',
      'opacity:.8;padding:2px 6px;border-radius:4px;',
    '}',
    '#vanna-panel-header button:hover{opacity:1;background:rgba(255,255,255,.15);}',

    '#vanna-panel iframe{flex:1;border:none;width:100%;}'
  ].join('');
  document.head.appendChild(style);

  /* ── FAB button ─────────────────────────────────────────────────── */
  var fab = document.createElement('button');
  fab.id = 'vanna-fab';
  fab.title = 'Ask your data';
  fab.setAttribute('aria-label', 'Ask your data');
  fab.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  document.body.appendChild(fab);

  /* ── Side panel ─────────────────────────────────────────────────── */
  var panel = document.createElement('div');
  panel.id = 'vanna-panel';
  panel.innerHTML = [
    '<div id="vanna-panel-header">',
      '<span>✦ Ask your data</span>',
      '<button id="vanna-panel-close" title="Close" aria-label="Close">&#x2715;</button>',
    '</div>',
    '<iframe src="/vanna/?embedded=1" title="Ask your data" allow="clipboard-write"></iframe>'
  ].join('');
  document.body.appendChild(panel);

  /* ── Toggle logic ───────────────────────────────────────────────── */
  var open = false;

  function openPanel() {
    open = true;
    panel.classList.add('open');
    document.body.style.marginRight = PANEL_WIDTH;
    fab.style.right = 'calc(' + PANEL_WIDTH + ' + 24px)';
  }

  function closePanel() {
    open = false;
    panel.classList.remove('open');
    document.body.style.marginRight = '';
    fab.style.right = '';
  }

  fab.addEventListener('click', openPanel);
  document.getElementById('vanna-panel-close').addEventListener('click', closePanel);
}());
