/* Voice workspace only. Move the existing UI, never clone project controls or media. */
(() => {
  const preferenceKey = 'nambeo.studio.voice.quad.v1';
  const make = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  };
  const move = (node, parent, before = null) => {
    // Atomic reparenting preserves playback/focus in supporting browsers.
    if (parent.moveBefore && node.isConnected && parent.isConnected) parent.moveBefore(node, before);
    else parent.insertBefore(node, before);
  };
  const visible = node => node?.isConnected && node.getClientRects().length && !node.closest('.hidden');
  class VoiceQuad {
    constructor({root, layout, controls, groups, resize, renderActions}) {
      Object.assign(this, {root, layout, controls, resize, renderActions});
      this.controlsAnchor = document.createComment('studio controls original slot');
      controls.before(this.controlsAnchor);
      this.active = false;
      this.step = '';
      this.enabled = true;
      this.frames = new Set();
      try { this.enabled = localStorage.getItem(preferenceKey) !== 'legacy'; } catch (_) {}
      this.grid = make('div', 'voiceQuad hidden');
      layout.append(this.grid);
      const titles = ['① Video / xem thử', '② Bản thoại & lời đọc', '③ Timeline & tác vụ', '④ Cấu hình giọng'];
      this.cards = groups.map((nodes, index) => {
        const card = make('section', 'quadCard quadCard' + (index + 1));
        card.dataset.quadPanel = String(index + 1);
        const header = make('div', 'quadHeader'), title = make('h3', '', titles[index]);
        title.id = 'studio-quad-title-' + index;
        card.setAttribute('aria-labelledby', title.id);
        const expand = make('button', 'quadExpand', '⛶ Mở rộng');
        expand.type = 'button';
        expand.setAttribute('aria-label', 'Mở rộng ' + titles[index].slice(2));
        expand.setAttribute('aria-expanded', 'false');
        expand.onpointerdown = () => { this.pendingFocus = this.captureFocus(); };
        expand.onclick = () => this.expand(index);
        const body = make('div', 'quadBody');
        header.append(title, expand);card.append(header, body);this.grid.append(card);
        const slots = nodes.map(node => {
          const anchor = document.createComment('voice workspace original slot');
          node.before(anchor);
          return {node, anchor};
        });
        body.addEventListener('focusout', event => {
          if (event.target.matches('input,textarea,select')) this.lastFocus = this.captureFocus(event.target);
        });
        return {card, body, expand, title: titles[index], slots};
      });
      this.toggle = make('div', 'quadModeSwitch');
      this.toggle.setAttribute('role', 'group');
      this.toggle.setAttribute('aria-label', 'Bố cục Lồng tiếng');
      this.toggle.append(make('span', '', 'Bố cục:'));
      this.modeButtons = ['4 ô', 'Bố cục cũ'].map((text, index) => {
        const b = make('button', '', text);b.type = 'button';
        b.onclick = () => {
          this.enabled = index === 0;
          try { localStorage.setItem(preferenceKey, this.enabled ? 'quad' : 'legacy'); } catch (_) {}
          this.sync(this.step);this.renderActions();
        };
        this.toggle.append(b);return b;
      });
      this.observer = new ResizeObserver(() => this.resize());
      this.observer.observe(this.grid);
    }
    attachToggle(target) {
      if (this.step !== 'voice') return;
      this.modeButtons.forEach((b, i) => b.setAttribute('aria-pressed', String(this.enabled === (i === 0))));
      target.append(this.toggle);
    }
    sync(step) {
      this.step = step;
      const active = step === 'voice' && this.enabled;
      if (active === this.active) return;
      this.collapse();
      const view = this.capture(this.grid.contains(document.activeElement) ? document.activeElement : null);
      if (active) {
        this.root.classList.add('quadVoice');
        this.grid.classList.remove('hidden');
        this.cards.forEach(c => c.slots.forEach(({node}) => move(node, c.body)));
      } else {
        this.cards.forEach(c => c.slots.forEach(({node, anchor}) => move(node, anchor.parentNode, anchor.nextSibling)));
        this.grid.classList.add('hidden');
        this.root.classList.remove('quadVoice');
      }
      this.active = active;
      this.restore(view);
    }
    captureFocus(node = document.activeElement) {
      if (!this.cards.some(c => c.body.contains(node))) return null;
      return {node, start: node.selectionStart, end: node.selectionEnd, direction: node.selectionDirection};
    }
    capture(focus) {
      return {
        focus: focus ? this.captureFocus(focus) : this.captureFocus(),
        scroll: [this.root, ...this.root.querySelectorAll('.quadBody,.cueViewport,.properties')].map(node => [node, node.scrollTop, node.scrollLeft]),
        media: [...this.root.querySelectorAll('video')].map(node => ({node, paused: node.paused, rate: node.playbackRate, time: node.currentTime})),
      };
    }
    later(callback) {
      const id = requestAnimationFrame(() => {this.frames.delete(id);callback();});
      this.frames.add(id);
    }
    restore(view, fallback) {
      this.frames.forEach(id => cancelAnimationFrame(id));this.frames.clear();
      this.resize();
      const restoreFocus = () => {
        view.scroll.forEach(([node, top, left]) => {node.scrollTop = top;node.scrollLeft = left;});
        const focus = view.focus;
        if (visible(focus?.node)) {
          focus.node.focus({preventScroll: true});
          if (typeof focus.start === 'number') focus.node.setSelectionRange(focus.start, focus.end, focus.direction);
        } else if (visible(fallback)) fallback.focus({preventScroll: true});
      };
      restoreFocus();
      view.media.forEach(({node, paused, rate, time}) => {
        if (node.playbackRate !== rate) node.playbackRate = rate;
        if (node.paused && Math.abs(node.currentTime - time) > .25) node.currentTime = time;
        if (!paused && node.paused && node.isConnected) node.play().catch(() => {});
      });
      // Run after the application dialog manager and resize/virtual-list observers.
      this.later(restoreFocus);
    }
    expand(index) {
      if (!this.active) return;
      this.collapse();
      const card = this.cards[index], view = this.capture();
      const recent = this.pendingFocus || this.lastFocus;
      if (recent && card.body.contains(recent.node)) view.focus = recent;
      if (!card.body.contains(view.focus?.node)) view.focus = null;
      this.pendingFocus = null;
      const overlay = make('div', 'popup quadDialog');
      overlay.setAttribute('role', 'dialog');overlay.setAttribute('aria-modal', 'true');
      const section = make('section'), header = make('div', 'quadDialogHeader');
      const title = make('h3', '', card.title);title.id = 'studio-quad-expanded-title';
      overlay.setAttribute('aria-labelledby', title.id);
      const close = make('button', 'quadCollapse', 'Thu về');close.type = 'button';
      close.title = 'Đóng chế độ mở rộng';
      close.onclick = () => this.collapse();
      header.append(title, close);section.append(header);overlay.append(section);
      overlay.dataset.quadPanel = String(index + 1);
      const placeholder = make('div', 'quadPlaceholder', 'Đang mở rộng trong cửa sổ riêng');
      card.card.append(placeholder);
      // Keep the page height stable, particularly when its mobile header wraps.
      const controlsPlaceholder = make('div', 'studioControlsPlaceholder');
      const rect = this.controls.getBoundingClientRect(), style = getComputedStyle(this.controls);
      controlsPlaceholder.style.cssText = `width:${rect.width}px;height:${rect.height}px;margin:${style.margin};flex:none;max-width:100%`;
      controlsPlaceholder.setAttribute('aria-hidden', 'true');this.controls.before(controlsPlaceholder);
      this.root.append(overlay);move(card.body, section);move(this.controls, header);
      card.expand.setAttribute('aria-expanded', 'true');
      this.expanded = {card, overlay, placeholder, controlsPlaceholder};
      overlay.onkeydown = e => {
        if (e.defaultPrevented) return;
        if (e.key === 'Escape') {e.preventDefault();e.stopPropagation();this.collapse();}
        if (e.key === 'Tab') {
          const items = [...overlay.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex="0"]')].filter(n => !n.disabled && n.tabIndex >= 0 && visible(n));
          const first = items[0], last = items.at(-1);
          if (e.shiftKey && (document.activeElement === first || !overlay.contains(document.activeElement))) {e.preventDefault();last?.focus();}
          else if (!e.shiftKey && (document.activeElement === last || !overlay.contains(document.activeElement))) {e.preventDefault();first?.focus();}
        }
      };
      this.restore(view, close);
    }
    collapse() {
      if (!this.expanded) return;
      const {card, overlay, placeholder, controlsPlaceholder} = this.expanded, view = this.capture();
      move(card.body, card.card);
      move(this.controls, this.controlsAnchor.parentNode, this.controlsAnchor.nextSibling);
      overlay.remove();placeholder.remove();controlsPlaceholder.remove();
      card.expand.setAttribute('aria-expanded', 'false');this.expanded = null;
      this.restore(view, card.expand);
    }
    destroy() {
      this.enabled = false;this.sync('');this.observer.disconnect();
      this.frames.forEach(id => cancelAnimationFrame(id));this.frames.clear();
      this.cards.forEach(c => c.slots.forEach(({anchor}) => anchor.remove()));
      this.controlsAnchor.remove();
      this.grid.remove();this.toggle.remove();
    }
  }
  window.NambeoVoiceQuad = VoiceQuad;
})();
