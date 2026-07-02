import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from '../../services/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.css',
})
export class LoginComponent {
  username = '';
  password = '';
  submitting = false;
  loginError = '';

  constructor(
    private authService: AuthService,
    private router: Router
  ) {}

  get canSubmit(): boolean {
    return !!this.username.trim() && !!this.password && !this.submitting;
  }

  login(): void {
    if (!this.canSubmit) return;
    this.submitting = true;
    this.loginError = '';

    this.authService.login(this.username.trim(), this.password).subscribe({
      next: () => {
        this.submitting = false;
        this.router.navigate(['/']);
      },
      error: (err) => {
        this.submitting = false;
        this.loginError = err?.error?.detail || 'Invalid username or password.';
      },
    });
  }
}
