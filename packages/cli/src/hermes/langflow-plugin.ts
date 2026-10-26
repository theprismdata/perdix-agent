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
    partition: 'persist:perdix-langflow',
    src: current.url,
    allowpopups: 'true',
    style: { display: 'flex', width: '100%', height: '100%', minHeight: '24rem' }
  })
}

export default {
  id: 'langflow',
  name: 'Langflow',
  description: 'Perdix의 Langflow 편집 화면을 왼쪽 사이드바 항목과 앱 안의 페이지로 엽니다.',
  register(ctx) {
    rest = ctx.rest
    void refresh()
    ctx.register({ id: 'page', area: 'routes', title: 'Langflow', data: { path: PATH }, render: () => jsx(Page, {}) })
    ctx.register({ id: 'nav', area: 'sidebar.nav', data: { codicon: 'type-hierarchy', label: 'Langflow', path: PATH } })
    // 대화창의 /langflow가 Perdix 어댑터에서 이 이벤트를 보낸다.
    ctx.onEvent('perdix.langflow.open', () => { void refresh(); host.navigate(PATH) })
  }
}
`;
