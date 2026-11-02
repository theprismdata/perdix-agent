/**
 * Hermes Desktop 런타임 플러그인(`$HERMES_HOME/desktop-plugins/workflow/plugin.js`)의 소스.
 * Hermes 앱은 수정하지 않는다. 역할(시스템 프롬프트 메타데이터)을 읽기 전용으로 보여주고,
 * Langflow 플로우를 이 페이지 안의 캔버스에서 편집한다.
 *
 * Langflow 캔버스 편집: 전에는 Langflow를 webview로 통째로 끌어왔다가 Hermes 자체 화면과
 * 구조가 섞여 어색해져서 뺐다(langflow-plugin.ts 참고). 이번엔 Langflow의 UI를 가져오지 않고
 * REST API(`/api/v1/flows`, `/api/v1/all`)로 노드·엣지·컴포넌트 데이터만 가져와 이 플러그인
 * 자신의 캔버스로 새로 그린다. Langflow 호출은 desktop-server.ts가 중계한다(CORS·API 키 노출을
 * 피하려고 플러그인이 Langflow를 직접 fetch하지 않는다, langflow-client.ts).
 *
 * 화면 배치는 Hermes 내장 kanban 페이지(`apps/desktop/src/plugins/kanban/board.tsx`)를 따른다:
 * 페이지가 콘텐츠 영역 전체 높이를 차지하고(헤더 한 줄 + 나머지는 본문), 색은 전부 호스트
 * 테마 변수(`--ui-*`)로만 칠한다. 고정 색을 쓰면 라이트/다크 테마 중 한쪽에서 노드가 떠 보인다.
 * 본문은 [컴포넌트 팔레트 | 캔버스 | 속성/역할 패널] 세 열이다. 팔레트는 Langflow 카테고리
 * 30여 개를 그대로 깔지 않고 Perdix에서 쓰는 몇 개만 펼쳐 두고 나머지는 "기타"로 접는다.
 */
export const WORKFLOW_PLUGIN_ID = "workflow";

type TemplateField = { value?: unknown };
type ComponentTemplate = { template?: Record<string, TemplateField> } & Record<string, unknown>;
type FlowNode = { id: string; data?: { type?: string; node?: ComponentTemplate } } & Record<string, unknown>;
type FlowData = { nodes: FlowNode[] } & Record<string, unknown>;

/**
 * 플로우에 저장된 예전 Perdix 컴포넌트 노드를 Langflow가 지금 가진 컴포넌트로 바꾼다.
 * Langflow는 노드에 컴포넌트 코드와 칸 목록을 통째로 저장하므로, 컴포넌트에 칸을 더해도
 * 기존 노드는 모른다. 코드가 다른 노드만 최신 정의로 갈아 끼우고, 두 쪽에 다 있는 칸의 값
 * (Role·Model 등)은 그대로 옮긴다. 노드 id와 연결선은 건드리지 않는다.
 * 플러그인 소스에 toString()으로 들어가므로 바깥 이름을 참조하지 않는다.
 */
export function upgradePerdixNodes(flowData: FlowData, components: Record<string, Record<string, ComponentTemplate>> | null): { data: FlowData; count: number } {
  const latest = (components && components.perdix) || {};
  let count = 0;
  const nodes = flowData.nodes.map((node) => {
    const type = node.data && node.data.type;
    const current = node.data && node.data.node;
    const next = type ? latest[type] : undefined;
    if (!type || !current || !next || !next.template) return node;
    const oldTemplate = current.template || {};
    const codeOf = (template: Record<string, TemplateField>) => (template.code ? template.code.value : undefined);
    if (codeOf(oldTemplate) === codeOf(next.template)) return node;
    const upgraded = JSON.parse(JSON.stringify(next)) as ComponentTemplate & { template: Record<string, TemplateField> };
    for (const key of Object.keys(upgraded.template)) {
      const field = upgraded.template[key];
      const old = oldTemplate[key];
      if (key === "code" || key === "_type" || !field || !old || !("value" in old)) continue;
      field.value = old.value;
    }
    count += 1;
    return { ...node, data: { ...node.data, node: upgraded } };
  });
  return { data: count > 0 ? { ...flowData, nodes } : flowData, count };
}

