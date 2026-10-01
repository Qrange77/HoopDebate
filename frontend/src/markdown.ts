import MarkdownIt from 'markdown-it'

// Only the parser's generated markup reaches v-html. Model-supplied HTML stays
// escaped, and markdown-it rejects unsafe link schemes such as javascript:.
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true })

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
