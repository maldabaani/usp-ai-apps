import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ApiService } from './api.service';

describe('ApiService', () => {
  let api: ApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    api = TestBed.inject(ApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('uses the environment base URL and typed endpoints', () => {
    api.createRun({ request: 'Build it now', repo_target: 'me/x', create_repo: true }).subscribe();
    const create = http.expectOne('http://localhost:8080/runs');
    expect(create.request.method).toBe('POST');
    expect(create.request.body.create_repo).toBeTrue();
    create.flush({ id: 'r1' });

    api.resume('r1', { action: 'answer', answer: 'yes', interrupt_id: 'i1' }).subscribe();
    const resume = http.expectOne('http://localhost:8080/runs/r1/resume');
    expect(resume.request.body).toEqual({ action: 'answer', answer: 'yes', interrupt_id: 'i1' });
    resume.flush({});

    api.readFile('r1', 'app/main.py', 'task:T1').subscribe();
    http.expectOne('http://localhost:8080/runs/r1/files/app/main.py?ref=task:T1').flush({});
    api.diff('r1', 'T1').subscribe();
    http.expectOne('http://localhost:8080/runs/r1/diff?task_id=T1').flush({});
  });

  it('builds the events URL with last_event_id for reconnects', () => {
    expect(api.eventsUrl('r1', null)).toBe('http://localhost:8080/runs/r1/events');
    expect(api.eventsUrl('r1', 7)).toBe('http://localhost:8080/runs/r1/events?last_event_id=7');
  });
});
