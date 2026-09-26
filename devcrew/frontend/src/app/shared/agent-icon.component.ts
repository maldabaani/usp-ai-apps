import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Accent color and emblem per agent role / workflow node kind. */
export const ICON_STYLE: Record<string, { color: string; emblem: string; robot: boolean }> = {
  planner: { color: '#3ee6ff', emblem: '☰', robot: true },
  architect: { color: '#a384ff', emblem: '◇', robot: true },
  developer: { color: '#4d8dff', emblem: '</>', robot: true },
  reviewer: { color: '#ffc14d', emblem: '◉', robot: true },
  qa: { color: '#2de2b0', emblem: '✓', robot: true },
  coordinator: { color: '#ff7ad9', emblem: '⚑', robot: true },
  requirements: { color: '#8fd3ff', emblem: '', robot: false },
  approval: { color: '#ffc14d', emblem: '', robot: false },
  system: { color: '#6fb6ff', emblem: '', robot: false },
  delivery: { color: '#3ddc84', emblem: '', robot: false },
};

/**
 * Inline-SVG icon: a robot head for agents (visor eyes glow in the role's color, the emblem
 * says which role), simple glyphs for the other workflow stages. No icon fonts or assets.
 */
@Component({
  selector: 'app-agent-icon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg [attr.width]="size()" [attr.height]="size()" viewBox="0 0 48 48" [class.active]="active()"
         role="img" [attr.aria-label]="kind() + ' icon'">
      @if (style().robot) {
        <line x1="24" y1="4" x2="24" y2="10" [attr.stroke]="style().color" stroke-width="2" />
        <circle cx="24" cy="4" r="2.6" [attr.fill]="style().color" class="beacon" />
        <rect x="6" y="10" width="36" height="30" rx="10" fill="#dfe9f7" stroke="#9fb4cf" stroke-width="1.2" />
        <rect x="2" y="20" width="5" height="10" rx="2" fill="#9fb4cf" />
        <rect x="41" y="20" width="5" height="10" rx="2" fill="#9fb4cf" />
        <rect x="11" y="16" width="26" height="14" rx="7" fill="#0b1426" />
        <circle cx="18.5" cy="23" r="3.2" [attr.fill]="style().color" class="eye" />
        <circle cx="29.5" cy="23" r="3.2" [attr.fill]="style().color" class="eye" />
        <rect x="18" y="33" width="12" height="2.4" rx="1.2" fill="#9fb4cf" />
        <circle cx="39" cy="40" r="7.5" fill="#0b1426" [attr.stroke]="style().color" stroke-width="1.5" />
        <text x="39" y="43" text-anchor="middle" [attr.fill]="style().color"
              [attr.font-size]="style().emblem.length > 1 ? 6.5 : 9" font-weight="700">{{ style().emblem }}</text>
      } @else {
        @switch (kind()) {
          @case ('requirements') {
            <path d="M12 5h18l8 8v30H12z" fill="#0b1426" [attr.stroke]="style().color" stroke-width="2" />
            <path d="M30 5v8h8" fill="none" [attr.stroke]="style().color" stroke-width="2" />
            <line x1="17" y1="21" x2="33" y2="21" [attr.stroke]="style().color" stroke-width="2" />
            <line x1="17" y1="27" x2="33" y2="27" [attr.stroke]="style().color" stroke-width="2" />
            <line x1="17" y1="33" x2="28" y2="33" [attr.stroke]="style().color" stroke-width="2" />
          }
          @case ('approval') {
            <circle cx="20" cy="14" r="7" fill="#0b1426" [attr.stroke]="style().color" stroke-width="2" />
            <path d="M6 42c0-9 6-15 14-15s14 6 14 15" fill="#0b1426" [attr.stroke]="style().color" stroke-width="2" />
            <circle cx="36" cy="34" r="9" fill="#0b1426" [attr.stroke]="style().color" stroke-width="2" />
            <path d="M31.5 34l3 3 6-6" fill="none" [attr.stroke]="style().color" stroke-width="2.4" />
          }
          @case ('delivery') {
            <circle cx="14" cy="10" r="4.5" fill="none" [attr.stroke]="style().color" stroke-width="2.4" />
            <circle cx="14" cy="38" r="4.5" fill="none" [attr.stroke]="style().color" stroke-width="2.4" />
            <circle cx="34" cy="38" r="4.5" fill="none" [attr.stroke]="style().color" stroke-width="2.4" />
            <line x1="14" y1="14.5" x2="14" y2="33.5" [attr.stroke]="style().color" stroke-width="2.4" />
            <path d="M34 33.5V20a6 6 0 0 0-6-6h-6" fill="none" [attr.stroke]="style().color" stroke-width="2.4" />
            <path d="M25 10l-4 4 4 4" fill="none" [attr.stroke]="style().color" stroke-width="2.4" />
          }
          @default {
            <path d="M24 5l17 9v20l-17 9-17-9V14z" fill="#0b1426" [attr.stroke]="style().color" stroke-width="2" />
            <path d="M7 14l17 9 17-9M24 23v20" fill="none" [attr.stroke]="style().color" stroke-width="2" />
          }
        }
      }
    </svg>
  `,
  styles: `
    :host { display: inline-flex; }
    svg { overflow: visible; }
    .eye { filter: drop-shadow(0 0 2px currentColor); }
    svg.active .eye { animation: blink 2.4s infinite; }
    svg.active .beacon { animation: beacon 1s infinite alternate; }
    @keyframes blink { 0%, 92%, 100% { opacity: 1; } 95% { opacity: 0.15; } }
    @keyframes beacon { from { opacity: 0.35; } to { opacity: 1; } }
  `,
})
export class AgentIconComponent {
  /** A role (planner, architect, developer, reviewer, qa, coordinator) or a node kind. */
  readonly kind = input.required<string>();
  readonly size = input(40);
  readonly active = input(false);
  readonly style = computed(() => ICON_STYLE[this.kind()] ?? ICON_STYLE['system']);
}
