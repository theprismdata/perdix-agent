import { withInkSuspended } from '@hermes/ink'
import type { SlashRunCtx } from './slash/types.js'
import { patchUiState } from './uiStore.js'

export async function runPerdixAuth(name: string, argument: string, ctx: SlashRunCtx): Promise<void> {
  if (ctx.session.guardBusySessionSwitch('인증 변경')) return
  const words = argument.trim().split(/\s+/).filter(Boolean)
  const action = name === 'auth' ? words.shift() ?? 'status' : name
  if (!['login', 'logout', 'status'].includes(action)) {
    ctx.transcript.sys('사용법: /login [openai|anthropic] · /logout [openai|anthropic] · /auth status [openai|anthropic]')
    return
  }
  if (words[0] === '--provider') words.shift()
  const selected = words[0] === 'claude' ? 'anthropic' : words[0]
  if (words.length > 1 || (selected !== undefined && selected !== 'openai' && selected !== 'anthropic')) {
    ctx.transcript.sys('공급자는 openai 또는 anthropic입니다. API 키는 채팅창에 입력하지 마세요.')
    return
  }
  patchUiState({ status: '인증 진행 중…' })
  try {
    let result: { output: string } | undefined
    await withInkSuspended(async () => {
      result = await ctx.gateway.gw.request<{ output: string }>('perdix.auth', {
        session_id: ctx.sid, action, ...(selected ? { provider: selected } : {})
      }, 600_000)
    })
    if (!ctx.stale() && result) ctx.transcript.page(result.output, 'Perdix 인증')
  } catch (error) {
    if (error instanceof Error) ctx.transcript.sys(`인증 연결 실패: ${error.message}`)
    else throw error
  } finally { patchUiState({ status: 'ready' }) }
}
