export const PERDIX_FEATURES = {
  globalModel: false,
  providerKeys: false,
  providerDisconnect: false,
  reasoning: false,
  sessionDelete: false,
  sessionEdit: false,
  background: false,
  agents: false,
  processes: false,
  goalDisplay: false,
  learning: false,
  skills: false,
  plugins: false,
  tools: false,
  reload: false,
  browser: false,
  rollback: false,
  voice: false,
  pet: false,
  billing: false,
  images: false,
  pathCompletion: false,
  shell: false,
  settings: false,
  battery: false,
  setup: false,
  update: false,
} as const;

export type PerdixFeature = keyof typeof PERDIX_FEATURES;

export function featureEnabled(feature: PerdixFeature, perdix = process.env.HERMES_TUI_PERDIX === '1'): boolean {
  return !perdix || PERDIX_FEATURES[feature];
}

const COMMAND_FEATURES: Readonly<Record<string, PerdixFeature>> = {
  title: 'sessionEdit', undo: 'sessionEdit', retry: 'sessionEdit', branch: 'sessionEdit', compress: 'sessionEdit',
  bg: 'background', btw: 'background', steer: 'background',
  agents: 'agents', replay: 'agents', 'replay-diff': 'agents', journey: 'learning',
  skills: 'skills', 'reload-skills': 'skills', plugins: 'plugins', tools: 'tools',
  reload: 'reload', 'reload-mcp': 'reload', browser: 'browser', rollback: 'rollback',
  voice: 'voice', wake: 'voice', pet: 'pet', topup: 'billing', subscription: 'billing',
  image: 'images', paste: 'images', personality: 'settings', skin: 'settings', indicator: 'settings',
  yolo: 'settings', reasoning: 'reasoning', fast: 'settings', busy: 'settings', verbose: 'settings',
  mouse: 'settings', density: 'settings', details: 'settings', focus: 'settings', statusbar: 'settings',
  battery: 'battery', setup: 'setup', update: 'update',
};

export function commandEnabled(name: string, perdix?: boolean): boolean {
  const feature = COMMAND_FEATURES[name];
  return feature === undefined || featureEnabled(feature, perdix);
}

const METHOD_FEATURES: Readonly<Record<string, PerdixFeature>> = {
  'model.save_key': 'providerKeys', 'model.disconnect': 'providerDisconnect',
  'session.delete': 'sessionDelete', 'session.title': 'sessionEdit', 'session.undo': 'sessionEdit',
  'session.branch': 'sessionEdit', 'session.compress': 'sessionEdit',
  'session.active_list': 'background', 'prompt.background': 'background', 'prompt.btw': 'background', 'session.steer': 'background',
  'session.control.read': 'goalDisplay', 'subagent.list': 'agents', 'delegation.status': 'agents',
  'process.list': 'processes', 'process.stop': 'processes',
  'clipboard.paste': 'images', 'paste.collapse': 'images', 'input.detect_drop': 'images',
  'complete.path': 'pathCompletion', 'shell.exec': 'shell', 'system.battery': 'battery',
};

const PREFIX_FEATURES: Readonly<Record<string, PerdixFeature>> = {
  subagent: 'agents', delegation: 'agents', spawn_tree: 'agents', learning: 'learning',
  skills: 'skills', plugins: 'plugins', tools: 'tools', reload: 'reload', browser: 'browser',
  rollback: 'rollback', voice: 'voice', wake: 'voice', pet: 'pet', billing: 'billing',
  subscription: 'billing', image: 'images', connection: 'setup', connectors: 'setup',
};

export function requestEnabled(method: string, params: Record<string, unknown>, perdix?: boolean): boolean {
  if (method === 'config.set') {
    if (params.key === 'model') {
      const value = typeof params.value === 'string' ? params.value : '';
      if (/(?:^|\s)--global(?:\s|$)/.test(value) && !featureEnabled('globalModel', perdix)) return false;
      if (/(?:^|\s)--reasoning(?:\s|$)/.test(value) && !featureEnabled('reasoning', perdix)) return false;
      return true;
    }
    if (params.key === 'theme' || params.key === 'display.tui_theme' || params.key === 'mode') return true;
    return featureEnabled(params.key === 'battery' ? 'battery' : params.key === 'reasoning' ? 'reasoning' : 'settings', perdix);
  }
  const feature = METHOD_FEATURES[method] ?? PREFIX_FEATURES[method.split('.')[0] ?? ''];
  return feature === undefined || featureEnabled(feature, perdix);
}
