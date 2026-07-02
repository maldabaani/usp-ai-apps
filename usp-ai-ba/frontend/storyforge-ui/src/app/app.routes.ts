import { Routes } from '@angular/router';

import { AssessComponent } from './pages/assess/assess.component';
import { ClarifyComponent } from './pages/clarify/clarify.component';
import { CodeMindComponent } from './pages/codemind/codemind.component';
import { DashboardComponent } from './pages/dashboard/dashboard.component';
import { LandingComponent } from './pages/landing/landing.component';
import { ReviewComponent } from './pages/review/review.component';
import { SettingsComponent } from './pages/settings/settings.component';
import { StatusComponent } from './pages/status/status.component';

export const routes: Routes = [
  { path: '', component: LandingComponent },
  { path: 'ai-ba', component: DashboardComponent },
  { path: 'assess', component: AssessComponent },
  { path: 'clarify/:jobId', component: ClarifyComponent },
  { path: 'review/:jobId', component: ReviewComponent },
  { path: 'status/:jobId', component: StatusComponent },
  { path: 'codemind', component: CodeMindComponent },
  { path: 'settings', component: SettingsComponent },
  { path: '**', redirectTo: '' },
];
