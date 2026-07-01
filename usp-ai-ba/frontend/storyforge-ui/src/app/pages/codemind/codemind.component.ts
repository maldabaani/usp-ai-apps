import { Component } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

import { environment } from '../../../environments/environment';

@Component({
  selector: 'app-codemind',
  standalone: true,
  imports: [],
  templateUrl: './codemind.component.html',
  styleUrl: './codemind.component.css',
})
export class CodeMindComponent {
  codemindUrl: SafeResourceUrl;

  constructor(private sanitizer: DomSanitizer) {
    this.codemindUrl = this.sanitizer.bypassSecurityTrustResourceUrl(
      `${environment.codemindUrl}/ui/jobs`
    );
  }
}
