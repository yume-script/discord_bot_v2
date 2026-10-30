// 좋은 글귀 홈 위젯의 문구·배경 갱신, 글꼴, 긴 문구 맞춤, 출처 패널을 처리합니다.
(async function () {
  shadowRoot.__goodQuotesCleanup?.();
  const card = shadowRoot.querySelector('.gq-quote');
  if (!card) return;
  card.hidden = true;
  const initial = Array.isArray(items) ? items.find(item => item?.widget_type === 'quotes') : null;
  if (!initial || initial.enabled === false) return;
  let alive = true, current, busy = false, language, fontsPromise;
  const selectedFonts = new Map();
  const controller = new AbortController();
  const query = selector => card.querySelector(selector);
  const status = query('[data-quote-status]');
  const reading = query('.gq-quote-reading');
  let fitFrame, backgroundTimer;
  let image = query('[data-quote-image]'), pendingImage, backgroundVersion = 0;
  let type = 'general';
  let recent = [];
  try { recent = JSON.parse(sessionStorage.getItem('good-quotes-recent') || '[]'); } catch {}
  if (!Array.isArray(recent)) recent = [];
  const safeUrl = value => {
    try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : ''; }
    catch { return ''; }
  };
  function credit(parent, label, url) {
    const target = safeUrl(url), element = document.createElement(target ? 'a' : 'span');
    element.textContent = label;
    if (target) { element.href = target; element.target = '_blank'; element.rel = 'noopener noreferrer'; }
    parent.append(element);
  }
  async function applyFont(name = '', element = query('.gq-quote-words')) {
    if (name === selectedFonts.get(element)) return;
    selectedFonts.set(element, name);
    element.style.removeProperty('font-family');
    if (element.matches('[data-quote-en]')) element.style.fontFamily = getComputedStyle(card).fontFamily;
    if (!name) return;
    try {
      fontsPromise ||= (async () => {
        const response = await fetch('/api/media/fonts', {credentials: 'same-origin', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)])});
        const data = await response.json();
        return response.ok && data.success && Array.isArray(data.fonts) ? data.fonts : [];
      })();
      const font = (await fontsPromise).find(font => font.name === name);
      if (!font?.url || !alive || selectedFonts.get(element) !== name) return;
      const url = new URL(font.url, location.origin);
      if (url.origin !== location.origin || !url.pathname.startsWith('/static/fonts/custom/')) return;
      const {loadAndApplyCustomFont} = await import('/static/js/viewer_settings.js');
      const family = await loadAndApplyCustomFont(name, url.href);
      if (alive && selectedFonts.get(element) === name && family === `CustomFont_${name.replace(/\s+/g, '_')}`) {
        element.style.fontFamily = `${JSON.stringify(family)}, ${getComputedStyle(card).fontFamily}`;
      }
    } catch {}
    finally { scheduleFit(); }
  }
  async function action(action_id, context) {
    const response = await fetch('/api/media/context-menu/book/plugins/action', {
      method: 'POST', credentials: 'same-origin', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
      headers: {'Content-Type': 'application/json', 'X-Good-Quotes': '1'},
      body: JSON.stringify({type, plugin_id: pluginId, action_id, context}),
    });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error || '글귀를 불러오지 못했습니다.');
    return data;
  }
  function displayLanguage() {
    if (!current) return;
    const button = query('[data-quote-language]');
    const selected = !current.text_en ? 'ko' : !current.text_ko ? 'en' : language;
    const bilingual = Boolean(current.text_en && current.text_ko);
    button.textContent = {en: 'En', ko: '한', both: 'En.한'}[selected];
    button.dataset.language = selected;
    button.disabled = !bilingual;
    button.setAttribute('aria-label', `${{en: '영어', ko: '한국어', both: '영어와 한국어'}[selected]} 표시. ${bilingual ? '언어 전환' : '제공된 언어로 표시'}`);
    const sizes = [];
    if (selected !== 'ko' && current.text_en) sizes.push(parseFloat(card.style.getPropertyValue('--quote-en-size')));
    if (selected !== 'en' && current.text_ko) sizes.push(parseFloat(card.style.getPropertyValue('--quote-font-size')));
    card.style.setProperty('--quote-smallest-body', `${Math.min(...sizes, 96)}px`);
    query('[data-quote-en]').hidden = selected === 'ko' || !current.text_en;
    query('[data-quote-ko]').hidden = selected === 'en' || !current.text_ko;
    const automatic = current.translation_status === 'automatic' && selected !== 'en';
    query('[data-quote-translation]').textContent = automatic ? (current.translation_provider === 'deepl' ? 'DeepL 자동 번역' : '자동 번역') : '';
    query('[data-quote-translation]').hidden = !automatic;
    reading.scrollTop = 0;
    scheduleFit();
  }
  function scheduleFit() {
    if (!alive) return;
    cancelAnimationFrame(fitFrame);
    fitFrame = requestAnimationFrame(fitText);
  }
  function fitText() {
    if (!alive || card.hidden || !reading.clientWidth || !reading.clientHeight) return;
    card.style.setProperty('--quote-fit', '1');
    const visible = [...card.querySelectorAll('[data-quote-en], [data-quote-ko]')].filter(element => !element.hidden);
    if (!visible.length) return;
    const smallest = Math.min(...visible.map(element => parseFloat(getComputedStyle(element).fontSize)));
    const fits = () => reading.scrollHeight <= reading.clientHeight + 1 && reading.scrollWidth <= reading.clientWidth + 1;
    if (!fits()) {
      let low = Math.min(1, 12 / smallest), high = 1;
      card.style.setProperty('--quote-fit', String(low));
      if (fits()) {
        for (let step = 0; step < 8; step++) {
          const scale = (low + high) / 2;
          card.style.setProperty('--quote-fit', String(scale));
          if (fits()) low = scale; else high = scale;
        }
      }
      card.style.setProperty('--quote-fit', String(low));
    }
    fitSources();
  }
  function photoCredits(photo = {}) {
    const sources = query('[data-quote-sources]');
    sources.querySelector('[data-photo-source]')?.remove();
    if (!safeUrl(photo.url)) return;
    const credits = document.createElement('div'); credits.dataset.photoSource = '';
    const author = document.createElement('p'), provider = document.createElement('p'), license = document.createElement('p');
    author.append(photo.provider === 'met' ? '작품 작가 · ' : '사진 작가 · '); credit(author, photo.creator || '작가 미상', photo.creator_url || photo.source_url);
    provider.append('이미지 출처 · '); credit(provider, {met: 'The Metropolitan Museum of Art', pexels: 'Pexels', unsplash: 'Unsplash'}[photo.provider] || '원본', photo.source_url);
    license.append('이미지 이용 조건 · '); credit(license, photo.license || '이용 조건', photo.license_url);
    credits.append(author, provider, license); sources.append(credits);
    for (const [label, value] of [['작품명', photo.title], ['소장 정보', photo.credit_line]]) {
      if (value) { const line = document.createElement('p'); line.textContent = `${label} · ${value}`; credits.append(line); }
    }
  }
  function scheduleBackground(seconds) {
    clearTimeout(backgroundTimer);
    if (!alive || !Number.isFinite(seconds) || seconds <= 0) return;
    backgroundTimer = setTimeout(() => {
      if (busy) { scheduleBackground(1); return; }
      update({photoOnly: true});
    }, Math.max(1, Math.min(86400, seconds)) * 1000);
  }
  function background(photo = {}) {
    scheduleBackground(photo.refresh_after);
    const url = safeUrl(photo.url), version = ++backgroundVersion;
    if (pendingImage) { pendingImage.onload = pendingImage.onerror = null; pendingImage.removeAttribute('src'); pendingImage = null; }
    const messages = {'terms-pending': '사진 공급원의 이용 조건 확인 후 배경을 사용할 수 있습니다.',
      'key-required': '사진 API 키를 설정하면 배경을 사용할 수 있습니다.',
      cooldown: '사진 공급원이 잠시 응답하지 않아 그라데이션을 표시합니다.',
      'request-failed': '사진을 불러오지 못해 그라데이션을 표시합니다.',
      'no-public-domain-art': '표시할 수 있는 Met 공개 작품이 없어 그라데이션을 표시합니다.',
      'invalid-photo': '사용할 수 있는 사진이 없어 그라데이션을 표시합니다.'};
    status.textContent = photo.reason && url ? '사진을 새로 받지 못해 기존 배경을 유지합니다.' : messages[photo.reason] || '';
    scheduleFit();
    function commit() {
      current.background = photo;
      card.dataset.background = url ? 'photo' : 'gradient';
      card.dataset.photoProvider = photo.provider || 'gradient';
      photoCredits(photo); fitSources();
    }
    if (!url) { image.hidden = true; image.removeAttribute('src'); commit(); return; }
    if (!image.hidden && image.src === url) { commit(); return; }
    const next = new Image();
    next.className = image.className; next.dataset.quoteImage = ''; next.alt = ''; next.setAttribute('aria-hidden', 'true');
    next.decoding = 'async'; next.fetchPriority = 'high'; pendingImage = next;
    next.onload = () => {
      if (!alive || version !== backgroundVersion) return;
      next.onload = next.onerror = null;
      image.replaceWith(next); image = next; pendingImage = null; commit();
    };
    next.onerror = () => {
      if (!alive || version !== backgroundVersion) return;
      next.onload = next.onerror = null; pendingImage = null;
      status.textContent = '사진을 불러오지 못했습니다. 기존 배경을 유지합니다.';
      if (photo.refresh_after > 0) scheduleBackground(60);
      scheduleFit();
    };
    next.src = url;
  }
  function render(item) {
    const previousBackground = current?.background;
    current = {...item, background: previousBackground};
    const size = Number(item.font_size);
    const fontSize = Number.isFinite(size) ? Math.max(16, Math.min(96, size)) : 32;
    const enSize = Number(item.en_font_size ?? item.font_size);
    const enFontSize = Number.isFinite(enSize) ? Math.max(16, Math.min(96, enSize)) : fontSize;
    const authorSize = Number(item.author_font_size);
    card.style.setProperty('--quote-font-size', `${fontSize}px`);
    card.style.setProperty('--quote-en-size', `${enFontSize}px`);
    card.style.setProperty('--quote-author-size', `${Math.max(8, Math.min(fontSize - 1, Number.isFinite(authorSize) ? authorSize : 15))}px`);
    query('[data-quote-en]').textContent = item.text_en || '';
    query('[data-quote-ko]').textContent = item.text_ko || '';
    const profile = item.provider === 'korean_advice' && typeof item.author_profile === 'string' ? item.author_profile.trim() : '';
    query('[data-quote-author]').textContent = `- ${item.author || '저자 미확인'}${profile ? ', ' + profile : ''} -`;
    const sources = query('[data-quote-sources]');
    sources.replaceChildren();
    const labels = {korean_advice: 'Korean Advice API', zenquotes: 'Inspirational quotes provided by ZenQuotes API', positivequotation: 'PositiveQuotation', builtin: '좋은 글귀 · 자체 작성 문구'};
    for (const [label, value, url] of [
      ['글귀 저자', item.author], ['콘텐츠 제공처', labels[item.provider], item.provider_url],
      ['확인된 원전', item.original_source_title || (item.original_source_url ? '원문' : '출전 미확인'), item.original_source_url],
      ['이용 조건', item.provider_license || (item.rights_evidence_url ? '확인 자료' : item.rights_status === 'original' ? '자체 작성' : '관리자가 확인한 사용 범위'), item.rights_evidence_url],
    ]) {
      const line = document.createElement('p'); line.append(`${label} · `); credit(line, value || '미확인', url); sources.append(line);
    }
    if (item.text_ko && item.translation_status === 'automatic' && item.translation_provider === 'deepl') {
      const line = document.createElement('p'); line.append('자동 번역 · '); credit(line, 'DeepL · deepl.com', 'https://www.deepl.com/'); sources.append(line);
    }
    language ||= ['both', 'ko', 'en'].includes(item.language) ? item.language : 'both';
    displayLanguage();
    if (Object.hasOwn(item, 'background')) background(item.background);
    else photoCredits(previousBackground);
    applyFont(item.font); applyFont(item.text_en ? item.en_font ?? item.font : '', query('[data-quote-en]'));
    applyFont(item.author_font, query('[data-quote-author]'));
    if (item.fingerprint) {
      recent = [...recent.filter(value => value !== item.fingerprint), item.fingerprint].slice(-20);
      try { sessionStorage.setItem('good-quotes-recent', JSON.stringify(recent)); } catch {}
    }
  }
  async function update({photoOnly = false, deferred = false, force = false} = {}) {
    if (busy || !alive) return;
    busy = true; card.setAttribute('aria-busy', 'true'); status.textContent = '불러오는 중입니다.';
    const actions = card.querySelectorAll('[data-quote-next], [data-quote-background]');
    actions.forEach(button => button.disabled = true);
    try {
      const loadPhoto = async () => {
        try {
          const result = await action('quotes_background', {tags: current?.tags || [], force});
          if (alive) background(result.background);
        } catch (error) { scheduleBackground(60); throw error; }
      };
      const loadQuote = async () => {
        const result = await action('quotes_next', {recent, text_only: true});
        if (alive) { render(result.item); if (!deferred) status.textContent = ''; }
      };
      if (deferred) {
        const results = await Promise.allSettled([loadQuote(), loadPhoto()]);
        const failed = results.find(result => result.status === 'rejected');
        if (failed) throw failed.reason;
      } else if (photoOnly) await loadPhoto();
      else await loadQuote();
    } catch (error) { if (alive) status.textContent = error.message || '다시 시도해 주세요.'; }
    finally { busy = false; if (alive) { card.setAttribute('aria-busy', 'false'); actions.forEach(button => button.disabled = false); scheduleFit(); } }
  }
  query('[data-quote-next]').addEventListener('click', () => update());
  query('[data-quote-background]').addEventListener('click', () => update({photoOnly: true, force: true}));
  query('[data-quote-language]').addEventListener('click', event => {
    const modes = ['en', 'ko', 'both'];
    language = modes[(modes.indexOf(event.currentTarget.dataset.language) + 1) % modes.length];
    displayLanguage();
  });
  const sourceWrap = query('.gq-quote-sources'), sourcePanel = query('[data-quote-sources]'), sourceButton = query('[data-quote-source-toggle]');
  let sourcePinned = false;
  function fitSources() {
    if (sourcePanel.hidden) return;
    const rect = sourceButton.getBoundingClientRect();
    if (rect.bottom <= 0 || rect.top >= innerHeight) { sourcePinned = false; showSources(false); return; }
    const above = Math.max(0, rect.top - 8), below = Math.max(0, innerHeight - rect.bottom - 8);
    const down = above < Math.min(sourcePanel.scrollHeight, 300, innerHeight * .6) && below > above;
    sourceWrap.dataset.placement = down ? 'below' : 'above';
    sourcePanel.style.setProperty('--quote-popover-space', `${down ? below : above}px`);
  }
  function showSources(open) {
    sourcePanel.hidden = !open;
    sourceWrap.dataset.open = String(open);
    sourceButton.setAttribute('aria-expanded', String(open));
    if (open) fitSources();
  }
  sourceWrap.addEventListener('pointerenter', event => { if (event.pointerType !== 'touch') showSources(true); });
  sourceWrap.addEventListener('pointerleave', () => { if (!sourcePinned && !sourceWrap.contains(shadowRoot.activeElement)) showSources(false); });
  sourceWrap.addEventListener('focusin', () => showSources(true));
  sourceWrap.addEventListener('focusout', event => { if (!sourcePinned && !sourceWrap.contains(event.relatedTarget)) showSources(false); });
  sourceButton.addEventListener('click', () => { sourcePinned = !sourcePinned; showSources(sourcePinned); });
  sourceWrap.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    event.preventDefault(); event.stopPropagation(); sourcePinned = false; sourceButton.focus(); showSources(false);
  });
  document.addEventListener('pointerdown', event => { if (!event.composedPath().includes(sourceWrap)) { sourcePinned = false; showSources(false); } }, {signal: controller.signal});
  window.addEventListener('resize', scheduleFit, {signal: controller.signal});
  document.addEventListener('scroll', fitSources, {capture: true, signal: controller.signal});
  const observer = new MutationObserver(() => { if (!shadowRoot.host.isConnected || !shadowRoot.contains(card)) cleanup(); });
  const resizer = new ResizeObserver(scheduleFit);
  resizer.observe(card);
  document.fonts?.ready.then(scheduleFit);
  function cleanup() {
    alive = false; controller.abort(); observer.disconnect(); resizer.disconnect(); cancelAnimationFrame(fitFrame); clearTimeout(backgroundTimer); image.onload = image.onerror = null;
    if (pendingImage) { pendingImage.onload = pendingImage.onerror = null; pendingImage.removeAttribute('src'); }
  }
  observer.observe(document.body, {childList: true, subtree: true}); observer.observe(shadowRoot, {childList: true});
  shadowRoot.__goodQuotesCleanup = cleanup;
  render(initial);
  card.hidden = false;
  const actions = card.querySelectorAll('[data-quote-next], [data-quote-background]');
  actions.forEach(button => button.disabled = true);
  try {
    const {state} = await import('/static/js/state.js');
    if (['general', 'adult', 'audiobook', 'video'].includes(state.currentLibraryType)) type = state.currentLibraryType;
    if (!alive) return;
    actions.forEach(button => button.disabled = false);
    if (initial.deferred) await update({deferred: true});
  } catch (error) { if (alive && !controller.signal.aborted) { status.textContent = error.message; scheduleFit(); } }
})();
