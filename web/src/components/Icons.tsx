import type { SVGProps } from 'react'

function IconBase(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props} />
  )
}

export function Plus(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </IconBase>
  )
}

export function Info(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 10v6" />
      <path d="M12 7h.01" />
    </IconBase>
  )
}

export function Download(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props} strokeWidth="2.5">
      <path d="M12 3.5v11" />
      <path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
      <path d="M4.5 19.5h15" />
    </IconBase>
  )
}

export function Palette(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="M12 3a9 9 0 1 0 0 18c1.1 0 2-.9 2-2 0-.5-.2-.9-.5-1.3-.3-.3-.5-.7-.5-1.2 0-1.1.9-2 2-2h1a5 5 0 0 0 0-10Z" />
      <path d="M7.5 10.5h.01" />
      <path d="M9.5 7.5h.01" />
      <path d="M14.5 7.5h.01" />
      <path d="M16.5 10.5h.01" />
    </IconBase>
  )
}

export function ChevronDown(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="m6 9 6 6 6-6" />
    </IconBase>
  )
}

export function ChevronLeft(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="m15 6-6 6 6 6" />
    </IconBase>
  )
}

export function ChevronsLeft(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="m13.5 6-6 6 6 6" />
      <path d="m18.5 6-6 6 6 6" />
    </IconBase>
  )
}

export function ChevronRight(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="m9 6 6 6-6 6" />
    </IconBase>
  )
}

export function ChevronsRight(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="m5.5 6 6 6-6 6" />
      <path d="m10.5 6 6 6-6 6" />
    </IconBase>
  )
}

export function ChevronUp(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="m6 15 6-6 6 6" />
    </IconBase>
  )
}

export function ChevronsUpDown(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="m7 9 5-5 5 5" />
      <path d="m7 15 5 5 5-5" />
    </IconBase>
  )
}

export function Funnel(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true" {...props}>
      <path d="M4.8 5.5h14.4c.56 0 .86.65.49 1.08L14 13.1v5.03c0 .18-.09.34-.25.43l-3 1.73a.5.5 0 0 1-.75-.43V13.1L4.31 6.58A.7.7 0 0 1 4.8 5.5Z" />
    </svg>
  )
}

export function Cog(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1 1 0 0 0 .2 1.1l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1 1 0 0 0-1.1-.2 1 1 0 0 0-.6.9V20a2 2 0 0 1-4 0v-.1a1 1 0 0 0-.6-.9 1 1 0 0 0-1.1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1 1 0 0 0 .2-1.1 1 1 0 0 0-.9-.6H4a2 2 0 0 1 0-4h.1a1 1 0 0 0 .9-.6 1 1 0 0 0-.2-1.1l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1 1 0 0 0 1.1.2 1 1 0 0 0 .6-.9V4a2 2 0 0 1 4 0v.1a1 1 0 0 0 .6.9 1 1 0 0 0 1.1-.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1 1 0 0 0-.2 1.1 1 1 0 0 0 .9.6H20a2 2 0 0 1 0 4h-.1a1 1 0 0 0-.9.6Z" />
    </IconBase>
  )
}

export function Play(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="M6.4 5.3Q6.4 4 7.6 4.7L18 10.8Q19.8 12 18 13.2L7.6 19.3Q6.4 20 6.4 18.7Z" fill="currentColor" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </IconBase>
  )
}

export function Pencil(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="m15 5 4 4" />
      <path d="M4 20h4l11-11a1.4 1.4 0 0 0 0-2L17 5a1.4 1.4 0 0 0-2 0L4 16v4Z" />
    </IconBase>
  )
}

export function Check(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="m5 12 4.5 4.5L19 7" />
    </IconBase>
  )
}

export function Eye(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
      <circle cx="12" cy="12" r="2.5" />
    </IconBase>
  )
}

export function X(props: SVGProps<SVGSVGElement>) {
  return (
    <IconBase {...props}>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </IconBase>
  )
}
