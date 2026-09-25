import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./features/runs-list/runs-list.component').then((m) => m.RunsListComponent),
    title: 'DevCrew · Runs',
  },
  {
    path: 'runs/new',
    loadComponent: () =>
      import('./features/new-run/new-run.component').then((m) => m.NewRunComponent),
    title: 'DevCrew · New run',
  },
  {
    path: 'runs/:id',
    loadComponent: () =>
      import('./features/run-detail/run-detail.component').then((m) => m.RunDetailComponent),
    title: 'DevCrew · Run',
  },
  { path: '**', redirectTo: '' },
];
