/**
 * Hermes Desktop 런타임 플러그인(`$HERMES_HOME/desktop-plugins/workflow/plugin.js`)의 소스.
 * Hermes 앱은 수정하지 않는다. 역할(시스템 프롬프트 메타데이터)을 사이드바에서 읽기 전용으로
 * 보여주고, 실제 그래프 편집은 Langflow(브라우저)로 보낸다 — langflow-plugin.ts와 같은 이유로
 * 편집 화면 자체는 이 플러그인에 넣지 않는다.
 * 외부 브라우저를 여는 공식 door가 없다(`@hermes/plugin-sdk`의 `host`에 openExternal 없음) —
 * `target="_blank"`/`window.open`은 Hermes의 window-open 정책이 Hub origin 외엔 전부 거부한다
 * (CVE-2026-70608 대응, `apps/desktop/electron/window-open-policy.ts` 확인함). 그래서 Langflow
 * 주소는 복사 버튼으로만 보여준다 — `window.hermesDesktop.openExternal` 같은 비공식 브릿지는
 * 공식 SDK 계약 밖이라 쓰지 않는다.
 */
export const WORKFLOW_PLUGIN_ID = "workflow";

export const WORKFLOW_PLUGIN_SOURCE = `import { atom, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const PATH = '/workflow'
const state = atom({ roles: [], langflow: { status: 'loading', url: '' }, copied: false })
let rest = null

async function refresh() {
  const [roles, langflowInfo] = await Promise.all([
    rest('/roles').catch(() => []),
    rest('/langflow-url').catch(() => null),
  ])
  const langflow = (langflowInfo && langflowInfo.url)
    ? { status: langflowInfo.ready ? 'ready' : 'down', url: langflowInfo.url }
    : { status: 'off', url: '' }
  state.set({ roles, langflow, copied: false })
}

function copyUrl(url) {
  navigator.clipboard.writeText(url).then(() => {
    state.set({ ...state.get(), copied: true })
    setTimeout(() => state.set({ ...state.get(), copied: false }), 1500)
  }).catch(() => {})
}

function RoleRow({ role }) {
  return jsxs('div', {
    style: { padding: '0.6rem 0', borderBottom: '1px solid var(--ui-border, #3332)' },
    children: [
      jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '0.5rem' }, children: [
        jsx('span', { style: { fontWeight: 600 }, children: role.id }),
        jsx('span', { style: { fontSize: '0.75rem', opacity: 0.6 }, children: role.readOnly ? '읽기 전용' : '쓰기 가능' })
      ] }),
      jsx('div', { style: { fontSize: '0.85rem', opacity: 0.8, marginTop: '0.15rem' }, children: role.description })
    ]
  })
}

function LangflowBox({ langflow, copied }) {
  if (langflow.status === 'off') {
    return jsx('div', { style: { opacity: 0.7, fontSize: '0.85rem' }, children: 'Langflow 연동이 꺼져 있습니다. Perdix 서버를 --langflow-config로 실행하세요.' })
  }
  return jsxs('div', { children: [
    jsx('div', { style: { fontSize: '0.85rem', marginBottom: '0.35rem' },
      children: langflow.status === 'ready' ? 'Langflow 캔버스에서 노드를 드래그로 연결해 편집하세요. 아래 주소를 복사해 브라우저로 여세요(앱 안에서 직접 열기는 지원하지 않습니다).' : 'Langflow가 응답하지 않습니다. 실행 중인지 확인하세요.' }),
    jsxs('div', { style: { display: 'flex', gap: '0.5rem', alignItems: 'center' }, children: [
      jsx('input', { readOnly: true, value: langflow.url, onFocus: (e) => e.target.select(),
        style: { flex: 1, padding: '0.35rem 0.5rem', fontFamily: 'monospace', fontSize: '0.8rem' } }),
      jsx('button', { type: 'button', onClick: () => copyUrl(langflow.url),
        style: { padding: '0.35rem 0.8rem', border: '1px solid currentColor', borderRadius: '0.4rem', background: 'transparent', color: 'inherit', cursor: 'pointer' },
        children: copied ? '복사됨' : '복사' })
    ] })
  ] })
}

function Page() {
  const current = useValue(state)
  return jsxs('div', { style: { padding: '1.5rem', maxWidth: '640px' }, children: [
    jsx('h2', { style: { marginBottom: '0.25rem' }, children: 'Workflow' }),
    jsx('p', { style: { opacity: 0.7, fontSize: '0.85rem', marginBottom: '1rem' },
      children: '에이전트 역할(시스템 프롬프트·도구 범위) 목록입니다. 읽기 전용 — 편집은 Langflow 캔버스에서 합니다.' }),
    jsx('div', { children: current.roles.map((role) => jsx(RoleRow, { role, key: role.id })) }),
    jsx('h3', { style: { marginTop: '1.5rem', marginBottom: '0.5rem' }, children: 'Langflow 캔버스' }),
    jsx(LangflowBox, { langflow: current.langflow, copied: current.copied })
  ] })
}

export default {
  id: 'workflow',
  name: 'Workflow',
  description: 'Perdix 역할 목록을 읽기 전용으로 보여주고 Langflow 캔버스 주소를 복사할 수 있게 합니다.',
  register(ctx) {
    rest = ctx.rest
    void refresh()
    ctx.register({ id: 'page', area: 'routes', title: 'Workflow', data: { path: PATH }, render: () => jsx(Page, {}) })
    ctx.register({ id: 'nav', area: 'sidebar.nav', data: { codicon: 'type-hierarchy', label: 'Workflow', path: PATH } })
  }
}
`;
