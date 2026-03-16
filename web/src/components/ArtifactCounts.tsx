type ArtifactCountState = 'pending' | 'stale' | 'ready'

type Counts = {
  pending: number
  stale: number
  ready: number
}

type ArtifactCountsProps = {
  counts: Counts
  className?: string
  compact?: boolean
  showLabels?: boolean
}

function joinClasses(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ')
}

function ArtifactCount({
  state,
  value,
  showLabel,
}: {
  state: ArtifactCountState
  value: number
  showLabel: boolean
}) {
  return (
    <span className={`artifact-count ${state}`}>
      <span className="artifact-count-value">{value}</span>
      {showLabel ? <span className="artifact-count-label">{state}</span> : null}
    </span>
  )
}

export function ArtifactCounts({ counts, className, compact = false, showLabels = false }: ArtifactCountsProps) {
  return (
    <span
      className={joinClasses('artifact-counts', compact && 'compact', className)}
      aria-label={`${counts.pending} pending, ${counts.stale} stale, ${counts.ready} ready`}
    >
      <ArtifactCount state="pending" value={counts.pending} showLabel={showLabels} />
      <ArtifactCount state="stale" value={counts.stale} showLabel={showLabels} />
      <ArtifactCount state="ready" value={counts.ready} showLabel={showLabels} />
    </span>
  )
}
