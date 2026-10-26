/**
 * Hermes Desktop 런타임 플러그인(`$HERMES_HOME/desktop-plugins/langflow/plugin.js`)의 소스.
 * Hermes 앱은 수정하지 않고 공식 플러그인 문(disk door)으로 왼쪽 사이드바에 `Langflow` 항목을 달고,
 * 누르면(또는 대화창의 `/langflow`) 메인 영역에 Langflow 편집 화면 페이지를 연다.
 * 주소는 Perdix 호환 서버의 `/api/plugins/langflow/config`에서 받으므로 파일에 값이 박혀 있지 않다.
 */
export const LANGFLOW_PLUGIN_ID = "langflow";

export const LANGFLOW_PLUGIN_SOURCE = `import { atom, host, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const PATH = '/langflow'
// status: loading | ready | down | off
const state = atom({ status: 'loading', url: '', error: '' })
let rest = null

async function refresh() {
  state.set({ status: 'loading', url: '', error: '' })
  try {
    const config = await rest('/config')
    if (!config || !config.url) {
      state.set({ status: 'off', url: '', error: '' })
    } else {
      state.set({ status: config.ready ? 'ready' : 'down', url: config.url, error: '' })
    }
  } catch (error) {
    state.set({ status: 'down', url: '', error: String((error && error.message) || error) })
  }
}

function Notice({ text, retry }) {
  return jsxs('div', {
    style: { padding: '2rem', color: 'var(--ui-text-secondary, #666)', fontSize: '0.9rem', lineHeight: 1.6 },
    children: [
      jsx('div', { children: text }),
      retry
        ? jsx('button', {
            type: 'button',
            onClick: refresh,
            style: { marginTop: '1rem', padding: '0.35rem 0.8rem', border: '1px solid currentColor', borderRadius: '0.4rem', background: 'transparent', color: 'inherit', cursor: 'pointer' },
            children: '다시 확인'
          })
        : null
    ]
  })
}

// Langflow 화면 안의 GitHub·Discord 별/인원 표시, 시작 안내 패널, 프로필 메뉴의 커뮤니티 링크는
// 이 앱과 어울리지 않아 숨긴다. 화면에 주입되는 스크립트라 이 함수는 외부 값을 쓰지 않는다.
// (숨길 뿐이며 Langflow가 보내는 별/인원 조회 요청 자체는 막지 않는다.)
function hideLangflowChrome() {
  const hide = (element) => {
    if (element && element.style.display !== 'none') element.style.setProperty('display', 'none', 'important')
  }
  const run = () => {
    // 상단 바의 GitHub(496x512)·Discord(640x512) 아이콘 버튼. 상단 바 밖의 버튼은 건드리지 않는다.
    for (const icon of document.querySelectorAll('svg[viewBox="0 0 496 512"], svg[viewBox="0 0 640 512"]')) {
      const button = icon.closest('button')
      if (button && button.getBoundingClientRect().top < 100) hide(button)
    }
    const title = document.querySelector('[data-testid="get_started_progress_title"]')
    if (title) hide(title.closest('[class*="10.8rem"]'))
    for (const id of ['menu_github_button', 'menu_discord_button', 'menu_twitter_button']) {
      hide(document.querySelector('[data-testid="' + id + '"]'))
    }
  }
  new MutationObserver(run).observe(document.documentElement, { childList: true, subtree: true })
  run()
}

function attachChromeHider(webview) {
  if (!webview || webview.__perdixHider) return
  webview.__perdixHider = true
  // 페이지를 새로 불러올 때마다 다시 주입한다(화면 안 이동은 같은 문서라 한 번이면 된다).
  webview.addEventListener('dom-ready', () => {
    Promise.resolve(webview.executeJavaScript('(' + hideLangflowChrome.toString() + ')()')).catch(() => {})
  })
}

function Page() {
  const current = useValue(state)
  if (current.status === 'loading') return jsx(Notice, { text: 'Langflow에 연결하는 중…' })
  if (current.status === 'off') {
    return jsx(Notice, { text: 'Langflow 연동이 꺼져 있습니다. Perdix 서버를 --langflow-config로 실행하세요.' })
  }
  if (current.status === 'down') {
    return jsx(Notice, {
      text: 'Langflow가 응답하지 않습니다: ' + (current.url || current.error) + ' (실행 중인지 확인하세요)',
      retry: true
    })
  }
  // webview는 앱과 분리된 최상위 창처럼 동작해 Langflow의 로그인 쿠키가 막히지 않는다.
  return jsx('webview', {
    ref: attachChromeHider,
    partition: 'persist:perdix-langflow',
    src: current.url,
    allowpopups: 'true',
    style: { display: 'flex', width: '100%', height: '100%', minHeight: '24rem' }
  })
}

// 새 세션 첫 화면의 큰 글자(HERMES AGENT)는 Hermes 코어가 상수로 그린다(플러그인이 대체할 수 있는 영역이
// 아님). 앱 소스는 건드리지 않고, 그려진 큰 글자만 숨긴다. 안내 문구는 그대로 둔다.
function hideSplashWordmark() {
  for (const wordmark of document.querySelectorAll('[data-slot="aui_intro"] .wordmark')) {
    if (wordmark.style.display !== 'none') wordmark.style.setProperty('display', 'none', 'important')
  }
}

function watchSplash(ctx) {
  let queued = false
  const observer = new MutationObserver(() => {
    if (queued) return
    queued = true
    requestAnimationFrame(() => { queued = false; hideSplashWordmark() })
  })
  observer.observe(document.body, { childList: true, subtree: true })
  ctx.onDispose(() => observer.disconnect())
  hideSplashWordmark()
}

export default {
  id: 'langflow',
  name: 'Langflow',
  description: 'Perdix 연결용: Langflow 편집 화면을 사이드바 항목으로 열고, 첫 화면의 큰 글자를 숨깁니다.',
  register(ctx) {
    rest = ctx.rest
    watchSplash(ctx)
    void refresh()
    ctx.register({ id: 'page', area: 'routes', title: 'Langflow', data: { path: PATH }, render: () => jsx(Page, {}) })
    ctx.register({ id: 'nav', area: 'sidebar.nav', data: { codicon: 'type-hierarchy', label: 'Langflow', path: PATH } })
    // 대화창의 /langflow가 Perdix 어댑터에서 이 이벤트를 보낸다.
    ctx.onEvent('perdix.langflow.open', () => { void refresh(); host.navigate(PATH) })
  }
}
`;
