import type { NodeActionItem } from '../appTypes'

export function ActionButtons({
  actions,
  itemClassName,
}: {
  actions: NodeActionItem[]
  itemClassName: string
}) {
  return (
    <>
      {actions.map((action) => {
        const toneClassName = action.tone === 'danger'
          ? ' danger-text'
          : action.tone === 'success'
            ? ' success-text'
            : ''
        const className = `${itemClassName}${toneClassName}${action.className ? ` ${action.className}` : ''}`
        if (action.href) {
          if (action.disabled) {
            return (
              <button key={action.key} className={className} disabled title={action.title}>
                {action.label}
              </button>
            )
          }
          return (
            <a
              key={action.key}
              className={`${className} link-button`}
              href={action.href}
              onClick={action.onClick}
              title={action.title}
              target={action.newTab ? '_blank' : undefined}
              rel={action.newTab ? 'noreferrer noopener' : undefined}
            >
              {action.label}
            </a>
          )
        }
        return (
          <button key={action.key} className={className} onClick={action.onClick} disabled={action.disabled} title={action.title}>
            {action.label}
          </button>
        )
      })}
    </>
  )
}
