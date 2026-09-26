import { Injectable, signal } from '@angular/core';

type Permission = 'default' | 'granted' | 'denied' | 'unsupported';

/** Browser notifications when a run needs you while its page is in the background. */
@Injectable({ providedIn: 'root' })
export class NotifyService {
  readonly permission = signal<Permission>(NotifyService.current());

  private static current(): Permission {
    return typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
  }

  async enable(): Promise<void> {
    if (typeof Notification === 'undefined') {
      return;
    }
    this.permission.set(await Notification.requestPermission());
  }

  /** Shows a notification unless the page is visible (then the page itself shows it). */
  notify(title: string, body: string, tag: string, onClick?: () => void): boolean {
    if (this.permission() !== 'granted' || typeof document === 'undefined' || !document.hidden) {
      return false;
    }
    const n = new Notification(title, { body, tag });
    n.onclick = () => {
      window.focus();
      onClick?.();
      n.close();
    };
    return true;
  }
}
