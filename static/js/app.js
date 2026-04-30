(function () {
  const drawer = document.getElementById('drawer');
  const backdrop = document.querySelector('.app-backdrop');
  const userMenu = document.getElementById('userMenu');
  const modalBackdrop = document.querySelector('.app-modal-backdrop');
  const toast = document.querySelector('.app-toast');
  const loadingOverlay = document.querySelector('.app-loading-overlay');
  let activeModal = null;
  let toastTimer = null;

  function openDrawer() {
    if (!drawer || !backdrop) return;
    drawer.classList.add('is-open');
    backdrop.classList.add('is-open');
  }

  function closeDrawer() {
    if (!drawer || !backdrop) return;
    drawer.classList.remove('is-open');
    backdrop.classList.remove('is-open');
  }

  function toggleUserMenu() {
    if (!userMenu) return;
    userMenu.classList.toggle('is-open');
  }

  function closeUserMenu() {
    if (!userMenu) return;
    userMenu.classList.remove('is-open');
  }

  function toggleCollapsible(event, trigger) {
    const targetId = trigger.getAttribute('data-target');
    const panel = targetId ? document.getElementById(targetId) : trigger.closest('.app-collapsible');
    if (!panel) return;
    const isOpen = panel.classList.toggle('is-open');
    const icon = trigger.querySelector('.app-collapsible__icon');
    trigger.setAttribute('aria-expanded', String(isOpen));
    if (icon) icon.textContent = isOpen ? '▾' : '▸';
  }

  function toggleDrawerSection(event, trigger) {
    const section = trigger.closest('.app-drawer__section');
    if (!section) return;
    const isOpen = section.classList.toggle('is-open');
    const icon = section.querySelector('.app-drawer__section-icon');
    trigger.setAttribute('aria-expanded', String(isOpen));
    if (icon) icon.textContent = isOpen ? '▾' : '▸';
  }

  function openModal(event, trigger) {
    const targetId = trigger.getAttribute('data-target');
    const modal = targetId ? document.getElementById(targetId) : null;
    if (!modal || !modalBackdrop) return;
    activeModal = modal;
    modalBackdrop.hidden = false;
    modal.hidden = false;
    const firstFocus = modal.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (firstFocus) firstFocus.focus();
  }

  function closeModal() {
    if (activeModal) activeModal.hidden = true;
    if (modalBackdrop) modalBackdrop.hidden = true;
    activeModal = null;
  }

  function showToast() {
    if (!toast) return;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.hidden = true;
    }, 2400);
  }

  function toggleLoading() {
    if (!loadingOverlay) return;
    loadingOverlay.hidden = !loadingOverlay.hidden;
  }

  function copyToClipboard(event, trigger) {
    const targetId = trigger.getAttribute('data-target');
    const el = targetId ? document.getElementById(targetId) : null;
    if (!el) return;
    navigator.clipboard.writeText(el.textContent).then(function () {
      const original = trigger.textContent;
      trigger.textContent = 'コピーしました';
      setTimeout(function () { trigger.textContent = original; }, 1500);
    });
  }

  const actions = {
    'toggle-drawer': openDrawer,
    'close-drawer': closeDrawer,
    'toggle-user-menu': toggleUserMenu,
    'toggle-collapsible': toggleCollapsible,
    'toggle-drawer-section': toggleDrawerSection,
    'open-modal': openModal,
    'close-modal': closeModal,
    'show-toast': showToast,
    'toggle-loading': toggleLoading,
    'copy-to-clipboard': copyToClipboard
  };

  document.addEventListener('click', function (event) {
    const trigger = event.target.closest('[data-action]');
    const action = trigger ? trigger.getAttribute('data-action') : null;
    if (action && actions[action]) {
      actions[action](event, trigger);
    }

    if (!event.target.closest('.app-user-menu')) {
      closeUserMenu();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeDrawer();
      closeUserMenu();
      closeModal();
    }
  });
})();

/* PC向けツールチップ。テーブルのoverflowに邪魔されないようbody直下に表示する。 */
(function () {
  const canHover = window.matchMedia && window.matchMedia('(hover: hover)').matches;
  if (!canHover) return;

  let tooltip = null;

  function removeTooltip() {
    if (tooltip) {
      tooltip.remove();
      tooltip = null;
    }
  }

  function showTooltip(target) {
    const text = target.getAttribute('data-tooltip');
    if (!text) return;
    removeTooltip();
    tooltip = document.createElement('div');
    tooltip.className = 'app-floating-tooltip';
    tooltip.textContent = text;
    document.body.appendChild(tooltip);
    const rect = target.getBoundingClientRect();
    const tipRect = tooltip.getBoundingClientRect();
    let left = rect.left;
    let top = rect.top - tipRect.height - 8;
    if (left + tipRect.width > window.innerWidth - 12) left = window.innerWidth - tipRect.width - 12;
    if (left < 12) left = 12;
    if (top < 12) top = rect.bottom + 8;
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
  }

  document.addEventListener('mouseover', function (event) {
    const target = event.target.closest('[data-tooltip]');
    if (target) showTooltip(target);
  });

  document.addEventListener('mouseout', function (event) {
    if (event.target.closest('[data-tooltip]')) removeTooltip();
  });

  /* overflow:scroll要素内も拾いやすいよう、windowではなくdocumentのキャプチャフェーズで監視する。 */
  document.addEventListener('scroll', removeTooltip, true);
  window.addEventListener('resize', removeTooltip);
})();

(function () {
  /* 日付・時刻の整形はあくまで入力補助。実在する日付・時刻かどうかの最終バリデーションはDjango Form側で行う。 */
  function formatDateValue(value) {
    const raw = value.replace(/\D/g, '');
    if (raw.length === 8) return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
    return value;
  }

  function formatTimeValue(value) {
    const raw = value.replace(/\D/g, '');
    if (raw.length === 4) return `${raw.slice(0, 2)}:${raw.slice(2, 4)}`;
    return value;
  }

  function formatDateTimeValue(value) {
    const raw = value.replace(/\D/g, '');
    if (raw.length === 12) {
      return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)} ${raw.slice(8, 10)}:${raw.slice(10, 12)}`;
    }
    return value;
  }

  const formatters = [
    ['.js-date-text', formatDateValue],
    ['.js-time-text', formatTimeValue],
    ['.js-datetime-text', formatDateTimeValue]
  ];

  formatters.forEach(([selector, formatter]) => {
    document.querySelectorAll(selector).forEach((input) => {
      input.addEventListener('focus', () => {
        setTimeout(() => input.select(), 0);
      });
      input.addEventListener('blur', () => {
        input.value = formatter(input.value);
      });
    });
  });
})();

/* flatpickrより後にこのapp.jsを読み込むこと。CDN失敗時は通常のTextInputとして動作する。 */
(function () {
  if (!window.flatpickr) return;

  const locale = window.flatpickr.l10ns && window.flatpickr.l10ns.ja ? window.flatpickr.l10ns.ja : undefined;

  window.flatpickr('.js-flatpickr-date', {
    dateFormat: 'Y-m-d',
    allowInput: true,
    locale
  });

  window.flatpickr('.js-flatpickr-time', {
    enableTime: true,
    noCalendar: true,
    dateFormat: 'H:i',
    time_24hr: true,
    allowInput: true,
    locale
  });

  window.flatpickr('.js-flatpickr-datetime', {
    enableTime: true,
    dateFormat: 'Y-m-d H:i',
    time_24hr: true,
    allowInput: true,
    locale
  });
})();
