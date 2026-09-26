# DevCrew benchmark

Run it with `scripts/run_benchmark.py` (see the main README, "Benchmark (Phase 9)").

| Stack | d1 | d2 | d3 | d4 |
|---|---|---|---|---|
| Python / FastAPI | `py-todo-crud` | `py-notes-tags` | `py-library-loans` | `py-shop-orders` |
| Java / Spring Boot | `java-todo-crud` | `java-contacts-validation` | `java-library-loans` | `java-shop-orders` |
| Angular | `ng-counter` | `ng-todo-list` | `ng-signup-form` | `ng-shopping-cart` |

## Layout

- `tasks/<stack>.yaml`: `stack` plus a list of tasks, each with `id`, `difficulty` (1-4),
  `title`, `request` (what the user would type) and an optional `hidden_test_cmd`.
- `hidden_tests/<task_id>/`: files copied over the generated project (at the stack's project
  path) before scoring. The agents never see them.
- `results/`: `<timestamp>.json` and `.md` per benchmark run (git-ignored).

## Hidden-test conventions

The request pins the public contract (HTTP paths, status codes, JSON fields, file paths,
class names, `data-testid`s) because the hidden tests check exactly that, black-box.

| Stack | Hidden files | Command (from the project dir, in the sandbox) | Counted cases |
|---|---|---|---|
| python | `tests_hidden/` (own `conftest.py`, `TestClient` on `app.main:app`) | `pytest -q -p no:cacheprovider tests_hidden` | `def test_*` |
| java | `src/test/java/com/devcrew/app/hidden/Hidden*Test.java` (`@SpringBootTest` + MockMvc) | `mvn test -Dtest='Hidden*Test' -Dsurefire.failIfNoSpecifiedTests=false` | `@Test` |
| angular | `src/hidden/*.spec.ts` (TestBed) | `npx ng test --watch=false --browsers=ChromeHeadless --include=src/hidden` | `it(` |

The expected number of cases is counted from the files. A suite that fails to compile or
collect scores 0 against that number instead of disappearing from the totals.

Hidden tests must not depend on state from other tests: generated apps usually keep one
in-memory store per process. So tests create their own data, use unique names, and assert on
what they created, not on list lengths.

## Adding a task

1. Add an entry to `tasks/<stack>.yaml` with an unambiguous contract in `request`.
2. Put its suite in `hidden_tests/<id>/`, following the conventions above.
3. Check the suite before committing:
   - Run it against a hand-written reference solution built on the stack's template: it
     must pass.
   - Run it against the bare template: it must fail.

   The shipped suites were checked this way. Python and Java ran in the sandbox images;
   Angular ran in headless Chromium.
4. `python scripts/run_benchmark.py --list` validates the task files and shows the counts.
