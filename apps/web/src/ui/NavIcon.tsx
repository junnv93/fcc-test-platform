/**
 * Sidebar glyphs (flowdeck-console-ui, icon pass 2026-09-09).
 *
 * ⚠️ Hand-drawn paths, not an icon package. `lucide-react` would be the obvious
 * pick and its geometry is what these follow — 24×24 box, 1.75 stroke, round
 * caps and joins, no fills — but pulling it in costs a runtime dependency and a
 * bundle re-measure for **twenty** glyphs that never change. The shell already
 * made this call once for the brand mark; this is the same call at a slightly
 * larger n, and it can be reversed by swapping this one file.
 *
 * ⚠️ `currentColor` only, no hex. The icon inherits the nav link's colour, so
 * it takes the active/hover/tertiary state for free and never needs a second
 * theme rule. This is also what keeps it past the inline-hex gate.
 *
 * ⚠️ Decorative. Every icon sits beside its own text label, so announcing it
 * would read each nav item twice — `aria-hidden` throughout, and the name is
 * never the accessible name of anything.
 */

export type NavIconName =
  | 'gauge'
  | 'trend'
  | 'grid'
  | 'folder'
  | 'box'
  | 'clipboard'
  | 'sliders'
  | 'antenna'
  | 'bars'
  | 'coverage'
  | 'checklist'
  | 'history'
  | 'shield'
  | 'file'
  | 'books'
  | 'instrument'
  | 'database'
  | 'users'
  | 'plug'
  | 'pulse';

/* Path data only — every glyph is drawn with the same stroke settings, which
   live on the <svg> below rather than being repeated twenty times. */
const PATHS: Record<NavIconName, readonly string[]> = {
  // 계기 바늘 — 「지금 내 시험이 어디쯤」
  gauge: ['M12 14.5 15.5 10', 'M4 18a9 9 0 1 1 16 0', 'M4 18h16'],
  trend: ['M3 17.5 9.5 11l4 4L21 7', 'M21 7h-5', 'M21 7v5'],
  grid: ['M4 4h7v7H4z', 'M13 4h7v7h-7z', 'M4 13h7v7H4z', 'M13 13h7v7h-7z'],
  folder: ['M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5h7A1.5 1.5 0 0 1 19 10v7a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 3 17z'],
  box: ['M12 3.5 20 8v8l-8 4.5L4 16V8z', 'M4 8l8 4.5L20 8', 'M12 12.5V21'],
  clipboard: ['M9 4.5h6v3H9z', 'M9 6H6.5A1.5 1.5 0 0 0 5 7.5v11A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5v-11A1.5 1.5 0 0 0 17.5 6H15', 'M9 12h6', 'M9 16h4'],
  sliders: ['M5 7h14', 'M5 12h14', 'M5 17h14', 'M9 7v0', 'M15 12v0', 'M11 17v0'],
  // 원격 측정 — 안테나. 브랜드 마크와 달리 여기서는 «전파»가 곧 그 화면의 일이다
  antenna: ['M12 13.5v6.5', 'M9.2 10.8a4 4 0 0 1 5.6 0', 'M6.6 8.2a7.7 7.7 0 0 1 10.8 0', 'M12 13.5a1.6 1.6 0 1 0 0-3.2 1.6 1.6 0 0 0 0 3.2z'],
  bars: ['M5 19V11', 'M10 19V5', 'M15 19v-6', 'M20 19v-9'],
  coverage: ['M4 5h16v14H4z', 'M4 10h16', 'M9 10v9', 'M14 5v14'],
  checklist: ['M4 7l2 2 3-3.5', 'M4 15l2 2 3-3.5', 'M12 7.5h8', 'M12 16h8'],
  history: ['M12 8v4.5l3 1.8', 'M4.2 12a7.8 7.8 0 1 0 2.4-5.6', 'M3.6 5.4V10h4.6'],
  shield: ['M12 3.5 19 6v6c0 4-3 7-7 8.5C8 19 5 16 5 12V6z', 'M9.3 12.2l1.9 1.9 3.6-4'],
  file: ['M13 3.5H7.5A1.5 1.5 0 0 0 6 5v14a1.5 1.5 0 0 0 1.5 1.5h9A1.5 1.5 0 0 0 18 19V8.5z', 'M13 3.5V8.5H18', 'M9 13h6', 'M9 16.5h4'],
  books: ['M4 5.5h5v13H4z', 'M9.5 5.5h5v13h-5z', 'M15.6 6.3l3.9 1-3 12-3.9-1z'],
  // 계측기 — 화면과 노브가 있는 앞판
  instrument: ['M3.5 5.5h17v13h-17z', 'M6 8.5h7v5H6z', 'M16 9v0', 'M16 12v0', 'M16 15.5h2'],
  database: ['M12 4c4 0 7 1.1 7 2.5S16 9 12 9 5 7.9 5 6.5 8 4 12 4z', 'M5 6.5v11C5 18.9 8 20 12 20s7-1.1 7-2.5v-11', 'M5 12c0 1.4 3 2.5 7 2.5s7-1.1 7-2.5'],
  users: ['M9 11.5a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z', 'M3.5 19.5a5.5 5.5 0 0 1 11 0', 'M16 5.6a3.2 3.2 0 0 1 0 6.2', 'M17 14.6a5.5 5.5 0 0 1 3.5 4.9'],
  plug: ['M9 3.5v5', 'M15 3.5v5', 'M6.5 8.5h11v3a5.5 5.5 0 0 1-11 0z', 'M12 17v3.5'],
  pulse: ['M3 12h4l2.5-6 4 12 2.5-6H21'],
};

export interface NavIconProps {
  readonly name: NavIconName;
  readonly className?: string;
}

export function NavIcon({ name, className }: NavIconProps): JSX.Element {
  return (
    <svg
      className={className ?? 'nav-icon'}
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name].map((d) => (
        <path d={d} key={d} />
      ))}
    </svg>
  );
}
