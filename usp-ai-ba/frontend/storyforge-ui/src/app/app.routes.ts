import { Routes } from '@angular/router';

import { authGuard } from './auth.guard';
import { AskAllComponent } from './pages/codemind/ask-all/ask-all.component';
import { JobAskComponent } from './pages/codemind/job-ask/job-ask.component';
import { JobDetailComponent } from './pages/codemind/job-detail/job-detail.component';
import { JobsListComponent } from './pages/codemind/jobs-list/jobs-list.component';
import { AssessComponent } from './pages/assess/assess.component';
import { ClarifyComponent } from './pages/clarify/clarify.component';
import { DashboardComponent } from './pages/dashboard/dashboard.component';
import { LandingComponent } from './pages/landing/landing.component';
import { LoginComponent } from './pages/login/login.component';
import { MonitoringComponent } from './pages/monitoring/monitoring.component';
import { ReviewComponent } from './pages/review/review.component';
import { SettingsComponent } from './pages/settings/settings.component';
import { StatusComponent } from './pages/status/status.component';

export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  { path: '', component: LandingComponent, canActivate: [authGuard] },
  { path: 'ai-ba', component: DashboardComponent, canActivate: [authGuard] },
  { path: 'assess', component: AssessComponent, canActivate: [authGuard] },
  { path: 'clarify/:jobId', component: ClarifyComponent, canActivate: [authGuard] },
  { path: 'review/:jobId', component: ReviewComponent, canActivate: [authGuard] },
  { path: 'status/:jobId', component: StatusComponent, canActivate: [authGuard] },
  // 'codemind/ask' must come before 'codemind/:jobId' -- the router matches
  // routes in array order, and ':jobId' would otherwise greedily swallow the
  // literal 'ask' segment.
  { path: 'codemind', component: JobsListComponent, canActivate: [authGuard] },
  { path: 'codemind/ask', component: AskAllComponent, canActivate: [authGuard] },
  { path: 'codemind/:jobId', component: JobDetailComponent, canActivate: [authGuard] },
  { path: 'codemind/:jobId/ask', component: JobAskComponent, canActivate: [authGuard] },
  { path: 'settings', component: SettingsComponent, canActivate: [authGuard] },
  { path: 'monitoring', component: MonitoringComponent, canActivate: [authGuard] },
  { path: '**', redirectTo: '' },
];
