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

/* ============================================================
 * D-3d-1 + D-3d-6: 共通 AJAX ヘルパー（CSRF + fetch ラッパー + toast 通知）
 *
 * window.appAjax 名前空間で公開：
 *   - window.appAjax.postJson(url, payload) → Promise<{ok, status, data}>
 *   - window.appAjax.showToastMessage(message) → void
 *
 * 既存 showToast() は引数なしで textContent を書き換えない仕様のため、
 * メッセージ付き表示の独自関数を本ブロック内で実装する（既存ブロックは触らない）。
 * ============================================================ */
(function () {
  function getCookie(name) {
    if (!document.cookie) return null;
    const cookies = document.cookie.split(';');
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.substring(0, name.length + 1) === name + '=') {
        return decodeURIComponent(cookie.substring(name.length + 1));
      }
    }
    return null;
  }

  let toastTimer = null;
  function showToastMessage(message) {
    const toast = document.querySelector('.app-toast');
    if (!toast) return;
    toast.textContent = message || '';
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toast.hidden = true;
    }, 2400);
  }

  function errorMessageForStatus(status) {
    if (status === 400) return '入力内容に問題があります';
    if (status === 403) return 'この項目は編集できません';
    if (status === 404) return 'データが見つかりません';
    if (status >= 500 && status < 600) return 'サーバーエラーが発生しました';
    if (status === 0) return '通信エラーが発生しました';
    return 'エラーが発生しました';
  }

  function postJson(url, payload) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || ''
      },
      body: JSON.stringify(payload)
    }).then(function (response) {
      const status = response.status;
      return response.json().then(
        function (data) {
          const success = response.ok && data && data.success === true;
          if (!success) {
            showToastMessage(errorMessageForStatus(status));
            return { ok: false, status: status, data: data };
          }
          return { ok: true, status: status, data: data };
        },
        function () {
          /* JSON パース失敗 */
          showToastMessage(errorMessageForStatus(0));
          return { ok: false, status: 0, data: null };
        }
      );
    }, function () {
      /* ネットワークエラー */
      showToastMessage(errorMessageForStatus(0));
      return { ok: false, status: 0, data: null };
    });
  }

  window.appAjax = {
    postJson: postJson,
    showToastMessage: showToastMessage
  };
})();

/* ============================================================
 * D-3d-2: Contact 詳細画面の個別フィールド「確認 OK」機能
 *
 * 対象 DOM（D-3b / D-3d-準備で _contact_field.html に仕込み済み）：
 *   - .js-contact-field-row[data-field-name][data-contact-id][data-confidence-state]
 *   - .js-contact-field-action（ラジオ confirm / edit）
 *   - .js-contact-field-confirm-btn（確定ボタン、初期 hidden）
 *   - .js-contact-field-edit-form（修正フォーム、初期 hidden）
 *   - .js-contact-field-cancel-btn（キャンセル）
 *   - .js-contact-field-badge-slot（confidence バッジ slot）
 *   - .js-unconfirmed-count（未確認件数の <strong>、画面に 1 個）
 *
 * 値修正（修正ボタンの AJAX）は D-3d-3 で実装、本ブロックでは UI 開閉まで。
 * ============================================================ */
(function () {
  const CONFIRMED_BADGE_HTML =
    '<span class="app-status-badge app-status-badge--success">確認済み</span>';

  function findRow(target) {
    return target.closest('.js-contact-field-row');
  }

  function setHidden(el, hidden) {
    if (!el) return;
    el.hidden = !!hidden;
  }

  function resetRow(row) {
    /* ラジオの選択を解除 + 確定ボタン / 修正フォームを hidden に戻す */
    row.querySelectorAll('.js-contact-field-action').forEach(function (input) {
      input.checked = false;
    });
    setHidden(row.querySelector('.js-contact-field-confirm-btn'), true);
    setHidden(row.querySelector('.js-contact-field-edit-form'), true);
  }

  function applyConfirmedState(row, unconfirmedCount) {
    /* AJAX 成功時の DOM 更新。
       修正 UI 全体（.app-contact-field-actions）を DOM から削除し、サーバ側で
       リロード時の状態（confirmed フィールドにはテンプレートが
       app-contact-field-actions を出力しない）と一致させる。 */
    const actionsDiv = row.querySelector('.app-contact-field-actions');
    if (actionsDiv) actionsDiv.remove();

    row.dataset.confidenceState = 'confirmed';
    const slot = row.querySelector('.js-contact-field-badge-slot');
    if (slot) slot.innerHTML = CONFIRMED_BADGE_HTML;

    if (typeof unconfirmedCount === 'number') {
      const counter = document.querySelector('.js-unconfirmed-count');
      if (counter) counter.textContent = String(unconfirmedCount);
    }
  }

  function onActionChange(input, row) {
    const value = input.value;
    const confirmBtn = row.querySelector('.js-contact-field-confirm-btn');
    const editForm = row.querySelector('.js-contact-field-edit-form');
    if (value === 'confirm') {
      setHidden(confirmBtn, false);
      setHidden(editForm, true);
    } else if (value === 'edit') {
      setHidden(confirmBtn, true);
      setHidden(editForm, false);
    }
  }

  function onConfirmClick(btn, row) {
    const contactId = row.dataset.contactId;
    const fieldName = row.dataset.fieldName;
    if (!contactId || !fieldName) return;

    btn.disabled = true;
    const url = '/contacts/' + contactId + '/ajax-confirm-fields/';
    window.appAjax
      .postJson(url, { field_names: [fieldName] })
      .then(function (result) {
        if (result.ok) {
          const count = result.data && result.data.unconfirmed_count;
          applyConfirmedState(row, count);
        }
        /* 失敗時はヘルパー側で toast 表示済み、UI はそのまま操作可能 */
      })
      .finally(function () {
        btn.disabled = false;
      });
  }

  function onCancelClick(row) {
    resetRow(row);
  }

  document.addEventListener('change', function (event) {
    const input = event.target.closest('.js-contact-field-action');
    if (!input) return;
    const row = findRow(input);
    if (!row) return;
    onActionChange(input, row);
  });

  document.addEventListener('click', function (event) {
    const confirmBtn = event.target.closest('.js-contact-field-confirm-btn');
    if (confirmBtn) {
      const row = findRow(confirmBtn);
      if (row) onConfirmClick(confirmBtn, row);
      return;
    }

    const cancelBtn = event.target.closest('.js-contact-field-cancel-btn');
    if (cancelBtn) {
      const row = findRow(cancelBtn);
      if (row) onCancelClick(row);
    }
  });
})();
