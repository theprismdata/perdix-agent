import type { Users } from "@/types/api";

// Perdix 판: `Get started` 안내(Star repo, Join the community)를 그리지 않는다.
// 구분선도 이 안내에 속하므로 아무것도 그리지 않으면 프로젝트 목록 위에 선이 남지 않는다.
export function CustomGetStartedProgress(_props: {
  userData: Users;
  isGithubStarred: boolean;
  isDiscordJoined: boolean;
  handleDismissDialog: () => void;
}) {
  return null;
}

export default CustomGetStartedProgress;
