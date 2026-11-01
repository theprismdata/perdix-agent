/**
 * Hermes Desktop 런타임 플러그인 `perdix-settings`(`$HERMES_HOME/desktop-plugins/perdix-settings/plugin.js`).
 *
 * Hermes 설정 창에는 Perdix에 대응하는 기능이 없는 메뉴가 많다(결제, 비밀번호 금고, 음성, Hermes 설정
 * 파일 기반 화면 등). Hermes 앱은 고치지 않으므로(ADR 0036) 메뉴를 지우는 대신, 이 플러그인이 설정 창
 * 메뉴 항목의 `data-tour="nav-<id>"` 표식에 스타일 규칙을 걸어 감춘다. 이 표식은 Hermes 화면 안내
 * 기능이 쓰는 안정 식별자다(apps/desktop/src/app/overlays/overlay-split-layout.tsx).
 *
 * 감추는 기준: Perdix에 연결되지 않아 열어도 실패하거나 저장되지 않는 메뉴. 남기는 메뉴는
 * Model(Main model), Appearance, Notifications, Gateways, Keyboard Shortcuts, Plugins다.
 * 메뉴 구성이 바뀌면 docs/hermes-desktop.md의 표와 함께 이 목록을 고친다.
 *
 * 한국어 메뉴: Hermes에는 한국어 번역이 없다. 원격 모드에서는 서버 언어 팩 동기화(i18n.languages /
 * i18n.catalog)가 연결 순서에 따라 돌지 않을 때가 있어서, 이 플러그인이 시작할 때 SDK의
 * `ctx.i18n.registerAppLocale`로 한국어 팩(locale-ko.ts)을 앱에 직접 등록한다. 언어를 바꾸지는 않는다.
 * 설정 → 모양 → 일반 → 언어에서 "한국어"를 고르면 적용된다.
 */
import { KO_DESKTOP_MESSAGES, type KoMessage } from "./locale-ko.js";

export const SETTINGS_PLUGIN_ID = "perdix-settings";

/** 통째로 감추는 설정 묶음(`nav-<id>`). 하위 항목도 함께 감춘다. */
export const HIDDEN_SETTINGS_GROUPS: readonly string[] = [
  "config:chat", "config:workspace", "config:safety", "config:browser", "vault", "config:memory",
  "config:voice", "config:advanced", "billing", "providers", "keys", "sessions", "about",
];

/** 남기는 묶음 안에서 감추는 하위 항목(`nav-<id>`). */
export const HIDDEN_SETTINGS_ITEMS: readonly string[] = [
  "config:model:fallbacks", "config:model:auxiliary", "config:model:moa",
  "config:appearance:pet", "gateway:managed-updates",
];

/** 감출 메뉴의 CSS. 묶음 줄(`div.relative`)과 하위 항목 상자(`aside > div`)를 통째로 접는다. */
export function hiddenSettingsCss(
  groups: readonly string[] = HIDDEN_SETTINGS_GROUPS, items: readonly string[] = HIDDEN_SETTINGS_ITEMS,
): string {
  const selectors: string[] = [];
  for (const id of groups) {
    selectors.push(`[data-tour="nav-${id}"]`, `[data-tour="nav-toggle-${id}"]`, `[data-tour^="nav-${id}:"]`);
    selectors.push(`div:has(> [data-tour="nav-${id}"])`, `aside > div:has(> [data-tour^="nav-${id}:"])`);
  }
  for (const id of items) selectors.push(`[data-tour="nav-${id}"]`);
  return `${selectors.join(",\n")} { display: none !important; }`;
}

/** Hermes 영어 번역에서 바로 아래 키 이름에 점이 들어가는 곳(예: keybinds.actions["session.slot.1"]). */
export const DOTTED_KEY_PARENTS: readonly string[] = [
  "keybinds.actions", "settings.fieldLabels", "settings.fieldDescriptions", "composer.hotkeyDescs",
];

type MessageTree = { [key: string]: MessageTree | KoMessage };

/**
 * 평평한 키를 Hermes 번역 트리로 펼친다. Hermes는 평평한 팩에서 문자열 값만 받고 배열(입력창 안내,
 * 빈 대화 안내)은 버리지만, 트리로 주면 배열도 그대로 받는다(apps/desktop/src/i18n/registry.ts toTree).
 * 점이 든 키 이름이 있는 곳(DOTTED_KEY_PARENTS) 아래는 나머지를 한 키로 둔다.
 */
export function toMessageTree(
  flat: Readonly<Record<string, KoMessage>>, dottedParents: readonly string[] = DOTTED_KEY_PARENTS,
): MessageTree {
  const root: MessageTree = {};
  for (const [key, value] of Object.entries(flat)) {
    const parent = dottedParents.filter((candidate) => key.startsWith(`${candidate}.`))
      .sort((a, b) => b.length - a.length)[0];
    const parts = parent === undefined ? key.split(".") : [...parent.split("."), key.slice(parent.length + 1)];
    let cursor = root;
    parts.forEach((part, index) => {
      if (index === parts.length - 1) {
        cursor[part] = value;
        return;
      }
      const next = cursor[part];
      if (typeof next !== "object" || next === null || Array.isArray(next)) cursor[part] = {};
      cursor = cursor[part] as MessageTree;
    });
  }
  return root;
}

export const SETTINGS_PLUGIN_SOURCE = `const STYLE_ID = 'perdix-settings-hidden'
const CSS = ${JSON.stringify(hiddenSettingsCss())}
const KO_MESSAGES = ${JSON.stringify(toMessageTree(KO_DESKTOP_MESSAGES))}

export default {
  id: '${SETTINGS_PLUGIN_ID}',
  name: 'Perdix 설정 정리',
  description: 'Perdix에 연결되지 않은 Hermes 설정 메뉴를 감추고 한국어 메뉴를 더합니다.',
  register(ctx) {
    // 플러그인이 다시 불러와지면(파일 변경) 이전 규칙을 바꿔 끼운다.
    let style = document.getElementById(STYLE_ID)
    if (!style) {
      style = document.createElement('style')
      style.id = STYLE_ID
      document.head.appendChild(style)
    }
    style.textContent = CSS
    const dropKorean = ctx && ctx.i18n && typeof ctx.i18n.registerAppLocale === 'function'
      ? ctx.i18n.registerAppLocale('ko', { endonym: '한국어', rtl: false, translations: KO_MESSAGES })
      : null
    return () => {
      style.remove()
      if (typeof dropKorean === 'function') dropKorean()
    }
  }
}
`;
