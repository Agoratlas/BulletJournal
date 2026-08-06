import { useEffect } from 'react'

import { appUrl } from './api'

type Favicon = 'default' | 'dashboard'

export function useDocumentMetadata(title: string | null, favicon: Favicon = 'default') {
  useEffect(() => {
    if (title !== null) {
      document.title = title
    }
    const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
    if (icon) {
      icon.href = appUrl(favicon === 'dashboard' ? '/favicon_dashboard.svg' : '/favicon.svg')
    }
  }, [favicon, title])
}
