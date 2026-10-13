import { messages } from '../i18n/runtime.js'
import { isMac, isRemoteShell } from '../lib/platform.js'
import { featureEnabled } from '../perdixFeatures.js'

const action = isMac ? 'Cmd' : 'Ctrl'
const paste = isMac ? 'Cmd' : 'Alt'

/** Hotkey table `[chord, description]`, resolved against the active language at call time. */
export function hotkeys(): [string, string][] {
  const h = messages().hints

  const copyHotkeys: [string, string][] = isMac
    ? [
        ['Cmd+C', h.copySelection],
        ['Ctrl+C', h.ctrlCMac]
      ]
    : isRemoteShell()
      ? [
          ['Cmd+C', h.copySelectionForwarded],
          ['Ctrl+C', h.ctrlC]
        ]
      : [['Ctrl+C', h.ctrlC]]

  return [
    ...copyHotkeys,
    [action + '+D', h.exit],
    [action + '+G / Alt+G', h.openEditor],
    [action + '+L', h.redraw],
    ...(featureEnabled('images') ? [[paste + '+V / /paste', h.paste] satisfies [string, string]] : []),
    ['Esc Esc', h.discardDraft],
    ['Tab', h.applyCompletion],
    ['↑/↓', h.arrows],
    ['Ctrl+X', h.sessionSwitcher],
    ...(featureEnabled('agents') ? [
      ['Ctrl+T', h.expandAgents] satisfies [string, string],
      ['Ctrl+R / F7', h.collapseAgents] satisfies [string, string]
    ] : []),
    ['Ctrl+O', h.modelPicker],
    [action + '+A/E', h.homeEnd],
    [action + '+Z / ' + action + '+Y', h.undoRedo],
    [action + '+W', h.deleteWord],
    [action + '+U/K', h.killLine],
    [action + '+←/→', h.jumpWord],
    ['Home/End', h.lineStartEnd],
    ['Shift+Enter / Alt+Enter', h.newline],
    ['\\+Enter', h.continuation],
    ...(featureEnabled('shell') ? [
      ['!<cmd>', h.shell] satisfies [string, string],
      ['{!<cmd>}', h.interpolate] satisfies [string, string]
    ] : [])
  ]
}
