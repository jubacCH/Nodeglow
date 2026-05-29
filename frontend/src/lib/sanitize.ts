/** HTML/SVG sanitization helpers built on DOMPurify (isomorphic for SSR safety). */

import DOMPurify from 'isomorphic-dompurify';

/**
 * Sanitize an SVG string before injecting it via dangerouslySetInnerHTML.
 * Enables the SVG profile so legitimate icon markup survives while scripts,
 * event handlers and foreignObject-based XSS vectors are stripped.
 */
export function sanitizeSvg(svg: string): string {
  return DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true } });
}

/**
 * Sanitize a fragment of HTML (default profile) before injecting it via
 * dangerouslySetInnerHTML. Use for rendered markdown / LLM output.
 */
export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html);
}
