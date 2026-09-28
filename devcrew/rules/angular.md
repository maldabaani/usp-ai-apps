# Angular standards (17+)

Rule ids are cited by the Reviewer as `rule_ref`. Edit freely; keep the `**ID**` markers.

## Components
- **NG-001** Standalone components only (no NgModules).
- **NG-002** `changeDetection: ChangeDetectionStrategy.OnPush` on every component.
- **NG-003** Component state uses signals (`signal`, `computed`); inputs via `input()` / `input.required()`, outputs via `output()`.
- **NG-004** Built-in control flow (`@if`, `@for` with `track`, `@switch`); no `*ngIf` / `*ngFor`.
- **NG-005** Inject dependencies with `inject()`; no constructor-parameter injection in new code.

## Forms & data
- **NG-010** Typed reactive forms (`FormBuilder.nonNullable`, `FormControl<T>`); no template-driven forms.
- **NG-011** HTTP access lives in `@Injectable({ providedIn: 'root' })` services returning typed Observables; components never call `HttpClient` directly.
- **NG-012** Interfaces/types for every API payload in `*.model.ts`.

## Quality
- **NG-020** Strict TypeScript; no `any`.
- **NG-021** Unsubscribe automatically (`takeUntilDestroyed`, `toSignal`, or `async` pipe).
- **NG-022** Lazy-load feature routes with `loadComponent`.

## Tests
- **NG-030** Jasmine/Karma specs next to the file (`*.spec.ts`) using `TestBed` with standalone `imports`.
- **NG-031** Mock HTTP with `provideHttpClient()` + `provideHttpClientTesting()`; no real network.
