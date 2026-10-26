/**
 * Hermes Desktop 런타임 플러그인(`$HERMES_HOME/desktop-plugins/langflow/plugin.js`)의 소스.
 * Hermes 앱은 수정하지 않고 공식 플러그인 문(disk door)으로 상태 표시줄에 Langflow 버튼을 달고,
 * 누르면(또는 대화창의 `/langflow`) 메인 영역에 Langflow 편집 화면 탭을 연다.
 * 주소는 Perdix 호환 서버의 `/api/plugins/langflow/config`에서 받으므로 파일에 값이 박혀 있지 않다.
 */
export const LANGFLOW_PLUGIN_ID = "langflow";

export const LANGFLOW_PLUGIN_SOURCE = `import { host } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const WORKSPACE_ID = 'langflow'

function Notice({ text }) {
  return jsx('div', {
    style: { padding: '2rem', color: 'var(--ui-text-secondary, #666)', fontSize: '0.9rem' },
    children: text
  })
}

async function openCanvas(ctx) {
  let config
  try {
    config = await ctx.rest('/config')
  } catch (error) {
    config = { ready: false, error: String((error && error.message) || error) }
  }

  host.openWorkspace(WORKSPACE_ID, {
    title: 'Langflow',
    render: () => {
      if (!config || !config.url) {
        return jsx(Notice, { text: 'Langflow 연동이 꺼져 있습니다. Perdix 서버를 --langflow-config로 실행하세요.' })
      }
      if (!config.ready) {
        return jsx(Notice, { text: 'Langflow가 응답하지 않습니다: ' + config.url + ' (실행 중인지 확인하세요)' })
      }
      // webview는 앱과 분리된 최상위 창처럼 동작해 Langflow의 로그인 쿠키가 막히지 않는다.
      return jsx('webview', {
        partition: 'persist:perdix-langflow',
        src: config.url,
        allowpopups: 'true',
        style: { display: 'flex', width: '100%', height: '100%', minHeight: '24rem' }
      })
    }
  })
}

function Chip({ ctx }) {
  return jsxs('button', {
    type: 'button',
    title: 'Langflow 편집 화면 열기',
    onClick: () => openCanvas(ctx),
    className: 'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] hover:underline',
    style: { background: 'transparent', border: 'none', cursor: 'pointer', color: 'inherit' },
    children: [jsx('span', { 'aria-hidden': true, children: '\u29C9' }), jsx('span', { children: 'Langflow' })]
  })
}

export default {
  id: 'langflow',
  name: 'Langflow',
  description: 'Perdix의 Langflow 편집 화면을 앱 안의 탭으로 엽니다.',
  register(ctx) {
    ctx.register({ id: 'chip', area: 'statusBar.left', order: 120, render: () => jsx(Chip, { ctx }) })
    // 대화창의 /langflow가 Perdix 어댑터에서 이 이벤트를 보낸다.
    ctx.onEvent('perdix.langflow.open', () => { void openCanvas(ctx) })
  }
}
`;
