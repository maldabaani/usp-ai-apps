import { Component } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

import { environment } from '../../../environments/environment';
import { AuthService } from '../../services/auth.service';

@Component({
  selector: 'app-codemind',
  standalone: true,
  imports: [],
  templateUrl: './codemind.component.html',
  styleUrl: './codemind.component.css',
})
export class CodeMindComponent {
  codemindUrl: SafeResourceUrl;

  constructor(
    private sanitizer: DomSanitizer,
    private authService: AuthService
  ) {
    // CodeMind's Thymeleaf UI is cross-origin inside this iframe and can't
    // share the shell's localStorage, so the token rides along as a query
    // param instead -- CodeMind's JwtAuthFilter accepts it from either place.
    const token = this.authService.getToken();
    const url = `${environment.codemindUrl}/ui/jobs${token ? `?token=${encodeURIComponent(token)}` : ''}`;
    this.codemindUrl = this.sanitizer.bypassSecurityTrustResourceUrl(url);
  }
}
