import figlet from "figlet";
import ansiShadowFont from "figlet/fonts/ANSI Shadow";

figlet.parseFont("ANSI Shadow", ansiShadowFont);

const LOGO_COLORS = ["#FFD700", "#F5C916", "#EABB2C", "#E0A73E", "#D69436", "#CD7F32"] as const;
export const PERDIX_LOGO = figlet.textSync("PERDIX", { font: "ANSI Shadow" }).trimEnd()
  .split("\n").map((line, index) => `[${LOGO_COLORS[index] ?? "#CD7F32"}]${line}[/]`).join("\n");
export const PERDIX_HERO = [
  "[#CD7F32]⢀⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⡀[/]",
  "[#CD7F32]⠈⢿⣷⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⡿⠁[/]",
  "[#FFBF00]⠀⠈⢿⣿⣷⣦⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣴⣾⣿⡿⠁⠀[/]",
  "[#FFBF00]⠐⢶⣤⣹⣿⣿⣿⣷⣦⣄⠀⢀⣤⣤⡀⠀⣠⣴⣾⣿⣿⣿⣏⣤⡶⠂[/]",
  "[#FFD700]⠀⠈⠻⣿⣿⣿⣿⣿⣿⣿⣷⣾⣿⠁⣿⣶⣿⣿⣿⣿⣿⣿⣿⠟⠁⠀[/]",
  "[#FFD700]⠐⠶⣦⣬⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⣤⡿⠿⣿⣿⣿⣿⣥⣴⠶⠂[/]",
  "[#FFBF00]⠀⠀⠈⠙⠻⠿⣿⣿⣿⠿⠛⣿⣿⣿⣿⣿⡟⠿⣿⣿⠿⠛⠉⠀⠀[/]",
  "[#FFBF00]⠀⠀⠀⠐⠶⠶⠿⠛⠁⠀⠀⢿⣿⣿⣿⣿⡿⠀⠀⠈⠛⠿⠶⠂⠀⠀⠀[/]",
  "[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢿⣿⣿⣿⡿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]",
  "[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⡾⣿⣿⣿⢷⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]",
  "[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣴⠟⢁⣿⣿⣿⡈⠻⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀[/]",
  "[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠛⠛⠁⣸⣿⣿⣿⣇⠈⠛⠛⠀⠀⠀⠀⠀⠀⠀⠀[/]",
  "[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣰⣿⣿⣿⣿⣿⣆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]",
  "[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠛⠋⢿⣿⡿⠙⠛⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]",
  "[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]",
].join("\n");
