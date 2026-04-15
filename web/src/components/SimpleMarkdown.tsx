type SimpleMarkdownProps = {
  text: string
  className?: string
}

export function SimpleMarkdown({ text, className }: SimpleMarkdownProps) {
  const lines = text.split(/\r?\n/)
  const blocks: JSX.Element[] = []
  let paragraphLines: string[] = []
  let listItems: string[] = []

  function flushParagraph() {
    if (!paragraphLines.length) {
      return
    }
    const key = `p-${blocks.length}`
    const linesForBlock = [...paragraphLines]
    paragraphLines = []
    blocks.push(
      <p key={key}>
        {linesForBlock.map((line, index) => (
          <InlineMarkdown key={`${key}-${index}`} text={line} withBreak={index < linesForBlock.length - 1} />
        ))}
      </p>,
    )
  }

  function flushList() {
    if (!listItems.length) {
      return
    }
    const key = `ul-${blocks.length}`
    const itemsForBlock = [...listItems]
    listItems = []
    blocks.push(
      <ul key={key}>
        {itemsForBlock.map((item, index) => (
          <li key={`${key}-${index}`}><InlineMarkdown text={item} /></li>
        ))}
      </ul>,
    )
  }

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      flushParagraph()
      flushList()
      continue
    }
    if (trimmed.startsWith('- ')) {
      flushParagraph()
      listItems.push(trimmed.slice(2))
      continue
    }
    flushList()
    paragraphLines.push(trimmed)
  }

  flushParagraph()
  flushList()

  return <div className={className ? `simple-markdown ${className}` : 'simple-markdown'}>{blocks}</div>
}

function InlineMarkdown({ text, withBreak = false }: { text: string; withBreak?: boolean }) {
  const parts = text.split(/(`[^`]+`)/g)

  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith('`') && part.endsWith('`') && part.length >= 2) {
          return <code key={index}>{part.slice(1, -1)}</code>
        }
        return part
      })}
      {withBreak ? <br /> : null}
    </>
  )
}
