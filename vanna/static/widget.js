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

    '#vanna-fab{',
      'position:fixed!important;bottom:24px!important;right:24px!important;',
      'width:52px!important;height:52px!important;border-radius:50%!important;',
      'background:linear-gradient(135deg,#7262ff,#4f46e5)!important;',
      'color:#fff!important;border:none!important;cursor:pointer!important;',
      'z-index:2147483646!important;',
      'box-shadow:0 4px 20px rgba(114,98,255,.55)!important;',
      'display:flex!important;align-items:center!important;justify-content:center!important;',
      'transition:transform .2s,box-shadow .2s,right ' + EASE + '!important;',
      'font-size:22px!important;line-height:1!important;',
    '}',
    '#vanna-fab:hover{transform:scale(1.08)!important;box-shadow:0 6px 28px rgba(114,98,255,.7)!important;}',

    '#vanna-panel{',
      'position:fixed!important;',
      'top:0!important;right:0!important;bottom:0!important;',
      'width:' + PANEL_WIDTH + '!important;',
      'height:100vh!important;',
      'background:#fff!important;',
      'box-shadow:-4px 0 32px rgba(0,0,0,.18)!important;',
      'z-index:2147483645!important;',
      'overflow:hidden!important;',
      'transform:translateX(100%)!important;',
      'transition:transform ' + EASE + '!important;',
      'display:block!important;',
      'margin:0!important;padding:0!important;',
      'border:none!important;border-radius:0!important;',
    '}',
    '#vanna-panel.open{transform:translateX(0)!important;}',

    '#vanna-panel-header{',
      'position:absolute!important;top:0!important;left:0!important;right:0!important;',
      'height:52px!important;box-sizing:border-box!important;',
      'background:#7262ff!important;color:#fff!important;',
      'padding:14px 16px!important;',
      'display:flex!important;align-items:center!important;justify-content:space-between!important;',
      'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif!important;',
      'font-size:14px!important;font-weight:600!important;',
    '}',
    '#vanna-panel-header button{',
      'background:none!important;border:none!important;color:#fff!important;',
      'font-size:18px!important;cursor:pointer!important;line-height:1!important;',
      'opacity:.8!important;padding:2px 6px!important;border-radius:4px!important;',
    '}',
    '#vanna-panel-header button:hover{opacity:1!important;background:rgba(255,255,255,.15)!important;}',

    '#vanna-panel iframe{',
      'position:absolute!important;',
      'top:52px!important;left:0!important;right:0!important;bottom:0!important;',
      'height:calc(100vh - 52px)!important;',
      'width:100%!important;border:none!important;',
      'display:block!important;margin:0!important;padding:0!important;',
    '}'
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

  /* ── Fixed-element adjustment ───────────────────────────────────── */
  var fixedEls = [];

  function collectFixedEls() {
    fixedEls = [];
    var all = document.querySelectorAll('body *');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.id === 'vanna-panel' || el.id === 'vanna-fab') continue;
      if (window.getComputedStyle(el).position === 'fixed') {
        fixedEls.push(el);
      }
    }
  }

  function shiftFixed(shift) {
    for (var i = 0; i < fixedEls.length; i++) {
      fixedEls[i].style.transition = 'right ' + EASE + ', width ' + EASE;
      if (shift) {
        var r = parseInt(window.getComputedStyle(fixedEls[i]).right) || 0;
        fixedEls[i].style.right = (r + 420) + 'px';
      } else {
        fixedEls[i].style.right = '';
      }
    }
  }

  /* ── Main content area adjustment ───────────────────────────────── */
  // Lightdash dashboard grid uses react-grid-layout with absolutely-positioned
  // tiles at fixed pixel widths. CSS parent resizing alone won't move them —
  // we must constrain #page-root then fire window.resize so react-grid-layout
  // recomputes tile widths from the new container offsetWidth.
  function shiftMain(shift) {
    var pageRoot = document.getElementById('page-root');
    if (pageRoot) {
      pageRoot.style.transition = 'max-width ' + EASE;
      pageRoot.style.maxWidth = shift ? 'calc(100vw - ' + PANEL_WIDTH + ')' : '';
    }
    // Give the CSS transition a head-start, then trigger grid reflow
    setTimeout(function () {
      window.dispatchEvent(new Event('resize'));
    }, 50);
  }

  /* ── Toggle logic ───────────────────────────────────────────────── */
  var open = false;

  function openPanel() {
    open = true;
    collectFixedEls();
    panel.classList.add('open');
    shiftFixed(true);
    shiftMain(true);
    fab.style.setProperty('display', 'none', 'important');
  }

  function closePanel() {
    open = false;
    panel.classList.remove('open');
    shiftFixed(false);
    shiftMain(false);
    fab.style.display = '';
  }

  fab.addEventListener('click', openPanel);
  document.getElementById('vanna-panel-close').addEventListener('click', closePanel);
}());
