import { test } from 'node:test'
import assert from 'node:assert/strict'
import { renderMarkdown } from '../src/markdown.ts'

test('renders the offensive rating formula from the reported reply', () => {
  const html = renderMarkdown(String.raw`- **Formula:** $\text{Offensive Rating} = 100 \times \frac{\text{PTS}}{\text{EST_POSS}}$`)
  assert.match(html, /class="katex"/)
  assert.match(html, /<mfrac>/)
  assert.doesNotMatch(html, /katex-error/)
  assert.match(html, /<strong>Formula:<\/strong>/)
})

test('supports inline and display math with both delimiter styles', () => {
  for (const source of [String.raw`$x^2$`, String.raw`\(x^2\)`, '$$\nx^2\n$$', '\\[\nx^2\n\\]']) {
    const html = renderMarkdown(source)
    assert.match(html, /class="katex"/)
    assert.doesNotMatch(html, /katex-error/)
  }
  assert.match(renderMarkdown('$$x^2$$'), /katex-display/)
})

test('metric label compatibility preserves real subscripts and escaped text', () => {
  const html = renderMarkdown(String.raw`$x_i + \text{EST\_POSS}$`)
  assert.match(html, /<msub>/)
  assert.doesNotMatch(html, /katex-error/)
})

test('leaves code, escaped dollars and normal currency literal', () => {
  for (const source of ['`$x^2$`', '```tex\n$x^2$\n```', String.raw`\$x^2\$`, 'Tickets cost $5 or $10.']) {
    assert.doesNotMatch(renderMarkdown(source), /class="katex"/)
  }
})

test('invalid or unfinished math cannot break the rest of the reply', () => {
  assert.match(renderMarkdown(String.raw`$\frac{$` + '\n\nStill readable.'), /Still readable/)
  assert.match(renderMarkdown('$unfinished\n\nStill readable.'), /Still readable/)
})

test('math does not enable HTML or external image injection', () => {
  const html = renderMarkdown(String.raw`<img src=x onerror=alert(1)> $\includegraphics{https://example.com/image.png}$ $\href{javascript:alert(1)}{click}$`)
  assert.doesNotMatch(html, /<img|href="javascript:/)
  assert.match(html, /&lt;img/)
})
