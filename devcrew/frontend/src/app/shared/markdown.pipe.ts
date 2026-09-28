import { Pipe, PipeTransform } from '@angular/core';
import { marked } from 'marked';

/**
 * Markdown -> HTML. Bind the result with [innerHTML]: Angular's sanitizer strips scripts and
 * event handlers from it, so model-written markdown cannot inject code into the UI.
 */
@Pipe({ name: 'markdown' })
export class MarkdownPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) {
      return '';
    }
    return marked.parse(value, { async: false, gfm: true, breaks: false });
  }
}
