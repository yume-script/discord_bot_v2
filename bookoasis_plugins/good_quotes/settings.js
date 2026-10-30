// 좋은 글귀 설정 폼을 코어 설정 저장 필드와 관리 액션에 연결합니다.
(function () {
  const panel = root.querySelector('.gq-quote-settings');
  if (!panel) return;
  const query = selector => panel.querySelector(selector);
  const message = query('[data-quote-management-status]');
  const defaults = {korean_enabled: true, korean_weight: config.local_weight ?? 60, zen_weight: 35, positive_weight: 5, quote_language: 'both', quote_font_size: 32, quote_author_font_size: 15, photo_provider: 'gradient', photo_refresh_hours: 3,
    quote_en_font: config.quote_en_font === undefined ? config.quote_font : config.quote_en_font,
    quote_en_font_size: config.quote_en_font_size === undefined ? config.quote_font_size ?? 32 : 32};
  const fontSelects = [query('[name=quote_font]'), query('[name=quote_en_font]'), query('[name=quote_author_font]')], fontStatus = query('[data-quote-font-status]');
  for (const select of fontSelects) {
    const value = config[select.name] ?? defaults[select.name];
    const saved = typeof value === 'string' ? value : '';
    if (saved) select.append(new Option(`${saved} · 저장된 글꼴`, saved));
  }
  for (const input of panel.querySelectorAll('[name]')) {
    const value = config[input.name] ?? defaults[input.name];
    if (input.type === 'checkbox') input.checked = [true, 1, '1', 'true', 'on'].includes(value);
    else if (value != null) input.value = String(value);
  }
  if (config.photo_provider === 'openverse') query('[name=photo_provider]').value = 'gradient';
  const refreshHours = query('[name=photo_refresh_hours]');
  const normalizeRefreshHours = () => {
    const value = refreshHours.value === '' ? 3 : Number(refreshHours.value);
    refreshHours.value = String(Math.max(1, Math.min(24, Math.round(Number.isFinite(value) ? value : 3))));
  };
  refreshHours.addEventListener('change', normalizeRefreshHours);
  refreshHours.addEventListener('blur', normalizeRefreshHours);
  normalizeRefreshHours();
  const fontSize = query('[name=quote_font_size]'), fontRange = query('[data-quote-font-size]');
  const enSize = query('[name=quote_en_font_size]'), enRange = query('[data-quote-en-font-size]');
  const authorSize = query('[name=quote_author_font_size]'), authorRange = query('[data-quote-author-font-size]');
  function normalizeAuthorSize() {
    const max = Number(fontSize.value) - 1, value = authorSize.value === '' ? 15 : Number(authorSize.value);
    authorSize.max = authorRange.max = String(max);
    authorSize.value = authorRange.value = String(Math.max(8, Math.min(max, Math.round(Number.isFinite(value) ? value : 15))));
  }
  for (const [size, range] of [[fontSize, fontRange], [enSize, enRange]]) {
    const normalize = () => {
      const value = size.value === '' ? 32 : Number(size.value);
      size.value = range.value = String(Math.max(16, Math.min(96, Math.round(Number.isFinite(value) ? value : 32))));
      if (size === fontSize) normalizeAuthorSize();
    };
    size.addEventListener('input', () => { if (size.value && size.validity.valid) range.value = size.value; });
    size.addEventListener('change', normalize);
    size.addEventListener('blur', normalize);
    range.addEventListener('input', () => { size.value = range.value; normalize(); });
    normalize();
  }
  authorSize.addEventListener('input', () => { if (authorSize.value && authorSize.validity.valid) authorRange.value = authorSize.value; });
  authorSize.addEventListener('change', normalizeAuthorSize);
  authorSize.addEventListener('blur', normalizeAuthorSize);
  authorRange.addEventListener('input', () => { authorSize.value = authorRange.value; });
  const fontsPromise = (async () => {
    try {
      const response = await fetch('/api/media/fonts', {credentials: 'same-origin', signal: AbortSignal.timeout(15000)});
      const data = await response.json();
      if (!response.ok || !data.success || !Array.isArray(data.fonts)) throw new Error();
      if (!panel.isConnected) return [];
      const fonts = [...new Set(data.fonts.map(font => font.name).filter(name => typeof name === 'string' && name))].sort((a, b) => a.localeCompare(b, 'ko'));
      let missing = false;
      for (const select of fontSelects) {
        const selected = select.value;
        select.replaceChildren(new Option('코어 글꼴 상속', ''), ...fonts.map(name => new Option(name, name)));
        if (selected && !fonts.includes(selected)) { select.append(new Option(`${selected} · 설치 목록에 없음`, selected)); missing = true; }
        select.value = selected;
      }
      fontStatus.textContent = missing
        ? '저장된 글꼴이 설치 목록에 없습니다. 선택값은 유지하며 해당 영역은 코어 글꼴을 사용합니다.'
        : '설치된 글꼴을 한국어·영어·저자에 각각 적용합니다. 아래 설정 저장 버튼을 눌러 적용하세요.';
      return data.fonts;
    } catch { fontStatus.textContent = '글꼴 목록을 불러오지 못했습니다. 기존 선택은 유지됩니다.'; }
    return [];
  })();
  for (const [language, label, select, size] of [
    ['ko', '한국어', fontSelects[0], fontSize], ['en', '영어', fontSelects[1], enSize], ['author', '저자', fontSelects[2], authorSize],
  ]) {
    const sample = root.querySelector(`[data-quote-preview=${language}]`), caption = root.querySelector(`[data-quote-preview-label=${language}]`);
    if (!sample || !caption) continue;
    let version = 0;
    const connected = () => root.isConnected && panel.isConnected && root.contains(panel) && root.contains(sample);
    const updateCaption = () => { caption.textContent = `${label} · ${select.value || '코어 기본'} · ${parseFloat(sample.style.fontSize)}px`; };
    const updateSize = () => {
      if (!connected() || !size.value || !size.validity.valid) return;
      sample.style.fontSize = `${Number(size.value)}px`; updateCaption();
    };
    const updateFont = async () => {
      const token = ++version, name = select.value;
      const current = () => connected() && version === token && select.value === name;
      if (!current()) return;
      sample.style.removeProperty('font-family'); updateCaption();
      if (!name) return;
      const base = getComputedStyle(sample).fontFamily;
      try {
        const font = (await fontsPromise).find(font => font.name === name);
        if (!font?.url || !current()) return;
        const url = new URL(font.url, location.origin);
        if (url.origin !== location.origin || url.username || url.password || !url.pathname.startsWith('/static/fonts/custom/')) return;
        const {loadAndApplyCustomFont} = await import('/static/js/viewer_settings.js');
        if (!current()) return;
        const family = await loadAndApplyCustomFont(name, url.href);
        if (current() && family === `CustomFont_${name.replace(/\s+/g, '_')}`) sample.style.fontFamily = `${JSON.stringify(family)}, ${base}`;
      } catch {}
    };
    for (const event of ['input', 'change', 'focusout']) panel.addEventListener(event, updateSize);
    select.addEventListener('change', updateFont);
    updateSize(); updateFont();
  }
  let busy = false, stopRequested = false;
  const translationStop = query('[data-quote-translation-stop]'), translationStatus = query('[data-quote-translation-status]');
  async function action(action_id, context = {}) {
    const {state} = await import('/static/js/state.js');
    const response = await fetch('/api/media/context-menu/book/plugins/action', {
      method: 'POST', credentials: 'same-origin', signal: AbortSignal.timeout(30000),
      headers: {'Content-Type': 'application/json', 'X-Good-Quotes': '1'},
      body: JSON.stringify({type: state.currentLibraryType || 'general', plugin_id: pluginId, action_id, context}),
    });
    const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.error || '글귀 작업에 실패했습니다.');
    return result;
  }
  async function perform(job, stoppable = false) {
    if (busy) return;
    busy = true; message.textContent = '처리 중입니다.';
    panel.querySelectorAll('button').forEach(button => button.disabled = true);
    translationStop.disabled = !stoppable;
    try { await job(); } catch (error) { message.textContent = error.message || '입력과 연결 상태를 확인해 주세요.'; }
    finally { busy = false; if (panel.isConnected) { panel.querySelectorAll('button').forEach(button => button.disabled = false); translationStop.disabled = true; } }
  }
  function translationInfo(result) {
    const count = value => Number(value || 0).toLocaleString('ko-KR');
    query('[data-quote-translation-target]').textContent = `대상 ${count(result.pending_count)}건 · 예상 ${count(result.characters)}자`;
    query('[data-quote-translation-usage]').textContent = result.usage
      ? `마지막 조회 사용량 ${count(result.usage.used)} / ${count(result.usage.limit)}자 · 잔여 ${count(result.usage.remaining)}자`
      : 'DeepL 사용량을 확인하지 못했습니다. 저장된 API 키와 연결 상태를 확인하세요.';
  }
  async function translate(run) {
    if (busy) return;
    stopRequested = false;
    await perform(async () => {
      try {
        translationStatus.textContent = '번역 대상과 사용량을 확인하고 있습니다.';
        const snapshot = await action('quotes_translation_status', run ? {} : {refresh_sources: true});
        if (!panel.isConnected) return;
        translationInfo(snapshot);
        const sourceErrors = (snapshot.errors || []).map(error =>
          `${({zenquotes: 'ZenQuotes', positivequotation: 'PositiveQuotation'})[error.provider] || error.provider} · ${error.message}` +
          (error.retry_after ? ` ${error.retry_after}초 뒤 재시도하세요.` : '')).join(' / ');
        const preparationError = sourceErrors ? `영문 공급원 준비 실패 · ${sourceErrors} ` : '';
        if (!snapshot.configured) { translationStatus.textContent = preparationError + 'DeepL API 키를 입력한 뒤 아래 설정 저장 버튼을 눌러 주세요.'; return; }
        if (!run) {
          translationStatus.textContent = preparationError
            ? preparationError + '공급원 설정과 연결 상태를 확인한 뒤 대상 확인을 다시 눌러 주세요.'
            : '대상 확인을 마쳤습니다. 미번역 일괄 번역 버튼으로 시작하세요.';
          return;
        }
        const ids = snapshot.pending_ids;
        const progress = query('[data-quote-translation-progress]');
        progress.max = Math.max(1, ids.length); progress.value = 0;
        let translated = 0, skipped = 0, failed = 0;
        query('[data-quote-translation-progress-text]').textContent = `진행 0 / ${ids.length}건`;
        if (!ids.length) { translationStatus.textContent = preparationError || '번역할 미번역 문구가 없습니다. 영문 공급원 설정을 저장한 뒤 대상 확인을 눌러 문구를 준비하세요.'; return; }
        translationStatus.textContent = '미번역 문구를 번역하고 있습니다.';
        for (let index = 0; index < ids.length; index += 10) {
          if (stopRequested || !panel.isConnected) break;
          const result = await action('quotes_translate', {ids: ids.slice(index, index + 10)});
          if (!panel.isConnected) return;
          translated += result.translated; skipped += result.skipped; failed += result.failed;
          progress.value = translated + skipped + failed;
          query('[data-quote-translation-progress-text]').textContent = `진행 ${progress.value} / ${ids.length}건 · 번역 ${translated} · 건너뜀 ${skipped} · 실패 ${failed}`;
          query('[data-quote-translation-target]').textContent = `남은 대상 ${result.remaining}건`;
          if (result.stopped || result.failed || result.reason) {
            const reason = {auth: 'DeepL API 키와 계정 이용 권한을 확인하세요.', quota: 'DeepL의 남은 번역 문자 한도가 부족합니다.',
              'rate-limit': 'DeepL 요청이 많아 잠시 대기해야 합니다.', network: 'DeepL 연결에 실패했습니다.',
              response: 'DeepL 응답 형식이 올바르지 않습니다.', storage: '번역 저장에 실패했습니다. 저장소를 확인하세요.',
              busy: '다른 번역 묶음을 처리 중입니다.', input: '번역 대상 ID는 한 번에 최대 10개여야 합니다.',
              'not-configured': 'DeepL API 키를 입력하고 설정 저장을 눌러 주세요.'}[result.reason] || result.reason || '번역 실패가 발생했습니다.';
            translationStatus.textContent = `번역을 중단했습니다. ${reason}` +
              (result.retry_after ? ` ${result.retry_after}초 뒤 대상 확인 후 다시 시도하세요.` : ' 대상 확인 후 다시 시도하세요.');
            return;
          }
        }
        if (!panel.isConnected) return;
        if (stopRequested) { translationStatus.textContent = '중지했습니다. 완료한 묶음은 저장되었으며 다시 실행하면 남은 항목부터 번역합니다.'; return; }
        const updated = await action('quotes_translation_status');
        if (!panel.isConnected) return;
        translationInfo(updated);
        translationStatus.textContent = `사전 번역을 완료했습니다. 번역 ${translated}건 · 건너뜀 ${skipped}건 · 남은 대상 ${updated.pending_count}건`;
      } catch (error) {
        if (panel.isConnected) translationStatus.textContent = `번역 작업을 중단했습니다. ${error.message || '연결 실패입니다.'} 완료된 묶음은 보존되며, 대상 확인 후 다시 시작할 수 있습니다.`;
      } finally { if (panel.isConnected) message.textContent = translationStatus.textContent; }
    }, run);
  }
  query('[data-quote-translation-check]').addEventListener('click', () => translate(false));
  query('[data-quote-translation-start]').addEventListener('click', () => translate(true));
  translationStop.addEventListener('click', () => {
    stopRequested = true; translationStop.disabled = true;
    translationStatus.textContent = '중지를 요청했습니다. 진행 중인 묶음이 저장될 때까지 기다려 주세요.';
  });
  query('[data-quote-refresh]').addEventListener('click', () => perform(async () => {
    const result = await action('quotes_manage');
    if (!panel.isConnected) return;
    const labels = {healthy: '정상', disabled: '꺼짐', 'terms-pending': '이용 범위 미확인', cooldown: '잠시 대기', 'no-eligible-content': '사용 가능한 문구 없음'};
    query('[data-quote-provider-state]').textContent = result.providers.map(provider => `${provider.name} ${labels[provider.status] || provider.status} (${provider.effective_weight}%)`).join(' / ');
    message.textContent = '공급원 상태를 확인했습니다. 설정 변경은 아래 설정 저장 버튼을 눌러 적용하세요.';
  }));
})();
