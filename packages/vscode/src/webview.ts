function attribute(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");
}

export function chatHtml(cspSource: string, script: string, style: string, nonce: string): string {
  return `<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${attribute(cspSource)}; script-src 'nonce-${attribute(nonce)}'; img-src ${attribute(cspSource)};">
<link rel="stylesheet" href="${attribute(style)}"><title>Perdix</title></head>
<body>
<header><div class="brand"><span class="mark">P</span><div><strong>Perdix</strong><small>함께 만드는 코드</small></div></div>
<nav aria-label="대화 메뉴"><button id="history" title="이전 대화">기록</button><button id="new" title="새 대화">＋ 새 대화</button></nav></header>
<main id="messages" aria-label="대화 내용" tabindex="0"><section id="welcome"><span class="eyebrow">YOUR CODING PARTNER</span><h1>무엇을 만들어 볼까요?</h1><p>현재 프로젝트를 살펴보고,<br>궁금한 점부터 수정 작업까지 함께해요.</p><div class="suggestions"><button data-prompt="이 프로젝트의 구조를 설명해줘">프로젝트 구조 이해하기 <span>↗</span></button><button data-prompt="이 프로젝트에서 개선할 부분을 찾아줘. 먼저 계획을 설명해줘.">개선할 부분 찾아보기 <span>↗</span></button></div></section></main>
<section id="permissions" aria-label="작업 승인" aria-live="polite"></section>
<footer><div class="selectors"><select id="mode" aria-label="작업 모드" disabled><option>채팅</option></select><select id="model" aria-label="모델" disabled><option>설치된 기본 모델</option></select></div>
<form id="composer"><textarea id="prompt" rows="3" maxlength="100000" placeholder="Perdix에게 요청하세요…" aria-label="메시지"></textarea><div class="compose-bottom"><span>Enter 전송 · Shift+Enter 줄바꿈</span><button id="cancel" type="button" hidden>■ 중지</button><button id="send" type="submit">전송 ↑</button></div></form>
<div class="statusline"><span id="status" role="status">메시지를 보내면 연결합니다</span><span id="usage"></span></div>
<div class="setup"><button id="folder">폴더 열기</button><button id="keys">키 설정</button><button id="settings">서버 설정</button></div></footer>
<script nonce="${attribute(nonce)}" src="${attribute(script)}"></script></body></html>`;
}