export const WORKFLOW_PLUGIN_SOURCE = `import { atom, useValue, Button, Input, SearchField, Textarea } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

const PATH = '/workflow'
const state = atom({ roles: [] })
let rest = null

const upgradePerdixNodes = ${upgradePerdixNodes.toString()}

async function refresh() {
  const roles = await rest('/roles').catch(() => [])
  state.set({ roles })
}

// ---------- 테마 ----------
// 전부 호스트 변수. 두 번째 값은 변수가 없는 호스트(테스트·구버전)용 비상 색이다.
// Hermes 테마 변수만 쓴다. 다크 테마에서 --ui-panel-background는 정의되지 않고 --ui-bg-input은
// 흰색으로 남아 있어서, 패널은 위젯 면(캔버스보다 살짝 어두움), 입력 칸은 컨트롤 배경을 쓴다.
const T = {
  surface: 'var(--ui-surface-background, transparent)',
  panel: 'var(--ui-widget-surface-background, var(--ui-surface-background, transparent))',
  card: 'var(--ui-bg-elevated, var(--ui-bg-card, transparent))',
  cardHeader: 'var(--ui-bg-tertiary, var(--ui-bg-elevated, transparent))',
  control: 'var(--ui-control-active-background, transparent)',
  border: 'var(--ui-stroke-secondary, var(--ui-border, rgba(128,128,128,0.3)))',
  strokeSoft: 'var(--ui-stroke-tertiary, rgba(128,128,128,0.2))',
  text: 'var(--ui-text-primary, inherit)',
  textSoft: 'var(--ui-text-secondary, inherit)',
  textFaint: 'var(--ui-text-tertiary, inherit)',
  accent: 'var(--ui-accent, #4a90e2)',
  good: 'var(--ui-green, #3fa66a)',
  hover: 'var(--ui-row-hover-background, rgba(128,128,128,0.12))',
}

// ---------- Langflow 데이터 ----------
// 핸들 인코딩은 Langflow 프런트엔드(src/frontend/src/utils/reactflowUtils.ts의
// scapedJSONStringfy/scapeJSONParse)와 똑같이 맞춘다 — 안 맞으면 여기서 저장한 플로우를
// Langflow 자체 화면에서 열 때 깨진다.
function encodeHandle(value) { return JSON.stringify(value).replace(/"/g, '\\u0153') }
function decodeHandle(value) { try { return JSON.parse(value.replace(/\\u0153/g, '"')) } catch (error) { return null } }

function genId(prefix) { return prefix + '-' + Math.random().toString(36).slice(2, 7) }

function isPortField(field) { return Array.isArray(field && field.input_types) && field.input_types.length > 0 }

// 칸 순서는 컴포넌트가 정한 field_order(입력 정의 순서)를 따른다. 템플릿 객체의 키 순서는
// 알파벳순이라 Perdix Agent에서 Model이 Role보다 먼저 나오는 식으로 어색해진다.
function templateEntries(node) {
  const meta = (node.data && node.data.node) || {}
  const template = meta.template || {}
  const order = Array.isArray(meta.field_order) ? meta.field_order : []
  const rank = (key) => { const index = order.indexOf(key); return index === -1 ? order.length : index }
  return Object.keys(template)
    .filter((key) => key !== '_type' && template[key] && template[key].show !== false)
    .sort((a, b) => rank(a) - rank(b))
    .map((key) => ({ key, field: template[key] }))
}

function nodeOutputs(node) { return (node.data && node.data.node && node.data.node.outputs) || [] }
function nodeTitle(node) { return (node.data && node.data.node && node.data.node.display_name) || (node.data && node.data.type) || node.id }

// Perdix Agent처럼 역할(role) 값을 가진 노드는 제목줄에 역할을 붙여 노드끼리 구분되게 한다.
// 중계 주소가 있으면 포트도 함께 보여 어느 중계(읽기 전용/코딩용)에 붙는지 드러낸다.
function ownRole(node) {
  const template = (node.data && node.data.node && node.data.node.template) || {}
  return template.role && template.role.value ? String(template.role.value) : ''
}

// Agent처럼 역할 값이 없는 노드는 자기에게 선을 댄 노드의 역할을 이어받아 표시한다.
// 예: Perdix Agent(build) → Agent 이면 Agent 제목줄에 "build 실행"이 붙는다.
function inheritedRoles(flowData) {
  const byId = {}
  flowData.nodes.forEach((node) => { byId[node.id] = node })
  const roles = {}
  flowData.edges.forEach((edge) => {
    const source = byId[edge.source]
    const target = byId[edge.target]
    if (!source || !target || ownRole(target)) return
    const role = ownRole(source)
    if (!role) return
    roles[target.id] = roles[target.id] || []
    if (!roles[target.id].includes(role)) roles[target.id].push(role)
  })
  return roles
}

function nodeBadges(node, inherited) {
  const template = (node.data && node.data.node && node.data.node.template) || {}
  const badges = []
  const role = template.role && template.role.value
  if (role) badges.push({ text: String(role), strong: true })
  ;(inherited || []).forEach((name) => badges.push({ text: name + ' 실행', strong: true }))
  const relay = template.relay_url && template.relay_url.value
  const port = relay ? String(relay).replace(/[/]+$/, '').split(':').pop() : ''
  if (port && /^[0-9]+$/.test(port)) badges.push({ text: ':' + port, strong: false })
  const override = template.model_override && template.model_override.value
  if (override && String(override).trim()) badges.push({ text: String(override).trim().split('/').pop(), strong: false })
  return badges
}

// ---------- 캔버스 기하 ----------
const ROW_HEIGHT = 22
const HEADER_HEIGHT = 32
const NODE_WIDTH = 220
const CANVAS_MARGIN = 40
// 마지막 노드 너머로 남겨 두는 스크롤 여유. 노드를 가장자리 밖으로 끌어 옮길 자리이기도 하다.
const CANVAS_SCROLL_ROOM = 480
// macOS 오버레이 스크롤바는 평소에 숨어 있어 캔버스가 스크롤되는지 알 수 없다. 늘 보이게 그린다.
const CANVAS_SCROLLBAR_CSS = '.perdix-wf-canvas::-webkit-scrollbar{width:10px;height:10px}' +
  '.perdix-wf-canvas::-webkit-scrollbar-thumb{background:var(--ui-border, #555);border-radius:5px;border:2px solid transparent;background-clip:padding-box}' +
  '.perdix-wf-canvas::-webkit-scrollbar-corner{background:transparent}'

/** 입력 포트를 먼저 쌓고 출력 포트를 그 아래 이어 그리므로(NodeBox 참고), 출력의 행 번호는
 *  입력 개수만큼 밀어야 한다. */
// 연결된 입력 점 목록("노드id|필드")을 만든다. 고급 입력은 연결돼 있을 때만 보이므로 필요하다.
function connectedInputs(flowData) {
  const keys = new Set()
  if (!flowData) return keys
  flowData.edges.forEach((edge) => {
    const handle = (edge.data && edge.data.targetHandle) || decodeHandle(edge.targetHandle || '')
    if (handle && handle.fieldName) keys.add(edge.target + '|' + handle.fieldName)
  })
  return keys
}

// 캔버스에 점으로 그릴 입력. Langflow 화면처럼 고급(advanced) 입력은 이미 연결된 것만 보인다.
// 노드 그리기·연결선 좌표·사각형 선택이 모두 이 기준을 써야 점과 선이 어긋나지 않는다.
function visibleInputs(node, connected) {
  return templateEntries(node).filter(({ key, field }) =>
    isPortField(field) && (field.advanced !== true || (connected && connected.has(node.id + '|' + key))))
}

function nodePortRow(node, side, key, connected) {
  const inputs = visibleInputs(node, connected)
  if (side === 'input') return inputs.findIndex((entry) => entry.key === key)
  return inputs.length + nodeOutputs(node).findIndex((output) => output.name === key)
}

/** Langflow가 저장한 좌표는 원점이 제각각이라(음수도 흔하다) 그대로 그리면 노드가 화면 밖에서
 *  시작한다. 플로우를 불러올 때 한 번 가장 왼쪽·위 노드가 여백 안에 오도록 이동량을 정하고,
 *  그리기에만 더한다 — 저장되는 position은 건드리지 않는다. 드래그 중에 다시 계산하면
 *  노드를 왼쪽으로 끌 때 전체가 튀므로 불러올 때만 정한다. */
function canvasOrigin(nodes) {
  if (!nodes || nodes.length === 0) return { x: 0, y: 0 }
  const minX = Math.min(...nodes.map((node) => (node.position && node.position.x) || 0))
  const minY = Math.min(...nodes.map((node) => (node.position && node.position.y) || 0))
  return { x: CANVAS_MARGIN - minX, y: CANVAS_MARGIN - minY }
}

/** 포트의 캔버스 좌표. DOM을 재서 구하지 않고 레이아웃 상수로 결정한다(드래그 중에도 일관됨). */
function portPoint(node, side, rowIndex, origin) {
  const x = ((node.position && node.position.x) || 0) + origin.x
  const y = ((node.position && node.position.y) || 0) + origin.y
  return { x: x + (side === 'output' ? NODE_WIDTH : 0), y: y + HEADER_HEIGHT + rowIndex * ROW_HEIGHT + ROW_HEIGHT / 2 }
}

function bezier(from, to) {
  const gap = Math.max(40, Math.abs(to.x - from.x) / 2)
  return 'M' + from.x + ' ' + from.y + ' C ' + (from.x + gap) + ' ' + from.y + ', ' + (to.x - gap) + ' ' + to.y + ', ' + to.x + ' ' + to.y
}

// ---------- 속성 패널 ----------
const selectStyle = {
  width: '100%', padding: '0.3rem 0.4rem', fontSize: '0.8rem', boxSizing: 'border-box',
  background: T.control, color: T.text, border: '1px solid ' + T.border, borderRadius: '0.3rem',
}

// Perdix Agent의 Model 칸. 노드에 적힌 중계에서 모델 목록을 받아 검색형 선택 칸으로 보여 준다.
// 비워 두면 agents.json의 역할 할당을 따르므로, 비우기 버튼으로 언제든 기본값에 돌아갈 수 있다.
function RelayModelInput({ field, relayUrl, onChange }) {
  const [models, setModels] = useState(null)
  const [error, setError] = useState('')
  const listId = useRef('relay-models-' + Math.random().toString(36).slice(2)).current
  useEffect(() => {
    if (!relayUrl) return
    setModels(null); setError('')
    rest('/relay/models?url=' + encodeURIComponent(relayUrl)).then((response) => {
      if (response.ok) setModels(response.result)
      else { setModels([]); setError(response.detail || response.error) }
    }).catch(() => { setModels([]); setError('목록을 받지 못했습니다.') })
  }, [relayUrl])
  const value = field.value == null ? '' : String(field.value)
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: '0.25rem' }, children: [
    jsxs('div', { style: { display: 'flex', gap: '0.3rem' }, children: [
      jsx(Input, { list: listId, value, placeholder: '비우면 역할 기본 모델(agents.json)', style: { flex: 1, minWidth: 0 },
        onChange: (event) => onChange(event.target.value) }),
      value ? jsx(Button, { size: 'xs', variant: 'ghost', title: '역할 기본 모델로 되돌립니다', onClick: () => onChange(''), children: '기본값' }) : null,
    ] }),
    jsx('datalist', { id: listId, children: (models || []).map((id) => jsx('option', { key: id, value: id })) }),
    jsx('span', { style: { fontSize: '0.6875rem', color: T.textFaint }, children:
      error ? '모델 목록을 받지 못했습니다: ' + error
        : models === null ? '모델 목록을 불러오는 중…'
        : value ? '이 노드만 ' + value + ' 모델로 호출합니다.'
        : '역할에 할당된 모델을 씁니다. 칸을 눌러 ' + models.length + '개 중에서 검색해 고를 수 있습니다.' }),
  ] })
}

// Perdix Agent의 Instructions 칸. 비어 있으면 역할 원본 시스템 프롬프트를 칸에 그대로 보여 준다(값은 비운 채라
// 원본을 따라간다). 칸에서 고치면 그 글이 이 노드 전용 값이 되고, 원본과 같아지면 다시 비운다.
// 값은 플로우에 저장되므로 원본은 그대로다.
function RelayPromptInput({ field, relayUrl, role, onChange }) {
  const [original, setOriginal] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!relayUrl || !role) { setOriginal(null); setError('중계 주소와 역할이 필요합니다.'); return }
    setOriginal(null); setError('')
    rest('/relay/prompt?url=' + encodeURIComponent(relayUrl) + '&role=' + encodeURIComponent(role)).then((response) => {
      if (response.ok) setOriginal(response.result)
      else setError(response.detail || response.error)
    }).catch(() => setError('원본을 받지 못했습니다.'))
  }, [relayUrl, role])
  const value = field.value == null ? '' : String(field.value)
  const custom = value.trim() !== ''
  const shown = custom ? value : (original || '')
  const onEdit = (text) => onChange(original !== null && text === original ? '' : text)
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: '0.25rem' }, children: [
    jsx(Textarea, { value: shown, rows: 14, readOnly: !custom && original === null,
      placeholder: error ? '원본을 받지 못했습니다. 여기에 쓰면 이 워크플로 전용 지시문이 됩니다.' : '원본 시스템 프롬프트를 불러오는 중…',
      style: { resize: 'vertical', fontSize: '0.75rem', fontFamily: 'ui-monospace, monospace', opacity: custom ? 1 : 0.85 },
      onChange: (event) => onEdit(event.target.value) }),
    custom ? jsx('div', { children: jsx(Button, { size: 'xs', variant: 'ghost', title: '고친 내용을 지우고 역할 원본을 씁니다',
      onClick: () => onChange(''), children: '원본으로 되돌리기' }) }) : null,
    jsx('span', { style: { fontSize: '0.6875rem', color: custom ? T.text : T.textFaint }, children:
      custom ? '수정됨: 이 워크플로에서만 이 지시문을 씁니다. 원본은 바뀌지 않으며, 원본이 바뀌어도 따라오지 않습니다. 저장해야 반영됩니다.'
        : error ? '원본을 받지 못했습니다: ' + error
        : '역할(' + role + ')의 원본입니다. 여기서 고치면 이 워크플로 전용이 됩니다.' }),
  ] })
}

function FieldInput({ field, onChange }) {
  const type = field.type
  if (type === 'bool') {
    return jsx('input', { type: 'checkbox', checked: !!field.value, onChange: (event) => onChange(event.target.checked) })
  }
  if (type === 'int' || type === 'float') {
    return jsx(Input, {
      type: 'number', value: field.value == null ? '' : field.value,
      onChange: (event) => onChange(type === 'int' ? parseInt(event.target.value, 10) : parseFloat(event.target.value)),
    })
  }
  if (Array.isArray(field.options) && field.options.length > 0) {
    return jsxs('select', {
      value: field.value || '', style: selectStyle, onChange: (event) => onChange(event.target.value),
      children: field.options.map((option) => jsx('option', { key: option, value: option, children: option })),
    })
  }
  if (type === 'file') {
    return jsx('p', { style: { fontSize: '0.75rem', color: T.textFaint, margin: 0 }, children: '파일 업로드는 지원하지 않습니다.' })
  }
  if (type === 'str' || type === 'code' || type === 'prompt' || type === undefined) {
    return jsx(Textarea, {
      value: field.value == null ? '' : String(field.value), rows: type === 'code' ? 6 : 2,
      style: { fontFamily: type === 'code' ? 'ui-monospace, monospace' : undefined, resize: 'vertical' },
      onChange: (event) => onChange(event.target.value),
    })
  }
  return jsx(Textarea, {
    defaultValue: JSON.stringify(field.value, null, 2), rows: 3, style: { fontFamily: 'ui-monospace, monospace' },
    onBlur: (event) => { try { onChange(JSON.parse(event.target.value)) } catch (error) { /* 잘못된 JSON이면 무시한다 */ } },
  })
}

function SectionLabel({ children }) {
  return jsx('div', { style: { fontSize: '0.6875rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: T.textFaint, padding: '0.75rem 0.75rem 0.35rem' }, children })
}

function MultiSelectPanel({ count, onDelete, onClear }) {
  return jsxs('div', { style: { padding: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.6rem', fontSize: '0.8125rem' }, children: [
    jsx('strong', { children: '노드 ' + count + '개 선택됨' }),
    jsx('span', { style: { fontSize: '0.75rem', color: T.textSoft }, children: '제목을 끌면 함께 움직입니다. Delete 키로 한꺼번에 지웁니다.' }),
    jsxs('div', { style: { display: 'flex', gap: '0.4rem' }, children: [
      jsx(Button, { size: 'xs', variant: 'destructive', onClick: onDelete, children: count + '개 삭제' }),
      jsx(Button, { size: 'xs', variant: 'ghost', onClick: onClear, children: '선택 해제' }),
    ] }),
  ] })
}

function FieldRow({ node, entry, onChangeField }) {
  const { key, field } = entry
  return jsxs('label', { style: { display: 'flex', flexDirection: 'column', gap: '0.2rem', fontSize: '0.75rem' }, children: [
    jsx('span', { style: { color: T.textSoft }, children: field.display_name || key }),
    key === 'model_override'
      ? jsx(RelayModelInput, { field, relayUrl: templateValue(node, 'relay_url'), onChange: (value) => onChangeField(node.id, key, value) })
      : key === 'instructions_override'
        ? jsx(RelayPromptInput, { field, relayUrl: templateValue(node, 'relay_url'), role: templateValue(node, 'role'), onChange: (value) => onChangeField(node.id, key, value) })
        : jsx(FieldInput, { field, onChange: (value) => onChangeField(node.id, key, value) }),
  ] })
}

function templateValue(node, key) {
  const template = (node.data && node.data.node && node.data.node.template) || {}
  return template[key] && template[key].value != null ? String(template[key].value) : ''
}

function PropertyPanel({ node, onChangeField, onDeleteNode }) {
  // 포트 필드는 캔버스에서 연결하고, 컴포넌트 소스(code)는 수백 줄짜리 Python이라 숨긴다.
  // Langflow가 고급으로 접어 두는 필드(예: Perdix Agent의 중계 주소)는 아래 "고급"에 접어 둔다.
  const editable = templateEntries(node).filter(({ key, field }) => !isPortField(field) && key !== 'code')
  const basic = editable.filter(({ field }) => field.advanced !== true)
  const advanced = editable.filter(({ field }) => field.advanced === true)
  // flex: 1로 오른쪽 열 높이에 맞춰야 아래 목록이 열 밖으로 넘치지 않고 스크롤된다.
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }, children: [
    jsxs('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.6rem 0.75rem', borderBottom: '1px solid ' + T.border }, children: [
      jsx('strong', { style: { fontSize: '0.8125rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: nodeTitle(node) }),
      jsx(Button, { size: 'xs', variant: 'destructive', title: 'Delete 키로도 지울 수 있습니다', onClick: () => onDeleteNode(node.id), children: '삭제' }),
    ] }),
    jsxs('div', { style: { padding: '0.6rem 0.75rem', display: 'flex', flexDirection: 'column', gap: '0.6rem', overflowY: 'auto' }, children: [
      editable.length === 0
        ? jsx('p', { style: { fontSize: '0.75rem', color: T.textFaint, margin: 0 }, children: '편집 가능한 값 필드가 없습니다.' })
        : null,
      ...basic.map((entry) => jsx(FieldRow, { key: entry.key, node, entry, onChangeField })),
      advanced.length > 0
        ? jsxs('details', { key: 'advanced:' + node.id, style: { fontSize: '0.75rem' }, children: [
            jsx('summary', { style: { cursor: 'pointer', color: T.textSoft, userSelect: 'none' }, children: '고급 (' + advanced.length + ')' }),
            jsx('div', { style: { display: 'flex', flexDirection: 'column', gap: '0.6rem', marginTop: '0.5rem' }, children:
              advanced.map((entry) => jsx(FieldRow, { key: entry.key, node, entry, onChangeField })) }),
          ] })
        : null,
    ] }),
  ] })
}

/** 노드를 고르기 전엔 오른쪽 열이 비니, 그 자리에 Perdix 역할 목록(읽기 전용)을 둔다. */
function RolesPanel({ roles }) {
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', minHeight: 0 }, children: [
    jsx(SectionLabel, { children: '역할' }),
    jsx('p', { style: { fontSize: '0.75rem', color: T.textFaint, margin: '0 0.75rem 0.5rem' }, children: '에이전트 역할(시스템 프롬프트·도구 범위)입니다. 읽기 전용입니다.' }),
    jsx('div', { style: { overflowY: 'auto' }, children: roles.length === 0
      ? jsx('p', { style: { fontSize: '0.75rem', color: T.textFaint, margin: '0 0.75rem' }, children: '역할이 없습니다.' })
      : roles.map((role) => jsxs('div', { key: role.id, style: { padding: '0.5rem 0.75rem', borderTop: '1px solid ' + T.border }, children: [
          jsx('div', { style: { fontSize: '0.8125rem', fontWeight: 600 }, children: role.id }),
          jsx('div', { style: { fontSize: '0.75rem', color: T.textSoft, marginTop: '0.15rem' }, children: role.description }),
        ] })) }),
    jsx('p', { style: { fontSize: '0.75rem', color: T.textFaint, margin: '0.75rem', borderTop: '1px solid ' + T.border, paddingTop: '0.75rem' }, children: '캔버스에서 노드를 선택하면 여기서 속성을 편집합니다.' }),
  ] })
}

// ---------- 컴포넌트 팔레트 ----------
// Perdix 플로우에서 실제로 쓰는 카테고리만 펼쳐 둔다. 나머지(벤더별 모델, crewai 등)는 "기타"
// 한 묶음으로 접어 두고 검색으로 찾게 한다.
const PALETTE_PRIMARY = [
  ['perdix', 'Perdix'], ['input_output', '입출력'], ['models_and_agents', '모델·에이전트'], ['llm_operations', 'LLM 작업'],
  ['tools', '도구'], ['flow_controls', '흐름 제어'], ['processing', '처리'], ['data_source', '데이터 소스'],
]

function PaletteItem({ label, onClick }) {
  const [hover, setHover] = useState(false)
  return jsx('button', {
    type: 'button', onClick, onMouseEnter: () => setHover(true), onMouseLeave: () => setHover(false),
    style: {
      display: 'block', width: '100%', textAlign: 'left', fontSize: '0.75rem', padding: '0.25rem 0.75rem 0.25rem 1.5rem',
      border: 'none', background: hover ? T.hover : 'transparent', color: T.text, cursor: 'pointer',
      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
    },
    children: label,
  })
}

function PaletteGroup({ label, items, open, onAdd }) {
  return jsxs('details', { open, children: [
    jsx('summary', { style: { cursor: 'pointer', fontSize: '0.75rem', fontWeight: 600, padding: '0.3rem 0.75rem', color: T.textSoft, userSelect: 'none' }, children: label + ' (' + items.length + ')' }),
    items.map((item) => jsx(PaletteItem, { key: item.category + '/' + item.name, label: item.label, onClick: () => onAdd(item) })),
  ] })
}

function ComponentPalette({ components, onAdd }) {
  const [query, setQuery] = useState('')
  let body
  if (!components) {
    body = jsx('p', { style: { fontSize: '0.75rem', color: T.textFaint, margin: '0.5rem 0.75rem' }, children: '컴포넌트를 불러오는 중…' })
  } else {
    const flat = []
    Object.keys(components).forEach((category) => {
      Object.keys(components[category]).forEach((name) => {
        const template = components[category][name]
        flat.push({ category, name, template, label: template.display_name || name })
      })
    })
    const needle = query.trim().toLowerCase()
    if (needle) {
      const hits = flat.filter((item) => item.label.toLowerCase().includes(needle) || item.name.toLowerCase().includes(needle))
      body = hits.length === 0
        ? jsx('p', { style: { fontSize: '0.75rem', color: T.textFaint, margin: '0.5rem 0.75rem' }, children: '일치하는 컴포넌트가 없습니다.' })
        : hits.map((item) => jsx(PaletteItem, { key: item.category + '/' + item.name, label: item.label + ' · ' + item.category, onClick: () => onAdd(item) }))
    } else {
      const primaryKeys = PALETTE_PRIMARY.map(([key]) => key)
      const groups = PALETTE_PRIMARY
        .map(([key, label]) => ({ key, label, items: flat.filter((item) => item.category === key) }))
        .filter((group) => group.items.length > 0)
      const rest_ = flat.filter((item) => !primaryKeys.includes(item.category)).map((item) => ({ ...item, label: item.label + ' · ' + item.category }))
      body = [
        ...groups.map((group) => jsx(PaletteGroup, { key: group.key, label: group.label, items: group.items, open: true, onAdd })),
        rest_.length > 0 ? jsx(PaletteGroup, { key: '_rest', label: '기타', items: rest_, open: false, onAdd }) : null,
      ]
    }
  }
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }, children: [
    jsx(SectionLabel, { children: '컴포넌트' }),
    jsx('div', { style: { padding: '0 0.75rem 0.5rem' }, children: jsx(SearchField, { 'aria-label': '컴포넌트 검색', placeholder: '검색', value: query, onChange: setQuery }) }),
    jsx('div', { style: { overflowY: 'auto', flex: 1, paddingBottom: '0.5rem' }, children: body }),
  ] })
}

// ---------- 캔버스 ----------
function nodeHeight(node, connected) {
  const inputs = visibleInputs(node, connected)
  return HEADER_HEIGHT + (inputs.length + nodeOutputs(node).length) * ROW_HEIGHT + 5
}

// Shift·Cmd·Ctrl 클릭은 선택에 더하거나 뺀다.
const isAdditive = (event) => event.shiftKey || event.metaKey || event.ctrlKey

function NodeBox({ node, origin, selected, inherited, connected, onSelect, onStartDrag, onStartConnect }) {
  const inputs = visibleInputs(node, connected)
  const outputs = nodeOutputs(node)
  const pos = node.position || { x: 0, y: 0 }
  return jsxs('div', {
    onPointerDown: (event) => event.stopPropagation(),
    onClick: (event) => { event.stopPropagation(); onSelect(node.id, isAdditive(event)) },
    style: {
      position: 'absolute', left: pos.x + origin.x, top: pos.y + origin.y, width: NODE_WIDTH,
      border: '1px solid ' + (selected ? T.accent : T.border),
      boxShadow: selected ? '0 0 0 2px color-mix(in srgb, ' + T.accent + ' 35%, transparent)' : '0 1px 2px rgba(0,0,0,0.08)',
      borderRadius: '0.5rem', background: T.card, color: T.text, fontSize: '0.75rem', userSelect: 'none',
    },
    children: [
      jsx('div', {
        onPointerDown: (event) => onStartDrag(node.id, event),
        style: { height: HEADER_HEIGHT, boxSizing: 'border-box', padding: '0 0.6rem', display: 'flex', alignItems: 'center', fontWeight: 600, cursor: 'grab', background: T.cardHeader, borderBottom: '1px solid ' + T.border, borderTopLeftRadius: '0.5rem', borderTopRightRadius: '0.5rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
        children: [
          jsx('span', { style: { whiteSpace: 'nowrap', flex: 'none' }, children: nodeTitle(node) }),
          ...nodeBadges(node, inherited).map((badge) => jsx('span', {
            key: badge.text,
            style: {
              marginLeft: '0.35rem', padding: '0 0.4rem', borderRadius: '999px', fontSize: '0.6875rem', lineHeight: '1.1rem',
              flex: '0 1 auto', minWidth: '1.5rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              fontWeight: badge.strong ? 600 : 400,
              background: badge.strong ? 'color-mix(in srgb, ' + T.accent + ' 18%, transparent)' : 'transparent',
              color: badge.strong ? T.accent : T.textFaint,
              border: badge.strong ? 'none' : '1px solid ' + T.border,
            },
            children: badge.text,
          })),
        ],
      }),
      jsx('div', { style: { padding: '0 0 0.3rem 0', minHeight: ((inputs.length + outputs.length) * ROW_HEIGHT) + 'px' }, children: [
        ...inputs.map(({ key, field }) => jsxs('div', {
          key: 'in-' + key, style: { display: 'flex', alignItems: 'center', gap: '0.35rem', height: ROW_HEIGHT, marginLeft: '-5px' },
          children: [
            jsx('span', {
              'data-port-node': node.id, 'data-port-field': key, 'data-port-kind': 'input',
              style: { width: 9, height: 9, borderRadius: '50%', background: T.accent, border: '1px solid ' + T.card, flex: 'none' },
            }),
            jsx('span', { style: { color: T.textSoft, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: field.display_name || key }),
          ],
        })),
        ...outputs.map((output) => jsxs('div', {
          key: 'out-' + output.name, style: { display: 'flex', alignItems: 'center', gap: '0.35rem', height: ROW_HEIGHT, justifyContent: 'flex-end', marginRight: '-5px' },
          children: [
            jsx('span', { style: { color: T.textSoft }, children: output.display_name || output.name }),
            jsx('span', {
              'data-port-node': node.id, 'data-port-output': output.name, 'data-port-kind': 'output',
              onPointerDown: (event) => onStartConnect(node.id, output, event),
              style: { width: 9, height: 9, borderRadius: '50%', background: T.good, border: '1px solid ' + T.card, flex: 'none', cursor: 'crosshair' },
            }),
          ],
        })),
      ] }),
    ],
  })
}

function Canvas({ flowData, origin, selectedIds, onSelectNode, onBoxSelect, onStartDrag, onStartConnect, pendingLine }) {
  const [marquee, setMarquee] = useState(null)
  const [spaceDown, setSpaceDown] = useState(false)
  const [panning, setPanning] = useState(false)
  const innerRef = useRef(null)
  const scrollRef = useRef(null)
  // Space를 누르고 있으면 빈 곳 끌기가 사각형 선택 대신 화면 이동이 된다. 입력 칸에서 친 Space는 무시한다.
  useEffect(() => {
    const typing = (event) => {
      const tag = event.target && event.target.tagName
      return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'BUTTON' || (event.target && event.target.isContentEditable)
    }
    const down = (event) => { if (event.code === 'Space' && !typing(event)) { event.preventDefault(); setSpaceDown(true) } }
    const up = (event) => { if (event.code === 'Space') setSpaceDown(false) }
    const blur = () => setSpaceDown(false)
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', blur)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.removeEventListener('blur', blur) }
  }, [])
  const startPan = (event) => {
    const scroller = scrollRef.current
    if (!scroller) return
    event.preventDefault()
    const start = { x: event.clientX, y: event.clientY, left: scroller.scrollLeft, top: scroller.scrollTop }
    setPanning(true)
    const onMove = (moveEvent) => {
      scroller.scrollLeft = start.left - (moveEvent.clientX - start.x)
      scroller.scrollTop = start.top - (moveEvent.clientY - start.y)
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      setPanning(false)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }
  const onBackgroundPointerDown = (event) => {
    // 가운데 버튼 끌기, 또는 Space를 누른 채 끌기는 화면 이동이다.
    if (event.button === 1 || (event.button === 0 && spaceDown)) { startPan(event); return }
    if (event.button !== 0 || !innerRef.current) return
    const additive = isAdditive(event)
    const rect = innerRef.current.getBoundingClientRect()
    const start = { x: event.clientX - rect.left, y: event.clientY - rect.top }
    let box = null
    const onMove = (moveEvent) => {
      const now = innerRef.current.getBoundingClientRect()
      const end = { x: moveEvent.clientX - now.left, y: moveEvent.clientY - now.top }
      if (!box && Math.abs(end.x - start.x) < 4 && Math.abs(end.y - start.y) < 4) return
      box = { x1: Math.min(start.x, end.x), y1: Math.min(start.y, end.y), x2: Math.max(start.x, end.x), y2: Math.max(start.y, end.y) }
      setMarquee(box)
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      setMarquee(null)
      if (!box) { if (!additive) onSelectNode(null, false); return }
      // 사각형과 겹치는 노드를 고른다(화면 좌표 = position + origin).
      const hit = flowData.nodes.filter((node) => {
        const p = node.position || { x: 0, y: 0 }
        const x = p.x + origin.x
        const y = p.y + origin.y
        return x < box.x2 && x + NODE_WIDTH > box.x1 && y < box.y2 && y + nodeHeight(node, connected) > box.y1
      }).map((node) => node.id)
      onBoxSelect(hit, additive)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }
  const nodes = flowData.nodes
  const inheritedMap = inheritedRoles(flowData)
  const connected = connectedInputs(flowData)
  const byId = {}
  nodes.forEach((node) => { byId[node.id] = node })
  const edgeLines = flowData.edges.map((edge) => {
    const source = byId[edge.source]
    const target = byId[edge.target]
    if (!source || !target) return null
    const sourceHandle = decodeHandle(edge.sourceHandle || '')
    const targetHandle = decodeHandle(edge.targetHandle || '')
    if (!sourceHandle || !targetHandle) return null
    const from = portPoint(source, 'output', nodePortRow(source, 'output', sourceHandle.name, connected), origin)
    const to = portPoint(target, 'input', nodePortRow(target, 'input', targetHandle.fieldName, connected), origin)
    return { id: edge.id, d: bezier(from, to) }
  }).filter(Boolean)
  const maxX = nodes.reduce((max, node) => Math.max(max, ((node.position && node.position.x) || 0) + origin.x + NODE_WIDTH + CANVAS_SCROLL_ROOM), 0)
  const maxY = nodes.reduce((max, node) => Math.max(max, ((node.position && node.position.y) || 0) + origin.y + nodeHeight(node, connected) + CANVAS_SCROLL_ROOM), 0)
  return jsxs('div', {
    ref: scrollRef, className: 'perdix-wf-canvas',
    style: {
      position: 'relative', flex: 1, minWidth: 0, overflow: 'scroll', background: T.surface,
      backgroundImage: 'radial-gradient(' + T.strokeSoft + ' 1px, transparent 1px)', backgroundSize: '20px 20px',
      cursor: panning ? 'grabbing' : spaceDown ? 'grab' : undefined,
    },
    onPointerDown: onBackgroundPointerDown,
    // 가운데 버튼 끌기에서 브라우저 자동 스크롤 표시가 뜨지 않게 막는다.
    onMouseDown: (event) => { if (event.button === 1) event.preventDefault() },
    children: [jsx('style', { children: CANVAS_SCROLLBAR_CSS }), jsxs('div', { ref: innerRef, style: { position: 'relative', width: maxX, height: maxY, minWidth: '100%', minHeight: '100%' }, children: [
      jsxs('svg', { style: { position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }, children: [
        edgeLines.map((line) => jsx('path', { key: line.id, d: line.d, stroke: T.good, strokeWidth: 1.5, fill: 'none', opacity: 0.8 })),
        pendingLine ? jsx('path', { d: bezier(pendingLine.from, pendingLine.to), stroke: T.accent, strokeWidth: 1.5, strokeDasharray: '4 3', fill: 'none' }) : null,
      ] }),
      nodes.map((node) => jsx(NodeBox, {
        key: node.id, node, origin, selected: selectedIds.includes(node.id), inherited: inheritedMap[node.id], connected,
        onSelect: onSelectNode, onStartDrag, onStartConnect,
      })),
      marquee ? jsx('div', { style: {
        position: 'absolute', left: marquee.x1, top: marquee.y1, width: marquee.x2 - marquee.x1, height: marquee.y2 - marquee.y1,
        border: '1px dashed ' + T.accent, background: 'color-mix(in srgb, ' + T.accent + ' 10%, transparent)', pointerEvents: 'none',
      } }) : null,
    ] })],
  })
}

// ---------- 페이지 ----------
function Centered({ children }) {
  return jsx('div', { style: { flex: 1, display: 'grid', placeItems: 'center', padding: '1rem', textAlign: 'center', fontSize: '0.8125rem', color: T.textFaint }, children })
}

// 오른쪽 패널 너비는 왼쪽 경계선을 끌어 바꾼다. 정한 값은 다음에 열 때도 쓴다.
const RIGHT_PANEL_KEY = 'perdix.workflow.rightPanelWidth'
const RIGHT_PANEL_DEFAULT = 384
const RIGHT_PANEL_MIN = 260
const clampPanelWidth = (width) => Math.round(Math.min(Math.max(width, RIGHT_PANEL_MIN), Math.max(RIGHT_PANEL_MIN, window.innerWidth * 0.7)))
function savedPanelWidth() {
  try {
    const saved = Number(window.localStorage.getItem(RIGHT_PANEL_KEY))
    return saved > 0 ? clampPanelWidth(saved) : RIGHT_PANEL_DEFAULT
  } catch (error) { return RIGHT_PANEL_DEFAULT }
}

function PanelResizer({ width, onResize }) {
  const [active, setActive] = useState(false)
  const onPointerDown = (event) => {
    if (event.button !== 0) return
    event.preventDefault()
    const startX = event.clientX
    const startWidth = width
    let latest = width
    setActive(true)
    const onMove = (moveEvent) => { latest = clampPanelWidth(startWidth + (startX - moveEvent.clientX)); onResize(latest) }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      setActive(false)
      try { window.localStorage.setItem(RIGHT_PANEL_KEY, String(latest)) } catch (error) { /* 저장 못 해도 이번 화면에는 적용된다 */ }
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }
  return jsx('div', {
    title: '끌어서 너비 조절 · 두 번 누르면 기본 너비',
    onPointerDown,
    onDoubleClick: () => { onResize(RIGHT_PANEL_DEFAULT); try { window.localStorage.removeItem(RIGHT_PANEL_KEY) } catch (error) { /* 무시 */ } },
    style: { position: 'absolute', left: -3, top: 0, bottom: 0, width: 6, cursor: 'col-resize', zIndex: 2,
      background: active ? T.accent : 'transparent', opacity: active ? 0.6 : 1 },
  })
}

const columnStyle = (side) => ({
  width: side === 'left' ? '13rem' : RIGHT_PANEL_DEFAULT, flex: 'none', minHeight: 0, display: 'flex', flexDirection: 'column',
  background: T.panel, [side === 'left' ? 'borderRight' : 'borderLeft']: '1px solid ' + T.border,
})

function WorkflowEditor({ langflowEnabled, roles }) {
  const [flows, setFlows] = useState([])
  const [loadError, setLoadError] = useState('')
  const [selectedFlowId, setSelectedFlowId] = useState('')
  const [flowData, setFlowData] = useState(null)
  const [origin, setOrigin] = useState({ x: 0, y: 0 })
  const [components, setComponents] = useState(null)
  const [selectedIds, setSelectedIds] = useState([])
  const draggedRef = useRef(false)
  const [status, setStatus] = useState('')
  const [pendingLine, setPendingLine] = useState(null)
  // 새 플로우 이름 입력: null이면 닫힘, 문자열이면 헤더에 인라인 입력란이 열린 상태.
  const [newFlowName, setNewFlowName] = useState(null)
  // 플로우 삭제 확인: true면 헤더에 "지울까요?"와 삭제·취소 버튼이 열린 상태.
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [panelWidth, setPanelWidth] = useState(savedPanelWidth)
  const connectingRef = useRef(null)

  const loadFlows = () => rest('/langflow/flows').then((response) => {
    if (response.ok) setFlows(response.result)
    else setLoadError('Langflow가 꺼져 있거나 응답하지 않습니다.')
    return response
  })

  useEffect(() => {
    if (!langflowEnabled) return
    loadFlows()
    rest('/langflow/components').then((response) => { if (response.ok) setComponents(response.result) })
  }, [langflowEnabled])

  const selectFlow = (id) => {
    setConfirmDelete(false)
    setSelectedFlowId(id)
    setSelectedIds([])
    setFlowData(null)
    if (!id) { setStatus(''); return }
    setStatus('불러오는 중…')
    rest('/langflow/flow?id=' + encodeURIComponent(id)).then((response) => {
      if (response.ok) { setOrigin(canvasOrigin(response.result.data.nodes)); setFlowData(response.result.data); setStatus('') }
      else setStatus('불러오지 못했습니다: ' + (response.detail || response.error))
    })
  }

  const createFlow = () => {
    const name = (newFlowName || '').trim()
    if (!name) { setStatus('플로우 이름을 입력하세요.'); return }
    setStatus('만드는 중…')
    rest('/langflow/flow', { method: 'POST', body: { name } }).then((response) => {
      if (!response.ok) { setStatus('만들지 못했습니다: ' + (response.detail || response.error)); return }
      setNewFlowName(null)
      // 목록을 다시 받아 새 플로우가 선택 목록에 들어간 뒤 바로 연다(빈 캔버스).
      loadFlows().then(() => selectFlow(response.result.id))
    })
  }

  const deleteFlow = () => {
    const flow = flows.find((item) => item.id === selectedFlowId)
    const name = flow ? flow.name : selectedFlowId
    setConfirmDelete(false)
    setStatus('지우는 중…')
    rest('/langflow/flow?id=' + encodeURIComponent(selectedFlowId), { method: 'DELETE' }).then((response) => {
      if (!response.ok) { setStatus('지우지 못했습니다: ' + (response.detail || response.error)); return }
      const aliases = (response.result && response.result.aliases) || []
      loadFlows().then(() => {
        selectFlow('')
        // 별칭은 사용자 설정 파일에 있어 여기서 고치지 않는다. 끊긴 별칭을 알려 직접 지우게 한다.
        setStatus(aliases.length === 0
          ? '"' + name + '" 플로우를 지웠습니다.'
          : '"' + name + '" 플로우를 지웠습니다. 채팅 별칭 ' + aliases.map((alias) => '/flow ' + alias).join(', ') + '은(는) 이제 동작하지 않으니 langflow.json에서 지우세요.')
      })
    })
  }

  // 예전 Perdix Agent 노드(새 칸이 없는 코드)는 열 때 최신 컴포넌트로 바꿔 둔다. 저장해야 Langflow에 남는다.
  useEffect(() => {
    if (!flowData || !components) return
    const result = upgradePerdixNodes(flowData, components)
    if (result.count === 0) return
    setFlowData(result.data)
    setStatus('Perdix 노드 ' + result.count + '개를 최신 버전으로 바꿨습니다. 저장하면 반영됩니다.')
  }, [flowData, components])

  const mutateFlow = (updater) => setFlowData((current) => (current ? updater(current) : current))

  const onSelectNode = (nodeId, additive) => {
    if (draggedRef.current) { draggedRef.current = false; return } // 끌기 끝의 click은 선택을 바꾸지 않는다
    if (nodeId === null) { setSelectedIds([]); return }
    if (!additive) { setSelectedIds([nodeId]); return }
    setSelectedIds((current) => current.includes(nodeId) ? current.filter((id) => id !== nodeId) : [...current, nodeId])
  }

  const onBoxSelect = (ids, additive) => setSelectedIds((current) => additive ? [...new Set([...current, ...ids])] : ids)

  // 제목을 끌면 선택된 노드 전체가 함께 움직인다. 선택 밖 노드를 끌면 그 노드만 선택해 옮긴다.
  const onStartDrag = (nodeId, event) => {
    event.stopPropagation()
    if (event.button !== 0 || isAdditive(event)) return
    draggedRef.current = false
    const ids = selectedIds.includes(nodeId) ? selectedIds : [nodeId]
    if (!selectedIds.includes(nodeId)) setSelectedIds([nodeId])
    const starts = {}
    flowData.nodes.forEach((node) => { if (ids.includes(node.id)) starts[node.id] = node.position || { x: 0, y: 0 } })
    const startX = event.clientX
    const startY = event.clientY
    const onMove = (moveEvent) => {
      const dx = moveEvent.clientX - startX
      const dy = moveEvent.clientY - startY
      if (Math.abs(dx) + Math.abs(dy) > 2) draggedRef.current = true
      mutateFlow((data) => ({
        ...data, nodes: data.nodes.map((node) => starts[node.id] ? { ...node, position: { x: starts[node.id].x + dx, y: starts[node.id].y + dy } } : node),
      }))
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      // 움직이지 않았으면 이어지는 click이 평소대로 선택하게 둔다.
      if (!draggedRef.current) return
      setTimeout(() => { draggedRef.current = false }, 0)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  const onChangeField = (nodeId, fieldKey, value) => mutateFlow((data) => ({
    ...data,
    nodes: data.nodes.map((node) => {
      if (node.id !== nodeId) return node
      const template = { ...node.data.node.template, [fieldKey]: { ...node.data.node.template[fieldKey], value } }
      return { ...node, data: { ...node.data, node: { ...node.data.node, template } } }
    }),
  }))

  const deleteNodes = (ids) => {
    if (ids.length === 0) return
    mutateFlow((data) => ({
      ...data,
      nodes: data.nodes.filter((node) => !ids.includes(node.id)),
      edges: data.edges.filter((edge) => !ids.includes(edge.source) && !ids.includes(edge.target)),
    }))
    setSelectedIds([])
    setStatus('노드 ' + ids.length + '개를 지웠습니다. 저장해야 반영됩니다.')
  }
  const onDeleteNode = (nodeId) => deleteNodes([nodeId])

  // Delete/Backspace는 선택 노드 삭제, Esc는 선택 해제, Cmd/Ctrl+A는 전체 선택.
  // 입력란에 포커스가 있으면 글자 편집이므로 건드리지 않는다.
  useEffect(() => {
    const onKeyDown = (event) => {
      const target = event.target
      const tag = target && target.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (target && target.isContentEditable)) return
      if (!flowData) return
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedIds.length > 0) {
        event.preventDefault(); deleteNodes(selectedIds)
      } else if (event.key === 'Escape') {
        setSelectedIds([])
      } else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'a') {
        event.preventDefault(); setSelectedIds(flowData.nodes.map((node) => node.id))
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [flowData, selectedIds])

  const onAddComponent = (item) => {
    // 플로우가 열려 있지 않으면 노드를 넣을 곳이 없다. 조용히 무시하면 클릭이 안 먹는 것처럼
    // 보이므로 헤더 상태 문구로 알린다.
    if (!flowData) { setStatus('먼저 플로우를 선택하세요.'); return }
    const id = genId(item.name)
    const count = flowData ? flowData.nodes.length : 0
    // 새 노드는 보이는 영역의 왼쪽 위에서 시작한다(origin을 빼서 캔버스 좌표로 되돌린다).
    const node = {
      id, type: 'genericNode',
      position: { x: CANVAS_MARGIN + (count % 6) * 40 - origin.x, y: CANVAS_MARGIN + (count % 6) * 30 - origin.y },
      data: { id, type: item.name, node: JSON.parse(JSON.stringify(item.template)) },
    }
    mutateFlow((data) => ({ ...data, nodes: [...data.nodes, node] }))
    setSelectedIds([id])
  }

  const onStartConnect = (nodeId, output, event) => {
    event.stopPropagation()
    const node = flowData.nodes.find((item) => item.id === nodeId)
    const from = portPoint(node, 'output', nodePortRow(node, 'output', output.name, connectedInputs(flowData)), origin)
    const startX = event.clientX
    const startY = event.clientY
    connectingRef.current = { nodeId, output }
    setPendingLine({ from, to: from })
    // 캔버스가 스크롤될 수 있어 clientX/Y를 그대로 쓰면 어긋난다 — 시작점 기준 델타로 옮긴다.
    const onMove = (moveEvent) => {
      setPendingLine({ from, to: { x: from.x + (moveEvent.clientX - startX), y: from.y + (moveEvent.clientY - startY) } })
    }
    const onUp = (upEvent) => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      const target = document.elementFromPoint(upEvent.clientX, upEvent.clientY)
      const portTarget = target && target.closest ? target.closest('[data-port-kind="input"]') : null
      if (portTarget && connectingRef.current) {
        finishConnect(connectingRef.current.nodeId, connectingRef.current.output, portTarget.getAttribute('data-port-node'), portTarget.getAttribute('data-port-field'))
      }
      connectingRef.current = null
      setPendingLine(null)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  const finishConnect = (sourceId, output, targetId, targetField) => {
    if (!targetId || !targetField || targetId === sourceId) return
    mutateFlow((data) => {
      const sourceNode = data.nodes.find((node) => node.id === sourceId)
      const targetNode = data.nodes.find((node) => node.id === targetId)
      if (!sourceNode || !targetNode) return data
      const targetField_ = targetNode.data.node.template[targetField]
      const sourceHandleObj = { dataType: sourceNode.data.type, id: sourceId, name: output.name, output_types: output.types || [] }
      const targetHandleObj = { fieldName: targetField, id: targetId, inputTypes: (targetField_ && targetField_.input_types) || [], type: (targetField_ && targetField_.type) || 'str' }
      const edge = {
        id: genId('edge'), source: sourceId, target: targetId,
        sourceHandle: encodeHandle(sourceHandleObj), targetHandle: encodeHandle(targetHandleObj),
        data: { sourceHandle: sourceHandleObj, targetHandle: targetHandleObj },
      }
      return { ...data, edges: [...data.edges.filter((item) => !(item.target === targetId && item.targetHandle === edge.targetHandle)), edge] }
    })
  }

  const saveFlow = () => {
    setStatus('저장 중…')
    rest('/langflow/flow?id=' + encodeURIComponent(selectedFlowId), { method: 'PATCH', body: { data: flowData } }).then((response) => {
      setStatus(response.ok ? '저장했습니다.' : '저장하지 못했습니다: ' + (response.detail || response.error))
    })
  }

  let body
  if (langflowEnabled === null) {
    body = jsx(Centered, { children: '불러오는 중…' })
  } else if (!langflowEnabled) {
    body = jsx(Centered, { children: 'Langflow 연동이 꺼져 있습니다. Perdix 서버를 --langflow-config로 실행하세요.' })
  } else if (loadError) {
    body = jsx(Centered, { children: loadError })
  } else {
    const selectedNode = flowData && selectedIds.length === 1 ? flowData.nodes.find((node) => node.id === selectedIds[0]) : null
    let rightPanel
    if (selectedNode) rightPanel = jsx(PropertyPanel, { node: selectedNode, onChangeField, onDeleteNode })
    else if (flowData && selectedIds.length > 1) rightPanel = jsx(MultiSelectPanel, { count: selectedIds.length, onDelete: () => deleteNodes(selectedIds), onClear: () => setSelectedIds([]) })
    else rightPanel = jsx(RolesPanel, { roles })
    body = jsxs('div', { style: { display: 'flex', flex: 1, minHeight: 0 }, children: [
      // 플로우가 없으면 팔레트를 흐리게 해 지금은 쓸 수 없음을 보인다(클릭하면 상태 문구가 뜬다).
      jsx('aside', { style: { ...columnStyle('left'), opacity: flowData ? 1 : 0.5 }, children: jsx(ComponentPalette, { components, onAdd: onAddComponent }) }),
      flowData
        ? jsx(Canvas, { flowData, origin, selectedIds, onSelectNode, onBoxSelect, onStartDrag, onStartConnect, pendingLine })
        : jsx(Centered, { children: flows.length === 0 ? 'Langflow에 플로우가 없습니다. 위 헤더의 "+ 새 플로우"로 만드세요.' : '위 헤더에서 플로우를 선택하면 왼쪽 컴포넌트를 캔버스에 추가할 수 있습니다.' }),
      jsxs('aside', { style: { ...columnStyle('right'), position: 'relative', width: panelWidth }, children: [
        jsx(PanelResizer, { width: panelWidth, onResize: setPanelWidth }),
        rightPanel,
      ] }),
    ] })
  }

  const colorScheme = document.documentElement.classList.contains('dark') ? 'dark' : 'light'
  return jsxs('div', { style: { position: 'relative', display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: T.surface, color: T.text, colorScheme }, children: [
    jsxs('header', { style: { display: 'flex', flex: 'none', alignItems: 'center', gap: '0.5rem', padding: '0.5rem 1rem', borderBottom: '1px solid ' + T.border }, children: [
      jsx('h1', { style: { fontSize: '0.875rem', fontWeight: 600, margin: 0 }, children: 'Workflow' }),
      langflowEnabled ? jsxs('select', {
        value: selectedFlowId, onChange: (event) => selectFlow(event.target.value), style: { ...selectStyle, width: 'auto', minWidth: '12rem' },
        children: [jsx('option', { value: '', children: '플로우 선택' }),
          ...flows.map((flow) => jsx('option', { key: flow.id, value: flow.id, children: flow.name }))],
      }) : null,
      langflowEnabled && newFlowName === null
        ? jsx(Button, { size: 'xs', variant: 'ghost', onClick: () => setNewFlowName(''), children: '+ 새 플로우' })
        : null,
      langflowEnabled && newFlowName !== null
        ? jsxs('form', {
            style: { display: 'flex', alignItems: 'center', gap: '0.35rem' },
            onSubmit: (event) => { event.preventDefault(); createFlow() },
            children: [
              jsx(Input, { autoFocus: true, placeholder: '새 플로우 이름', value: newFlowName, 'aria-label': '새 플로우 이름',
                onChange: (event) => setNewFlowName(event.target.value),
                onKeyDown: (event) => { if (event.key === 'Escape') setNewFlowName(null) },
                style: { width: '12rem' } }),
              jsx(Button, { size: 'xs', type: 'submit', children: '만들기' }),
              jsx(Button, { size: 'xs', variant: 'ghost', type: 'button', onClick: () => setNewFlowName(null), children: '취소' }),
            ],
          })
        : null,
      langflowEnabled && selectedFlowId && newFlowName === null && !confirmDelete
        ? jsx(Button, { size: 'xs', variant: 'ghost', title: '선택한 플로우를 Langflow에서 지웁니다', onClick: () => setConfirmDelete(true), children: '플로우 삭제' })
        : null,
      confirmDelete
        ? jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: '0.35rem' }, children: [
            jsx('span', { style: { fontSize: '0.75rem', color: T.text }, children:
              '"' + ((flows.find((item) => item.id === selectedFlowId) || {}).name || '') + '" 플로우를 지울까요? 되돌릴 수 없습니다.' }),
            jsx(Button, { size: 'xs', variant: 'destructive', onClick: deleteFlow, children: '삭제' }),
            jsx(Button, { size: 'xs', variant: 'ghost', onClick: () => setConfirmDelete(false), children: '취소' }),
          ] })
        : null,
      jsx('span', { style: { fontSize: '0.75rem', color: T.textFaint, marginLeft: '0.25rem' }, children: status }),
      jsx('div', { style: { marginLeft: 'auto' }, children: langflowEnabled ? jsx(Button, { size: 'sm', disabled: !flowData, onClick: saveFlow, children: '저장' }) : null }),
    ] }),
    body,
  ] })
}

function Page() {
  const current = useValue(state)
  const [langflowEnabled, setLangflowEnabled] = useState(null)
  useEffect(() => {
    rest('/langflow-url').then((info) => setLangflowEnabled(!!(info && info.url))).catch(() => setLangflowEnabled(false))
  }, [])
  return jsx(WorkflowEditor, { langflowEnabled, roles: current.roles })
}

export default {
  id: 'workflow',
  name: 'Workflow',
  description: 'Perdix 역할 목록과 Langflow 노드 편집기를 보여줍니다.',
  register(ctx) {
    rest = ctx.rest
    void refresh()
    ctx.register({ id: 'page', area: 'routes', title: 'Workflow', data: { path: PATH }, render: () => jsx(Page, {}) })
    ctx.register({ id: 'nav', area: 'sidebar.nav', data: { codicon: 'type-hierarchy', label: 'Workflow', path: PATH } })
  }
}
`;
