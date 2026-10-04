declare module 'markdown-it-texmath' {
  import type MarkdownIt from 'markdown-it'
  import type { KatexOptions } from 'katex'
  export default function texmath(markdown: MarkdownIt, options: {
    engine: Pick<typeof import('katex').default, 'renderToString'>
    delimiters: ('dollars' | 'brackets')[]
    katexOptions?: KatexOptions
  }): void
}
