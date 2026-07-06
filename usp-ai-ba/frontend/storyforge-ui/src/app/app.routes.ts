import { Routes } from '@angular/router';

import { authGuard } from './auth.guard';
import { AskBusinessComponent } from './pages/ask-business/ask-business.component';
import { AskTechnicalComponent } from './pages/ask-technical/ask-technical.component';
import { AssessComponent } from './pages/assess/assess.component';
import { ClarifyComponent } from './pages/clarify/clarify.component';
import { CorpusComponent } from './pages/corpus/corpus.component';
import { DashboardComponent } from './pages/dashboard/dashboard.component';
import { IngestionComponent } from './pages/ingestion/ingestion.component';
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
  { path: 'ingestion', component: IngestionComponent, canActivate: [authGuard] },
  { path: 'corpus', component: CorpusComponent, canActivate: [authGuard] },
  { path: 'ask/technical', component: AskTechnicalComponent, canActivate: [authGuard] },
  { path: 'ask/business', component: AskBusinessComponent, canActivate: [authGuard] },
  { path: 'settings', component: SettingsComponent, canActivate: [authGuard] },
  { path: 'monitoring', component: MonitoringComponent, canActivate: [authGuard] },
  { path: '**', redirectTo: '' },
];
