import type { PaletteEntry } from '../appTypes'

export function BlockPalette({
  entries,
  onCreate,
  onInspectTemplate,
  onDragStart,
  onDragEnd,
}: {
  entries: PaletteEntry[]
  onCreate: (entry: PaletteEntry) => Promise<void>
  onInspectTemplate: (ref: string) => void
  onDragStart: (entry: PaletteEntry, position?: { x: number; y: number }) => void
  onDragEnd: () => void
}) {
  const sections = [
    {
      title: 'Core blocks',
      items: entries.filter((entry) => entry.kind === 'empty' || entry.kind === 'value_input' || entry.kind === 'file_input'),
    },
    {
      title: 'Notebook templates',
      items: entries.filter((entry) => entry.kind === 'template'),
    },
    {
      title: 'Pipeline templates',
      items: entries.filter((entry) => entry.kind === 'pipeline'),
    },
  ]

  return (
    <div className="block-palette">
      {sections.map((section) => (
        <section key={section.title} className="palette-section">
          <h3>{section.title}</h3>
          <div className="stack-list templates-list">
            {section.items.map((entry) => {
              return (
                <div key={entry.key} className="template-tile palette-tile">
                  <button
                    className="palette-main draggable-block"
                    onClick={() => void onCreate(entry)}
                    draggable
                    onDragStart={(event) => {
                      event.dataTransfer.effectAllowed = 'copy'
                      event.dataTransfer.setData('text/plain', entry.key)
                      onDragStart(entry, { x: event.clientX, y: event.clientY })
                    }}
                    onDragEnd={onDragEnd}
                  >
                    <strong>{entry.title}</strong>
                    <span>{entry.description}</span>
                  </button>
                  {entry.kind === 'template' || entry.kind === 'value_input' || entry.kind === 'pipeline' ? (
                    <button className="secondary small" onClick={(event) => {
                      event.stopPropagation()
                      if (entry.templateRef) {
                        onInspectTemplate(entry.templateRef)
                      }
                    }}>
                      View
                    </button>
                  ) : null}
                </div>
              )
            })}
            {!section.items.length ? <p className="muted-copy">No matching blocks.</p> : null}
          </div>
        </section>
      ))}
    </div>
  )
}
