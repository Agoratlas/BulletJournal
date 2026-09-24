import type { ReactNode } from 'react'

type SimpleMarkdownProps = { text: string; className?: string }
type ListItem = { text: string; children: ListBlock[] }
type ListBlock = { kind: 'list'; ordered: boolean; items: ListItem[] }
type TableAlignment = 'left' | 'center' | 'right'
type Block =
  | { kind: 'paragraph'; lines: string[] }
  | ListBlock
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'rule' }
  | { kind: 'table'; headers: string[]; alignments: TableAlignment[]; rows: string[][] }

function tableCells(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

function isTableSeparator(line: string, count: number): boolean {
  const cells = tableCells(line)
  return cells.length === count && cells.every((cell) => /^:?-{3,}:?$/.test(cell))
}

export function SimpleMarkdown({ text, className }: SimpleMarkdownProps) {
  const lines = text.split(/\r?\n/)
  const blocks: Block[] = []
  let paragraph: string[] = []
  let listStack: Array<{ indent: number; block: ListBlock }> = []

  function flushParagraph() {
    if (paragraph.length) blocks.push({ kind: 'paragraph', lines: paragraph })
    paragraph = []
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const trimmed = line.trim()
    if (!trimmed) {
      flushParagraph()
      listStack = []
      continue
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/)
    const list = line.match(/^(\s*)([-*+]|\d+\.)\s+(.+)$/)
    const headers = trimmed.includes('|') ? tableCells(trimmed) : []
    if (headers.length >= 2 && index + 1 < lines.length && isTableSeparator(lines[index + 1], headers.length)) {
      flushParagraph()
      listStack = []
      const alignments = tableCells(lines[index + 1]).map((cell): TableAlignment => {
        if (cell.startsWith(':') && cell.endsWith(':')) return 'center'
        return cell.endsWith(':') ? 'right' : 'left'
      })
      index += 1
      const rows: string[][] = []
      while (index + 1 < lines.length && lines[index + 1].trim().includes('|')) {
        rows.push(tableCells(lines[++index]))
      }
      blocks.push({ kind: 'table', headers, alignments, rows })
    } else if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushParagraph()
      listStack = []
      blocks.push({ kind: 'rule' })
    } else if (heading) {
      flushParagraph()
      listStack = []
      blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2].trim() })
    } else if (list) {
      flushParagraph()
      const indent = list[1].replace(/\t/g, '  ').length
      const ordered = /\d/.test(list[2][0])
      while (listStack.length && (listStack[listStack.length - 1].indent > indent
        || (listStack[listStack.length - 1].indent === indent && listStack[listStack.length - 1].block.ordered !== ordered))) {
        listStack.pop()
      }
      if (!listStack.length || listStack[listStack.length - 1].indent < indent) {
        const block: ListBlock = { kind: 'list', ordered, items: [] }
        if (listStack.length) {
          const parent = listStack[listStack.length - 1].block.items.at(-1)
          parent?.children.push(block)
        } else {
          blocks.push(block)
        }
        listStack.push({ indent, block })
      }
      listStack[listStack.length - 1].block.items.push({ text: list[3].trim(), children: [] })
    } else {
      listStack = []
      paragraph.push(trimmed)
    }
  }
  flushParagraph()

  return <div className={className ? `simple-markdown ${className}` : 'simple-markdown'}>
    {blocks.map((block, index) => renderBlock(block, index))}
  </div>
}

function renderBlock(block: Block, key: number): JSX.Element {
  if (block.kind === 'paragraph') return <p key={key}>{block.lines.map((line, index) =>
    <InlineMarkdown key={index} text={line} withBreak={index < block.lines.length - 1} />)}</p>
  if (block.kind === 'list') {
    const items = block.items.map((item, index) => <li key={index}>
      <InlineMarkdown text={item.text} />
      {item.children.map((child, childIndex) => renderBlock(child, childIndex))}
    </li>)
    return block.ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>
  }
  if (block.kind === 'rule') return <hr key={key} />
  if (block.kind === 'table') return <div key={key} className="simple-markdown-table-scroll"><table>
    <thead><tr>{block.headers.map((cell, index) => <th key={index} style={{ textAlign: block.alignments[index] }}>{renderInlineMarkdown(cell)}</th>)}</tr></thead>
    <tbody>{block.rows.map((row, index) => <tr key={index}>{block.headers.map((_, column) =>
      <td key={column} style={{ textAlign: block.alignments[column] }}>{renderInlineMarkdown(row[column] ?? '')}</td>)}</tr>)}</tbody>
  </table></div>
  const HeadingTag = (`h${Math.min(block.level + 1, 6)}` as keyof JSX.IntrinsicElements)
  return <HeadingTag key={key}>{renderInlineMarkdown(block.text)}</HeadingTag>
}

function InlineMarkdown({ text, withBreak = false }: { text: string; withBreak?: boolean }) {
  return <>{renderInlineMarkdown(text)}{withBreak ? <br /> : null}</>
}

function renderInlineMarkdown(text: string): ReactNode[] {
  const nodes: ReactNode[] = []
  // Only this narrowly specified color span is interpreted as markup. All other HTML
  // remains ordinary escaped React text; never inject asset HTML into the DOM.
  const pattern = /(<span style="color:#[0-9a-fA-F]{6}">[\s\S]*?<\/span>|\[[^\]]+\]\((?:https?:\/\/|mailto:)[^)]+\)|`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g
  let lastIndex = 0
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0
    if (start > lastIndex) nodes.push(text.slice(lastIndex, start))
    nodes.push(renderInlineToken(match[0], nodes.length))
    lastIndex = start + match[0].length
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

function renderInlineToken(token: string, key: number): ReactNode {
  const colorSpan = token.match(/^<span style="color:(#[0-9a-fA-F]{6})">([\s\S]*?)<\/span>$/)
  if (colorSpan) return <span key={key} style={{ color: colorSpan[1] }}>{renderInlineMarkdown(colorSpan[2])}</span>
  if (token.startsWith('`')) return <code key={key}>{token.slice(1, -1)}</code>
  if (token.startsWith('**') || token.startsWith('__')) return <strong key={key}>{renderInlineMarkdown(token.slice(2, -2))}</strong>
  if (token.startsWith('*') || token.startsWith('_')) return <em key={key}>{renderInlineMarkdown(token.slice(1, -1))}</em>
  const link = token.match(/^\[([^\]]+)\]\(((?:https?:\/\/|mailto:)[^)]+)\)$/)
  if (link) return <a key={key} href={link[2]} target="_blank" rel="noreferrer">{renderInlineMarkdown(link[1])}</a>
  return token
}
