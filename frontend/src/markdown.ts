import MarkdownIt from 'markdown-it'
import katex from 'katex'
import texmath from 'markdown-it-texmath'

// Only the parser's generated markup reaches v-html. Model-supplied HTML stays
// escaped, and markdown-it rejects unsafe link schemes such as javascript:.
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true })
markdown.use(texmath, {
  engine: {
    renderToString(source, options) {
      // LLMs often put metric names such as EST_POSS in text without escaping
      // underscores. Normalize only simple text groups inside parsed math;
      // preserve actual subscripts, escaped underscores and Markdown/code.
      const normalized = source.replace(/\\text\{([^{}$]*)\}/g, (_match, text: string) =>
        `\\text{${text.replace(/(?<!\\)_/g, '\\_')}}`)
      return katex.renderToString(normalized, { ...options, macros: { ...options?.macros } })
    },
  } satisfies Pick<typeof katex, 'renderToString'>,
  delimiters: ['dollars', 'brackets'],
  katexOptions: { throwOnError: false, trust: false, maxExpand: 1000, maxSize: 20 },
})

// Photos remain in the validated ResultPanel rather than loading arbitrary
// model-supplied image URLs. Preserve the image description as plain text.
markdown.renderer.rules.image = (tokens, index) =>
  markdown.utils.escapeHtml(tokens[index].content)

markdown.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return renderer.renderToken(tokens, index, options)
}

// Do not pre-decode entities or strip backslashes: that would alter code blocks,
// escaped Markdown examples, and text that should remain literal.
export function renderMarkdown(source: string): string {
  return markdown.render(source)
}
