# DevCrew UI

Angular 19 (standalone components, signals, OnPush, Angular Material) front end for the DevCrew
backend. See the main [README](../README.md#web-ui-phase-7) for features.

```bash
npm ci
npx ng serve          # http://localhost:4200 (backend expected at http://localhost:8080)
npx ng lint
npx ng test --watch=false --browsers=ChromeHeadless
npx ng build          # dist/frontend/browser (served by nginx in the Docker image)
```

The backend URL is set in `src/environments/`.
