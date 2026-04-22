import type { ReactNode } from 'react'

type SimpleMarkdownProps = {
  text: string
  className?: string
}

type Block =
  | { kind: 'paragraph'; lines: string[] }
  | { kind: 'unordered-list'; items: string[] }
  | { kind: 'ordered-list'; items: string[] }
  | { kind: 'heading'; level: number; text: string }

export function SimpleMarkdown({ text, className }: SimpleMarkdownProps) {
  const lines = text.split(/\r?\n/)
  const blocks: Block[] = []
  let paragraphLines: string[] = []
  let unorderedListItems: string[] = []
  let orderedListItems: string[] = []

  function flushParagraph() {
    if (!paragraphLines.length) {
      return
    }
    blocks.push({ kind: 'paragraph', lines: [...paragraphLines] })
    paragraphLines = []
  }

  function flushUnorderedList() {
    if (!unorderedListItems.length) {
      return
    }
    blocks.push({ kind: 'unordered-list', items: [...unorderedListItems] })
    unorderedListItems = []
  }

  function flushOrderedList() {
    if (!orderedListItems.length) {
      return
    }
    blocks.push({ kind: 'ordered-list', items: [...orderedListItems] })
    orderedListItems = []
  }

  function flushOpenBlocks() {
    flushParagraph()
    flushUnorderedList()
    flushOrderedList()
  }

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      flushOpenBlocks()
      continue
    }

    const headingMatch = trimmed.match(/^(#{1,6})\s+(.+)$/)
    if (headingMatch) {
      flushOpenBlocks()
      blocks.push({ kind: 'heading', level: headingMatch[1].length, text: headingMatch[2].trim() })
      continue
    }

    const unorderedListMatch = trimmed.match(/^[-*+]\s+(.+)$/)
    if (unorderedListMatch) {
      flushParagraph()
      flushOrderedList()
      unorderedListItems.push(unorderedListMatch[1].trim())
      continue
    }

    const orderedListMatch = trimmed.match(/^\d+\.\s+(.+)$/)
    if (orderedListMatch) {
      flushParagraph()
      flushUnorderedList()
      orderedListItems.push(orderedListMatch[1].trim())
      continue
    }

    flushUnorderedList()
    flushOrderedList()
    paragraphLines.push(trimmed)
  }

  flushOpenBlocks()

  return (
    <div className={className ? `simple-markdown ${className}` : 'simple-markdown'}>
      {blocks.map((block, index) => renderBlock(block, index))}
    </div>
  )
}

function renderBlock(block: Block, index: number): JSX.Element {
  if (block.kind === 'paragraph') {
    const key = `p-${index}`
    return (
      <p key={key}>
        {block.lines.map((line, lineIndex) => (
          <InlineMarkdown key={`${key}-${lineIndex}`} text={line} withBreak={lineIndex < block.lines.length - 1} />
        ))}
      </p>
    )
  }

  if (block.kind === 'unordered-list') {
    return (
      <ul key={`ul-${index}`}>
        {block.items.map((item, itemIndex) => (
          <li key={`ul-${index}-${itemIndex}`}><InlineMarkdown text={item} /></li>
        ))}
      </ul>
    )
  }

  if (block.kind === 'ordered-list') {
    return (
      <ol key={`ol-${index}`}>
        {block.items.map((item, itemIndex) => (
          <li key={`ol-${index}-${itemIndex}`}><InlineMarkdown text={item} /></li>
        ))}
      </ol>
    )
  }

  const HeadingTag = (`h${Math.min(block.level + 1, 6)}` as keyof JSX.IntrinsicElements)
  return <HeadingTag key={`h-${index}`}>{renderInlineMarkdown(block.text)}</HeadingTag>
}

function InlineMarkdown({ text, withBreak = false }: { text: string; withBreak?: boolean }) {
  return (
    <>
      {renderInlineMarkdown(text)}
      {withBreak ? <br /> : null}
    </>
  )
}

function renderInlineMarkdown(text: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const pattern = /(\[[^\]]+\]\((?:https?:\/\/|mailto:)[^)]+\)|`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g
  let lastIndex = 0
  let match: RegExpExecArray | null = pattern.exec(text)

  while (match) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }
    nodes.push(renderInlineToken(match[0], nodes.length))
    lastIndex = match.index + match[0].length
    match = pattern.exec(text)
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }

  return nodes
}

function renderInlineToken(token: string, key: number): ReactNode {
  if (token.startsWith('`') && token.endsWith('`')) {
    return <code key={key}>{token.slice(1, -1)}</code>
  }

  if ((token.startsWith('**') && token.endsWith('**')) || (token.startsWith('__') && token.endsWith('__'))) {
    return <strong key={key}>{renderInlineMarkdown(token.slice(2, -2))}</strong>
  }

  if ((token.startsWith('*') && token.endsWith('*')) || (token.startsWith('_') && token.endsWith('_'))) {
    return <em key={key}>{renderInlineMarkdown(token.slice(1, -1))}</em>
  }

  const linkMatch = token.match(/^\[([^\]]+)\]\(((?:https?:\/\/|mailto:)[^)]+)\)$/)
  if (linkMatch) {
    return (
      <a key={key} href={linkMatch[2]} target="_blank" rel="noreferrer">
        {renderInlineMarkdown(linkMatch[1])}
      </a>
    )
  }

  return token
}
