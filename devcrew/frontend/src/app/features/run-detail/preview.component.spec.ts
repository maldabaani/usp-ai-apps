import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { of } from 'rxjs';

import { PreviewState } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { PreviewComponent } from './preview.component';

const OFF: PreviewState = {
  enabled: true, status: 'off', stack: null, command: null, url: null, started_at: null, error: null, logs: null,
  options: [{ stack: 'python', path: '.', command: 'uvicorn app.main:app --host 0.0.0.0 --port 8000', port: 8000 }],
};

describe('PreviewComponent', () => {
  function setup(first: PreviewState) {
    const api = jasmine.createSpyObj<ApiService>('ApiService', ['preview', 'startPreview', 'stopPreview']);
    api.preview.and.returnValue(of(first));
    TestBed.configureTestingModule({
      imports: [PreviewComponent],
      providers: [provideNoopAnimations(), { provide: ApiService, useValue: api }],
    });
    const fixture = TestBed.createComponent(PreviewComponent);
    fixture.componentRef.setInput('runId', 'r1');
    fixture.detectChanges();
    return { fixture, api, el: fixture.nativeElement as HTMLElement };
  }

  it('starts the preview, polls until it runs and links to it', fakeAsync(() => {
    const { fixture, api, el } = setup(OFF);
    expect(el.textContent).toContain('uvicorn app.main:app');
    api.startPreview.and.returnValue(of({ ...OFF, status: 'installing', command: OFF.options[0].command, options: [] }));
    ([...el.querySelectorAll('button')].find((b) => b.textContent?.includes('Start preview')) as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(api.startPreview).toHaveBeenCalledWith('r1', 'python');
    expect(el.textContent).toContain('installing dependencies');
    api.preview.and.returnValue(of({ ...OFF, status: 'running', url: 'http://localhost:32768/', logs: 'Uvicorn running' }));
    tick(2000);
    fixture.detectChanges();
    const link = el.querySelector('a[href="http://localhost:32768/"]');
    expect(link?.textContent).toContain('Open');
    expect(el.textContent).toContain('Uvicorn running');
    api.stopPreview.and.returnValue(of(undefined));
    api.preview.and.returnValue(of(OFF));
    ([...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Stop') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(api.stopPreview).toHaveBeenCalledWith('r1');
    expect(el.textContent).toContain('not running');
    fixture.destroy();
  }));

  it('explains when the preview is off or has nothing to run', () => {
    expect(setup({ ...OFF, enabled: false }).el.textContent).toContain('PREVIEW_ENABLED=true');
    TestBed.resetTestingModule();
    expect(setup({ ...OFF, options: [] }).el.textContent).toContain('.devcrew.yaml');
  });
});
