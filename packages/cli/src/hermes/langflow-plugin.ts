/**
 * Hermes Desktop 런타임 플러그인(`$HERMES_HOME/desktop-plugins/langflow/plugin.js`)의 소스.
 * Hermes 앱은 수정하지 않는다. Langflow 편집(플로우 만들기·노드 편집)은 브라우저로 Langflow를 직접 여는
 * 용도라 이 플러그인에는 넣지 않는다 — Langflow의 사이드바·컴포넌트 팔레트까지 통째로 webview로 끌어오면
 * Hermes 자체 화면과 구조가 섞여 어색해진다(과거엔 넣었다가 뺐다). 플로우 실행(`/flow`, 모델의
 * `langflow_run` 도구 호출)과 목록 보기(Capabilities → Skills)는 이 플러그인과 무관하게 Perdix 서버가
 * API로 직접 처리하므로 영향받지 않는다.
 */
export const LANGFLOW_PLUGIN_ID = "langflow";

export const LANGFLOW_PLUGIN_SOURCE = `// 새 세션 첫 화면의 큰 글자(HERMES AGENT)는 Hermes 코어가 상수로 그린다(플러그인이 대체할 수 있는 영역이
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
  description: 'Perdix 연결용: 쓰지 않는 사이드바 항목과 첫 화면의 큰 글자를 숨깁니다. Langflow 편집은 브라우저로 직접 엽니다.',
  register(ctx) {
    watchSplash(ctx)
    // Perdix에 없는 기능이라 늘 비어 있는 사이드바 항목을 숨긴다. Capabilities는 Hermes가 숨기지 못하게 막아 둔다
    // (Plugins 탭으로 가는 길). 플러그인을 끄면 항목이 다시 나타난다.
    ctx.register({ id: 'nav-prefs', area: 'sidebarNav.prefs', data: { hide: ['messaging', 'artifacts', 'cron'] } })
  }
}
`;
