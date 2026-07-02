import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';

import { AuthService } from './services/auth.service';

// Attaches the JWT to every outgoing request (both StoryForge's own API and
// CodeMind's, when called directly via HttpClient -- same token, same shared
// secret, that's what makes single sign-on work across the two apps). A 401
// means the token is missing/expired/invalid, so log out and send the user
// back to /login rather than leaving the app stuck on a broken screen.
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const authService = inject(AuthService);
  const token = authService.getToken();
  const authedReq = token
    ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
    : req;

  return next(authedReq).pipe(
    catchError((err) => {
      if (err?.status === 401) {
        authService.logout();
      }
      return throwError(() => err);
    })
  );
};
